# components/buttons.py
# Push button handling.
#
#   Start button  (GPIO27, physical pin 13): starts a batch from IDLE.
#   Stop button   (GPIO17, physical pin 11): the former "Safe Shutdown" button.
#                 During a process it is an emergency stop (back to IDLE);
#                 in IDLE it shuts the Raspberry Pi down safely.
#
# Both are polled in main.py. The kernel gpio-shutdown overlay on GPIO17 must
# be REMOVED from /boot/firmware/config.txt, otherwise the overlay powers the
# Pi off on every press and fights with main.py over the same pin.
#
# Import and call setup_buttons() once at startup.

import time
import RPi.GPIO as GPIO

# ─── Pin Config ─────────────────────────────────────────────────────────────
# BCM numbering. Both buttons are wired to a common GND, active LOW with the
# internal pull-up enabled.
START_BUTTON_PIN = 27     # physical pin 13
STOP_BUTTON_PIN  = 17     # physical pin 11 (Stop / Safe Shutdown)
DEBOUNCE_SEC     = 0.05


def setup_buttons():
    """Configure GPIO mode and pin directions for all buttons.
    Call this once, early in main.py."""
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(START_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(STOP_BUTTON_PIN,  GPIO.IN, pull_up_down=GPIO.PUD_UP)


def wait_for_start_button():
    """Block until the Start button is pressed (active LOW, pulled up
    internally). Simple polling debounce."""
    while GPIO.input(START_BUTTON_PIN) == GPIO.HIGH:
        time.sleep(0.05)
    time.sleep(DEBOUNCE_SEC)  # ignore contact bounce right after press
    # Wait for release so a long press isn't read as multiple triggers.
    while GPIO.input(START_BUTTON_PIN) == GPIO.LOW:
        time.sleep(0.05)


def cleanup_buttons():
    """Release GPIO resources. Call once at program exit."""
    GPIO.cleanup()
