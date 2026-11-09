# test_loadcell.py
# Standalone test for the load cells + LCD. Run from the project root:
#   python test_loadcell.py tare            -> zero with empty hopper
#   python test_loadcell.py calibrate 500   -> with a 500 g known weight on it
#   python test_loadcell.py run             -> live weight on LCD + terminal
#
# Stop coffee-sorter.service first (sudo systemctl stop coffee-sorter.service)
# so it isn't holding the LCD/GPIO.

import sys
import time
import RPi.GPIO as GPIO
from RPLCD.i2c import CharLCD
from components.loadcell import LoadCell, ThresholdDetector, cleanup_loadcell

TARGET_G = 2000.0

lcd = CharLCD('PCF8574', 0x27, cols=16, rows=2)
cell = LoadCell()


def lcd_line(row, text):
    """Overwrite a row in place (no clear(), so no flicker)."""
    lcd.cursor_pos = (row, 0)
    lcd.write_string(text.ljust(16)[:16])


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"

    if cmd == "tare":
        lcd_line(0, "Taring...")
        raw = cell.tare()
        print(f"Tare done. offset={raw:.0f}")
        lcd_line(0, "Tare OK")

    elif cmd == "calibrate":
        known = float(sys.argv[2])
        lcd_line(0, "Calibrating...")
        scale = cell.calibrate(known)
        print(f"Calibrated with {known} g. scale={scale:.4f} counts/g")
        lcd_line(0, "Calibrated")
        lcd_line(1, f"{known:.0f} g ref")

    else:
        det = ThresholdDetector(target_g=TARGET_G, confirm=3)
        lcd_line(0, "Weight:")
        while True:
            w = cell.get_weight_g(samples=5)
            if w is None:
                lcd_line(1, "Sensor error")
                print("[ERR] HX711 no response")
                continue
            print(f"{w:8.1f} g")
            lcd_line(1, f"{w:7.1f} g")
            if det.update(w):
                print(">>> 2 kg reached: flapper would open here")
                lcd_line(0, "2kg reached!")
                time.sleep(2)
                det.reset()
                lcd_line(0, "Weight:")


try:
    main()
except KeyboardInterrupt:
    pass
finally:
    cleanup_loadcell()
    GPIO.cleanup()