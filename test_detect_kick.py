# test_detect_kick.py
# Detection + reject servo + conveyor/dispenser motors, with live preview.
# No load cell, LCD, flapper, vibration or buttons.
# Same model, tracker and confirmation logic as main.py.
#
#   sudo systemctl stop coffee-sorter.service
#   cd ~/coffee_bean && source venv313/bin/activate
#   python test_detect_kick.py                  # preview window + motors
#   SHOW_PREVIEW=0 python test_detect_kick.py   # headless (no window, no keys)
#
# Preview window keys:  M = toggle motors   Q = quit
# Motors start ON as soon as the camera is ready. Ctrl+C also stops safely.

import os
import queue
import signal
import subprocess
import threading
import time

import cv2
import numpy as np
from ai_edge_litert.interpreter import Interpreter

from webcam_config import DEVICE_INDEX, RESOLUTION, FRAMERATE, CAMERA_CONTROLS, INITIAL_WAIT, LOCK_WAIT
from components.rejector import setup_rejector, kick_reject, cleanup_rejector
from components.motors import setup_motors, motors_on, motors_off, cleanup_motors

SHOW_PREVIEW = os.environ.get("SHOW_PREVIEW", "1") == "1"

MODEL_PATH     = "/home/admin/coffee_bean/models/coffee_bean_model_v2_float32.tflite"
CLASSES        = {0: 'green', 1: 'not_green'}
CONF_THRESHOLD = 0.5
NMS_THRESHOLD  = 0.4
CONFIRM_FRAMES = 6
PROXIMITY_PX   = 40

COLOR_PENDING   = (0, 255, 255)
COLOR_GREEN     = (0, 255, 0)
COLOR_NOT_GREEN = (0, 0, 255)

stop_event = threading.Event()
signal.signal(signal.SIGINT,  lambda s, f: stop_event.set())
signal.signal(signal.SIGTERM, lambda s, f: stop_event.set())

# --- Setup ------------------------------------------------------------------
setup_rejector()
setup_motors()

interpreter = Interpreter(model_path=MODEL_PATH, num_threads=4)
interpreter.allocate_tensors()
in_det  = interpreter.get_input_details()[0]
out_det = interpreter.get_output_details()[0]
img_h, img_w = in_det['shape'][1], in_det['shape'][2]
print(f"Model loaded: input {img_w}x{img_h}")

cap = cv2.VideoCapture(DEVICE_INDEX, cv2.CAP_V4L2)
if not cap.isOpened():
    raise RuntimeError(f"Could not open webcam at {DEVICE_INDEX}")
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  RESOLUTION[0])
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, RESOLUTION[1])
cap.set(cv2.CAP_PROP_FPS,          FRAMERATE)
time.sleep(INITIAL_WAIT)
for ctrl, value in CAMERA_CONTROLS.items():
    r = subprocess.run(["v4l2-ctl", "-d", DEVICE_INDEX, f"--set-ctrl={ctrl}={value}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[WARN] {ctrl}={value}: {r.stderr.strip()}")
time.sleep(LOCK_WAIT)
for _ in range(5):          # drop stale buffered frames
    cap.grab()

# --- Kick trigger -----------------------------------------------------------
counts = {"green": 0, "reject": 0, "kicks": 0}

def trigger_actuator():
    counts["kicks"] += 1
    print(f"[KICK #{counts['kicks']}] reject confirmed at {time.strftime('%H:%M:%S')}")
    threading.Thread(target=kick_reject, daemon=True).start()

# --- Tracker (same as main.py) ----------------------------------------------
class BeanTracker:
    def __init__(self, confirm_frames, proximity_px):
        self.confirm_frames = confirm_frames
        self.proximity_px   = proximity_px
        self.pool = []

    @staticmethod
    def _center(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def _nearest(self, cx, cy):
        best, best_d = None, float('inf')
        for e in self.pool:
            ex, ey = self._center(e['box'])
            d = ((cx - ex) ** 2 + (cy - ey) ** 2) ** 0.5
            if d < self.proximity_px and d < best_d:
                best, best_d = e, d
        return best

    def update(self, detections):
        for e in self.pool:
            e['seen_this_frame'] = False
        for det in detections:
            cx, cy = self._center(det['box'])
            e = self._nearest(cx, cy)
            if e is not None:
                e.update(box=det['box'], class_id=det['class_id'],
                         class_name=det['class_name'], confidence=det['confidence'],
                         seen_this_frame=True, missed=0)
                if not e['confirmed']:
                    e['hits'] += 1
                    if e['hits'] >= self.confirm_frames:
                        e['confirmed'] = True
                        print(f"[CONFIRMED] {e['class_name']} (conf={e['confidence']:.2f})")
                        if e['class_id'] == 0:
                            counts["green"] += 1
                        else:
                            counts["reject"] += 1
                            if not e['actuator_fired']:
                                e['actuator_fired'] = True
                                trigger_actuator()
            else:
                self.pool.append({
                    'box': det['box'], 'class_id': det['class_id'],
                    'class_name': det['class_name'], 'confidence': det['confidence'],
                    'hits': 1, 'confirmed': False, 'actuator_fired': False,
                    'missed': 0, 'seen_this_frame': True,
                })
        for e in self.pool:
            if not e['seen_this_frame']:
                e['missed'] += 1
        self.pool = [e for e in self.pool if e['missed'] <= self.confirm_frames]
        return self.pool

tracker = BeanTracker(CONFIRM_FRAMES, PROXIMITY_PX)

# --- Inference (same as main.py) --------------------------------------------
def run_inference(frame_bgr):
    orig_h, orig_w = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (img_w, img_h))
    interpreter.set_tensor(in_det['index'],
                           np.expand_dims(resized.astype(np.float32) / 255.0, axis=0))
    interpreter.invoke()
    output = interpreter.get_tensor(out_det['index'])[0]

    boxes, scores, class_ids = [], [], []
    for i in range(output.shape[1]):
        probs = output[4:, i]
        cid = int(np.argmax(probs))
        conf = float(probs[cid])
        if conf < CONF_THRESHOLD:
            continue
        x_c, y_c, w, h = output[:4, i]
        boxes.append([int((x_c - w / 2) * orig_w), int((y_c - h / 2) * orig_h),
                      int(w * orig_w), int(h * orig_h)])
        scores.append(conf)
        class_ids.append(cid)

    dets = []
    for cid in set(class_ids):
        idx = [i for i, c in enumerate(class_ids) if c == cid]
        cb = [boxes[i] for i in idx]
        cs = [scores[i] for i in idx]
        keep = cv2.dnn.NMSBoxes(cb, cs, CONF_THRESHOLD, NMS_THRESHOLD)
        if len(keep) == 0:
            continue
        for k in keep.flatten():
            x, y, bw, bh = cb[k]
            dets.append({"class_id": cid, "class_name": CLASSES.get(cid, 'unknown'),
                         "confidence": round(cs[k], 4), "box": [x, y, x + bw, y + bh]})
    dets.sort(key=lambda d: d['confidence'], reverse=True)
    return dets

frame_queue     = queue.Queue(maxsize=1)
detection_queue = queue.Queue(maxsize=1)

def inference_worker():
    while not stop_event.is_set():
        try:
            frame = frame_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        dets = run_inference(frame)
        if not detection_queue.empty():
            try:
                detection_queue.get_nowait()
            except queue.Empty:
                pass
        detection_queue.put(dets)

threading.Thread(target=inference_worker, daemon=True).start()

# --- Main loop --------------------------------------------------------------
motors_running = False
motors_on()
motors_running = True
print("Camera ready, motors ON. Keys in preview: M = toggle motors, Q = quit.")

tracked = []
frames, fps_t = 0, time.time()
current_fps = 0.0
try:
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            continue
        if frame_queue.empty():
            frame_queue.put(frame.copy())
        if not detection_queue.empty():
            tracked = tracker.update(detection_queue.get_nowait())

        frames += 1
        if time.time() - fps_t >= 2.0:
            current_fps = frames / (time.time() - fps_t)
            print(f"[FPS] {current_fps:.1f}   "
                  f"G:{counts['green']} R:{counts['reject']} kicks:{counts['kicks']}")
            frames, fps_t = 0, time.time()

        if SHOW_PREVIEW:
            for b in tracked:
                x1, y1, x2, y2 = b['box']
                if not b['confirmed']:
                    color  = COLOR_PENDING
                    status = f"? {b['class_name']} {b['confidence']:.2f} ({b['hits']}/{CONFIRM_FRAMES})"
                elif b['class_id'] == 0:
                    color  = COLOR_GREEN
                    status = f"OK {b['class_name']} {b['confidence']:.2f}"
                else:
                    color  = COLOR_NOT_GREEN
                    status = f"REJECT {b['class_name']} {b['confidence']:.2f}"
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, status, (x1, max(y1 - 10, 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2)
            cv2.putText(frame,
                        f"G:{counts['green']} R:{counts['reject']} Kicks:{counts['kicks']}  "
                        f"FPS:{current_fps:.0f}  Motors:{'ON' if motors_running else 'OFF'}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imshow("Detect + Kick test", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            if key == ord('m'):
                if motors_running:
                    motors_off()
                else:
                    motors_on()
                motors_running = not motors_running
                print(f"[MOTORS] {'ON' if motors_running else 'OFF'}")
finally:
    stop_event.set()
    motors_off()
    cap.release()
    if SHOW_PREVIEW:
        cv2.destroyAllWindows()
    cleanup_rejector()
    cleanup_motors()
    print(f"Stopped. G:{counts['green']} R:{counts['reject']} kicks:{counts['kicks']}")