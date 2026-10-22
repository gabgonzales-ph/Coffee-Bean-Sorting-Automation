# test_raw.py
# Dumps raw HX711 readings (decimal + 24-bit binary) with nothing touching
# the hopper, to check how noisy the signal really is.
#   python test_raw.py
import statistics
import RPi.GPIO as GPIO
from components.loadcell import LoadCell

cell = LoadCell()
vals = []
try:
    for i in range(40):
        v = cell._read_raw()
        if v is None:
            print(f"{i:2d}: None")
            continue
        vals.append(v)
        print(f"{i:2d}: {v:9d}  {v & 0xFFFFFF:024b}")
    if len(vals) > 1:
        print(f"\nmin={min(vals)} max={max(vals)} "
              f"spread={max(vals) - min(vals)} stdev={statistics.pstdev(vals):.0f}")
finally:
    GPIO.cleanup()