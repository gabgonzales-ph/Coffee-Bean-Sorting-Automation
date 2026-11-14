# capture.py
# Dataset capture for the USB webcam (Sonix). Uses the SAME camera setup as
# main.py (MJPG requested first, resolution, locked v4l2 controls from
# webcam_config.py), so training images match what the model sees in the machine.
# No Picamera2 / libcamera: the USB webcam goes through OpenCV + V4L2.
#
# Stop the service first (it holds the camera):
#   sudo systemctl stop coffee-sorter.service
#   cd ~/coffee_bean && source venv313/bin/activate && python capture.py
#
# This file works from either ~/coffee_bean/capture.py or
# ~/coffee_bean/capture/capture.py. Images are always saved to:
#   ~/coffee_bean/capture/images/{green, non_green, empty, mixed}
#
# FILE NAMES
#   Every image is named <class>_<number>.jpg, the same convention as the
#   dataset: green_0321.jpg, non_green_0501.jpg, mixed_0201.jpg, empty_0062.jpg.
#   One counter per class, 4 digits. A new image gets the next number after the
#   highest one already used for that class: the larger of
#     - LAST_USED_IN_DATASET below (what the dataset on the laptop already has),
#     - the number saved in capture/capture_counter.json (so numbers never restart,
#       even after you move or delete the images from this folder),
#     - the highest number found in the class folder.
#   Undo (U) gives the number back. Existing files are never overwritten.
#   The session and the time of each image are NOT in the name; they are written
#   to capture/capture_log.csv (name, class, session, saved_at_ms, width, height).
#
# CROP AREA
#   A fixed CROP_SIZE (320x320) box sits in the CENTER of the frame. The area
#   outside it is dimmed on the preview, and saved images contain ONLY the box
#   (cropped from the full frame, no overlay). To shift the box, change
#   CROP_OFFSET below. The box is also written to crop_config.json so main.py
#   can use the same numbers.
#
# Keys (click the preview window first):
#   G / N / E / M   save ONE frame now: green / non_green / empty / mixed
#                   (mixed = green and rejects in the same frame, labeled per bean later)
#   1 / 2 / 3 / 4   select the class used by burst mode: green / non_green / empty / mixed
#   B               burst mode on/off: auto-saves a frame every BURST_INTERVAL_SEC
#   U               undo: delete the last saved image (its number is reused)
#   T               conveyor + dispenser motors on/off (to capture beans moving on the belt)
#   C               crop on/off (off = save the full frame)
#   Q               quit

import os
import re
import sys
import csv
import json
import time
import subprocess
import cv2

# webcam_config.py and components/ live in ~/coffee_bean. Find that folder
# whether this script sits in it or in its capture/ subfolder.
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR) if os.path.basename(SCRIPT_DIR) == "capture" else SCRIPT_DIR
sys.path.insert(0, PROJECT_DIR)

from webcam_config import (DEVICE_INDEX, RESOLUTION, FRAMERATE,
                           CAMERA_CONTROLS, INITIAL_WAIT, LOCK_WAIT)

# ─── Settings ───────────────────────────────────────────────────────────────
CAPTURE_DIR  = os.path.join(PROJECT_DIR, "capture")
BASE_DIR     = os.path.join(CAPTURE_DIR, "images")             # capture/images/<class>
COUNTER_FILE = os.path.join(CAPTURE_DIR, "capture_counter.json")
LOG_FILE     = os.path.join(CAPTURE_DIR, "capture_log.csv")
CLASSES    = {"green": "g", "non_green": "n", "empty": "e", "mixed": "m"}
TARGETS    = {"green": 150, "non_green": 150, "empty": 60, "mixed": 400}  # images wanted per folder
# Highest number ALREADY USED per class in the dataset on the laptop. New captures
# continue after it, so names never collide with the dataset. Raise a value here
# if you add more images of that class to the dataset from somewhere else.
LAST_USED_IN_DATASET = {"green": 320, "non_green": 500, "mixed": 200, "empty": 61}
# Capture size: same as main.py (640x480). Training uses imgsz=320.
# main.py must use the same crop (see crop_config.json).
CAPTURE_RESOLUTION = RESOLUTION
PREVIEW_WIDTH      = 640           # the on-screen preview is shrunk to this width (saved image is not)
JPEG_QUALITY       = 95
BURST_INTERVAL_SEC = 0.7     # time between burst saves
BLUR_MIN           = 60.0    # Laplacian variance below this = too blurry, skipped in burst
SESSION            = time.strftime("%Y%m%d_%H%M%S")
CROP_FILE          = os.path.join(PROJECT_DIR, "crop_config.json")
CROP_SIZE   = (320, 320)     # (width, height) of the fixed crop, in capture pixels
CROP_OFFSET = (0, 0)         # (dx, dy) shift from the exact center; +x right, +y down

KEY_TO_CLASS    = {ord("g"): "green", ord("n"): "non_green", ord("e"): "empty", ord("m"): "mixed"}
SELECT_TO_CLASS = {ord("1"): "green", ord("2"): "non_green", ord("3"): "empty", ord("4"): "mixed"}

for name in CLASSES:
    os.makedirs(os.path.join(BASE_DIR, name), exist_ok=True)


def count_images(name):
    folder = os.path.join(BASE_DIR, name)
    return sum(1 for f in os.listdir(folder) if f.lower().endswith(".jpg"))


# ─── File numbering ─────────────────────────────────────────────────────────
NAME_RE = {name: re.compile(rf"^{re.escape(name)}_(\d+)\.jpg$", re.IGNORECASE) for name in CLASSES}


def highest_number_in_folder(name):
    """Highest N among files called <name>_<N>.jpg (older timestamp-style names are ignored)."""
    best = 0
    for f in os.listdir(os.path.join(BASE_DIR, name)):
        m = NAME_RE[name].match(f)
        if m:
            best = max(best, int(m.group(1)))
    return best


def load_counter():
    try:
        with open(COUNTER_FILE) as f:
            data = json.load(f)
        return {k: int(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_counter():
    tmp = COUNTER_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(last_number, f, indent=2)
    os.replace(tmp, COUNTER_FILE)


_saved = load_counter()
last_number = {name: max(LAST_USED_IN_DATASET.get(name, 0),
                         _saved.get(name, 0),
                         highest_number_in_folder(name)) for name in CLASSES}


def file_name(name, n):
    return f"{name}_{n:04d}.jpg"


def append_log(fname, name, img):
    new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["name", "class", "session", "saved_at_ms", "width", "height"])
        w.writerow([fname, name, SESSION, int(time.time() * 1000), img.shape[1], img.shape[0]])


def remove_log_row(fname):
    try:
        with open(LOG_FILE, newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return
    rows = [r for i, r in enumerate(rows) if i == 0 or (r and r[0] != fname)]
    with open(LOG_FILE, "w", newline="") as f:
        csv.writer(f).writerows(rows)


counts = {name: count_images(name) for name in CLASSES}
saved_stack = []          # (class, path, number) saved this run, for undo
burst_on = False
burst_class = "green"
last_burst = 0.0
status_msg = ""
status_until = 0.0
motors_running = False
last_saved = "-"


def sharpness(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def set_status(msg, secs=1.5):
    global status_msg, status_until
    status_msg, status_until = msg, time.time() + secs


# ─── Fixed crop box (stored as fractions of the frame: x0, y0, x1, y1) ──────
crop_norm = [0.0, 0.0, 1.0, 1.0]
crop_on   = True


def compute_crop(frame_w, frame_h):
    """Centered CROP_SIZE box (plus CROP_OFFSET), clamped inside the frame."""
    cw, ch = min(CROP_SIZE[0], frame_w), min(CROP_SIZE[1], frame_h)
    x0 = (frame_w - cw) // 2 + CROP_OFFSET[0]
    y0 = (frame_h - ch) // 2 + CROP_OFFSET[1]
    x0 = max(0, min(x0, frame_w - cw))
    y0 = max(0, min(y0, frame_h - ch))
    return [x0 / frame_w, y0 / frame_h, (x0 + cw) / frame_w, (y0 + ch) / frame_h]


def save_crop(frame_w, frame_h):
    """Write the crop as fractions AND as pixels for the capture size, so
    main.py can use either form."""
    x0, y0, x1, y1 = crop_pixels(frame_w, frame_h)
    data = {
        "x0": crop_norm[0], "y0": crop_norm[1], "x1": crop_norm[2], "y1": crop_norm[3],
        "capture_width": frame_w, "capture_height": frame_h,
        "pixel_box": [x0, y0, x1, y1],
        "crop_width": x1 - x0, "crop_height": y1 - y0,
    }
    with open(CROP_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved crop to {CROP_FILE}: pixels {x0},{y0} -> {x1},{y1}  ({x1 - x0}x{y1 - y0})")


def crop_pixels(w, h, box=None):
    b = box if box is not None else crop_norm
    x0, y0 = int(round(b[0] * w)), int(round(b[1] * h))
    x1, y1 = int(round(b[2] * w)), int(round(b[3] * h))
    return x0, y0, x1, y1


def apply_crop(img):
    """Crop a full-resolution frame to the box (or return it unchanged)."""
    if not crop_on:
        return img
    h, w = img.shape[:2]
    x0, y0, x1, y1 = crop_pixels(w, h)
    return img[y0:y1, x0:x1]


def save_frame(img, name):
    """Save img as <name>_<next number>.jpg. Returns the path, or None if the write failed."""
    global last_saved
    n = last_number[name] + 1
    while os.path.exists(os.path.join(BASE_DIR, name, file_name(name, n))):
        n += 1                                   # never overwrite an existing file
    fname = file_name(name, n)
    path = os.path.join(BASE_DIR, name, fname)
    if not cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
        return None
    last_number[name] = n
    save_counter()
    append_log(fname, name, img)
    saved_stack.append((name, path, n))
    counts[name] += 1
    last_saved = fname
    return path


# ─── Camera (same init order as main.py) ────────────────────────────────────
cap = cv2.VideoCapture(DEVICE_INDEX, cv2.CAP_V4L2)
if not cap.isOpened():
    raise RuntimeError(f"Could not open webcam at {DEVICE_INDEX} (is the service stopped?)")

cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))   # before size/fps
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAPTURE_RESOLUTION[0])
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_RESOLUTION[1])
cap.set(cv2.CAP_PROP_FPS,          FRAMERATE)
got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
got_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print(f"Requested {CAPTURE_RESOLUTION[0]}x{CAPTURE_RESOLUTION[1]}, camera gave {got_w}x{got_h} "
      f"@ {cap.get(cv2.CAP_PROP_FPS):.0f} fps")
if (got_w, got_h) != tuple(CAPTURE_RESOLUTION):
    print("[WARN] Camera does not support that size in MJPG; check: "
          f"v4l2-ctl -d {DEVICE_INDEX} --list-formats-ext")

print(f"Webcam opened at {DEVICE_INDEX}. Settling {INITIAL_WAIT}s...")
time.sleep(INITIAL_WAIT)
# Applied in dict order (each auto mode is switched to manual before the
# value that depends on it), same as main.py.
for ctrl, value in CAMERA_CONTROLS.items():
    r = subprocess.run(["v4l2-ctl", "-d", str(DEVICE_INDEX), f"--set-ctrl={ctrl}={value}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[WARN] {ctrl}={value}: {r.stderr.strip()}")
time.sleep(LOCK_WAIT)

crop_norm = compute_crop(got_w, got_h)
save_crop(got_w, got_h)
print(f"Session {SESSION}. Saving to {BASE_DIR}")
print("Next file names: " + ", ".join(file_name(n, last_number[n] + 1) for n in CLASSES))
print("Keys: G/N/E/M save one | 1/2/3/4 burst class | B burst | U undo | T motors | "
      "C crop on/off | Q quit")
print(f"Fixed center crop {CROP_SIZE[0]}x{CROP_SIZE[1]} (offset {CROP_OFFSET}).")

WINDOW = "Capture"
cv2.namedWindow(WINDOW)

motors_mod = None   # loaded only if T is pressed, so GPIO isn't touched otherwise
no_frame_since = None
no_frame_warned = False

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            # Camera not delivering frames. Don't spin, warn once, and let Q still quit.
            if no_frame_since is None:
                no_frame_since = time.time()
            elif time.time() - no_frame_since > 2.0 and not no_frame_warned:
                print("[WARN] No frames for 2 s - the camera may have dropped off USB "
                      "(check: lsusb, ls /dev/v4l/by-id/)")
                no_frame_warned = True
            time.sleep(0.05)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            continue
        no_frame_since, no_frame_warned = None, False
        raw = frame.copy()          # saved image has no overlay
        raw_h, raw_w = raw.shape[:2]
        now = time.time()

        # Draw on a smaller preview copy so the Pi screen/VNC stays smooth.
        h, w = frame.shape[:2]
        if w > PREVIEW_WIDTH:
            frame = cv2.resize(frame, (PREVIEW_WIDTH, int(h * PREVIEW_WIDTH / w)))
        ph, pw = frame.shape[:2]

        # Sharpness is measured on the area that will actually be saved,
        # at preview scale, so BLUR_MIN stays resolution-independent.
        if crop_on:
            sx0, sy0, sx1, sy1 = crop_pixels(pw, ph)
            region = frame[sy0:sy1, sx0:sx1]
        else:
            region = frame
        blur = sharpness(region) if region.size else 0.0

        # Burst mode (saves the CROPPED image)
        if burst_on and now - last_burst >= BURST_INTERVAL_SEC:
            last_burst = now
            if blur >= BLUR_MIN:
                if save_frame(apply_crop(raw), burst_class) is None:
                    set_status("Save FAILED (disk full?)", 3)
            else:
                set_status(f"Skipped blurry frame ({blur:.0f})", 0.6)

        # Crop box overlay: dim everything outside the box, outline the box
        if crop_on:
            bx0, by0, bx1, by1 = crop_pixels(pw, ph)
            if bx1 > bx0 and by1 > by0:
                dimmed = (frame * 0.4).astype(frame.dtype)
                dimmed[by0:by1, bx0:bx1] = frame[by0:by1, bx0:bx1]
                frame = dimmed
                cv2.rectangle(frame, (bx0, by0), (bx1 - 1, by1 - 1), (0, 200, 0), 2)
                real_w = int(round((crop_norm[2] - crop_norm[0]) * raw_w))
                real_h = int(round((crop_norm[3] - crop_norm[1]) * raw_h))
                cv2.putText(frame, f"{real_w}x{real_h}px", (bx0 + 4, max(by0 - 6, 14)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 2)

        # Text overlay
        y = 24
        for name in CLASSES:
            done = counts[name] >= TARGETS[name]
            color = (0, 255, 0) if done else (255, 255, 255)
            mark = ">" if (name == burst_class) else " "
            cv2.putText(frame, f"{mark}[{CLASSES[name].upper()}] {name}: {counts[name]}/{TARGETS[name]}",
                        (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            y += 24
        mode = f"BURST ON -> {burst_class}" if burst_on else "burst off"
        cv2.putText(frame, f"{mode}   motors {'ON' if motors_running else 'off'}   sharp {blur:.0f}",
                    (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 255, 255) if burst_on else (200, 200, 200), 2)
        y += 24
        crop_txt = ("crop ON" if crop_on else "crop OFF (full frame)") + f"   last: {last_saved}"
        cv2.putText(frame, crop_txt,
                    (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 2)
        if now < status_until:
            cv2.putText(frame, status_msg, (10, frame.shape[0] - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
        if burst_on:
            cv2.circle(frame, (frame.shape[1] - 20, 20), 8, (0, 0, 255), -1)   # recording dot
        cv2.imshow(WINDOW, frame)

        key = cv2.waitKey(1) & 0xFF
        if key == 255:
            continue
        if key == ord("q"):
            break
        elif key in KEY_TO_CLASS:
            name = KEY_TO_CLASS[key]
            out = apply_crop(raw)
            path = save_frame(out, name)
            if path is None:
                set_status("Save FAILED (disk full?)", 3)
            else:
                set_status(f"Saved {os.path.basename(path)} ({counts[name]}/{TARGETS[name]})")
        elif key in SELECT_TO_CLASS:
            burst_class = SELECT_TO_CLASS[key]
            set_status(f"Burst class: {burst_class}")
        elif key == ord("b"):
            burst_on = not burst_on
            last_burst = 0.0
            set_status("Burst " + ("ON" if burst_on else "OFF"))
        elif key == ord("u"):
            if saved_stack:
                name, path, n = saved_stack.pop()
                try:
                    os.remove(path)
                    counts[name] -= 1
                    remove_log_row(os.path.basename(path))
                    if last_number[name] == n:          # give the number back
                        last_number[name] = n - 1
                        save_counter()
                    set_status(f"Undid {os.path.basename(path)}")
                except OSError:
                    set_status("Undo failed")
            else:
                set_status("Nothing to undo")
        elif key == ord("c"):
            crop_on = not crop_on
            set_status("Crop " + ("ON" if crop_on else "OFF (saving full frame)"))
        elif key == ord("t"):
            if motors_mod is None:
                import components.motors as motors_mod
                motors_mod.setup_motors()
            motors_running = not motors_running
            if motors_running:
                motors_mod.motors_on()
            else:
                motors_mod.motors_off()
            set_status("Motors " + ("ON" if motors_running else "OFF"))
finally:
    if motors_mod is not None:
        motors_mod.motors_off()
        motors_mod.cleanup_motors()
        import RPi.GPIO as GPIO
        GPIO.cleanup()
    cap.release()
    cv2.destroyAllWindows()
    print("Final counts:", counts)
