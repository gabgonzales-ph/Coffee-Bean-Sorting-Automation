# components/loadcell.py
# Simple HX711 driver (two 5kg load cells in parallel, channel A, gain 128).
#
# Wiring (BCM): DT -> GPIO5 (pin 29), SCK -> GPIO6 (pin 31), VCC 3.3V, GND
#
# Changes (Nov 16): automatic tare at Start (tare_auto), a lock so the weight
# thread and a tare never read the HX711 at the same time, atomic calibration
# save, and the fixed WEIGHT_OFFSET_G correction removed (the tare replaces it).

import json
import os
import statistics
import threading
import time
import RPi.GPIO as GPIO

DT_PIN = 5
SCK_PIN = 6
MAX_PULSE_S = 50e-6
MAX_PLAUSIBLE_G = 12000.0

# Fixed correction in grams. Keep at 0.0: tare_auto() zeroes the scale before
# every batch, so a fixed correction would be subtracted twice.
WEIGHT_OFFSET_G = 0.0

CAL_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "loadcell_cal.json"
)


class LoadCell:
    def __init__(self, dt_pin=DT_PIN, sck_pin=SCK_PIN):
        self.dt = dt_pin
        self.sck = sck_pin
        self.offset = 0.0
        self.scale = 1.0
        self._skip = 0
        # RLock: the weight thread and tare_auto() must never clock the HX711
        # at the same time. Re-entrant so tare_auto() can call read_raw().
        self._lock = threading.RLock()

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.sck, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.dt, GPIO.IN)

        self.load_calibration()

    def _read_raw(self, timeout=1.0):
        """One 24-bit reading, or None if the HX711 doesn't respond."""
        t0 = time.time()

        while GPIO.input(self.dt) == GPIO.HIGH:
            if time.time() - t0 > timeout:
                return None
            time.sleep(0.001)

        val = 0
        slow = False

        for _ in range(25):
            t = time.perf_counter()
            GPIO.output(self.sck, GPIO.HIGH)
            bit = GPIO.input(self.dt)
            GPIO.output(self.sck, GPIO.LOW)

            if time.perf_counter() - t > MAX_PULSE_S:
                slow = True

            val = (val << 1) | bit

        val >>= 1

        if slow:
            self._skip = 1
            return None

        if self._skip:
            self._skip -= 1
            return None

        if val & 0x800000:
            val -= 1 << 24

        if val in (0x7FFFFF, -0x800000, -1):
            return None

        return val

    def read_raw(self, samples=5):
        """Median of several valid readings."""
        with self._lock:
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
        """Zero the scale with the empty hopper (manual use)."""
        raw = self.read_raw(samples)

        if raw is None:
            raise RuntimeError(
                "HX711 not responding (check wiring/power)"
            )

        self.offset = raw
        self.save_calibration()

        return raw

    def tare_auto(self, groups=6, group_samples=3, max_spread_g=30.0,
                  max_load_g=150.0, min_load_g=-400.0, tries=3):
        """Automatic tare, meant to run when Start is pressed (motors off).

        Takes `groups` median readings and checks they agree (max_spread_g),
        so a moving hopper or vibration is not locked in as the zero.
        Returns (status, grams_before_tare):
          "ok"        offset updated and saved (scale untouched)
          "not_empty" hopper reads more than max_load_g: NOT tared
                      (e.g. beans left after an emergency stop)
          "unstable"  readings kept moving for `tries` attempts: NOT tared
          "error"     HX711 not answering, or reads below min_load_g
                      (wiring or mechanical fault): NOT tared
        """
        for _ in range(tries):
            with self._lock:
                raws = []
                for _ in range(groups):
                    v = self.read_raw(group_samples)
                    if v is not None:
                        raws.append(v)

            if len(raws) < groups // 2 + 1:
                return "error", None

            scale = abs(self.scale) or 1.0
            spread_g = (max(raws) - min(raws)) / scale
            median_raw = statistics.median(raws)
            before = (median_raw - self.offset) / self.scale - WEIGHT_OFFSET_G

            if spread_g > max_spread_g:
                time.sleep(0.5)
                continue
            if before > max_load_g:
                return "not_empty", before
            if before < min_load_g:
                return "error", before

            self.offset = median_raw
            self.save_calibration()
            return "ok", before

        return "unstable", None

    def calibrate(self, known_grams, samples=15):
        """Put a known weight on the hopper, then call this."""
        raw = self.read_raw(samples)

        if raw is None:
            raise RuntimeError(
                "HX711 not responding (check wiring/power)"
            )

        scale = (raw - self.offset) / known_grams

        if scale == 0:
            raise RuntimeError("No load detected")

        self.scale = scale
        self.save_calibration()

        return scale

    def save_calibration(self):
        # Write to a temp file, then replace: a power cut during the write
        # can no longer leave a half-written loadcell_cal.json.
        tmp = CAL_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({
                "offset": self.offset,
                "scale": self.scale
            }, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CAL_FILE)

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
        """Weight in grams relative to the last tare."""
        raw = self.read_raw(samples)

        if raw is None:
            return None

        w = ((raw - self.offset) / self.scale) - WEIGHT_OFFSET_G

        if not (-MAX_PLAUSIBLE_G < w < MAX_PLAUSIBLE_G):
            return None

        return w


class ThresholdDetector:
    """Fires once when weight stays >= target for confirm readings."""

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
