# components/rejector.py
# SG90 servo control (hardware PWM): pushes reject beans off the conveyor belt.
#
# Requires in /boot/firmware/config.txt (under [all]), then a reboot:
#   dtoverlay=pwm,pin=12,func=4
# and in venv313:
#   pip install rpi-hardware-pwm
#
# Wiring:
#   Servo signal (orange) -> GPIO12 (physical pin 32), PWM channel 0
#   Servo power  (red)    -> Buck converter 5V output
#   Servo ground (brown)  -> Buck converter GND output, tied to Pi GND
#
# Import and call setup_rejector() once at startup, then use
# kick_reject() from main.py whenever a reject bean is confirmed.
#
# Pulses are sent continuously, including at rest, so the arm holds its
# position firmly. Hardware PWM does not jitter like RPi.GPIO software PWM.

import subprocess
import threading
import time
from rpi_hardware_pwm import HardwarePWM

# --- Pin / PWM Config -------------------------------------------------------
SERVO_PIN   = 12    # BCM numbering, physical pin 32. Used only to force the
                    # pin to its PWM function; the library uses PWM_CHANNEL.
PWM_CHANNEL = 0     # channel 0 = GPIO12 with dtoverlay=pwm,pin=12,func=4
PWM_CHIP    = 0     # chip 0 on Pi 4 (use 2 on Pi 5)
PWM_FREQ    = 50    # SG90 expects a 50Hz control signal

# Pulse widths (duty-cycle %) for each servo position.
# SG90 range is roughly 1ms (0 deg) to 2ms (180 deg) pulse width at 50Hz (20ms period).
#   duty% = (pulse_ms / 20ms) * 100
REST_ANGLE_DUTY = 2.5    # ~0 deg, resting position, out of the beans' path
KICK_ANGLE_DUTY = 6.0    # ~63 deg, swung out; lower until it just clears the bean

KICK_HOLD_SEC   = 0.05   # hold at kick position before returning
KICK_TRAVEL_SEC = 0.12   # time allowed to reach kick position
RETURN_SEC      = 0.12   # time allowed to return to rest
MOVE_SETTLE_SEC = 0.15   # used only by setup_rejector() at startup

# Delay between a reject being confirmed and the arm swinging. Set this to
# (distance from camera to arm) / belt speed, minus the servo travel time.
# 0.0 = kick immediately on confirmation.
KICK_DELAY_SEC  = 0.0

_pwm = None
_lock = threading.Lock()   # one swing at a time; a second reject waits its turn


def setup_rejector():
    """Start hardware PWM for the reject servo at the rest position.
    Call this once, early in main.py, before using kick_reject()."""
    global _pwm
    # Force GPIO12 to its PWM function (ALT0) in case something reset it to GPIO
    subprocess.run(["pinctrl", "set", str(SERVO_PIN), "a0"], check=False)
    _pwm = HardwarePWM(pwm_channel=PWM_CHANNEL, hz=PWM_FREQ, chip=PWM_CHIP)
    _pwm.start(REST_ANGLE_DUTY)
    time.sleep(MOVE_SETTLE_SEC)


def kick_reject():
    """Wait KICK_DELAY_SEC, then swing the servo out to push a reject bean
    off the belt and return to rest. Blocking call. The delay runs outside
    the lock, so closely spaced rejects keep their spacing; the lock only
    makes two swings queue up instead of fighting over the duty cycle."""
    if _pwm is None:
        raise RuntimeError("setup_rejector() must be called before kick_reject()")

    if KICK_DELAY_SEC > 0:
        time.sleep(KICK_DELAY_SEC)

    with _lock:
        if _pwm is None:      # cleanup ran while this kick was waiting
            return
        _pwm.change_duty_cycle(KICK_ANGLE_DUTY)
        time.sleep(KICK_TRAVEL_SEC + KICK_HOLD_SEC)

        _pwm.change_duty_cycle(REST_ANGLE_DUTY)
        time.sleep(RETURN_SEC)
        # Pulses keep running at the rest duty, so the arm stays held in place.


def cleanup_rejector():
    """Return the arm to rest and stop PWM. Call once at program exit.
    Waits for any swing in progress to finish first."""
    global _pwm
    with _lock:
        if _pwm is not None:
            try:
                _pwm.change_duty_cycle(REST_ANGLE_DUTY)
                time.sleep(MOVE_SETTLE_SEC)
            finally:
                _pwm.stop()
                _pwm = None
    # Note: GPIO.cleanup() is called globally in main.py's cleanup section
    # (shared across buttons.py and the flapper); not called here.
