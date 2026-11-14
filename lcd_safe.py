from RPLCD.i2c import CharLCD
import time

try:
    lcd = CharLCD('PCF8574', 0x27)
    lcd.clear()
    lcd.write_string("Unplug Now")
    time.sleep(1)  # give I2C bus time to finish flushing all characters
except Exception:
    pass
