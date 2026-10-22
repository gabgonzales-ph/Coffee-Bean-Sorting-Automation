#main.py // do not remove this comment
#this is the current main logic pipeline script

import numpy as np
import cv2
import time
import threading
import queue
import os
import subprocess
import signal
import RPi.GPIO as GPIO
from ai_edge_litert.interpreter import Interpreter
from webcam_config import DEVICE_INDEX, RESOLUTION, FRAMERATE, CAMERA_CONTROLS, INITIAL_WAIT, LOCK_WAIT
from RPLCD.i2c import CharLCD
from components.buttons import setup_buttons, cleanup_buttons, START_BUTTON_PIN, DEBOUNCE_SEC
from components.rejector import setup_rejector, kick_reject, cleanup_rejector
from components.motors import setup_motors, motors_on, motors_off, cleanup_motors
from components.loadcell import LoadCell, ThresholdDetector, cleanup_loadcell
from components.flapper import setup_flapper, open_flapper, close_flapper, cleanup_flapper
from components.vibration import setup_vibration, vibration_on, vibration_off, cleanup_vibration

setup_buttons()
setup_rejector()
setup_motors()
setup_flapper()
setup_vibration()

# ─── Debug / Display Toggle ────────────────────────────────────────────────
# Set SHOW_PREVIEW=1 in env when running manually via VNC for a live window.
# Leave unset/0 when running headless via systemd — imshow will crash otherwise.
SHOW_PREVIEW = os.environ.get("SHOW_PREVIEW", "0") == "1"

# ─── LCD Init ───────────────────────────────────────────────────────────────
lcd = CharLCD('PCF8574', 0x27, cols=16, rows=2)
lcd.clear()
lcd.write_string("Booting...")

# ─── Configuration ────────────────────────────────────────────────────────────

MODEL_PATH     = "/home/admin/coffee_bean/models/coffee_bean_model_v2_float32.tflite"
CLASSES        = {0: 'green', 1: 'not_green'}
CONF_THRESHOLD = 0.5
NMS_THRESHOLD  = 0.4

CONFIRM_FRAMES = 4
PROXIMITY_PX = 40

COLOR_PENDING   = (0, 255, 255)
COLOR_GREEN     = (0, 255, 0)
COLOR_NOT_GREEN = (0, 0, 255)

LCD_INTERVAL = 0.25   # seconds between LCD refreshes (I2C writes are slow)

# ─── Batch / Weight Config ───────────────────────────────────────────────────
TARGET_G         = 2000.0  # trigger weight. Beans still on the belt land after
                           # the motors stop, so try 1950 if batches overshoot.
TARGET_CONFIRM   = 3       # consecutive readings >= target before triggering
SETTLE_SEC       = 1.0     # wait after motors stop so in-flight beans land
FLAPPER_OPEN_SEC = 15.0    # how long the door stays open to empty the hopper
SORT_SECONDS     = 20.0    # how long the vibration motor runs (timer-based).

try:
    lcd.clear()
    lcd.write_string("Loading Model")

    interpreter = Interpreter(model_path=MODEL_PATH, num_threads=4)
    interpreter.allocate_tensors()

    in_det  = interpreter.get_input_details()[0]
    out_det = interpreter.get_output_details()[0]

    img_h = in_det['shape'][1]
    img_w = in_det['shape'][2]

    print(f"Model loaded — input: {img_w}x{img_h}")

    lcd.clear()
    lcd.write_string("Init Load Cell")
    # Uses the tare/scale saved in components/loadcell_cal.json
    loadcell = LoadCell()
    first_reading = loadcell.get_weight_g(samples=3)
    if first_reading is None:
        raise RuntimeError("Load cell (HX711) not responding")
    print(f"Load cell OK — {first_reading:.1f} g")

    lcd.clear()
    lcd.write_string("Init Camera")

    cap = cv2.VideoCapture(DEVICE_INDEX, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open webcam at {DEVICE_INDEX}")

    # MJPG must be requested BEFORE width/height/fps. Without it OpenCV usually
    # picks YUYV, which is capped at 25 fps at 640x480 on this camera.
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, RESOLUTION[1])
    cap.set(cv2.CAP_PROP_FPS,          FRAMERATE)

    print(f"Webcam opened at {DEVICE_INDEX} — "
          f"requested {RESOLUTION[0]}x{RESOLUTION[1]} @ {FRAMERATE}fps")

    lcd.clear()
    lcd.write_string("Calibrating...")
    print(f"Settling... waiting {INITIAL_WAIT}s")
    time.sleep(INITIAL_WAIT)

    # Controls are applied in the order they are listed in webcam_config.py
    # (dict order), so each auto mode is switched to manual before the value
    # that depends on it. Any control added to CAMERA_CONTROLS is applied.
    device_path = DEVICE_INDEX
    for ctrl, value in CAMERA_CONTROLS.items():
        result = subprocess.run(
            ["v4l2-ctl", "-d", device_path, f"--set-ctrl={ctrl}={value}"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"[WARN] Failed to set {ctrl}={value}: {result.stderr.strip()}")
        else:
            print(f"Set {ctrl} = {value}")

    print(f"Waiting {LOCK_WAIT}s for settings to take effect...")
    time.sleep(LOCK_WAIT)
    print("Camera ready — press Q to quit")

    lcd.clear()
    lcd.write_string("System Ready")
    time.sleep(1)

except Exception as e:
    lcd.clear()
    lcd.write_string("Init Failed")
    with open("/home/admin/coffee_bean/error.log", "a") as f:
        f.write(f"{time.ctime()}: {repr(e)}\n")
    raise

frame_queue     = queue.Queue(maxsize=1)
detection_queue = queue.Queue(maxsize=1)
stop_event      = threading.Event()

# systemd sends SIGTERM to stop the service (including during a real
# shutdown, right before the Pi powers off). Python does NOT run cleanup
# code on SIGTERM by default — it just terminates. This handler makes the
# main loop below exit on its next iteration instead, so the normal
# cleanup section (motors_off, servo rest, camera release, LCD clear)
# actually runs before the process dies. SIGINT (Ctrl+C) is included too,
# for manual testing.
def _handle_shutdown_signal(signum, frame):
    print(f"[SIGNAL] Received signal {signum} — requesting clean shutdown")
    stop_event.set()

signal.signal(signal.SIGTERM, _handle_shutdown_signal)
signal.signal(signal.SIGINT, _handle_shutdown_signal)

def trigger_actuator():
    """Signal the servo kicker arm to reject the bean.

    kick_reject() is a blocking call (~0.55s total, between servo travel
    time and the hold duration). trigger_actuator() is called from
    BeanTracker.update(), which runs on the main loop thread (frame
    capture + display) — blocking there would stall camera reads and tank
    FPS every time a reject fires. Running it in a short-lived daemon
    thread lets the kick happen in the background without holding up
    frame capture.
    """
    print("[ACTUATOR] Kicker triggered — rejecting bean")
    threading.Thread(target=kick_reject, daemon=True).start()

class BeanTracker:
    def __init__(self, confirm_frames: int, proximity_px: int):
        self.confirm_frames = confirm_frames
        self.proximity_px   = proximity_px
        self.pool: list[dict] = []

    @staticmethod
    def _center(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def _nearest(self, cx, cy):
        best_entry, best_dist = None, float('inf')
        for entry in self.pool:
            ex, ey = self._center(entry['box'])
            dist = ((cx - ex) ** 2 + (cy - ey) ** 2) ** 0.5
            if dist < self.proximity_px and dist < best_dist:
                best_entry, best_dist = entry, dist
        return best_entry

    def update(self, detections: list[dict]) -> list[dict]:
        for entry in self.pool:
            entry['seen_this_frame'] = False

        for det in detections:
            cx, cy = self._center(det['box'])
            entry  = self._nearest(cx, cy)

            if entry is not None:
                entry['box']              = det['box']
                entry['class_id']         = det['class_id']
                entry['class_name']       = det['class_name']
                entry['confidence']       = det['confidence']
                entry['seen_this_frame']  = True
                entry['missed']           = 0

                if not entry['confirmed']:
                    entry['hits'] += 1
                    if entry['hits'] >= self.confirm_frames:
                        entry['confirmed'] = True
                        print(
                            f"[CONFIRMED] {entry['class_name']} "
                            f"(conf={entry['confidence']:.2f}) after "
                            f"{entry['hits']} frames"
                        )
                        if entry['class_id'] == 1 and not entry['actuator_fired']:
                            entry['actuator_fired'] = True
                            trigger_actuator()
            else:
                self.pool.append({
                    'box':            det['box'],
                    'class_id':       det['class_id'],
                    'class_name':     det['class_name'],
                    'confidence':     det['confidence'],
                    'hits':           1,
                    'confirmed':      False,
                    'actuator_fired': False,
                    'missed':         0,
                    'seen_this_frame': True,
                })

        for entry in self.pool:
            if not entry['seen_this_frame']:
                entry['missed'] += 1

        self.pool = [e for e in self.pool if e['missed'] <= self.confirm_frames]

        return self.pool


tracker = BeanTracker(confirm_frames=CONFIRM_FRAMES, proximity_px=PROXIMITY_PX)

def run_inference(frame_bgr):
    orig_h, orig_w = frame_bgr.shape[:2]

    frame_rgb  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    resized    = cv2.resize(frame_rgb, (img_w, img_h))
    input_data = np.expand_dims(resized.astype(np.float32) / 255.0, axis=0)

    interpreter.set_tensor(in_det['index'], input_data)
    interpreter.invoke()

    output = interpreter.get_tensor(out_det['index'])[0]

    raw_boxes, raw_scores, raw_class_ids = [], [], []

    for i in range(output.shape[1]):
        class_probs = output[4:, i]
        class_id    = int(np.argmax(class_probs))
        confidence  = float(class_probs[class_id])

        if confidence < CONF_THRESHOLD:
            continue

        x_c, y_c, w, h = output[:4, i]
        x1    = int((x_c - w / 2) * orig_w)
        y1    = int((y_c - h / 2) * orig_h)
        box_w = int(w * orig_w)
        box_h = int(h * orig_h)

        raw_boxes.append([x1, y1, box_w, box_h])
        raw_scores.append(confidence)
        raw_class_ids.append(class_id)

    detections = []
    for cls_id in set(raw_class_ids):
        indices    = [i for i, c in enumerate(raw_class_ids) if c == cls_id]
        cls_boxes  = [raw_boxes[i]  for i in indices]
        cls_scores = [raw_scores[i] for i in indices]

        keep = cv2.dnn.NMSBoxes(cls_boxes, cls_scores, CONF_THRESHOLD, NMS_THRESHOLD)
        if len(keep) == 0:
            continue

        for k in keep.flatten():
            x, y, bw, bh = cls_boxes[k]
            detections.append({
                "class_id":   cls_id,
                "class_name": CLASSES.get(cls_id, 'unknown'),
                "confidence": round(cls_scores[k], 4),
                "box":        [x, y, x + bw, y + bh]
            })

    detections.sort(key=lambda d: d['confidence'], reverse=True)
    return detections

def inference_worker():
    while not stop_event.is_set():
        try:
            frame = frame_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        detections = run_inference(frame)

        if not detection_queue.empty():
            try:
                detection_queue.get_nowait()
            except queue.Empty:
                pass
        detection_queue.put(detections)

        if detections:
            print(f"[DETECTIONS] {len(detections)} raw found")
            for det in detections:
                print(f"  {det['class_name']} {det['confidence']:.2f}")

inference_thread = threading.Thread(target=inference_worker, daemon=True)
inference_thread.start()

# ─── Weight reader thread ───────────────────────────────────────────────────
# The HX711 takes ~0.3s per averaged reading, so it runs in its own thread
# and just publishes the latest value. The main loop (the only thread that
# writes to the LCD) reads weight_state["g"], so no LCD lock is needed.
# None means the sensor failed to respond.
weight_state = {"g": first_reading}

def weight_worker():
    while not stop_event.is_set():
        try:
            weight_state["g"] = loadcell.get_weight_g(samples=3)
        except Exception as e:
            print(f"[WEIGHT] read error: {e!r}")
            weight_state["g"] = None
            time.sleep(0.5)

weight_thread = threading.Thread(target=weight_worker, daemon=True)
weight_thread.start()

tracked_beans = []
fps_counter   = 0
fps_timer     = time.time()
current_fps   = 0.0

# ─── LCD helpers ────────────────────────────────────────────────────────────
# Overwrite rows in place instead of lcd.clear() every update (no flicker),
# and skip the I2C write entirely if the row text hasn't changed.
last_lcd_lines = ["", ""]
last_lcd_time  = 0.0

def lcd_row(row: int, text: str):
    text = text.ljust(16)[:16]
    if last_lcd_lines[row] != text:
        lcd.cursor_pos = (row, 0)
        lcd.write_string(text)
        last_lcd_lines[row] = text

# ─── Start button (non-blocking) ────────────────────────────────────────────
# Polled only in the IDLE state, so it can't be triggered during a process
# (spec requirement). Same pin and debounce as components/buttons.py.
def start_pressed() -> bool:
    if GPIO.input(START_BUTTON_PIN) == GPIO.HIGH:
        return False
    time.sleep(DEBOUNCE_SEC)
    if GPIO.input(START_BUTTON_PIN) == GPIO.HIGH:   # was contact bounce
        return False
    while GPIO.input(START_BUTTON_PIN) == GPIO.LOW and not stop_event.is_set():
        time.sleep(0.02)                            # wait for release
    return True

# ─── State machine ──────────────────────────────────────────────────────────
#   IDLE ──Start──> RUNNING ──2kg──> FILLED ──> IDLE ("Complete")
#
#   IDLE     motors off, waiting for Start (LCD: Idle / Complete)
#   RUNNING  motors on, detecting + rejecting, weight monitored
#   FILLED   motors off, settle, then flapper opens AND vibration starts
#            together. Flapper closes at FLAPPER_OPEN_SEC, vibration stops
#            at SORT_SECONDS. LCD shows the vibration countdown.
# Motors only turn back on when the person presses Start again.
state      = "IDLE"
idle_title = "Idle"
detector   = ThresholdDetector(target_g=TARGET_G, confirm=TARGET_CONFIRM)

lcd.clear()

while not stop_event.is_set():

    # ── IDLE ────────────────────────────────────────────────────────────────
    if state == "IDLE":
        lcd_row(0, idle_title)
        lcd_row(1, "Press Start")
        if start_pressed() and not stop_event.is_set():
            print("[STATE] Start pressed — beginning batch")
            tracker.pool  = []
            tracked_beans = []
            try:
                frame_queue.get_nowait()
            except queue.Empty:
                pass
            for _ in range(5):          # drop stale buffered camera frames
                cap.grab()
            detector.reset()
            motors_on()
            lcd.clear()
            last_lcd_lines[0] = last_lcd_lines[1] = ""
            state = "RUNNING"
        else:
            time.sleep(0.05)
        continue

    # ── FILLED: stop motors, dump the hopper ────────────────────────────────
    if state == "FILLED":
        print(f"[STATE] {TARGET_G:.0f} g reached — stopping motors")
        motors_off()
        lcd_row(0, "Target reached")
        lcd_row(1, "Stopping belt")
        stop_event.wait(SETTLE_SEC)           # let in-flight beans land
        # Flapper and vibration start together. The flapper closes after
        # FLAPPER_OPEN_SEC; the vibration keeps running until SORT_SECONDS.
        print("[STATE] Flapper open + vibration trays running")
        vibration_on()
        dump_start = time.time()
        open_flapper()
        flapper_closed = False
        while not stop_event.is_set():
            elapsed = time.time() - dump_start
            if not flapper_closed and elapsed >= FLAPPER_OPEN_SEC:
                close_flapper()
                flapper_closed = True
                print(f"[STATE] Flapper closed — weight {weight_state['g']}")
                continue
            remaining = SORT_SECONDS - elapsed
            if remaining <= 0:
                break
            lcd_row(0, "Sorting")
            lcd_row(1, f"Time left: {int(remaining) + 1}s")
            time.sleep(0.2)
        vibration_off()
        if not flapper_closed:      # stop requested, or SORT_SECONDS < open time
            close_flapper()
        print("[STATE] Process complete — waiting for Start")
        idle_title = "Complete"
        state = "IDLE"
        continue

    # ── RUNNING ─────────────────────────────────────────────────────────────
    ret, frame_bgr = cap.read()
    if not ret:
        print("[WARN] Frame grab failed — retrying")
        continue

    if frame_queue.empty():
        frame_queue.put(frame_bgr.copy())

    if not detection_queue.empty():
        raw_detections = detection_queue.get_nowait()
        tracked_beans  = tracker.update(raw_detections)

    for bean in tracked_beans:
        x1, y1, x2, y2 = bean['box']

        if not bean['confirmed']:
            color  = COLOR_PENDING
            status = f"? {bean['class_name']} {bean['confidence']:.2f} ({bean['hits']}/{CONFIRM_FRAMES})"
        elif bean['class_id'] == 0:
            color  = COLOR_GREEN
            status = f"OK {bean['class_name']} {bean['confidence']:.2f}"
        else:
            color  = COLOR_NOT_GREEN
            status = f"REJECT {bean['class_name']} {bean['confidence']:.2f}"

        cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame_bgr, status, (x1, max(y1 - 10, 10)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2
        )

    fps_counter += 1
    if time.time() - fps_timer >= 1.0:
        current_fps = fps_counter / (time.time() - fps_timer)
        fps_counter = 0
        fps_timer   = time.time()
        print(f"[FPS] {current_fps:.1f}")

    confirmed_green = sum(1 for b in tracked_beans if b['confirmed'] and b['class_id'] == 0)
    confirmed_reject = sum(1 for b in tracked_beans if b['confirmed'] and b['class_id'] == 1)
    pending_count   = sum(1 for b in tracked_beans if not b['confirmed'])

    weight_g = weight_state["g"]

    now = time.time()
    if now - last_lcd_time >= LCD_INTERVAL:
        last_lcd_time = now
        if weight_g is None:
            lcd_row(0, "Wt: SENSOR ERR")
        else:
            shown = 0.0 if abs(weight_g) < 0.05 else weight_g   # avoid "-0.0"
            lcd_row(0, f"Wt:{shown:8.1f} g")
        lcd_row(1, f"G:{confirmed_green} R:{confirmed_reject} F:{current_fps:.0f}")

    weight_text = "ERR" if weight_g is None else f"{weight_g:.1f}g"
    cv2.putText(
        frame_bgr,
        f"Confirmed G:{confirmed_green} R:{confirmed_reject}  Pending: {pending_count}  Wt: {weight_text}",
        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2
    )

    if SHOW_PREVIEW:
        cv2.imshow("Coffee Bean Detection", frame_bgr)
        if cv2.waitKey(1) == ord('q'):
            break

    # 2 kg check: needs TARGET_CONFIRM consecutive readings at/above target
    if detector.update(weight_g):
        state = "FILLED"

stop_event.set()
inference_thread.join(timeout=3.0)
weight_thread.join(timeout=3.0)
cap.release()
if SHOW_PREVIEW:
    cv2.destroyAllWindows()
cleanup_motors()
cleanup_rejector()
cleanup_vibration()
cleanup_flapper()
cleanup_loadcell()
cleanup_buttons()   # calls GPIO.cleanup(), keep this last
lcd.clear()
lcd.write_string("Stopped")
print("Stopped")
