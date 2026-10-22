# components/motors.py
# Conveyor belt and bean dispenser motor control — both motors share a
# single relay channel, so they turn on/off together. Simple on/off,
# no speed control.
#
# Wiring:
#   Relay IN   -> GPIO23 (physical pin 16)
#   Relay VCC  -> Pi 5V (shared rail)
#   Relay GND  -> Pi GND (shared — required for signal reference)
#   Relay COM/NO -> 12V motor circuit, with BOTH motors wired in parallel
#                   off the same relay switch output (see wiring notes)
#
# Import and call setup_motors() once at startup, then use
# motors_on()/motors_off() as needed.

import RPi.GPIO as GPIO

# ─── Pin Config ─────────────────────────────────────────────────────────────
MOTOR_PIN = 23   # BCM numbering — physical pin 16. Drives both motors.

# Most cheap relay modules are ACTIVE LOW (GPIO LOW = relay energized/ON).
# Set this to False if testing shows your module is active HIGH instead —
# i.e. if motors turn ON when you call motors_off() and vice versa, flip this.
ACTIVE_LOW = True

_ON_LEVEL  = GPIO.LOW  if ACTIVE_LOW else GPIO.HIGH
_OFF_LEVEL = GPIO.HIGH if ACTIVE_LOW else GPIO.LOW


def setup_motors():
    """Configure the GPIO pin for the shared motor relay channel.
    Call this once, early in main.py, before using motors_on()/motors_off().
    Starts OFF."""
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(MOTOR_PIN, GPIO.OUT, initial=_OFF_LEVEL)


def motors_on():
    """Turn both the conveyor and dispenser motors on together."""
    GPIO.output(MOTOR_PIN, _ON_LEVEL)
    print("[MOTOR] Conveyor + Dispenser ON")


def motors_off():
    """Turn both the conveyor and dispenser motors off together."""
    GPIO.output(MOTOR_PIN, _OFF_LEVEL)
    print("[MOTOR] Conveyor + Dispenser OFF")


def cleanup_motors():
    """Turn motors off. Call once at program exit, before the final
    GPIO.cleanup() (handled in buttons.py's cleanup_buttons())."""
    motors_off()
