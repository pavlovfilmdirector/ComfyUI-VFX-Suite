r"""
3D Mesh Generation & Proxy Replacement Service (Hunyuan3D-Omni & TRELLIS.2)
===========================================================================
Bridges the VFX AI Studio 3D Viewport & Details Inspector Panel with:
1. Tencent Hunyuan3D-Omni (external install, path in keshiki_local.json) — supports BBox-conditioned 3D shape generation
2. Microsoft TRELLIS.2-4B (external install, path in keshiki_local.json) — supports high-detail O-Voxel cascade + PBR textured GLB
3. Project 3D Asset Library (output_cache/meshes3d) — instant .glb proxy swapping & persistence
"""

import os
import sys
import time
import json
import uuid
import shutil
import base64
import threading
import subprocess
from pathlib import Path
import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "output_cache")
MESH3D_DIR = os.path.join(CACHE_DIR, "meshes3d")
MESH3D_INPUTS_DIR = os.path.join(MESH3D_DIR, "inputs")
MESH3D_INDEX_PATH = os.path.join(MESH3D_DIR, "library.json")

os.makedirs(MESH3D_DIR, exist_ok=True)
os.makedirs(MESH3D_INPUTS_DIR, exist_ok=True)

try:
    from .local_config import local_setting
except ImportError:  # pragma: no cover
    from local_config import local_setting

# Optional external generators (own repos + Python envs); configured per machine in keshiki_local.json
HUNYUAN_ROOT = local_setting("hunyuan3d_root")
HUNYUAN_PYTHON = local_setting("hunyuan3d_python")
HUNYUAN_SCRIPT = os.path.join(HUNYUAN_ROOT, "generate_3d.py") if HUNYUAN_ROOT else ""

TRELLIS_ROOT = local_setting("trellis_root")
TRELLIS_PYTHON = local_setting("trellis_python")
TRELLIS_SCRIPT = os.path.join(TRELLIS_ROOT, "generate_3d.py") if TRELLIS_ROOT else ""

# In-memory job tracker for async 3D generation
_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _load_library_index():
    if os.path.exists(MESH3D_INDEX_PATH):
        try:
            with open(MESH3D_INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def _save_library_index(items):
    try:
        with open(MESH3D_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Mesh3DService] Failed saving library index: {e}")


def seed_existing_glb_assets():
    """
    Ensure existing .glb models from the Hunyuan3D-Omni and TRELLIS.2 outputs folders
    are available in output_cache/meshes3d so the user can test Cube -> 3D replacement immediately.
    """
    items = _load_library_index()
    known_files = {it.get("filename") for it in items}

    candidates = []
    hy_out = os.path.join(HUNYUAN_ROOT, "outputs") if HUNYUAN_ROOT else ""
    if hy_out and os.path.isdir(hy_out):
        for fname in os.listdir(hy_out):
            if fname.lower().endswith(".glb"):
                candidates.append((os.path.join(hy_out, fname), fname, os.path.splitext(fname)[0].replace("_", " "),
                                   "hunyuan_omni", "3D mesh generated via Hunyuan3D-Omni"))

    # Also scan the TRELLIS.2 outputs folder for any .glb files
    trellis_out = os.path.join(TRELLIS_ROOT, "outputs") if TRELLIS_ROOT else ""
    if trellis_out and os.path.exists(trellis_out):
        for fname in os.listdir(trellis_out):
            if fname.lower().endswith(".glb"):
                candidates.append((
                    os.path.join(trellis_out, fname),
                    fname,
                    os.path.splitext(fname)[0].replace("_", " "),
                    "trellis2",
                    "Textured 3D mesh generated via Microsoft TRELLIS.2-4B"
                ))

    changed = False
    for src_path, dst_name, title, engine, desc in candidates:
        if os.path.exists(src_path):
            dst_path = os.path.join(MESH3D_DIR, dst_name)
            if not os.path.exists(dst_path):
                try:
                    shutil.copy2(src_path, dst_path)
                except Exception:
                    continue
            if dst_name not in known_files and os.path.exists(dst_path):
                size_mb = round(os.path.getsize(dst_path) / (1024 * 1024), 2)
                items.append({
                    "id": f"mesh_{uuid.uuid4().hex[:8]}",
                    "title": title,
                    "filename": dst_name,
                    "url": f"/vfx/mesh3d/file/{dst_name}",
                    "engine": engine,
                    "description": desc,
                    "size_mb": size_mb,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
                })
                known_files.add(dst_name)
                changed = True

    # Also scan MESH3D_DIR for any unindexed .glb files
    for fname in os.listdir(MESH3D_DIR):
        if fname.lower().endswith(".glb") and fname not in known_files:
            fpath = os.path.join(MESH3D_DIR, fname)
            size_mb = round(os.path.getsize(fpath) / (1024 * 1024), 2)
            items.append({
                "id": f"mesh_{uuid.uuid4().hex[:8]}",
                "title": os.path.splitext(fname)[0].replace("_", " "),
                "filename": fname,
                "url": f"/vfx/mesh3d/file/{fname}",
                "engine": "trellis2" if "trellis" in fname.lower() else "hunyuan_omni",
                "description": "Cached 3D GLB asset in project library",
                "size_mb": size_mb,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            known_files.add(fname)
            changed = True

    if changed:
        _save_library_index(items)
    return items


def list_mesh3d_library():
    items = seed_existing_glb_assets()
    return {
        "meshes": items,
        "engines": {
            "trellis2": {
                "name": "TRELLIS.2 (4B)",
                "available": bool(TRELLIS_ROOT) and os.path.isdir(TRELLIS_ROOT) and bool(TRELLIS_PYTHON) and os.path.exists(TRELLIS_PYTHON),
                "modes": ["generate", "texture"],
                "weights_ready": bool(TRELLIS_ROOT) and os.path.exists(os.path.join(TRELLIS_ROOT, "weights", "microsoft", "TRELLIS.2-4B", "pipeline.json")),
                "features": ["High-detail O-Voxel cascade", "PBR 2K/4K Texture Atlas", "GLB UV Unwrap"]
            },
            "hunyuan_omni": {
                "name": "Hunyuan3D-Omni",
                "available": bool(HUNYUAN_SCRIPT) and os.path.exists(HUNYUAN_SCRIPT) and os.path.exists(HUNYUAN_PYTHON),
                "weights_ready": bool(HUNYUAN_ROOT) and os.path.exists(os.path.join(HUNYUAN_ROOT, "weights", "tencent", "Hunyuan3D-Omni")),
                "features": ["Native Cube BBox Conditioning", "Fast SiT Flow Matching", "Clean Topology"]
            }
        }
    }


def _resolve_input_image(image_source: str, prompt_hint: str = "3D Object") -> str:
    """
    Resolves an image source (server route URL, local path, base64 data URI, or SVG concept card)
    into a physical PNG file on disk in MESH3D_INPUTS_DIR.
    """
    ts = int(time.time() * 1000)
    out_png = os.path.join(MESH3D_INPUTS_DIR, f"input_{ts}.png")

    if not image_source:
        image_source = "/vfx/plate_prep/default/frame/cutout/0"

    # 1. Base64 PNG/JPG data URI
    if image_source.startswith("data:image/") and ";base64," in image_source:
        b64_part = image_source.split(";base64,", 1)[1]
        raw = base64.b64decode(b64_part)
        with open(out_png, "wb") as f:
            f.write(raw)
        return out_png

    # 2. Server relative route: /vfx/plate_prep/default/frame/{stage}/{idx} or /vfx/actor_cutout/{idx}
    if image_source.startswith("/vfx/"):
        if "/frame/cutout/" in image_source or "/actor_cutout/" in image_source:
            cand = os.path.join(CACHE_DIR, "plate", "node_default", "cutout", "frame_0000.png")
            if not os.path.exists(cand):
                cand = os.path.join(CACHE_DIR, "actor_cutout", "frame_0000.png")
            if os.path.exists(cand):
                shutil.copy2(cand, out_png)
                return out_png
        elif "/frame/raw/" in image_source or "/frame/undist/" in image_source:
            cand = os.path.join(CACHE_DIR, "plate", "node_default", "raw", "frame_0000.jpg")
            if not os.path.exists(cand):
                cand = os.path.join(CACHE_DIR, "plate", "raw", "frame_0000.jpg")
            if os.path.exists(cand):
                shutil.copy2(cand, out_png)
                return out_png

    # 3. Direct local filesystem path
    if os.path.exists(image_source) and os.path.isfile(image_source):
        shutil.copy2(image_source, out_png)
        return out_png

    # 4. Fallback to TRELLIS.2 example image or cutout frame_0000.png if SVG concept card was passed
    cutout_cand = os.path.join(CACHE_DIR, "plate", "node_default", "cutout", "frame_0000.png")
    trellis_sample = os.path.join(TRELLIS_ROOT, "assets", "example_image", "T.png") if TRELLIS_ROOT else ""
    if os.path.exists(cutout_cand):
        shutil.copy2(cutout_cand, out_png)
        return out_png
    if trellis_sample and os.path.exists(trellis_sample):
        shutil.copy2(trellis_sample, out_png)
        return out_png

    # 5. Synthesize a high-contrast RGBA silhouette object image so generation never crashes
    canvas = np.zeros((512, 512, 4), dtype=np.uint8)
    cv2.rectangle(canvas, (140, 90), (372, 430), (180, 200, 220, 255), -1)
    cv2.rectangle(canvas, (160, 110), (352, 410), (90, 140, 190, 255), 6)
    cv2.imwrite(out_png, canvas)
    return out_png


def start_3d_generation_job(params: dict) -> dict:
    """
    Launches an asynchronous 3D generation job using either TRELLIS.2 or Hunyuan3D-Omni.
    Returns job metadata immediately so the frontend can display progress and swap the selected Cube when done.
    """
    raw_eng = str(params.get("engine", "hunyuan_omni")).lower().strip()
    if "trellis" in raw_eng:
        engine = "trellis2"
    else:
        engine = "hunyuan_omni"

    target_object_id = params.get("target_object_id", "")
    actor_name = params.get("actor_name") or params.get("name") or "Generated_3D_Actor"
    prompt = params.get("prompt", "") or actor_name
    image_source = params.get("image_source") or params.get("image_data_url") or ""
    bbox_scale = params.get("bbox_scale") or params.get("bbox") or [1.0, 1.0, 1.0]

    # Normalize bbox_scale to [0.15 .. 0.90] range for Hunyuan3D-Omni bbox conditioning
    try:
        sx, sy, sz = float(bbox_scale[0]), float(bbox_scale[1]), float(bbox_scale[2])
        max_dim = max(0.01, sx, sy, sz)
        norm_bbox = [
            round(max(0.15, min(0.95, (sx / max_dim) * 0.85)), 3),
            round(max(0.15, min(0.95, (sy / max_dim) * 0.85)), 3),
            round(max(0.15, min(0.95, (sz / max_dim) * 0.85)), 3)
        ]
    except Exception:
        norm_bbox = [0.8, 0.8, 0.8]

    steps = int(params.get("steps", 30))
    raw_res = int(params.get("resolution", 384))
    resolution = 384 if raw_res > 512 and engine == "hunyuan_omni" else raw_res
    pipeline_type = str(params.get("pipeline_type", str(raw_res) if raw_res in (512, 1024, 1536) else "1024"))
    texture_size = int(params.get("texture_size", 1024))
    seed = int(params.get("seed", 42))

    input_img_path = _resolve_input_image(image_source, prompt)
    trellis_mode = str(params.get("mode") or "generate")
    mesh_path = ""
    if engine == "trellis2" and trellis_mode == "texture":
        src = str(params.get("mesh_source") or "")
        name = src.rsplit("/", 1)[-1] if src.startswith("/vfx/mesh3d/file/") else ""
        mesh_path = os.path.join(MESH3D_DIR, name) if name else src
        if not mesh_path or not os.path.isfile(mesh_path):
            raise ValueError("Texture mode needs a mesh: pick one from the 3D library or export the selected object")
    job_id = f"job3d_{uuid.uuid4().hex[:10]}"

    job_record = {
        "job_id": job_id,
        "status": "running",
        "progress": 10,
        "message": f"Initializing {engine.upper()}...",
        "engine": engine,
        "target_object_id": target_object_id,
        "actor_name": actor_name,
        "prompt": prompt,
        "bbox_scale": bbox_scale,
        "norm_bbox": norm_bbox,
        "input_image": input_img_path,
        "started_at": time.time(),
        "elapsed_sec": 0.0,
        "logs": [
            f"[Mesh3D] Job {job_id} started ({engine.upper()})",
            f"[Mesh3D] Target Actor: {actor_name} ({target_object_id or 'Selected Cube'})",
            f"[Mesh3D] BBox Conditioning: {norm_bbox} (from Scene Scale {bbox_scale})"
        ],
        "result_mesh": None,
        "output_url": None,
        "output_filename": None,
        "error": None
    }

    with _JOBS_LOCK:
        _JOBS[job_id] = job_record

    def _worker():
        t0 = time.time()
        try:
            safe_slug = "".join(c if c.isalnum() or c in "_-" else "_" for c in actor_name)[:32] or "prop3d"
            out_subdir = os.path.join(MESH3D_DIR, f"build_{job_id}")
            os.makedirs(out_subdir, exist_ok=True)

            if engine == "hunyuan_omni":
                cmd = [
                    HUNYUAN_PYTHON,
                    HUNYUAN_SCRIPT,
                    "--image", input_img_path,
                    "--output_dir", out_subdir,
                    "--steps", str(steps),
                    "--resolution", str(resolution),
                    "--seed", str(seed),
                    "--bbox", str(norm_bbox[0]), str(norm_bbox[1]), str(norm_bbox[2]),
                    "--flashvdm"
                ]
                cwd_dir = HUNYUAN_ROOT
            else:
                # our runner (tools/trellis_runner.py) inside the TRELLIS.2 env: every setting of its web demo,
                # plus "texture" mode that paints an existing mesh (e.g. a blockout from the 3D scene) from the image
                res_key = str(params.get("resolution_key") or ("512" if raw_res <= 512 else "1536" if raw_res >= 1536 else "1024"))
                cmd = [
                    TRELLIS_PYTHON, os.path.join(BASE_DIR, "tools", "trellis_runner.py"),
                    "--mode", "texture" if trellis_mode == "texture" else "generate",
                    "--image", input_img_path,
                    "--out", os.path.join(out_subdir, "result.glb"),
                    "--resolution", res_key,
                    "--texture_size", str(texture_size if texture_size in (1024, 2048, 4096) else 2048),
                    "--decimation", str(int(params.get("decimation", 500000))),
                    "--seed", str(seed),
                ]
                if trellis_mode == "texture":
                    cmd += ["--mesh", mesh_path]
                if params.get("no_rembg"):
                    cmd.append("--no_rembg")
                for pre in ("ss", "shape", "tex"):
                    for key in ("steps", "guidance", "rescale", "rescale_t"):
                        val = params.get(f"{pre}_{key}")
                        if val not in (None, ""):
                            cmd += [f"--{pre}_{key}", str(val)]
                cwd_dir = TRELLIS_ROOT

            with _JOBS_LOCK:
                job_record["logs"].append(f"[Exec] {' '.join(cmd)}")

            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            # Apply user's custom model directory in-place (if configured in Studio Settings)
            try:
                from .pipeline_config_service import load_processes_config
                p_cfg = load_processes_config().get("processes", {}).get("mesh_gen", {})
                custom_dir = str(p_cfg.get("custom_model_dir", "")).strip()
                if custom_dir and os.path.isdir(custom_dir):
                    env["HUNYUAN3D_MODEL_DIR"] = custom_dir
                    env["TRELLIS_MODEL_DIR"] = custom_dir
                    env["HF_HOME"] = custom_dir
                    with _JOBS_LOCK:
                        job_record["logs"].append(f"[ModelDir] Using custom local folder in-place: {custom_dir}")
            except Exception:
                pass

            proc = subprocess.Popen(
                cmd,
                cwd=cwd_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env
            )

            for line in proc.stdout:
                clean = line.strip()
                if clean.startswith("STATUS_UPDATE:"):
                    try:
                        msg = json.loads(clean[len("STATUS_UPDATE:"):])
                        with _JOBS_LOCK:
                            job_record["progress"] = max(job_record.get("progress", 10), min(99, int(msg.get("progress", 0))))
                            job_record["message"] = str(msg.get("status", ""))[:120]
                            job_record["elapsed_sec"] = round(time.time() - t0, 1)
                    except ValueError:
                        pass
                    continue
                if clean:
                    with _JOBS_LOCK:
                        job_record["logs"].append(clean)
                        if len(job_record["logs"]) > 60:
                            job_record["logs"] = job_record["logs"][-60:]
                        job_record["elapsed_sec"] = round(time.time() - t0, 1)
                        job_record["progress"] = min(95, job_record.get("progress", 10) + 5)
                        job_record["message"] = clean[:90]

            proc.wait()

            # Locate generated .glb inside out_subdir
            generated_glb = None
            if os.path.exists(out_subdir):
                for fn in os.listdir(out_subdir):
                    if fn.lower().endswith(".glb"):
                        generated_glb = os.path.join(out_subdir, fn)
                        break

            if not generated_glb or not os.path.exists(generated_glb):
                raise RuntimeError(f"3D generator exited (code {proc.returncode}) without producing a .glb file.")

            final_filename = f"{safe_slug}_{engine}_{job_id[-4:]}.glb"
            final_path = os.path.join(MESH3D_DIR, final_filename)
            shutil.copy2(generated_glb, final_path)
            shutil.rmtree(out_subdir, ignore_errors=True)

            size_mb = round(os.path.getsize(final_path) / (1024 * 1024), 2)
            out_url = f"/vfx/mesh3d/file/{final_filename}"
            mesh_entry = {
                "id": f"mesh_{uuid.uuid4().hex[:8]}",
                "title": f"{actor_name} ({'TRELLIS.2' if engine == 'trellis2' else 'Hunyuan3D'})",
                "filename": final_filename,
                "url": out_url,
                "engine": engine,
                "description": prompt,
                "bbox_scale": bbox_scale,
                "size_mb": size_mb,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            items = _load_library_index()
            items.insert(0, mesh_entry)
            _save_library_index(items)

            with _JOBS_LOCK:
                job_record["status"] = "completed"
                job_record["progress"] = 100
                job_record["message"] = f"Saved {final_filename} ({size_mb} MB)"
                job_record["elapsed_sec"] = round(time.time() - t0, 1)
                job_record["result_mesh"] = mesh_entry
                job_record["output_url"] = out_url
                job_record["output_filename"] = final_filename
                job_record["logs"].append(f"[Done] Saved GLB: {final_filename} ({size_mb} MB)")

        except Exception as e:
            with _JOBS_LOCK:
                job_record["status"] = "failed"
                job_record["message"] = str(e)
                job_record["elapsed_sec"] = round(time.time() - t0, 1)
                job_record["error"] = str(e)
                job_record["logs"].append(f"[Error] {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return job_record


def get_3d_job_status(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return {"error": f"Job {job_id} not found", "status": "not_found"}
        if job["status"] == "running":
            job["elapsed_sec"] = round(time.time() - job["started_at"], 1)
        return dict(job)
