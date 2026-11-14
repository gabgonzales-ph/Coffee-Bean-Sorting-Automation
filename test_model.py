# test_model.py: run the TFLite model on ONE image and print what it finds.
# Usage (Pi, venv active, in ~/coffee_bean):
#   python test_model.py <image.jpg> [model.tflite]
# A 640x480 image is centre-cropped to 320x320 (like the training data); a 320x320 image is used as is.
# Writes test_out.jpg with the boxes drawn.
import sys

import cv2
import numpy as np
from ai_edge_litert.interpreter import Interpreter

if len(sys.argv) < 2:
    sys.exit("usage: python test_model.py <image.jpg> [model.tflite]")
img_path = sys.argv[1]
model = sys.argv[2] if len(sys.argv) > 2 else "models/coffee_bean_model_v4_float32.tflite"
CLASSES = {0: "green", 1: "reject"}
CONF, NMS = 0.5, 0.4          # same as main.py

interp = Interpreter(model_path=model, num_threads=4)
interp.allocate_tensors()
inp = interp.get_input_details()[0]
out = interp.get_output_details()[0]
shape = [int(v) for v in inp["shape"]]
channels_first = shape[1] == 3 and shape[3] != 3          # [1,3,H,W] vs [1,H,W,3]
H, W = (shape[2], shape[3]) if channels_first else (shape[1], shape[2])
print(f"input {shape} {inp['dtype'].__name__} -> {'channels-first' if channels_first else 'channels-last'}, {W}x{H}")

img = cv2.imread(img_path)
if img is None:
    sys.exit(f"cannot read {img_path}")
oh, ow = img.shape[:2]
if (ow, oh) != (W, H):
    if ow >= W and oh >= H:
        x0, y0 = (ow - W) // 2, (oh - H) // 2
        img = img[y0:y0 + H, x0:x0 + W]
        print(f"centre-cropped {ow}x{oh} -> {W}x{H}")
    else:
        img = cv2.resize(img, (W, H))
        print(f"resized {ow}x{oh} -> {W}x{H}")

x = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
if channels_first:
    x = np.transpose(x, (2, 0, 1))
interp.set_tensor(inp["index"], np.expand_dims(x, 0))
interp.invoke()
pred = interp.get_tensor(out["index"])[0]                  # [6, N]
print("output", pred.shape, "| box values range", float(pred[:4].min()), "to", float(pred[:4].max()),
      "(about 0..1 means normalised, about 0..320 means pixels)")

scale = 1.0 if pred[:4].max() > 2.0 else float(W)           # pixels vs normalised
boxes, scores, cls = [], [], []
for i in range(pred.shape[1]):
    p = pred[4:, i]
    c = int(np.argmax(p))
    if p[c] < CONF:
        continue
    xc, yc, w, h = pred[:4, i] * scale
    boxes.append([int(xc - w / 2), int(yc - h / 2), int(w), int(h)])
    scores.append(float(p[c]))
    cls.append(c)
keep = cv2.dnn.NMSBoxes(boxes, scores, CONF, NMS) if boxes else []
print(f"{len(keep)} detection(s) at conf >= {CONF}")
for k in np.array(keep).flatten():
    bx, by, bw, bh = boxes[k]
    name = CLASSES.get(cls[k], "?")
    print(f"  {name:6s} {scores[k]:.2f}  box=({bx},{by},{bw},{bh})")
    col = (0, 255, 0) if cls[k] == 0 else (0, 0, 255)
    cv2.rectangle(img, (bx, by), (bx + bw, by + bh), col, 2)
    cv2.putText(img, f"{name} {scores[k]:.2f}", (bx, max(by - 5, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
cv2.imwrite("test_out.jpg", img)
print("wrote test_out.jpg")