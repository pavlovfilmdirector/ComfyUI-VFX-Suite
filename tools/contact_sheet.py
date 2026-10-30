"""Contact sheet of a video's layers at one frame: python tools/contact_sheet.py <video_dir> <frame> <out.png> [name filter]"""
import json
import os
import sys

from PIL import Image, ImageDraw

vdir, frame, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
flt = sys.argv[4] if len(sys.argv) > 4 else ""
tiles = []
raw = os.path.join(vdir, "plate", "raw")
src = sorted(f for f in os.listdir(raw) if f.endswith((".png", ".jpg")))
tiles.append(("source", Image.open(os.path.join(raw, src[min(frame, len(src) - 1)])).convert("RGB")))
meta = json.load(open(os.path.join(vdir, "layers.json"), encoding="utf-8"))
for l in meta.get("layers", meta):
    if flt and flt not in l.get("name", ""):
        continue
    p = os.path.join(vdir, "layers", l["id"], f"frame_{frame:04d}.png")
    if os.path.isfile(p):
        im = Image.open(p)
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (0, 0, 0)); bg.paste(im, mask=im.split()[3]); im = bg
        tiles.append((l["name"], im.convert("RGB")))
W = 480
rows = []
for name, im in tiles:
    im = im.resize((W, int(im.height * W / im.width)))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 22], fill=(0, 0, 0)); d.text((6, 4), name, fill=(255, 255, 0))
    rows.append(im)
cols = 3
h = max(r.height for r in rows)
sheet = Image.new("RGB", (W * cols, h * ((len(rows) + cols - 1) // cols)), (30, 30, 30))
for i, r in enumerate(rows):
    sheet.paste(r, ((i % cols) * W, (i // cols) * h))
sheet.save(out)
print(out, len(rows))
