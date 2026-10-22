# webcam_config.py
# Settings for the new USB webcam: Sonix "Webcam: Webcam" (usb-0000:01:00.0-1.3).
# Every value below is a starting point taken from THIS camera's own defaults
# and ranges (v4l2-ctl --all). The old camera's values do not carry over: for
# example saturation=11 was fine on the old camera (range 0-20) but is almost
# greyscale here (range 1-128). Tune by eye with SHOW_PREVIEW=1, then lock in.

import os

# The /dev/videoN numbers shift between boots and when other devices change
# (this camera was video1, now video0). The by-id link always points at the
# capture node, so resolve it to the real node at start-up. If the link is
# missing (camera unplugged), fall back to /dev/video0.
_BY_ID   = "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._Webcam_Webcam-video-index0"
_FALLBACK = "/dev/video0"
DEVICE_INDEX = os.path.realpath(_BY_ID) if os.path.exists(_BY_ID) else _FALLBACK

RESOLUTION   = (640, 480)
FRAMERATE    = 30         # needs MJPG: this camera gives 30 fps in MJPG but only 25 fps in YUYV

INITIAL_WAIT = 2          # seconds to let the sensor settle after opening
LOCK_WAIT    = 1          # seconds for locked settings to take effect

# Control names match `v4l2-ctl -d <device> --list-ctrls`.
# ORDER MATTERS: main.py applies these top to bottom, so each auto mode is
# switched to manual BEFORE the absolute value that depends on it.
CAMERA_CONTROLS = {
    "auto_exposure"              : 1,     # 1 = manual, 3 = aperture priority (auto)
    "exposure_time_absolute"     : 200,   # units of 100 us, so 200 = 20 ms. Range 1-5000.
                                          # Keep it at or under 333 (33 ms) or 30 fps is impossible.
                                          # Too dark: raise it (or raise gain). Too bright or blurry: lower it.
    "exposure_dynamic_framerate" : 0,     # 0 = never slow the frame rate down in dim light
    "white_balance_automatic"    : 0,     # 0 = manual, so the temperature below applies
    "white_balance_temperature"  : 4600,  # range 2800-6500, any value (camera default 4600).
                                          # Match your light: warm bulb ~3000, daylight/white LED ~5500-6500.
    "gain"                       : 0,     # range 0-100. Lowest noise at 0; raise only if too dark.
    "brightness"                 : 0,     # range -64 to 64
    "contrast"                   : 32,    # range 0-64 (camera default 32)
    "saturation"                 : 64,    # range 1-128 (camera default 64). Keep this near the
                                          # default: green vs non-green depends on colour.
    "sharpness"                  : 0,     # range 0-6 (0 = no added sharpening)
    "gamma"                      : 100,   # range 72-500 (camera default 100)
    "backlight_compensation"     : 0,     # range 0-2; off so brightness doesn't shift as beans pass
    "power_line_frequency"       : 2,     # 0 = off, 1 = 50 Hz, 2 = 60 Hz (Philippines mains is 60 Hz)
}
