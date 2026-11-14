#!/usr/bin/env python3
"""Load cell diagnostic. Run with the coffee-sorter service stopped:

    sudo systemctl stop coffee-sorter.service
    cd ~/coffee_bean && source venv313/bin/activate
    python test_loadcell_check.py                 # default 2000 g target
    python test_loadcell_check.py --object-g 1500 # if you know the object's weight

Three phases: empty hopper, object on, object removed. Everything is printed
and also saved to loadcell_test_log.txt. Send me that file.
"""
import argparse
import inspect
import json
import os
import statistics
import sys
import time

from components.loadcell import LoadCell, ThresholdDetector

LOG_PATH = "loadcell_test_log.txt"
CAL_PATH = os.path.join("components", "loadcell_cal.json")

_log = open(LOG_PATH, "w")


def out(msg=""):
    print(msg)
    _log.write(msg + "\n")
    _log.flush()


def collect(lc, label, seconds, samples):
    """Read for `seconds`; return list of (t, value, call_ms)."""
    out(f"\n--- {label}: reading for {seconds} s (samples={samples}) ---")
    rows = []
    t_start = time.time()
    while time.time() - t_start < seconds:
        t0 = time.time()
        try:
            v = lc.get_weight_g(samples=samples)
        except Exception as e:
            v = None
            out(f"  EXCEPTION: {e!r}")
        ms = (time.time() - t0) * 1000
        rows.append((time.time() - t_start, v, ms))
        shown = "None" if v is None else f"{v:10.2f}"
        out(f"  t={rows[-1][0]:5.1f}s  {shown} g   (call {ms:5.0f} ms)")
        time.sleep(0.1)
    return rows


def summarize(label, rows):
    vals = [v for _, v, _ in rows if v is not None]
    nones = sum(1 for _, v, _ in rows if v is None)
    out(f"\n[{label}] summary")
    out(f"  readings: {len(rows)}   None: {nones}")
    if not vals:
        out("  no valid readings")
        return None
    repeats = sum(1 for a, b in zip(vals, vals[1:]) if a == b)
    out(f"  min {min(vals):.2f}  max {max(vals):.2f}  "
        f"mean {statistics.mean(vals):.2f}  median {statistics.median(vals):.2f}")
    if len(vals) > 1:
        out(f"  stdev {statistics.stdev(vals):.2f}  span {max(vals) - min(vals):.2f}")
    out(f"  identical-to-previous readings: {repeats} of {len(vals) - 1}"
        f"  (many repeats = stale/cached value)")
    ms = [m for _, _, m in rows]
    out(f"  call time ms: min {min(ms):.0f}  avg {statistics.mean(ms):.0f}  max {max(ms):.0f}")
    return statistics.median(vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--object-g", type=float, default=None,
                    help="true weight of the test object in grams (optional)")
    ap.add_argument("--target-g", type=float, default=2000.0)
    ap.add_argument("--seconds", type=int, default=12, help="seconds per phase")
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()

    out(f"Load cell test  {time.ctime()}")
    out(f"python {sys.version.split()[0]}")

    out("\n=== loadcell_cal.json ===")
    try:
        with open(CAL_PATH) as f:
            out(json.dumps(json.load(f), indent=2))
    except Exception as e:
        out(f"could not read {CAL_PATH}: {e!r}")

    lc = LoadCell()
    out("\n=== LoadCell public attributes ===")
    out(", ".join(n for n in dir(lc) if not n.startswith("_")))
    try:
        out(f"get_weight_g{inspect.signature(lc.get_weight_g)}")
    except Exception:
        pass

    input("\nPhase 1: hopper EMPTY and still. Press Enter...")
    r1 = collect(lc, "EMPTY", args.seconds, args.samples)
    m1 = summarize("EMPTY", r1)

    input("\nPhase 2: place the object on the hopper, let it settle, "
          "keep hands off. Press Enter to start reading...")
    r2 = collect(lc, "LOADED", args.seconds, args.samples)
    m2 = summarize("LOADED", r2)

    input("\nPhase 3: remove the object. Press Enter...")
    r3 = collect(lc, "REMOVED", args.seconds, args.samples)
    m3 = summarize("REMOVED", r3)

    out("\n=== comparison ===")
    if m1 is not None and m2 is not None:
        out(f"empty median {m1:.2f} g -> loaded median {m2:.2f} g "
            f"(change {m2 - m1:+.2f} g)")
        if args.object_g:
            err = (m2 - m1) / args.object_g * 100 - 100
            out(f"known object {args.object_g:.0f} g -> reads {m2 - m1:.1f} g "
                f"({err:+.1f}% error; scale factor off by x{(m2 - m1) / args.object_g:.3f})")
    if m1 is not None and m3 is not None:
        out(f"removed median {m3:.2f} g vs empty median {m1:.2f} g "
            f"(drift {m3 - m1:+.2f} g)")

    out(f"\n=== ThresholdDetector replay (target {args.target_g:.0f} g, confirm 3) ===")
    det = ThresholdDetector(target_g=args.target_g, confirm=3)
    fired_at = None
    for t, v, _ in r2:
        if det.update(v):
            fired_at = t
            break
    out(f"fired during LOADED phase: {'yes at t=%.1fs' % fired_at if fired_at is not None else 'NO'}")

    out(f"\nSaved to {LOG_PATH}. Send me that file.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        out("\ninterrupted")
    finally:
        try:
            from components.loadcell import cleanup_loadcell
            cleanup_loadcell()
        except Exception:
            pass
