"""
Single-image preprocessors (Depth / Canny / Pose) for the 1D workspace
=====================================================================
Runs the API-JSON templates in workflows/api/deconstruct/<kind>_image.json through
the unified process runner (process id: "deconstruct"), waits for the result and
returns it as a PNG data URL so the UI can store it as a layer of the image.
"""

import base64
import hashlib
import os
import time
from typing import Any, Dict, Optional

try:
    from .process_runner import ProcessError, get_run_status, run_process
except ImportError:  # pragma: no cover
    from process_runner import ProcessError, get_run_status, run_process

KINDS = {"depth": "depth_image", "normal": "normal_image", "canny": "canny_image", "pose": "pose_image"}


def _input_dir() -> str:
    import folder_paths
    return folder_paths.get_input_directory()


def _output_path(item: Dict[str, Any]) -> str:
    import folder_paths
    base = {
        "output": folder_paths.get_output_directory(),
        "temp": folder_paths.get_temp_directory(),
        "input": folder_paths.get_input_directory(),
    }.get(item.get("type", "output"), folder_paths.get_output_directory())
    path = os.path.normpath(os.path.join(base, item.get("subfolder", ""), item["filename"]))
    if os.path.commonpath([base, path]) != os.path.normpath(base):
        raise ProcessError("Unexpected output path")
    return path


def _read_pose_people(since: float):
    """Body keypoints from the newest SavePoseKpsAsJsonFile output: one 18-point OpenPose skeleton per person,
    normalised to 0..1 as [x, y, visible] so the UI can turn them into editable skeleton layers."""
    import glob
    import json
    import folder_paths
    try:
        files = glob.glob(os.path.join(folder_paths.get_output_directory(), "keshiki", "preprocess", "pose_kps*.json"))
        files = [f for f in files if os.path.getmtime(f) >= since - 2]
        if not files:
            return []
        with open(max(files, key=os.path.getmtime), "r", encoding="utf-8") as f:
            frames = json.load(f)
        fr = frames[0] if isinstance(frames, list) and frames else frames
        W, H = float(fr.get("canvas_width") or 1), float(fr.get("canvas_height") or 1)
        out = []
        for person in fr.get("people") or []:
            k = person.get("pose_keypoints_2d") or []
            pts = []
            for i in range(18):
                x, y, c = (k[i * 3:i * 3 + 3] + [0, 0, 0])[:3] if len(k) >= i * 3 + 3 else (0, 0, 0)
                pts.append([float(x), float(y), 1 if (c or 0) > 0.05 and (x or y) else 0])
            if sum(p[2] for p in pts) < 4:
                continue
            if max(max(p[0], p[1]) for p in pts) > 1.5:  # absolute pixels -> 0..1
                pts = [[p[0] / W, p[1] / H, p[2]] for p in pts]
            out.append(pts)
        return out
    except Exception as e:  # keypoints are a bonus, never fail the preprocess
        print(f"[VFX-Suite] pose keypoints unavailable: {e}")
        return []


def run_image_preprocess(kind: str, image_data_url: str, params: Optional[Dict[str, Any]] = None,
                         timeout_s: float = 240.0) -> Dict[str, Any]:
    if kind not in KINDS:
        raise ProcessError(f"Unknown preprocessor '{kind}' (expected one of {', '.join(KINDS)})")
    if not isinstance(image_data_url, str) or "," not in image_data_url:
        raise ProcessError("Expected an image data URL")
    raw = base64.b64decode(image_data_url.split(",", 1)[1])
    digest = hashlib.sha1(raw).hexdigest()[:16]
    ext = ".png" if image_data_url.startswith("data:image/png") else ".jpg"
    filename = f"keshiki_pre_{digest}{ext}"
    path = os.path.join(_input_dir(), filename)
    if not os.path.isfile(path):
        with open(path, "wb") as f:
            f.write(raw)

    run_params = {"Input Image.image": filename}
    run_params.update(params or {})
    queued = run_process("deconstruct", template=KINDS[kind], params=run_params, client_id="keshiki_1d_preprocess")
    prompt_id = queued["prompt_id"]

    started = time.time()
    while time.time() - started < timeout_s:
        st = get_run_status(prompt_id)
        if st["status"] == "cancelled":
            raise ProcessError("Stopped")
        if st["status"] == "error":
            msgs = [e.get("exception_message", "").strip() for e in (st.get("errors") or []) if isinstance(e, dict)]
            raise ProcessError(f"{kind} failed: {'; '.join(m for m in msgs if m) or st.get('errors')}")
        if st["status"] == "done":
            images = [o for o in st["outputs"] if o.get("kind") == "images"]
            if not images:
                raise ProcessError(f"{kind}: workflow finished without an image output")
            with open(_output_path(images[-1]), "rb") as f:
                data = f.read()
            mime = "image/png" if images[-1]["filename"].lower().endswith(".png") else "image/jpeg"
            extra = {"people": _read_pose_people(started)} if kind == "pose" else {}
            return {
                **extra,
                "kind": kind,
                "prompt_id": prompt_id,
                "seconds": round(time.time() - started, 1),
                "image": f"data:{mime};base64," + base64.b64encode(data).decode("ascii"),
            }
        time.sleep(0.7)
    raise ProcessError(f"{kind}: timed out after {int(timeout_s)} s (prompt {prompt_id} may still be running)")
