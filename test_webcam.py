"""test_webcam.py - webcam stability test for the coffee sorter.

Opens the camera exactly like main.py (MJPG, 640x480, locked controls from
webcam_config.py) and reads frames for a set time. If the camera stops
delivering frames it logs the moment, whether the /dev/v4l/by-id link is
still there, the latest USB/UVC kernel messages and how long ago the last
servo kick was, tries to reopen the camera, and prints a summary at the end.

Stop the service first:  sudo systemctl stop coffee-sorter.service

  python test_webcam.py                  10 minutes, no window
  python test_webcam.py --minutes 30
  python test_webcam.py --preview        live window (needs a display), Q quits
  python test_webcam.py --kick 2         also swing the reject servo every 2 s,
                                         to see whether a kick drops the camera
Ctrl+C stops early and still prints the summary.
"""
import argparse
import os
import subprocess
import threading
import time

import cv2
from webcam_config import (DEVICE_INDEX, RESOLUTION, FRAMERATE,
                           CAMERA_CONTROLS, INITIAL_WAIT, LOCK_WAIT)

BY_ID = "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._Webcam_Webcam-video-index0"
REPORT_EVERY = 5.0      # seconds between normal status lines
REOPEN_AFTER = 2.0      # first reopen attempt this long after frames stop
REOPEN_EVERY = 4.0      # then retry this often
GIVE_UP_AFTER = 30.0    # stop the test if the camera is gone this long

ap = argparse.ArgumentParser(description="Webcam stability test")
ap.add_argument("--minutes", type=float, default=10.0, help="test length (default 10)")
ap.add_argument("--preview", action="store_true", help="show a live window (needs a display)")
ap.add_argument("--kick", type=float, default=0.0, metavar="SECONDS",
                help="swing the reject servo every SECONDS while the camera runs")
args = ap.parse_args()


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def camera_node():
    """The camera's CURRENT device node (the by-id link follows re-enumeration)."""
    return os.path.realpath(BY_ID) if os.path.exists(BY_ID) else DEVICE_INDEX


def apply_controls(dev):
    for ctrl, value in CAMERA_CONTROLS.items():
        try:
            r = subprocess.run(["v4l2-ctl", "-d", dev, f"--set-ctrl={ctrl}={value}"],
                               capture_output=True, text=True)
        except FileNotFoundError:
            log("[WARN] v4l2-ctl not found - camera controls not applied")
            return
        if r.returncode != 0:
            log(f"[WARN] could not set {ctrl}={value}: {r.stderr.strip()}")


def open_camera(dev, first=False):
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        return None
    # MJPG must be requested before width/height/fps (same order as main.py)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, RESOLUTION[1])
    cap.set(cv2.CAP_PROP_FPS, FRAMERATE)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if first:
        time.sleep(INITIAL_WAIT)
    apply_controls(dev)
    time.sleep(LOCK_WAIT)
    return cap


def usb_kernel_lines(n=12):
    """Last USB/UVC/power lines from the kernel log, or None if dmesg is not allowed."""
    for cmd in (["dmesg", "-T"], ["sudo", "-n", "dmesg", "-T"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        if r.returncode == 0:
            keys = ("usb", "uvc", "voltage", "over-current")
            lines = [ln for ln in r.stdout.splitlines() if any(k in ln.lower() for k in keys)]
            return lines[-n:]
    return None


# ---------------------------------------------------------------- servo kicks
kick_times = []                 # time.monotonic() at the start of each kick
stop = threading.Event()
rejector_used = False
if args.kick > 0:
    from components.rejector import setup_rejector, kick_reject, cleanup_rejector
    setup_rejector()
    rejector_used = True

    def kick_loop(interval):
        while not stop.wait(interval):
            kick_times.append(time.monotonic())
            kick_reject()

    threading.Thread(target=kick_loop, args=(args.kick,), daemon=True).start()
    log(f"Servo kick every {args.kick:g} s enabled")

# ---------------------------------------------------------------- open camera
log(f"Opening camera {DEVICE_INDEX} ...")
cap = open_camera(DEVICE_INDEX, first=True)
if cap is None:
    stop.set()
    if rejector_used:
        cleanup_rejector()
    raise SystemExit("Could not open the camera. Is the service stopped? "
                     "Check: sudo lsof /dev/video0 /dev/video1")
log(f"Camera opened: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
    f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}, driver reports "
    f"{cap.get(cv2.CAP_PROP_FPS):.0f} fps. Running for {args.minutes:g} min "
    "(Ctrl+C to stop early).")

# ---------------------------------------------------------------- main loop
t0 = time.monotonic()
t_end = t0 + args.minutes * 60
frames = win_frames = 0
win_start = t0
fps_samples = []
outages = []
outage = None
next_reopen = 0.0
gave_up = False

try:
    while time.monotonic() < t_end:
        ok, frame = cap.read()
        now = time.monotonic()

        if ok:
            if outage is not None:
                outage["recovered"] = True
                outage["duration"] = now - outage["start"]
                log(f"[RECOVERED] camera back after {outage['duration']:.1f} s")
                outages.append(outage)
                outage = None
                win_start, win_frames = now, 0
            frames += 1
            win_frames += 1
            if now - win_start >= REPORT_EVERY:
                fps = win_frames / (now - win_start)
                fps_samples.append(fps)
                log(f"[ok] {now - t0:5.0f} s   {fps:5.1f} fps   frames={frames}")
                win_start, win_frames = now, 0
            if args.preview:
                cv2.imshow("test_webcam (Q to quit)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            continue

        # ---- no frame
        if outage is None:
            since_kick = (now - kick_times[-1]) if kick_times else None
            outage = {"start": now, "offset": now - t0, "since_kick": since_kick,
                      "recovered": False, "duration": 0.0, "dmesg_done": False}
            link = "present" if os.path.exists(BY_ID) else "MISSING"
            msg = f"[LOST] no frame at {outage['offset']:.1f} s; by-id link {link}"
            if since_kick is not None:
                msg += f"; last servo kick {since_kick:.2f} s earlier"
            log(msg)
            next_reopen = now + REOPEN_AFTER

        lost_for = now - outage["start"]

        if not outage["dmesg_done"] and lost_for >= 1.0:
            outage["dmesg_done"] = True
            lines = usb_kernel_lines()
            if lines is None:
                log("[KERNEL] dmesg not allowed for this user - run: sudo dmesg -T | tail -30")
            else:
                log("[KERNEL] latest USB/UVC lines:")
                for ln in lines:
                    print("    " + ln, flush=True)

        if now >= next_reopen:
            next_reopen = now + REOPEN_EVERY
            node = camera_node()
            log(f"[REOPEN] trying {node} ...")
            cap.release()
            time.sleep(1.0)
            new = open_camera(node)
            if new is not None:
                cap = new
            else:
                log("[REOPEN] open failed")

        if lost_for >= GIVE_UP_AFTER:
            log(f"[GIVE UP] camera still gone after {lost_for:.0f} s - "
                "unplug and replug it, then rerun.")
            outage["duration"] = lost_for
            outages.append(outage)
            outage = None
            gave_up = True
            break

        time.sleep(0.05)
except KeyboardInterrupt:
    log("Stopped by Ctrl+C")

if outage is not None:                    # test ended while the camera was still lost
    outage["duration"] = time.monotonic() - outage["start"]
    outages.append(outage)

elapsed = time.monotonic() - t0
stop.set()
try:
    cap.release()
except Exception:
    pass
if args.preview:
    cv2.destroyAllWindows()
if rejector_used:
    cleanup_rejector()

# ---------------------------------------------------------------- summary
print("\n===== SUMMARY =====")
print(f"Ran {elapsed:.0f} s, {frames} frames, average {frames / elapsed:.1f} fps"
      if elapsed > 0 else "No time elapsed")
if fps_samples:
    print(f"FPS per {REPORT_EVERY:.0f} s window: min {min(fps_samples):.1f}, "
          f"avg {sum(fps_samples) / len(fps_samples):.1f}, max {max(fps_samples):.1f}")
if args.kick > 0:
    print(f"Servo kicks: {len(kick_times)}")
if not outages:
    print("Camera stayed up the whole time.")
else:
    print(f"Camera dropped {len(outages)} time(s):")
    for i, o in enumerate(outages, 1):
        sk = "no kicks yet" if o["since_kick"] is None else f"{o['since_kick']:.2f} s after a kick"
        state = "recovered" if o["recovered"] else "did NOT come back"
        print(f"  {i}. at {o['offset']:.1f} s, lost for {o['duration']:.1f} s, {state}, {sk}")
    if args.kick > 0:
        WINDOW = 0.5      # a kick lasts about 0.3 s, so a drop caused by it lands within ~0.5 s
        close = [o for o in outages if o["since_kick"] is not None and o["since_kick"] <= WINDOW]
        chance = min(1.0, WINDOW / args.kick) ** len(outages)
        if len(close) == len(outages):
            print(f"All {len(outages)} drop(s) came within {WINDOW:g} s after a kick. With a kick every "
                  f"{args.kick:g} s that would happen by chance only ~{chance * 100:.0f}% of the time, "
                  "so the servo (its power, ground or mounting) is the likely cause.")
            if len(outages) == 1:
                print("Only one drop, so confirm it: run again, and once more WITHOUT --kick "
                      "for the same length. Dropping with kicks and staying up without is the proof.")
        else:
            print("Some drops did not follow a kick closely: the servo is probably not the only cause.")
    else:
        print("This run had no servo kicks. If it stays up here but drops in the machine, "
              "rerun with --kick 2.")
    print("Then check: sudo dmesg -T | grep -i -E 'usb|uvc|voltage' | tail -30")
    if gave_up:
        print("The camera did not return on its own: unplug and replug it before the next test.")