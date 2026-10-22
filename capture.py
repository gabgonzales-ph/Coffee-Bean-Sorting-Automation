#capture.py
import os
import sys
import time
import cv2
from picamera2 import Picamera2

# Ensure the core folder is available for module importing
sys.path.append("/home/admin/coffee_bean")
from camera_config import RESOLUTION, FRAMERATE, CAMERA_CONTROLS, INITIAL_WAIT, LOCK_WAIT

# Base directories configuration
BASE_DIR = "/home/admin/coffee_bean/capture/images"
PATHS = {
    'g': os.path.join(BASE_DIR, "green"),
    'n': os.path.join(BASE_DIR, "non_green"),
    'e': os.path.join(BASE_DIR, "empty")
}

def get_next_image_number(directory):
    """Calculates the next image number based on the current files in that specific folder."""
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
        return 1
        
    files = [f for f in os.listdir(directory) if f.lower().endswith('.jpg')]
    return len(files) + 1

def capture_image(frame, category, prefix):
    """Handles directory path creation, file numbering, and saving the image."""
    target_dir = PATHS[category]
    os.makedirs(target_dir, exist_ok=True)
    
    img_num = get_next_image_number(target_dir)
    filename = f"{prefix}_{img_num}.jpg"
    filepath = os.path.join(target_dir, filename)
    
    cv2.imwrite(filepath, frame)
    print(f"Saved: {filepath} (Folder count: {img_num})")

def main():
    # Pre-create folders for visual verification
    for path in PATHS.values():
        os.makedirs(path, exist_ok=True)

    print("Initializing Picamera2 using production color management profile...")
    picam2 = Picamera2()
    
    # Matching main.py system profile configuration: XRGB8888
    config = picam2.create_video_configuration(
        main={"size": RESOLUTION, "format": "XRGB8888"},
        controls={"FrameRate": FRAMERATE}
    )
    picam2.configure(config)
    picam2.start()

    # Step 1: Let AWB run to settle (Mirroring main.py logic)
    print(f"Adapting to lighting... waiting {INITIAL_WAIT}s")
    picam2.set_controls({
        "AwbEnable":          True,
        "NoiseReductionMode": CAMERA_CONTROLS.get("NoiseReductionMode", 2)
    })
    time.sleep(INITIAL_WAIT)

    # Step 2: Read settled gains
    metadata      = picam2.capture_metadata()
    settled_gains = metadata.get("ColourGains")
    print(f"Settled gains from sensor: {settled_gains}")

    if settled_gains and len(settled_gains) == 2 and all(0.5 < g < 20.0 for g in settled_gains):
        colour_gains = tuple(float(g) for g in settled_gains)
        print(f"Using sensor gains: {colour_gains}")
    else:
        colour_gains = CAMERA_CONTROLS["ColourGains"]
        print(f"Sensor gains out of range — using calibrated profile: {colour_gains}")

    # Step 3: Lock all controls identically to main.py
    picam2.set_controls({
        "AwbEnable":   False,
        "ColourGains": colour_gains,
        "ExposureTime": CAMERA_CONTROLS["ExposureTime"],
        "AnalogueGain": CAMERA_CONTROLS["AnalogueGain"],
        "Brightness":   CAMERA_CONTROLS["Brightness"],
        "Contrast":     CAMERA_CONTROLS["Contrast"],
        "Sharpness":    CAMERA_CONTROLS["Sharpness"],
        "Saturation":   CAMERA_CONTROLS["Saturation"]
    })
    print(f"Waiting {LOCK_WAIT}s for settings to take effect...")
    time.sleep(LOCK_WAIT)
    print("Camera ready")

    print("\n--- Interactive Sorting Camera Active ---")
    print("Press [G] to capture -> green/green_{no}.jpg")
    print("Press [N] to capture -> non_green/non_green_{no}.jpg")
    print("Press [E] to capture -> empty/empty_{no}.jpg")
    print("Press [Q] to safely exit.")
    print("-----------------------------------------")

    try:
        while True:
            raw_frame = picam2.capture_array()
            
            # XRGB8888 → drop X channel → native BGR format layout
            frame_bgr = raw_frame[:, :, :3].copy()
            
            cv2.imshow("Camera Preview", frame_bgr)
            
            # Sync key capture frame rate loop timing windows
            key = cv2.waitKey(int(1000 / FRAMERATE)) & 0xFF

            # Key mappings evaluation
            if key == ord('g'):
                capture_image(frame_bgr, 'g', 'green')
                
            elif key == ord('n'):
                capture_image(frame_bgr, 'n', 'non_green')
                
            elif key == ord('e'):
                capture_image(frame_bgr, 'e', 'empty')
                
            elif key == ord('q'):
                print("Exiting application loop...")
                break

    finally:
        picam2.stop()
        picam2.close()
        cv2.destroyAllWindows()
        print("Camera system interface and window assets released safely.")

if __name__ == "__main__":
    main()
