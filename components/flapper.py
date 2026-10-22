# components/flapper.py
# Servo-actuated flapper door under the hopper. Opens to dump the 2 kg batch
# onto the vibration trays, then closes again.
#
# Wiring (same style as the rejector servo):
#   Servo signal (orange) -> GPIO13 (physical pin 33)
#   Servo power  (red)    -> 5V supply (buck converter, NOT the Pi's 5V pin)
#   Servo ground (brown)  -> supply GND, tied to Pi GND
#
# Import and call setup_flapper() once at startup, then use open_flapper() /
# close_flapper() from main.py.

import time
import RPi.GPIO as GPIO

# ─── Pin / PWM Config ───────────────────────────────────────────────────────
FLAPPER_PIN = 13    # BCM numbering — physical pin 33
PWM_FREQ    = 50    # servo expects a 50Hz control signal

# Duty-cycle % per position (2.5 ≈ 0°, 7.5 ≈ 90°, 12.5 ≈ 180°).
# Tune these to your door's real travel so it fully seals and fully clears.
CLOSED_DUTY = 2.5
OPEN_DUTY   = 7.5

# Time to let the servo physically reach its position. A heavier door or a
# load of beans may need longer than the rejector's 0.15s.
MOVE_SETTLE_SEC = 0.6

# After the door has reached OPEN, stop sending PWM so the servo doesn't
# buzz or jitter. The door is empty at that point so it needs no holding
# torque.
RELEASE_WHEN_OPEN = True

# Same idea for the CLOSED position. Software PWM is timed by the CPU, so
# while inference is running the pulse widths wobble and the servo twitches
# ("flapping"). Releasing the signal once the door is closed stops that, but
# the servo then holds only by gear friction. If 2 kg of beans can push the
# door open, set this back to True and use hardware PWM instead.
HOLD_WHEN_CLOSED = False

_pwm = None


def setup_flapper():
    """Configure GPIO and start PWM at the CLOSED position."""
    global _pwm
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(FLAPPER_PIN, GPIO.OUT)
    _pwm = GPIO.PWM(FLAPPER_PIN, PWM_FREQ)
    _pwm.start(CLOSED_DUTY)
    time.sleep(MOVE_SETTLE_SEC)
    if not HOLD_WHEN_CLOSED:
        _pwm.ChangeDutyCycle(0)


def open_flapper():
    """Swing the door open. Blocking for MOVE_SETTLE_SEC."""
    if _pwm is None:
        raise RuntimeError("setup_flapper() must be called first")
    print("[FLAPPER] Opening")
    _pwm.ChangeDutyCycle(OPEN_DUTY)
    time.sleep(MOVE_SETTLE_SEC)
    if RELEASE_WHEN_OPEN:
        _pwm.ChangeDutyCycle(0)


def close_flapper():
    """Swing the door closed and keep holding it. Blocking."""
    if _pwm is None:
        raise RuntimeError("setup_flapper() must be called first")
    print("[FLAPPER] Closing")
    _pwm.ChangeDutyCycle(CLOSED_DUTY)
    time.sleep(MOVE_SETTLE_SEC)
    if not HOLD_WHEN_CLOSED:
        _pwm.ChangeDutyCycle(0)


def cleanup_flapper():
    """Stop PWM. Call once at exit, before the final GPIO.cleanup()
    (done in buttons.py's cleanup_buttons())."""
    global _pwm
    if _pwm is not None:
        _pwm.stop()
        _pwm = None
