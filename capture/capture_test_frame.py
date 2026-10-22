import sys
import os
sys.path.append('/home/admin/coffee_bean')

from picamera2 import Picamera2
from camera_config import RESOLUTION, CAMERA_CONTROLS
import time
import cv2

print("Script started")

SAVE_DIR = '/home/admin/coffee_bean/capture/tests'
os.makedirs(SAVE_DIR, exist_ok=True)

existing  = len([f for f in os.listdir(SAVE_DIR) if f.endswith('.jpg')])
image_no  = existing + 1
save_path = os.path.join(SAVE_DIR, f'test_frame{image_no}.jpg')
print(f"Save path: {save_path}")

picam2 = Picamera2()
config = picam2.create_still_configuration(
    main={"size": RESOLUTION, "format": "XRGB8888"}
)
picam2.configure(config)
picam2.start()
print("Camera started")

picam2.set_controls({
    "AwbEnable"  : False,
    "ColourGains": CAMERA_CONTROLS["ColourGains"]
})
print("Stage 1 — AWB locked")
time.sleep(2)

picam2.set_controls(CAMERA_CONTROLS)
print("Stage 2 — all settings applied")
time.sleep(1)

print("Capturing frame...")
frame     = picam2.capture_array()
picam2.stop()
picam2.close()

# XRGB8888 on this camera → channels [R, G, B, X]
# Take first 3 channels directly — correct for cv2
frame_out = frame[:, :, :3]
cv2.imwrite(save_path, frame_out)

print(f"Frame shape  : {frame.shape}")
print(f"Image number : {image_no}")
print(f"Saved to     : {save_path}")
