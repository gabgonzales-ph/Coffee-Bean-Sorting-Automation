# components/vibration.py
# Vibration motor for the 3-layer size-sorting trays, switched by a relay.
# Simple on/off, same style as components/motors.py.
#
# Wiring:
#   Relay IN   -> GPIO26 (physical pin 37)   <-- CHANGE if different
#   Relay VCC  -> Pi 5V (shared rail)
#   Relay GND  -> Pi GND (shared, required for signal reference)
#   Relay COM/NO -> vibration motor's supply circuit
#
# Import and call setup_vibration() once at startup, then use
# vibration_on()/vibration_off() as needed.

import RPi.GPIO as GPIO

# ─── Pin Config ─────────────────────────────────────────────────────────────
VIBRATION_PIN = 26   # BCM numbering — physical pin 37

# Most cheap relay modules are ACTIVE LOW (GPIO LOW = relay energized/ON).
# If the motor turns ON when it should be off (and vice versa), flip this.
ACTIVE_LOW = False

_ON_LEVEL  = GPIO.LOW  if ACTIVE_LOW else GPIO.HIGH
_OFF_LEVEL = GPIO.HIGH if ACTIVE_LOW else GPIO.LOW


def setup_vibration():
    """Configure the relay pin. Starts OFF."""
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(VIBRATION_PIN, GPIO.OUT, initial=_OFF_LEVEL)


def vibration_on():
    GPIO.output(VIBRATION_PIN, _ON_LEVEL)
    print("[VIBRATION] ON")


def vibration_off():
    GPIO.output(VIBRATION_PIN, _OFF_LEVEL)
    print("[VIBRATION] OFF")


def cleanup_vibration():
    """Turn the motor off. Call once at exit, before the final
    GPIO.cleanup() (done in buttons.py's cleanup_buttons())."""
    vibration_off()
