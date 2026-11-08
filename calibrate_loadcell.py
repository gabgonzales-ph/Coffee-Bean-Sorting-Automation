from components.loadcell import LoadCell

lc = LoadCell()

input("1) Hopper EMPTY and still, motors off. Press Enter to tare...")
print("Tare raw:", lc.tare())

input("2) Put a known weight on the hopper (e.g. 1000 g). Press Enter...")
known = float(input("Enter the weight in grams: "))
print("Scale (counts/g):", lc.calibrate(known))

input("3) Remove the weight, leave the hopper empty. Press Enter to check...")
for _ in range(5):
    print("Reading:", lc.get_weight_g(samples=5))
