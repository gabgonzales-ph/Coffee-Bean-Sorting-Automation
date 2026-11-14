"""
Standalone test for the hopper flapper servo (the one under the weighed hopper).
Stop the service first:  sudo systemctl stop coffee-sorter.service

Usage (from ~/coffee_bean, venv313 active):
    python test_flapper.py                 interactive: o = open, c = close, t = toggle, q = quit
    python test_flapper.py --cycle 3       open/close 3 times, then exit
    python test_flapper.py --open-sec 5    how long it stays open in --cycle (default 3)
    python test_flapper.py --pin 13 --open 7.5 --closed 2.5   override pin / duty values
    python test_flapper.py --hold          keep sending PWM while open/closed (stronger hold)

Duty values match the notes: open = 7.5, closed = 2.5, GPIO13, 50 Hz software PWM.
If the door travels too far or not enough, change --open / --closed (try 2.0-12.0).
"""
import argparse
import time

import RPi.GPIO as GPIO

FREQ_HZ = 50
MOVE_SEC = 0.6  # time for the servo to reach its position


def move(pwm, duty, hold):
    pwm.ChangeDutyCycle(duty)
    time.sleep(MOVE_SEC)
    if not hold:
        pwm.ChangeDutyCycle(0)  # stop pulses; door is held by gear friction only


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pin", type=int, default=13)
    ap.add_argument("--open", dest="open_duty", type=float, default=7.5)
    ap.add_argument("--closed", dest="closed_duty", type=float, default=2.5)
    ap.add_argument("--cycle", type=int, default=0, help="number of open/close cycles")
    ap.add_argument("--open-sec", type=float, default=3.0)
    ap.add_argument("--hold", action="store_true")
    args = ap.parse_args()

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(args.pin, GPIO.OUT)
    pwm = GPIO.PWM(args.pin, FREQ_HZ)
    pwm.start(0)

    state = "unknown"
    try:
        print(f"Flapper servo on GPIO{args.pin}: open={args.open_duty}, closed={args.closed_duty}")
        print("Closing to a known position first...")
        move(pwm, args.closed_duty, args.hold)
        state = "closed"

        if args.cycle > 0:
            for i in range(1, args.cycle + 1):
                print(f"[{i}/{args.cycle}] OPEN")
                move(pwm, args.open_duty, args.hold)
                time.sleep(args.open_sec)
                print(f"[{i}/{args.cycle}] CLOSE")
                move(pwm, args.closed_duty, args.hold)
                time.sleep(1.0)
            print("Done.")
            return

        print("Commands: o = open, c = close, t = toggle, q = quit")
        while True:
            cmd = input(f"[{state}] > ").strip().lower()
            if cmd == "q":
                break
            if cmd == "t":
                cmd = "c" if state == "open" else "o"
            if cmd == "o":
                move(pwm, args.open_duty, args.hold)
                state = "open"
            elif cmd == "c":
                move(pwm, args.closed_duty, args.hold)
                state = "closed"
            else:
                print("Use o, c, t or q.")
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted.")
    finally:
        # leave the door closed
        try:
            move(pwm, args.closed_duty, False)
        except Exception:
            pass
        pwm.stop()
        GPIO.cleanup(args.pin)
        print("Flapper closed, GPIO released.")


if __name__ == "__main__":
    main()