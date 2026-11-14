# test_rejector.py
# Standalone SG90 test (hardware PWM on GPIO12). No camera, no model, no LCD.
# Run from /home/admin/coffee_bean with the service stopped:
#   sudo systemctl stop coffee-sorter.service
#   python test_rejector.py

import time
from components import rejector

print("Testing reject servo on GPIO12 (hardware PWM channel 0)")
rejector.setup_rejector()
print("At rest position (watch the arm hold still)")
time.sleep(2)

try:
    for i in range(1, 4):
        print(f"Kick {i}/3")
        rejector.kick_reject()
        time.sleep(1.5)

    print("Rapid fire: 5 kicks back to back")
    for _ in range(5):
        rejector.kick_reject()
    print("Done")
except KeyboardInterrupt:
    print("Interrupted")
finally:
    rejector.cleanup_rejector()
