# camera_config.py
# Calibrated settings — do not change unless lighting/setup changes

RESOLUTION = (640,480)
FRAMERATE  = 30

INITIAL_WAIT = 5    # seconds for auto settings to stabilize
LOCK_WAIT    = 3    # seconds for locked settings to take effect

CAMERA_CONTROLS = {
    "ExposureTime"      : 32000,   # ~half of previous, fits 30fps's ~33.3ms budget
    "AnalogueGain"      : 12,      # raised to compensate for shorter exposure
    "AwbEnable"         : False,
    "ColourGains"       : (1.5394062995910645, 1.388886570930481),
    "Brightness"        : 0.0,
    "Contrast"          : 1.25,
    "Sharpness"         : 0,
    "Saturation"        : 0.7,
    "NoiseReductionMode": 2
}
