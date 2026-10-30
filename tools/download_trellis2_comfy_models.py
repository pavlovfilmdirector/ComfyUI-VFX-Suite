import os
import sys
import time
import subprocess

MODELS_DIR = r"B:\StabilityMatrix2\ComfyUI\models"

ITEMS = [
    {
        "name": "birefnet.safetensors",
        "rel_dir": "background_removal",
        "url": "https://huggingface.co/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors",
        "expected_size": 444473596,  # 423.88 MB
    },
    {
        "name": "trellis_2_shape_vae_bf16.safetensors",
        "rel_dir": "vae",
        "url": "https://huggingface.co/Comfy-Org/TRELLIS.2/resolve/main/vae/trellis_2_shape_vae_bf16.safetensors",
        "expected_size": 1095874224,  # ~1045.1 MB
    },
    {
        "name": "trellis_2_texture_vae_bf16.safetensors",
        "rel_dir": "vae",
        "url": "https://huggingface.co/Comfy-Org/TRELLIS.2/resolve/main/vae/trellis_2_texture_vae_bf16.safetensors",
        "expected_size": 948439408,  # ~904.5 MB
    },
    {
        "name": "dino_v3_L_naf_fp32.safetensors",
        "rel_dir": "clip_vision",
        "url": "https://huggingface.co/Comfy-Org/Pixal3D/resolve/main/clip_vision/dino_v3_L_naf_fp32.safetensors",
        "expected_size": 1215181744,  # ~1158.9 MB
    },
    {
        "name": "trellis_2_int8_convrot.safetensors",
        "rel_dir": "diffusion_models",
        "url": "https://huggingface.co/Comfy-Org/TRELLIS.2/resolve/main/diffusion_models/trellis_2_int8_convrot.safetensors",
        "expected_size": 5253093232,  # ~5009.7 MB
    },
    {
        "name": "pixal3d_int8_convrot.safetensors",
        "rel_dir": "diffusion_models",
        "url": "https://huggingface.co/Comfy-Org/Pixal3D/resolve/main/diffusion_models/pixal3d_int8_convrot.safetensors",
        "expected_size": 5584577888,  # ~5325.8 MB
    },
]


def download_file(item):
    name = item["name"]
    target_dir = os.path.join(MODELS_DIR, item["rel_dir"])
    os.makedirs(target_dir, exist_ok=True)
    target_file = os.path.join(target_dir, name)
    part_file = target_file + ".part"

    expected = item["expected_size"]

    if os.path.exists(target_file):
        sz = os.path.getsize(target_file)
        if sz >= expected * 0.99:
            print(f"[SKIP] {name} already complete: {sz / (1024*1024):.1f} MB", flush=True)
            return True
        else:
            print(f"[WARN] {name} exists but size is {sz} (expected {expected}). Moving to .part to resume.", flush=True)
            if os.path.exists(part_file):
                os.remove(part_file)
            os.rename(target_file, part_file)

    initial_sz = os.path.getsize(part_file) if os.path.exists(part_file) else 0
    print(f"\n[START] Downloading {name} ({expected / (1024*1024):.1f} MB, resuming from {initial_sz / (1024*1024):.1f} MB)...", flush=True)

    # Use curl with --silent --show-error and direct stderr to null or a log file so no pipe deadlock can ever occur
    cmd = [
        "curl.exe",
        "-L",
        "-C", "-",
        "--silent",
        "--show-error",
        "--retry", "5",
        "--retry-delay", "3",
        "-o", part_file,
        item["url"]
    ]

    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")

    last_sz = initial_sz
    last_t = t0

    while proc.poll() is None:
        time.sleep(3)
        now = time.time()
        if os.path.exists(part_file):
            cur_sz = os.path.getsize(part_file)
            delta_bytes = cur_sz - last_sz
            delta_t = now - last_t
            speed_mb = (delta_bytes / (1024 * 1024)) / delta_t if delta_t > 0 else 0
            pct = (cur_sz / expected) * 100 if expected else 0
            elapsed = now - t0
            print(
                f"[{name}] {cur_sz / (1024*1024):.1f} / {expected / (1024*1024):.1f} MB ({pct:.1f}%) | Speed: {speed_mb:.2f} MB/s | Elapsed: {elapsed:.0f}s",
                flush=True
            )
            last_sz = cur_sz
            last_t = now

    stderr_output = proc.stderr.read() if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        print(f"[ERROR] curl exited with code {rc} for {name}: {stderr_output}", flush=True)
        return False

    if os.path.exists(part_file):
        final_sz = os.path.getsize(part_file)
        if final_sz >= expected * 0.99:
            if os.path.exists(target_file):
                os.remove(target_file)
            os.rename(part_file, target_file)
            print(f"[SUCCESS] {name} downloaded successfully! ({final_sz / (1024*1024):.1f} MB in {time.time() - t0:.1f}s)", flush=True)
            return True
        else:
            print(f"[ERROR] Size mismatch: got {final_sz}, expected {expected}", flush=True)
            return False
    return False


def main():
    print(f"Starting Trellis 2 / Pixal 3D model downloads into {MODELS_DIR}", flush=True)
    for item in ITEMS:
        success = download_file(item)
        if not success:
            print(f"[FAILED] Failed downloading {item['name']}. Stopping batch.", flush=True)
            sys.exit(1)
    print("\nALL MODELS DOWNLOADED AND VERIFIED SUCCESSFULLY!", flush=True)


if __name__ == "__main__":
    main()
