# components/buttons.py
# Push button handling — Start Process button.
# Safe Shutdown is handled separately at the kernel level via the
# gpio-shutdown overlay on GPIO17 (see /boot/firmware/config.txt) —
# it is NOT polled here, to avoid two systems fighting over the same pin.
#
# Import and call setup_buttons() once at startup, then use
# wait_for_start_button() from main.py as needed.

import time
import RPi.GPIO as GPIO

# ─── Pin Config ─────────────────────────────────────────────────────────────
# BCM numbering — physical pin 13. Button wired to common GND (shared with
# the Safe Shutdown button's ground rail).
START_BUTTON_PIN = 27
DEBOUNCE_SEC     = 0.05


def setup_buttons():
    """Configure GPIO mode and pin directions for all buttons.
    Call this once, early in main.py, before using any wait_for_* functions."""
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(START_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)


def wait_for_start_button():
    """Block until the Start Process button is pressed (active LOW, pulled
    up internally). Simple polling debounce — good enough for a manual
    push button, no need for interrupt-based edge detection here."""
    while GPIO.input(START_BUTTON_PIN) == GPIO.HIGH:
        time.sleep(0.05)
    time.sleep(DEBOUNCE_SEC)  # debounce: ignore contact bounce right after press
    # Wait for release before returning, so a long press doesn't get
    # re-read as multiple triggers by whatever comes next.
    while GPIO.input(START_BUTTON_PIN) == GPIO.LOW:
        time.sleep(0.05)


def cleanup_buttons():
    """Release GPIO resources. Call once at program exit."""
    GPIO.cleanup()
