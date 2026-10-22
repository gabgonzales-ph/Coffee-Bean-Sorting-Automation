from picamera2 import Picamera2, Preview
import time

# ─── Configuration ────────────────────────────────────────────────────────────

RESOLUTION   = (640, 480)
FRAMERATE    = 15
STABILIZE    = 5   # seconds to let AWB stabilize

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    picam2 = Picamera2()

    config = picam2.create_preview_configuration(
        main={"size": RESOLUTION, "format": "XRGB8888"},
        controls={"FrameRate": FRAMERATE}
    )
    picam2.configure(config)

    # Try preview modes
    try:
        picam2.start_preview(Preview.QTGL)
        print("Preview: QTGL")
    except Exception:
        try:
            picam2.start_preview(Preview.QT)
            print("Preview: QT")
        except Exception:
            picam2.start_preview(Preview.NULL)
            print("Preview: NULL (no display)")

    # Start with full auto — let camera adjust freely
    picam2.start()
    print("\nAuto mode running — letting camera stabilize...")
    print("Make sure your lighting is ON and beans are in position\n")

    # Sample AWB gains every second for STABILIZE seconds
    print("Sampling AWB gains:")
    samples = []
    for i in range(STABILIZE):
        time.sleep(1)
        metadata     = picam2.capture_metadata()
        awb_gains    = metadata.get('ColourGains', (1.5, 1.5))
        exposure     = metadata.get('ExposureTime', 10000)
        analogue_gain= metadata.get('AnalogueGain', 1.0)
        samples.append((awb_gains, exposure, analogue_gain))
        print(f"  [{i+1}/{STABILIZE}] "
              f"ColourGains: {awb_gains} | "
              f"Exposure: {exposure} | "
              f"Gain: {analogue_gain:.2f}")

    # Average the last 3 samples for stability
    last_samples   = samples[-3:]
    avg_r          = sum(s[0][0] for s in last_samples) / len(last_samples)
    avg_b          = sum(s[0][1] for s in last_samples) / len(last_samples)
    avg_exposure   = int(sum(s[1] for s in last_samples) / len(last_samples))
    avg_gain       = sum(s[2] for s in last_samples) / len(last_samples)
    avg_awb        = (avg_r, avg_b)

    print(f"\n--- Averaged Calibrated Values ---")
    print(f"ColourGains  : {avg_awb}")
    print(f"ExposureTime : {avg_exposure}")
    print(f"AnalogueGain : {avg_gain:.2f}")

    # Lock the averaged values
    picam2.set_controls({
        "AwbEnable"  : False,
        "ColourGains": avg_awb
    })
    print("\nAWB locked — checking stability...")
    time.sleep(2)

    picam2.set_controls({
        "ExposureTime"      : avg_exposure,
        "AnalogueGain"      : avg_gain,
        "AwbEnable"         : False,
        "ColourGains"       : avg_awb,
        "Brightness"        : 0.0,
        "Contrast"          : 1.10,
        "Sharpness"         : 5,
        "Saturation"        : 0.95,
        "NoiseReductionMode": 2
    })

    print("\nAll settings locked — preview running")
    print("Check preview window — colors should look natural")
    print("Press Ctrl+C to stop and print final values\n")

    # Monitor live values
    try:
        while True:
            time.sleep(3)
            metadata  = picam2.capture_metadata()
            gains     = metadata.get('ColourGains', (0, 0))
            exposure  = metadata.get('ExposureTime', 0)
            gain      = metadata.get('AnalogueGain', 0)
            print(f"[Live] ColourGains: {gains} | "
                  f"Exposure: {exposure} | "
                  f"Gain: {gain:.2f}")

    except KeyboardInterrupt:
        print("\n\n--- Final Calibrated Values ---")
        print(f"Copy these into camera_config.py:\n")
        print(f"CAMERA_CONTROLS = {{")
        print(f'    "ExposureTime"      : {avg_exposure},')
        print(f'    "AnalogueGain"      : {avg_gain:.2f},')
        print(f'    "AwbEnable"         : False,')
        print(f'    "ColourGains"       : {avg_awb},')
        print(f'    "Brightness"        : 0.0,')
        print(f'    "Contrast"          : 1.10,')
        print(f'    "Sharpness"         : 5,')
        print(f'    "Saturation"        : 0.95,')
        print(f'    "NoiseReductionMode": 2')
        print(f'}}')

    finally:
        picam2.stop_preview()
        picam2.stop()
        picam2.close()
        print("\nCamera closed")

if __name__ == "__main__":
    main()
