# test_webcam.py
# Standalone webcam test: no model, no LCD, no GPIO. Opens the camera the same
# way main.py does, applies webcam_config.py, prints what the camera actually
# negotiated, and shows a live preview with an FPS counter. Press Q to quit.
#
# Stop the service first (it holds the camera):
#   sudo systemctl stop coffee-sorter.service

import cv2
import time
import subprocess
from webcam_config import DEVICE_INDEX, RESOLUTION, FRAMERATE, CAMERA_CONTROLS, INITIAL_WAIT, LOCK_WAIT

cap = cv2.VideoCapture(DEVICE_INDEX, cv2.CAP_V4L2)
if not cap.isOpened():
    raise RuntimeError(f"Could not open webcam at {DEVICE_INDEX}")

# MJPG must be requested BEFORE width/height/fps. Without it OpenCV usually
# picks YUYV, which is capped at 25 fps at 640x480 on this camera.
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  RESOLUTION[0])
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, RESOLUTION[1])
cap.set(cv2.CAP_PROP_FPS,          FRAMERATE)

fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
fourcc_str = "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4))
print(f"Webcam opened at {DEVICE_INDEX}")
print(f"  requested: {RESOLUTION[0]}x{RESOLUTION[1]} @ {FRAMERATE} fps MJPG")
print(f"  negotiated: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} "
      f"@ {cap.get(cv2.CAP_PROP_FPS):.0f} fps, format {fourcc_str!r}")

print(f"Settling... waiting {INITIAL_WAIT}s")
time.sleep(INITIAL_WAIT)

# Dict order = apply order (auto modes first, then the values that depend on them).
for ctrl, value in CAMERA_CONTROLS.items():
    result = subprocess.run(
        ["v4l2-ctl", "-d", DEVICE_INDEX, f"--set-ctrl={ctrl}={value}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[WARN] Failed to set {ctrl}={value}: {result.stderr.strip()}")
    else:
        print(f"Set {ctrl} = {value}")

print(f"Waiting {LOCK_WAIT}s for settings to take effect...")
time.sleep(LOCK_WAIT)
print("Camera ready. Press Q in the preview window to quit.")

fps_counter = 0
fps_timer   = time.time()
fps_text    = "FPS: --"

while True:
    ret, frame = cap.read()
    if not ret:
        print("[WARN] Frame grab failed, retrying")
        continue

    fps_counter += 1
    if time.time() - fps_timer >= 1.0:
        fps = fps_counter / (time.time() - fps_timer)
        fps_counter = 0
        fps_timer   = time.time()
        fps_text    = f"FPS: {fps:.1f}"
        print(f"[FPS] {fps:.1f}")

    cv2.putText(frame, f"Webcam test  {fps_text}  (Q to quit)", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.imshow("Webcam Test", frame)
    if cv2.waitKey(1) == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
print("Stopped")
