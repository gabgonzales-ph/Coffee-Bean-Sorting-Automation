# components/loadcell.py
# HX711 driver for the two 5kg load cells (wired in parallel into ONE HX711,
# channel A, gain 128). Bit-banged with RPi.GPIO, no external library.
#
# Wiring (BCM numbering, change below if needed):
#   HX711 DT  -> GPIO5  (physical pin 29)
#   HX711 SCK -> GPIO6  (physical pin 31)
#   HX711 VCC -> 3.3V, GND -> GND  (use 3.3V so DT is safe for the Pi's input)
#
# Hardening added Nov 13:
#   1. Every SCK pulse is timed. The HX711 powers down if SCK stays HIGH for
#      more than ~60 us and the word being read is garbage. On a busy Pi
#      (inference threads) Python can be paused in the middle of a pulse, so a
#      read with a slow pulse is thrown away (and the next conversion too).
#   2. read_raw_avg() uses the MEDIAN of valid samples (a burst of noise on
#      one or two samples no longer moves the result).
#   3. get_weight_g() holds the last good value when the weight jumps by more
#      than MAX_JUMP_G, and only accepts the jump if the next reading agrees
#      with it (within CONFIRM_BAND_G). Real changes (flapper dump) are
#      accepted one reading later; single spikes are ignored.

import json
import os
import statistics
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

# Longest SCK-high time accepted for one pulse (datasheet limit is ~60 us).
HIGH_LIMIT = 50e-6
# Conversions dropped after a glitch (the chip may have reset itself).
DISCARD_AFTER_GLITCH = 1

# Weight spike filter (grams)
MAX_JUMP_G     = 300.0   # a change bigger than this must be confirmed
CONFIRM_BAND_G = 100.0   # two readings this close agree with each other


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
        self._discard = 0
        self._last = None       # last accepted weight (g)
        self._pending = None    # big jump waiting for confirmation (g)
        self.last_spread = 0.0  # max-min of the raw samples of the last average
        self.last_high_us = 0.0
        # Counters for diagnosis (see test_loadcell_noise.py)
        self.stats = {"reads": 0, "glitches": 0, "saturated": 0,
                      "timeouts": 0, "held_spikes": 0, "max_high_us": 0.0}
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.sck, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.dt, GPIO.IN)
        self.load_calibration()

    # ─── Low level ─────────────────────────────────────────────────────────
    def _read_raw(self, timeout=1.0):
        """One 24-bit conversion. Returns int, or None on timeout, saturation
        or a read that was disturbed (slow SCK pulse)."""
        t0 = time.time()
        while GPIO.input(self.dt) == GPIO.HIGH:
            if time.time() - t0 > timeout:
                self.stats["timeouts"] += 1
                return None
            time.sleep(0.001)

        self.stats["reads"] += 1
        val = 0
        worst = 0.0                          # longest SCK-high time seen
        for _ in range(24):
            t = time.perf_counter()
            GPIO.output(self.sck, GPIO.HIGH)
            _spin(CLK_DELAY)
            bit = GPIO.input(self.dt)        # sample while SCK is high
            GPIO.output(self.sck, GPIO.LOW)
            high = time.perf_counter() - t
            if high > worst:
                worst = high
            _spin(CLK_DELAY)
            val = (val << 1) | bit

        # 25th pulse: select channel A, gain 128 for the next conversion
        t = time.perf_counter()
        GPIO.output(self.sck, GPIO.HIGH)
        _spin(CLK_DELAY)
        GPIO.output(self.sck, GPIO.LOW)
        high = time.perf_counter() - t
        if high > worst:
            worst = high

        self.last_high_us = worst * 1e6
        if self.last_high_us > self.stats["max_high_us"]:
            self.stats["max_high_us"] = self.last_high_us
        if worst > HIGH_LIMIT:               # Python was paused mid-pulse
            self.stats["glitches"] += 1
            self._discard = DISCARD_AFTER_GLITCH
            return None

        if val & 0x800000:          # sign extend 24-bit two's complement
            val -= 1 << 24
        if val in (0x7FFFFF, -0x800000):   # saturated / wiring fault
            self.stats["saturated"] += 1
            return None
        if self._discard > 0:       # first conversion(s) after a glitch
            self._discard -= 1
            return None
        return val

    def read_raw_avg(self, samples=5):
        """Median of several valid readings. None if there is not enough
        valid data (fewer than half of the requested samples)."""
        vals = []
        attempts = 0
        while len(vals) < samples and attempts < samples * 3:
            attempts += 1
            v = self._read_raw()
            if v is not None:
                vals.append(v)
        if len(vals) < max(1, (samples + 1) // 2):
            return None
        self.last_spread = max(vals) - min(vals)
        return statistics.median(vals)

    # ─── Calibration ───────────────────────────────────────────────────────
    def tare(self, samples=15):
        """Zero the scale. Do this with the empty hopper in place, motors off."""
        raw = self.read_raw_avg(samples)
        if raw is None:
            raise RuntimeError("HX711 not responding (check wiring/power)")
        self.offset = raw
        self.save_calibration()
        self.reset_filter()
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
        self.reset_filter()
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
    def reset_filter(self):
        """Forget the last weight (call at the start of a batch if wanted)."""
        self._last = None
        self._pending = None

    def _weight_from_raw(self, raw):
        return (raw - self.offset) / self.scale

    def _prime(self, samples):
        """First reading after start/reset: take up to 3 readings and return
        the first one that agrees with the one before it."""
        prev = None
        for _ in range(3):
            raw = self.read_raw_avg(samples)
            if raw is None:
                continue
            w = self._weight_from_raw(raw)
            if prev is not None and abs(w - prev) <= CONFIRM_BAND_G:
                return w
            prev = w
        return prev

    def get_weight_g(self, samples=5):
        """Weight in grams, or None if the sensor didn't respond.
        Single spikes are ignored (the last good value is returned)."""
        if self._last is None:
            self._last = self._prime(samples)
            return self._last

        raw = self.read_raw_avg(samples)
        if raw is None:
            return None
        w = self._weight_from_raw(raw)

        if abs(w - self._last) <= MAX_JUMP_G:
            self._pending = None
            self._last = w
            return w
        # Big jump: accept only if the previous reading was a similar value.
        if self._pending is not None and abs(w - self._pending) <= CONFIRM_BAND_G:
            self._pending = None
            self._last = w
            return w
        self._pending = w
        self.stats["held_spikes"] += 1
        return self._last


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
