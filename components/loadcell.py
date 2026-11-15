# components/loadcell.py
# Simple HX711 driver (two 5kg load cells in parallel, channel A, gain 128).
#
# Wiring (BCM):  DT -> GPIO5 (pin 29), SCK -> GPIO6 (pin 31), VCC 3.3V, GND

import json
import os
import statistics
import time
import RPi.GPIO as GPIO

DT_PIN = 5
SCK_PIN = 6
MAX_PULSE_S = 50e-6       # HX711 resets if SCK stays high > ~60 us
MAX_PLAUSIBLE_G = 12000.0  # 2 x 5 kg cells can't read more than this

CAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "loadcell_cal.json")


class LoadCell:
    def __init__(self, dt_pin=DT_PIN, sck_pin=SCK_PIN):
        self.dt = dt_pin
        self.sck = sck_pin
        self.offset = 0.0   # raw counts at zero load
        self.scale = 1.0    # raw counts per gram
        self._skip = 0
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.sck, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.dt, GPIO.IN)
        self.load_calibration()

    def _read_raw(self, timeout=1.0):
        """One 24-bit reading, or None if the HX711 doesn't respond."""
        t0 = time.time()
        while GPIO.input(self.dt) == GPIO.HIGH:      # DT low = data ready
            if time.time() - t0 > timeout:
                return None
            time.sleep(0.001)

        val = 0
        slow = False
        for _ in range(25):                          # 24 data bits + 1 gain pulse
            t = time.perf_counter()
            GPIO.output(self.sck, GPIO.HIGH)
            bit = GPIO.input(self.dt)
            GPIO.output(self.sck, GPIO.LOW)
            if time.perf_counter() - t > MAX_PULSE_S:
                slow = True                          # Python was paused mid-pulse
            val = (val << 1) | bit
        val >>= 1                                    # drop the 25th (gain) bit

        if slow:                                     # HX711 may have reset: bad data
            self._skip = 1
            return None
        if self._skip:                               # first reading after a reset
            self._skip -= 1
            return None

        if val & 0x800000:                           # sign-extend 24-bit
            val -= 1 << 24
        if val in (0x7FFFFF, -0x800000, -1):         # saturated / floating DT
            return None
        return val

    def read_raw(self, samples=5):
        """Median of several valid readings. None if fewer than half of the
        requested samples were good."""
        vals = []
        for _ in range(samples * 2):
            v = self._read_raw()
            if v is not None:
                vals.append(v)
                if len(vals) == samples:
                    break
        if len(vals) < (samples + 1) // 2:
            return None
        return statistics.median(vals)

    # ── Calibration ──
    def tare(self, samples=15):
        """Zero the scale with the empty hopper."""
        raw = self.read_raw(samples)
        if raw is None:
            raise RuntimeError("HX711 not responding (check wiring/power)")
        self.offset = raw
        self.save_calibration()
        return raw

    def calibrate(self, known_grams, samples=15):
        """Put a known weight on the hopper, then call this."""
        raw = self.read_raw(samples)
        if raw is None:
            raise RuntimeError("HX711 not responding (check wiring/power)")
        scale = (raw - self.offset) / known_grams
        if scale == 0:
            raise RuntimeError("No load detected")
        self.scale = scale
        self.save_calibration()
        return scale

    def save_calibration(self):
        with open(CAL_FILE, "w") as f:
            json.dump({"offset": self.offset, "scale": self.scale}, f)

    def load_calibration(self):
        try:
            with open(CAL_FILE) as f:
                data = json.load(f)
            self.offset = float(data["offset"])
            self.scale = float(data["scale"])
        except (FileNotFoundError, KeyError, ValueError):
            pass

    # ── Reading ──
    def get_weight_g(self, samples=5):
        """Weight in grams, or None if the sensor didn't respond."""
        raw = self.read_raw(samples)
        if raw is None:
            return None
        w = (raw - self.offset) / self.scale
        if not (-MAX_PLAUSIBLE_G < w < MAX_PLAUSIBLE_G):   # impossible value
            return None
        return w


class ThresholdDetector:
    """Fires once when weight stays >= target for `confirm` readings in a row.
    Call reset() after the flapper has dumped the load."""

    def __init__(self, target_g=2000.0, confirm=3):
        self.target_g = target_g
        self.confirm = confirm
        self._count = 0
        self._fired = False

    def update(self, weight_g):
        if self._fired or weight_g is None:
            return False
        if weight_g >= self.target_g:
            self._count += 1
            if self._count >= self.confirm:
                self._fired = True
                return True
        else:
            self._count = 0
        return False

    def reset(self):
        self._count = 0
        self._fired = False


def cleanup_loadcell():
    GPIO.output(SCK_PIN, GPIO.LOW)
