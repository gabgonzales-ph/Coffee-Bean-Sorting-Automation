# components/loadcell.py
# HX711 driver for the two 5kg load cells (wired in parallel into ONE HX711,
# channel A, gain 128). Bit-banged with RPi.GPIO, no external library.
#
# Wiring (BCM numbering, change below if needed):
#   HX711 DT  -> GPIO5  (physical pin 29)
#   HX711 SCK -> GPIO6  (physical pin 31)
#   HX711 VCC -> 3.3V, GND -> GND  (use 3.3V so DT is safe for the Pi's input)

import json
import os
import time
import RPi.GPIO as GPIO

DT_PIN  = 5
SCK_PIN = 6
CAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "loadcell_cal.json")


# Half-period of each SCK pulse. The HX711 needs >=0.2us per phase but resets
# if SCK stays HIGH for more than ~60us. Busy-wait (not sleep) to keep it short.
# If readings are still corrupted, try 1e-6 or 5e-6.
CLK_DELAY = 3e-6


def _spin(seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


class LoadCell:
    def __init__(self, dt_pin=DT_PIN, sck_pin=SCK_PIN):
        self.dt  = dt_pin
        self.sck = sck_pin
        self.offset = 0.0   # raw counts at zero load (tare)
        self.scale  = 1.0   # raw counts per gram
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.sck, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.dt, GPIO.IN)
        self.load_calibration()

    # ─── Low level ─────────────────────────────────────────────────────────
    def _read_raw(self, timeout=1.0):
        """One 24-bit conversion. Returns int, or None on timeout/saturation."""
        t0 = time.time()
        while GPIO.input(self.dt) == GPIO.HIGH:
            if time.time() - t0 > timeout:
                return None
            time.sleep(0.001)

        val = 0
        for _ in range(24):
            GPIO.output(self.sck, GPIO.HIGH)
            _spin(CLK_DELAY)
            bit = GPIO.input(self.dt)      # sample while SCK is high
            GPIO.output(self.sck, GPIO.LOW)
            _spin(CLK_DELAY)
            val = (val << 1) | bit

        # 25th pulse: select channel A, gain 128 for the next conversion
        GPIO.output(self.sck, GPIO.HIGH)
        _spin(CLK_DELAY)
        GPIO.output(self.sck, GPIO.LOW)

        if val & 0x800000:          # sign extend 24-bit two's complement
            val -= 1 << 24
        if val in (0x7FFFFF, -0x800000):   # saturated / wiring fault
            return None
        return val

    def read_raw_avg(self, samples=5):
        """Median-trimmed average of several readings. None if no valid data."""
        vals = []
        for _ in range(samples):
            v = self._read_raw()
            if v is not None:
                vals.append(v)
        if not vals:
            return None
        vals.sort()
        if len(vals) >= 5:
            vals = vals[1:-1]       # drop min and max outliers
        return sum(vals) / len(vals)

    # ─── Calibration ───────────────────────────────────────────────────────
    def tare(self, samples=15):
        """Zero the scale. Do this with the empty hopper in place."""
        raw = self.read_raw_avg(samples)
        if raw is None:
            raise RuntimeError("HX711 not responding (check wiring/power)")
        self.offset = raw
        self.save_calibration()
        return raw

    def calibrate(self, known_grams, samples=15):
        """Place a known weight on the hopper, then call this."""
        raw = self.read_raw_avg(samples)
        if raw is None:
            raise RuntimeError("HX711 not responding (check wiring/power)")
        if known_grams <= 0:
            raise ValueError("known_grams must be > 0")
        self.scale = (raw - self.offset) / known_grams
        if self.scale == 0:
            raise RuntimeError("Scale is 0, no load detected")
        self.save_calibration()
        return self.scale

    def save_calibration(self):
        with open(CAL_FILE, "w") as f:
            json.dump({"offset": self.offset, "scale": self.scale}, f)

    def load_calibration(self):
        try:
            with open(CAL_FILE) as f:
                data = json.load(f)
            self.offset = float(data["offset"])
            self.scale  = float(data["scale"])
        except (FileNotFoundError, KeyError, ValueError):
            pass    # uncalibrated; readings are meaningless until calibrated

    # ─── Reading ───────────────────────────────────────────────────────────
    def get_weight_g(self, samples=5):
        """Weight in grams, or None if the sensor didn't respond."""
        raw = self.read_raw_avg(samples)
        if raw is None:
            return None
        return (raw - self.offset) / self.scale


class ThresholdDetector:
    """Fires once when weight stays >= target for `confirm` consecutive
    readings. This stops a single falling bean's impact spike from opening
    the flapper early. Call reset() after the flapper has dumped the load."""

    def __init__(self, target_g=2000.0, confirm=3):
        self.target_g = target_g
        self.confirm  = confirm
        self._count   = 0
        self._fired   = False

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
