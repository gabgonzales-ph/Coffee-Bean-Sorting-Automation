import glob, zipfile, os, re

zips = sorted(glob.glob("combined_dataset_part*.zip"))
assert len(zips) == 3, f"Expected 3 zips, found {len(zips)}"

for z in zips:
    with zipfile.ZipFile(z) as f:
        f.extractall(".")
    print("extracted", z)

root = os.path.abspath("combined_dataset")

# point data.yaml at this folder
p = os.path.join(root, "data.yaml")
txt = open(p).read()
txt = re.sub(r"^path:.*$", f"path: {root.replace(os.sep, '/')}", txt, flags=re.M)
open(p, "w").write(txt)

# check
for sp in ("train", "val", "test"):
    n_img = len(os.listdir(f"{root}/images/{sp}"))
    n_lbl = len(os.listdir(f"{root}/labels/{sp}"))
    print(sp, n_img, "images,", n_lbl, "labels")
    assert n_img == n_lbl
print("Done. data.yaml:", p)