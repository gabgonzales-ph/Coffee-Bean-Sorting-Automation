"""Load cell noise test. Stop the service first. Hopper EMPTY and still.

    python test_loadcell_noise.py                  60 s, nothing else running
    python test_loadcell_noise.py --seconds 120
    python test_loadcell_noise.py --load           3 busy threads (imitates the inference load)
    python test_loadcell_noise.py --motors         conveyor + dispenser on

Uses the saved calibration (loadcell_cal.json). It looks at the UNFILTERED
median reading, so spikes that the filter in get_weight_g() would hide are
still visible here. Run it three times (plain, --load, --motors) and compare.
"""
import argparse
import statistics
import threading
import time

from components.loadcell import LoadCell, cleanup_loadcell


def burn(stop):
    x = 1
    while not stop.is_set():
        for _ in range(20000):
            x = (x * 31 + 7) % 1000003


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--load", action="store_true", help="3 busy threads")
    ap.add_argument("--motors", action="store_true", help="motors on")
    args = ap.parse_args()

    lc = LoadCell()
    stop = threading.Event()
    motors_off = None

    if args.motors:
        from components.motors import setup_motors, motors_on, motors_off as _off
        setup_motors()
        motors_on()
        motors_off = _off
        time.sleep(2)
    if args.load:
        for _ in range(3):
            threading.Thread(target=burn, args=(stop,), daemon=True).start()

    data = []
    t_start = time.time()
    try:
        while time.time() - t_start < args.seconds:
            raw = lc.read_raw_avg(args.samples)
            t = time.time() - t_start
            if raw is None:
                print(f"t={t:5.1f}s  no valid reading")
                continue
            data.append((t, (raw - lc.offset) / lc.scale))
    finally:
        stop.set()
        if motors_off:
            motors_off()
        cleanup_loadcell()

    if not data:
        print("No data. Check wiring and power.")
        return

    g = [v for _, v in data]
    med = statistics.median(g)
    print()
    print(f"mode: load={'on' if args.load else 'off'}  motors={'on' if args.motors else 'off'}  "
          f"{len(g)} readings in {args.seconds} s")
    print(f"weight  median {med:8.1f} g   min {min(g):8.1f}   max {max(g):8.1f}   "
          f"stdev {statistics.pstdev(g):6.2f}")
    print(f"counters: {lc.stats}")
    print("  glitches    = reads thrown away because an SCK pulse was too long (Python paused)")
    print("  max_high_us = longest SCK-high time seen (limit about 60 us)")
    out = [(t, v - med) for t, v in data if abs(v - med) > 50]
    print(f"readings more than 50 g from the median: {len(out)}")
    for t, d in out[:20]:
        print(f"  t={t:5.1f}s  {d:+8.1f} g")


if __name__ == "__main__":
    main()
