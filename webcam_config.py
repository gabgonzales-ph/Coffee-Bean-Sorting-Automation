# webcam_config.py
# USB webcam: Sonix "Webcam: Webcam" (lsusb 0c45:6366).
#
# CAMERA MODE
#   USE_CAMERA_DEFAULTS = True   the camera's own factory defaults (auto exposure,
#                                auto white balance, ...), read from the camera itself.
#   USE_CAMERA_DEFAULTS = False  the old locked manual values (LOCKED_CONTROLS below).
#
# Why defaults are re-applied instead of "doing nothing": the camera keeps the last
# v4l2 settings until it is unplugged. If an earlier run locked exposure/white balance,
# skipping the controls would leave them locked. So the defaults are set explicitly.
#
# WARNING: with auto exposure and auto white balance the picture changes when the black
# band of the belt fills the frame (brighter, colour shift). The model was trained on
# frames captured with LOCKED settings. If detections get worse (green vs reject colour
# mistakes, beans on the black band), go back to False, or recapture and retrain with
# the defaults.

import os
import re
import subprocess

USE_CAMERA_DEFAULTS = True

# The /dev/videoN numbers shift between boots; the by-id link always points at the
# capture node. If the link is missing (camera dropped off USB), fall back to video0.
_BY_ID   = "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._Webcam_Webcam-video-index0"
_FALLBACK = "/dev/video0"
DEVICE_INDEX = os.path.realpath(_BY_ID) if os.path.exists(_BY_ID) else _FALLBACK

RESOLUTION   = (640, 480)
FRAMERATE    = 30         # needs MJPG: 30 fps in MJPG, only 25 fps in YUYV

INITIAL_WAIT = 2          # seconds to let the sensor settle after opening
LOCK_WAIT    = 1          # seconds for the settings to take effect (auto modes need
                          # a little longer to settle: raise to 2-3 if the first frames are off)

# Controls that must stay out of the defaults: they only work in manual mode and fail
# ("inactive") while the matching auto mode is on.
_MANUAL_ONLY = ("exposure_time_absolute", "white_balance_temperature")

# Put changes here to override a single default, e.g. {"power_line_frequency": 2}
# (2 = 60 Hz mains, Philippines). Empty = pure camera defaults.
DEFAULT_OVERRIDES = {}

# Old manual values (used when USE_CAMERA_DEFAULTS = False).
LOCKED_CONTROLS = {
    "auto_exposure"              : 1,
    "exposure_time_absolute"     : 200,
    "exposure_dynamic_framerate" : 0,
    "white_balance_automatic"    : 0,
    "white_balance_temperature"  : 4600,
    "gain"                       : 0,
    "brightness"                 : 45,
    "contrast"                   : 32,
    "saturation"                 : 32,
    "sharpness"                  : 0,
    "gamma"                      : 100,
    "backlight_compensation"     : 0,
    "power_line_frequency"       : 2,
}

# Used only if the camera cannot be read. Values are the ones known for this camera;
# the rest is left alone.
_FALLBACK_DEFAULTS = {
    "auto_exposure"           : 3,     # aperture priority (auto)
    "white_balance_automatic" : 1,
    "contrast"                : 32,
    "saturation"              : 64,
    "gamma"                   : 100,
}


def read_camera_defaults(device=None):
    """Read each control's default= value from v4l2-ctl --list-ctrls.
    Returns an ordered dict with the auto modes first. {} if the camera can't be read."""
    device = device or DEVICE_INDEX
    try:
        txt = subprocess.run(["v4l2-ctl", "-d", device, "--list-ctrls"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {}
    found = {}
    for line in txt.splitlines():
        m = re.match(r"\s*(\w+)\s+0x[0-9a-f]+\s+\((int|bool|menu|intmenu)\)\s*:(.*)", line)
        if not m:
            continue
        name, rest = m.group(1), m.group(3)
        d = re.search(r"default=(-?\d+)", rest)
        if d and "read-only" not in rest and name not in _MANUAL_ONLY:
            found[name] = int(d.group(1))
    ordered = {}
    for first in ("auto_exposure", "white_balance_automatic"):   # auto modes first
        if first in found:
            ordered[first] = found.pop(first)
    ordered.update(found)
    return ordered


def _build_controls():
    if not USE_CAMERA_DEFAULTS:
        return dict(LOCKED_CONTROLS)
    controls = read_camera_defaults() or dict(_FALLBACK_DEFAULTS)
    controls.update(DEFAULT_OVERRIDES)
    return controls


# main.py applies these top to bottom (auto modes first, so manual-only values are never needed).
CAMERA_CONTROLS = _build_controls()

if __name__ == "__main__":
    print("device:", DEVICE_INDEX)
    print("mode  :", "camera defaults" if USE_CAMERA_DEFAULTS else "locked manual")
    for k, v in CAMERA_CONTROLS.items():
        print(f"  {k:28} {v}")
