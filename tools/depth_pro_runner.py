"""
Apple Depth Pro runner for KESHIKI video depth (runs in the ComfyUI Python env, needs torch + timm).

  python depth_pro_runner.py --root <ml-depth-pro repo> --frames <dir of frame_XXXX.jpg> --out <layer dir>
                             [--start 0 --end -1 --checkpoint <depth_pro.pt>]

Depth Pro is a single-image model, so for video we keep it stable:
  * the focal length is estimated on a few frames and then fixed for the whole clip
    (per-frame FOV guesses are the main source of flicker);
  * log depth (or inverse depth) is normalised with one global range for the clip (1st/99.5th percentiles), not per frame;
  * a light temporal EMA removes the remaining shimmer.
Output: 8-bit grayscale PNGs, near = white (same convention as Depth Anything).
Progress lines: "STATUS_UPDATE:{json}".
"""
import argparse
import glob
import json
import os
import sys
import types


def status(p, msg, **details):
    print("STATUS_UPDATE:" + json.dumps({"progress": p, "status": msg, "details": details or None}, ensure_ascii=False), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--frames", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--checkpoint", default="")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=-1)
    ap.add_argument("--ema", type=float, default=0.6, help="weight of the current frame (1 = no smoothing)")
    ap.add_argument("--focal_frames", type=int, default=8)
    ap.add_argument("--mapping", choices=["inverse", "log"], default="inverse",
                    help="inverse = disparity like Depth Anything (default); log = flatter near range")
    a = ap.parse_args()

    # depth_pro/__init__ imports utils -> pillow_heif (HEIC loading only); not needed here.
    if "pillow_heif" not in sys.modules:
        try:
            import pillow_heif  # noqa: F401
        except ImportError:
            stub = types.ModuleType("pillow_heif")
            stub.register_heif_opener = lambda *x, **k: None
            sys.modules["pillow_heif"] = stub
    sys.path.insert(0, os.path.join(a.root, "src"))

    import cv2
    import numpy as np
    import torch
    from depth_pro.depth_pro import DEFAULT_MONODEPTH_CONFIG_DICT, create_model_and_transforms

    files = sorted(glob.glob(os.path.join(a.frames, "frame_*.jpg")) + glob.glob(os.path.join(a.frames, "frame_*.png")))
    if not files:
        status(100, "No frames", error="no frames")
        sys.exit(2)
    end = len(files) - 1 if a.end < 0 else min(a.end, len(files) - 1)
    idx = list(range(max(0, a.start), end + 1))

    ckpt = a.checkpoint or os.path.join(a.root, "checkpoints", "depth_pro.pt")
    cfg = DEFAULT_MONODEPTH_CONFIG_DICT
    cfg.checkpoint_uri = ckpt
    status(5, "Loading Depth Pro…")
    dev = torch.device("cuda")
    model, transform = create_model_and_transforms(config=cfg, device=dev, precision=torch.half)
    model.eval()

    def load(i):
        img = cv2.imread(files[i], cv2.IMREAD_COLOR)
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    # 1) fixed focal length from a few frames spread over the clip
    picks = [idx[int(k * (len(idx) - 1) / max(1, a.focal_frames - 1))] for k in range(min(a.focal_frames, len(idx)))]
    fs = []
    with torch.no_grad():
        for i in picks:
            fs.append(float(model.infer(transform(load(i)).to(dev))["focallength_px"]))
    f_px = float(np.median(fs))
    status(12, f"Focal length fixed at {f_px:.0f} px")

    # 2) inverse depth for every frame (kept at half resolution in RAM)
    inv = []
    H = W = None
    with torch.no_grad():
        for n, i in enumerate(idx):
            rgb = load(i)
            H, W = rgb.shape[:2]
            d = model.infer(transform(rgb).to(dev), f_px=torch.tensor(f_px, device=dev))["depth"]
            d = d.clamp(min=0.05).float()
            x = (-torch.log(d) if a.mapping == "log" else 1.0 / d).cpu().numpy()
            inv.append(cv2.resize(x, (W // 2, H // 2), interpolation=cv2.INTER_AREA).astype(np.float16))
            if n % 4 == 0:
                status(12 + int(78 * (n + 1) / len(idx)), f"Depth Pro: frame {n + 1}/{len(idx)}")
    del model
    torch.cuda.empty_cache()

    # 3) one global range + temporal EMA -> 8-bit PNG, near = white
    sample = np.concatenate([v[::8, ::8].ravel() for v in inv]).astype(np.float32)
    lo, hi = np.percentile(sample, 1), np.percentile(sample, 99.5)
    os.makedirs(a.out, exist_ok=True)
    prev = None
    for n, i in enumerate(idx):
        cur = np.clip((inv[n].astype(np.float32) - lo) / max(1e-6, hi - lo), 0, 1)
        prev = cur if prev is None else a.ema * cur + (1 - a.ema) * prev
        img = (cv2.resize(prev, (W, H), interpolation=cv2.INTER_CUBIC).clip(0, 1) * 255).astype(np.uint8)
        cv2.imwrite(os.path.join(a.out, f"frame_{i:04d}.png"), img)
    status(100, f"Done: {len(idx)} frames", frames=len(idx), focal_px=f_px)


if __name__ == "__main__":
    main()
