"""Add camera-failure handling to main.py. A timestamped backup is kept.

Run on the Pi, inside ~/coffee_bean (service stopped is not required):
    python patch_main.py            # patches main.py
    python patch_main.py other.py   # patches another file

Nothing is written unless all three edit points are found exactly once and
the patched file still compiles.
"""
import re, sys, time, shutil
from pathlib import Path

TARGET = Path(sys.argv[1] if len(sys.argv) > 1 else "main.py")
raw = TARGET.read_bytes()
CRLF = b"\r\n" in raw          # keep the file's own line endings when writing back
text = raw.decode("utf-8").replace("\r\n", "\n")

if "def reopen_camera" in text:
    sys.exit(f"{TARGET} already contains reopen_camera(): patch was applied before.")

# ---------------------------------------------------------------- edit 1
HELPERS = '''# --- Camera failure handling (added by patch_main.py) ----------------------
# If cap.read() returns no frame while RUNNING:
#   CAM_PAUSE_SEC  : stop the belt so beans do not pass uninspected
#   CAM_REOPEN_SEC : close and reopen the webcam (then again every 4 s)
#   CAM_ABORT_SEC  : give up, back to IDLE with "Camera Error" on the LCD
# If frames come back at any point, the belt restarts and the batch continues.
CAM_PAUSE_SEC  = 0.5
CAM_REOPEN_SEC = 2.0
CAM_ABORT_SEC  = 15.0
CAM_BY_ID = "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._Webcam_Webcam-video-index0"
cam_fail_since  = None
cam_next_reopen = 0.0
cam_paused      = False

def reopen_camera():
    """Release and reopen the webcam, then re-apply the locked controls.
    The by-id link always points at the camera's CURRENT /dev/videoN node, so
    this still works if the camera re-enumerated under a new number.
    Returns True if a frame can be read afterwards."""
    global cap
    try:
        cap.release()
    except Exception:
        pass
    time.sleep(1.0)
    dev = os.path.realpath(CAM_BY_ID) if os.path.exists(CAM_BY_ID) else DEVICE_INDEX
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    if not cap.isOpened():
        print(f"[CAMERA] reopen failed: {dev} did not open")
        return False
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, RESOLUTION[1])
    cap.set(cv2.CAP_PROP_FPS,          FRAMERATE)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    for ctrl, value in CAMERA_CONTROLS.items():
        subprocess.run(["v4l2-ctl", "-d", dev, f"--set-ctrl={ctrl}={value}"],
                       capture_output=True, text=True)
    time.sleep(LOCK_WAIT)
    ok, _ = cap.read()
    print(f"[CAMERA] reopened {dev}, first read {'ok' if ok else 'FAILED'}")
    return ok

'''

# ---------------------------------------------------------------- edit 2
FAIL_RE = re.compile(
    r'^    ret, frame_bgr = cap\.read\(\)\n'
    r'    if not ret:\n'
    r'        print\("\[WARN\] Frame grab failed[^\n]*\n'
    r'        continue\n', re.M)

FAIL_NEW = '''    ret, frame_bgr = cap.read()
    if not ret:
        now_f = time.time()
        if cam_fail_since is None:
            cam_fail_since  = now_f
            cam_next_reopen = CAM_REOPEN_SEC
            print("[WARN] Frame grab failed - retrying")
        failed_for = now_f - cam_fail_since
        if failed_for >= CAM_PAUSE_SEC and not cam_paused:
            print(f"[CAMERA] no frame for {failed_for:.1f} s - belt paused so nothing passes unchecked")
            motors_off()
            cam_paused = True
            lcd_row(0, "Camera problem")
            lcd_row(1, "Belt paused")
        if failed_for >= cam_next_reopen:
            cam_next_reopen += 4.0
            reopen_camera()
        if failed_for >= CAM_ABORT_SEC:
            print("[CAMERA] still no frame - aborting batch")
            motors_off()
            tracker.pool   = []
            tracked_beans  = []
            cam_fail_since = None
            cam_paused     = False
            idle_title = "Camera Error"
            state = "IDLE"
            continue
        time.sleep(0.05)
        continue

    if cam_fail_since is not None:
        print(f"[CAMERA] recovered after {time.time() - cam_fail_since:.1f} s")
        cam_fail_since = None
        if cam_paused:
            motors_on()
            cam_paused = False
            last_lcd_lines[0] = last_lcd_lines[1] = ""
'''

# ---------------------------------------------------------------- edit 3
START_RE = re.compile(r'^            detector\.reset\(\)\n            motors_on\(\)\n', re.M)
START_NEW = ('            detector.reset()\n'
             '            cam_fail_since = None\n'
             '            cam_paused     = False\n'
             '            motors_on()\n')

SHUT_RE = re.compile(r'^shutdown_requested = False', re.M)

edits = [("helpers (before 'shutdown_requested = False')", SHUT_RE, lambda m: HELPERS + m.group(0)),
         ("frame-grab failure block", FAIL_RE, lambda m: FAIL_NEW),
         ("Start-button reset", START_RE, lambda m: START_NEW)]

new = text
for label, rx, fn in edits:
    found = rx.findall(new)
    if len(found) != 1:
        sys.exit(f"NOT PATCHED: expected exactly 1 match for {label}, found {len(found)}. "
                 "main.py differs from the version I was given; send me the section.")
    new = rx.sub(fn, new, count=1)

try:
    compile(new, str(TARGET), "exec")
except SyntaxError as e:
    sys.exit(f"NOT PATCHED: patched file would not compile: {e}")

backup = TARGET.with_name(TARGET.name + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
shutil.copy2(TARGET, backup)
TARGET.write_bytes((new.replace("\n", "\r\n") if CRLF else new).encode("utf-8"))
print(f"Patched {TARGET} (backup: {backup.name}). Restart the service to use it.")