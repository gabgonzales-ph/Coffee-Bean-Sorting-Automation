Coffee bean sorting - combined YOLO dataset (classes: 0 green, 1 reject)
Extract all parts into ONE folder (they share the same layout), then train with data.yaml.

images/{train,val,test}, labels/{train,val,test}: YOLO txt (class cx cy w h, normalised)
Sources (filename prefix): g1 = green batch 1 (640x480, untouched), g2 = green batch 2 (subtle softer green, resized 640x480),
g3 = green batch 3 (subtle softer green, 320x320), r1 / r2 = reject batches 1 / 2, mix_ = 200 synthetic frames.
Splits: real photos split in blocks of 8 consecutive frames per batch (train ~80 / val ~10 / test ~10 %).
Synthetic mix_ frames are train-only and were built only from beans in train photos (no val/test bean appears in them).
Boxes were generated automatically (real photos: colour segmentation + review; mix_: exact paste positions).
review_flags.csv lists real photos with beans touching the frame edge or other doubtful cases.
Image sizes differ (320x320 and 640x480); train with imgsz=640.
