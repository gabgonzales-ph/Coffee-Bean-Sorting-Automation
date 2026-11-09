import time
from components.loadcell import LoadCell
from components.motors import setup_motors, motors_on, motors_off

lc = LoadCell()
setup_motors()

try:
    input("1) Hopper EMPTY and still. Press Enter to turn motors ON and tare...")
    motors_on()
    time.sleep(2)                      # let the relay and supply settle
    print("Tare raw:", lc.tare(samples=15))

    input("2) Put a known weight in the middle of the hopper (e.g. 1000 g). Press Enter...")
    known = float(input("Enter the weight in grams: "))
    time.sleep(2)                      # let the hopper stop swinging
    print("Scale (counts/g):", lc.calibrate(known, samples=15))

    input("3) Remove the weight, hopper empty. Press Enter to check (motors still ON)...")
    time.sleep(2)
    for _ in range(5):
        print("Reading (motors on): ", round(lc.get_weight_g(samples=5), 1))

    motors_off()
    time.sleep(2)
    print("--- motors OFF for comparison ---")
    for _ in range(5):
        print("Reading (motors off):", round(lc.get_weight_g(samples=5), 1))
finally:
    motors_off()
