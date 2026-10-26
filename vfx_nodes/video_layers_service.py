"""
2D Video Layers & Jobs
======================
Per-video plate and layer sequences, stored inside the project:

    projects/<p>/video/<video_id>/plate/        raw/ undist/ automask/ finalmask/ cutout/ fg/ info.json
    projects/<p>/video/<video_id>/layers/<lid>/frame_0000.png ...
    projects/<p>/video/<video_id>/layers.json   ordered stack (bottom -> top)

The plate reuses plate_prep_service (frame extraction, lens, YOLO/SAM2, CorridorKey) with the
plate root registered for node id "vid_<video_id>".

Long operations run as background jobs (progress polled by the UI):
  prepare | depth | canny | pose | detect_all | track_object | corridor_key | undistort
"""

import json
import os
import re
import shutil
import threading
import time
import traceback
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

try:
    from . import plate_prep_service as pps
    from .project_manager import get_project_dir
    from .process_runner import ProcessError, get_run_status, load_template, run_process
except ImportError:  # pragma: no cover
    import plate_prep_service as pps
    from project_manager import get_project_dir
    from process_runner import ProcessError, get_run_status, load_template, run_process

_ID_RE = re.compile(r"[^A-Za-z0-9_\-]")
LAYER_TYPES = ("mask", "matte", "alpha", "depth", "canny", "pose", "plate")
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_GPU_LOCK = threading.Lock()


def _safe(v: str, fb: str = "x") -> str:
    return _ID_RE.sub("_", str(v or ""))[:96] or fb


# ---------------------------------------------------------------------------
# Paths & manifest
# ---------------------------------------------------------------------------
def video_root(pid: str, vid: str) -> str:
    return os.path.join(get_project_dir(pid), "video", _safe(vid, "video"))


def node_id_for(vid: str) -> str:
    return f"vid_{_safe(vid, 'video')}"


def ensure_plate_registered(pid: str, vid: str) -> str:
    root = os.path.join(video_root(pid, vid), "plate")
    os.makedirs(root, exist_ok=True)
    pps.register_plate_root(node_id_for(vid), root)
    return root


def _manifest_path(pid: str, vid: str) -> str:
    return os.path.join(video_root(pid, vid), "layers.json")


def load_manifest(pid: str, vid: str) -> Dict[str, Any]:
    p = _manifest_path(pid, vid)
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"video_id": vid, "layers": []}


def save_manifest(pid: str, vid: str, manifest: Dict[str, Any]) -> None:
    os.makedirs(video_root(pid, vid), exist_ok=True)
    with open(_manifest_path(pid, vid), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def _layer_dir(pid: str, vid: str, lid: str) -> str:
    return os.path.join(video_root(pid, vid), "layers", _safe(lid))


def _count_frames(d: str) -> int:
    return len([f for f in os.listdir(d) if f.startswith("frame_")]) if os.path.isdir(d) else 0


def get_state(pid: str, vid: str) -> Dict[str, Any]:
    ensure_plate_registered(pid, vid)
    info = pps.get_info(node_id_for(vid))
    man = load_manifest(pid, vid)
    for l in man["layers"]:
        d = _layer_dir(pid, vid, l["id"])
        l["frames"] = _count_frames(d)
        g = l.get("grow") or {}
        l["rev"] = f"g{g.get('px', 0)}f{g.get('feather', 0)}"
        keys = _paint_keys(d) if l.get("type") == "paint" else []
        if keys or os.path.isfile(os.path.join(d, "still.png")):
            l["still"] = not keys
            l["keys"] = keys
            l["frames"] = max(l["frames"], int(info.get("total_frames") or 0))
            stamps = [os.path.getmtime(os.path.join(d, fn)) for fn in os.listdir(d) if fn.endswith(".png")]
            l["updated"] = max(stamps) if stamps else 0
    return {"plate": info, "layers": man["layers"]}


def _paint_keys(d: str) -> List[int]:
    out = []
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.startswith("key_") and fn.endswith(".png"):
                try:
                    out.append(int(fn[4:-4]))
                except ValueError:
                    pass
    return sorted(out)


def _paint_key_for(d: str, idx: int) -> Optional[str]:
    """Drawing in effect at a frame: the last key at or before it (hold), else the first key, else still.png."""
    keys = _paint_keys(d)
    if keys:
        prev = [k for k in keys if k <= idx]
        k = prev[-1] if prev else keys[0]
        return os.path.join(d, f"key_{k:04d}.png")
    still = os.path.join(d, "still.png")
    return still if os.path.isfile(still) else None


def save_still_layer(pid: str, vid: str, layer_id: Optional[str], name: str, png_bytes: bytes,
                     frame: Optional[int] = None, autokey: bool = False) -> Dict[str, Any]:
    """Paint over the video.

    Without keys a layer is one image for every frame (still.png). With Auto-key on, a stroke on frame N
    writes key N; without it the stroke edits the key that is in effect on that frame. Morph (if on)
    re-bakes the in-between frames after every save.
    """
    ensure_plate_registered(pid, vid)
    man = load_manifest(pid, vid)
    layer = next((l for l in man["layers"] if l["id"] == layer_id), None) if layer_id else None
    if layer is None:
        layer = _add_layer(pid, vid, "paint", name or "Paint", extra={"morph": True})
    d = _layer_dir(pid, vid, layer["id"])
    keys = _paint_keys(d)
    if frame is not None and (autokey or keys):
        if autokey or not keys:
            target = int(frame)
            if not keys and os.path.isfile(os.path.join(d, "still.png")) and target != 0:
                # first key on another frame: keep the old drawing as key 0
                shutil.move(os.path.join(d, "still.png"), os.path.join(d, "key_0000.png"))
        else:
            prev = [k for k in keys if k <= int(frame)]
            target = prev[-1] if prev else keys[0]
        path = os.path.join(d, f"key_{target:04d}.png")
        still = os.path.join(d, "still.png")
        if os.path.isfile(still) and target == 0:
            os.remove(still)
    else:
        path = os.path.join(d, "still.png")
    with open(path, "wb") as f:
        f.write(png_bytes)
    if layer.get("morph") and path.endswith(".png") and os.path.basename(path).startswith("key_"):
        bake_paint_morph(pid, vid, layer["id"], only_key=int(os.path.basename(path)[4:8]))
    st = get_state(pid, vid)
    st["layer_id"] = layer["id"]
    return st


def save_layer_frame(pid: str, vid: str, layer_id: str, frame: int, png_bytes: bytes) -> Dict[str, Any]:
    """Hand edit of one frame of a mask/matte layer (brush / eraser on the selected layer)."""
    ensure_plate_registered(pid, vid)
    man = load_manifest(pid, vid)
    if not any(l["id"] == layer_id for l in man["layers"]):
        raise ProcessError("Layer not found")
    d = _layer_dir(pid, vid, layer_id)
    fn = f"frame_{int(frame):04d}.png"
    with open(os.path.join(d, fn), "wb") as f:
        f.write(png_bytes)
    if os.path.isdir(os.path.join(d, "_orig")):  # keep grow/shrink re-applicable
        with open(os.path.join(d, "_orig", fn), "wb") as f:
            f.write(png_bytes)
    return get_state(pid, vid)


def paint_key_op(pid: str, vid: str, layer_id: str, frame, op: str) -> Dict[str, Any]:
    """+Key copies the drawing in effect to this frame; Del removes the key(s) (frame may be a list)."""
    if isinstance(frame, (list, tuple)):
        if op == "del":
            d = _layer_dir(pid, vid, layer_id)
            for f in frame:
                fp = os.path.join(d, f"key_{int(f):04d}.png")
                if os.path.isfile(fp):
                    os.remove(fp)
            man = load_manifest(pid, vid)
            lay = next((l for l in man["layers"] if l["id"] == layer_id), None)
            if lay and lay.get("morph"):
                bake_paint_morph(pid, vid, layer_id)
            return get_state(pid, vid)
        st = None
        for f in frame:
            st = paint_key_op(pid, vid, layer_id, int(f), op)
        return st or get_state(pid, vid)
    ensure_plate_registered(pid, vid)
    d = _layer_dir(pid, vid, layer_id)
    frame = int(frame)
    target = os.path.join(d, f"key_{frame:04d}.png")
    if op == "add":
        src = _paint_key_for(d, frame)
        if not src:
            raise ProcessError("Nothing drawn on this layer yet")
        if os.path.abspath(src) != os.path.abspath(target):
            shutil.copy2(src, target)
        still = os.path.join(d, "still.png")
        if os.path.isfile(still) and os.path.abspath(src) == os.path.abspath(still):
            shutil.move(still, os.path.join(d, "key_0000.png")) if frame != 0 else os.remove(still)
    elif op == "del":
        if os.path.isfile(target):
            os.remove(target)
    man = load_manifest(pid, vid)
    lay = next((l for l in man["layers"] if l["id"] == layer_id), None)
    if lay and lay.get("morph"):
        bake_paint_morph(pid, vid, layer_id)
    return get_state(pid, vid)


def set_paint_morph(pid: str, vid: str, layer_id: str, on: bool) -> Dict[str, Any]:
    man = load_manifest(pid, vid)
    layer = next((l for l in man["layers"] if l["id"] == layer_id), None)
    if not layer:
        raise ProcessError("Layer not found")
    layer["morph"] = bool(on)
    save_manifest(pid, vid, man)
    if on:
        bake_paint_morph(pid, vid, layer_id)
    else:
        d = _layer_dir(pid, vid, layer_id)
        for fn in os.listdir(d):
            if fn.startswith("frame_"):
                os.remove(os.path.join(d, fn))
    return get_state(pid, vid)


def _sdf(alpha: np.ndarray) -> np.ndarray:
    inside = (alpha > 127).astype(np.uint8)
    din = cv2.distanceTransform(inside, cv2.DIST_L2, 3)
    dout = cv2.distanceTransform(1 - inside, cv2.DIST_L2, 3)
    return dout - din  # negative inside


def _fill_color_outward(rgba: np.ndarray) -> np.ndarray:
    """Spreads the stroke colors into the empty area so a morphing shape always has a color to show."""
    a = rgba[..., 3]
    if a.max() == 0:
        return rgba[..., :3].copy()
    mask = (a == 0).astype(np.uint8) * 255
    small = cv2.resize(rgba[..., :3], None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    msk = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    filled = cv2.inpaint(small, msk, 3, cv2.INPAINT_TELEA)
    big = cv2.resize(filled, (rgba.shape[1], rgba.shape[0]), interpolation=cv2.INTER_LINEAR)
    out = rgba[..., :3].copy()
    out[a == 0] = big[a == 0]
    return out


def _point_morph_prepare(A: np.ndarray, B: np.ndarray, max_pts: int = 6000):
    """Line drawings morph best as point clouds: every stroke pixel of one key is paired with a pixel of
    the other (optimal assignment on a subsample, nearest-neighbour for the rest). Returns None if the
    drawings are too big (filled areas) or scipy is missing; the SDF morph handles those."""
    try:
        from scipy.optimize import linear_sum_assignment
        from scipy.spatial import cKDTree
    except Exception:
        return None
    def pts_of(img):
        ys, xs = np.nonzero(img[..., 3] > 100)
        return np.stack([xs, ys], 1).astype(np.float32), img[ys, xs, :3].astype(np.float32)
    pa, ca = pts_of(A)
    pb, cb = pts_of(B)
    if not len(pa) or not len(pb) or len(pa) > max_pts or len(pb) > max_pts:
        return None
    swap = len(pa) < len(pb)
    big, small = (pb, pa) if swap else (pa, pb)
    rng = np.random.default_rng(1)
    k = min(len(small), 1500)
    si = rng.choice(len(small), k, replace=False) if len(small) > k else np.arange(len(small))
    bi = rng.choice(len(big), k, replace=False) if len(big) > k else np.arange(len(big))
    cost = ((big[bi][:, None, :] - small[si][None, :, :]) ** 2).sum(-1)
    r, c = linear_sum_assignment(cost)
    disp = small[si][c] - big[bi][r]
    _, nn = cKDTree(big[bi][r]).query(big)
    _, j = cKDTree(small).query(big + disp[nn])
    ia, ib = (j, np.arange(len(big))) if swap else (np.arange(len(big)), j)
    dt = cv2.distanceTransform((A[..., 3] > 100).astype(np.uint8), cv2.DIST_L2, 3)
    thick = int(max(1, min(6, round(float(dt.max()) * 0.5))))
    return pa[ia], ca[ia], pb[ib], cb[ib], thick


def _point_morph_frame(prep, t: float, H: int, W: int) -> np.ndarray:
    pa, ca, pb, cb, thick = prep
    P = pa * (1 - t) + pb * t
    C = ca * (1 - t) + cb * t
    out = np.zeros((H, W, 4), np.uint8)
    x = np.clip(np.round(P[:, 0]).astype(int), 0, W - 1)
    y = np.clip(np.round(P[:, 1]).astype(int), 0, H - 1)
    out[y, x, :3] = C.astype(np.uint8)
    out[y, x, 3] = 255
    # The moved points already cover the stroke at its real width; only the gaps between them need filling.
    # Closing (dilate, then erode by the same kernel) fills the gaps without making the line thicker,
    # which a plain dilation did (lines grew by ~2x the kernel on every in-between).
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * thick + 1, 2 * thick + 1))
    out[..., :3] = cv2.dilate(out[..., :3], k)
    a = cv2.morphologyEx(out[..., 3], cv2.MORPH_CLOSE, k)
    out[..., 3] = cv2.GaussianBlur(a, (0, 0), 0.6)
    return out


def bake_paint_morph(pid: str, vid: str, layer_id: str, only_key: Optional[int] = None) -> int:
    """In-between frames for a keyed paint layer: shapes morph through signed-distance blending,
    colors cross-fade. Before the first / after the last key the key is held."""
    ensure_plate_registered(pid, vid)
    info = pps.get_info(node_id_for(vid))
    total = int(info.get("total_frames") or 0)
    d = _layer_dir(pid, vid, layer_id)
    keys = _paint_keys(d)
    segs = list(zip(keys, keys[1:]))
    if only_key is not None:
        segs = [(a, b) for a, b in segs if a == only_key or b == only_key]
        for a, b in segs:
            for f in range(a + 1, b):
                fp = os.path.join(d, f"frame_{f:04d}.png")
                if os.path.isfile(fp):
                    os.remove(fp)
    else:
        for fn in os.listdir(d):
            if fn.startswith("frame_"):
                os.remove(os.path.join(d, fn))
    if len(keys) < 2 or total <= 0:
        return 0
    imgs = {}
    for k in keys:
        im = cv2.imread(os.path.join(d, f"key_{k:04d}.png"), cv2.IMREAD_UNCHANGED)
        if im is None:
            continue
        if im.ndim == 2 or im.shape[2] == 3:
            im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGRA if im.ndim == 2 else cv2.COLOR_BGR2BGRA)
        imgs[k] = im
    n = 0
    for a, b in segs:
        if a not in imgs or b not in imgs:
            continue
        A, B = imgs[a], imgs[b]
        if B.shape != A.shape:
            B = cv2.resize(B, (A.shape[1], A.shape[0]))
        prep = _point_morph_prepare(A, B)
        if prep is not None:  # line drawings: point-to-point morph
            H0, W0 = A.shape[:2]
            for f in range(a + 1, b):
                cv2.imwrite(os.path.join(d, f"frame_{f:04d}.png"), _point_morph_frame(prep, (f - a) / float(b - a), H0, W0))
                n += 1
            continue
        ca, cb = _fill_color_outward(A).astype(np.float32), _fill_color_outward(B).astype(np.float32)
        # move the drawings along their centroids, then blend the shapes (otherwise far-apart strokes vanish mid-way)
        def _centroid(alpha):
            m = cv2.moments((alpha > 127).astype(np.uint8))
            return (m["m10"] / m["m00"], m["m01"] / m["m00"]) if m["m00"] else None
        pa, pb = _centroid(A[..., 3]), _centroid(B[..., 3])
        dx, dy = ((pb[0] - pa[0]), (pb[1] - pa[1])) if (pa and pb) else (0.0, 0.0)
        H, W = A.shape[:2]
        def _shift(img, sx, sy):
            return cv2.warpAffine(img, np.float32([[1, 0, sx], [0, 1, sy]]), (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        for f in range(a + 1, b):
            t = (f - a) / float(b - a)
            As, Bs = _shift(A[..., 3], dx * t, dy * t), _shift(B[..., 3], -dx * (1 - t), -dy * (1 - t))
            sa, sb = _sdf(As), _sdf(Bs)
            ca_t = _shift(ca, dx * t, dy * t) if (dx or dy) else ca
            cb_t = _shift(cb, -dx * (1 - t), -dy * (1 - t)) if (dx or dy) else cb
            sd = sa * (1 - t) + sb * t
            alpha = np.clip(0.5 - sd, 0, 1)  # ~1px soft edge
            out = np.zeros_like(A)
            out[..., :3] = np.clip(ca_t * (1 - t) + cb_t * t, 0, 255).astype(np.uint8)
            out[..., 3] = (alpha * 255).astype(np.uint8)
            cv2.imwrite(os.path.join(d, f"frame_{f:04d}.png"), out)
            n += 1
    return n


def update_layers_meta(pid: str, vid: str, layers: List[Dict[str, Any]], deleted: Optional[List[str]] = None) -> Dict[str, Any]:
    """Saves order / visibility / opacity / names coming from the UI.

    Only layers listed in `deleted` are removed. Layers the client does not know about yet (made by a
    background job after the UI loaded its state) are kept on top, so a stale UI can never erase them.
    """
    man = load_manifest(pid, vid)
    known = {l["id"]: l for l in man["layers"]}
    gone = {_safe(x) for x in (deleted or []) if x}
    out, seen = [], set()
    for l in layers:
        lid = _safe(l.get("id"))
        if lid not in known or lid in gone or lid in seen:
            continue
        seen.add(lid)
        base = known[lid]
        base.update({
            "name": str(l.get("name") or base.get("name"))[:80],
            "visible": bool(l.get("visible", True)),
            "opacity": max(0.0, min(1.0, float(l.get("opacity", base.get("opacity", 1.0))))),
            "inverted": bool(l.get("inverted", base.get("inverted", False))),
            "comment": str(l.get("comment", base.get("comment", "")) or "")[:4000],
            "bw": bool(l.get("bw", base.get("bw", False))),
        })
        out.append(base)
    out += [l for lid, l in known.items() if lid not in seen and lid not in gone]
    for lid in gone & set(known):
        shutil.rmtree(_layer_dir(pid, vid, lid), ignore_errors=True)
    man["layers"] = out
    save_manifest(pid, vid, man)
    return get_state(pid, vid)


def _add_layer(pid: str, vid: str, ltype: str, name: str, color: str = "", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    man = load_manifest(pid, vid)
    lid = f"vl_{ltype}_{uuid.uuid4().hex[:8]}"
    layer = {"id": lid, "name": name, "type": ltype, "visible": True,
             "opacity": 0.6 if ltype == "mask" else 1.0, "color": color, "created_at": time.time()}
    if extra:
        layer.update(extra)
    man["layers"].append(layer)
    save_manifest(pid, vid, man)
    os.makedirs(_layer_dir(pid, vid, lid), exist_ok=True)
    return layer


def frame_path(pid: str, vid: str, lid: str, idx: int) -> Optional[str]:
    if lid in ("plate_raw", "plate_undist"):
        d = pps.get_dirs(node_id_for(vid))
        p = os.path.join(d["raw" if lid == "plate_raw" else "undist"], f"frame_{idx:04d}.jpg")
    else:
        key = os.path.join(_layer_dir(pid, vid, lid), f"key_{idx:04d}.png")
        if os.path.isfile(key):  # a key always wins over a baked in-between
            return key
        p = os.path.join(_layer_dir(pid, vid, lid), f"frame_{idx:04d}.png")
        if not os.path.isfile(p):
            held = _paint_key_for(_layer_dir(pid, vid, lid), idx)
            if held:
                return held
    return p if os.path.isfile(p) else None


# ---------------------------------------------------------------------------
# Media path resolution (UI URLs -> files on disk)
# ---------------------------------------------------------------------------
def resolve_media_path(url: str) -> str:
    if not url:
        raise ProcessError("Video has no URL")
    if url.startswith("/vfx/project_file/"):
        parts = url.split("?", 1)[0].split("/")
        pid, sub = parts[3], "/".join(urllib.parse.unquote(x) for x in parts[4:])
        base = get_project_dir(pid)
        full = os.path.normpath(os.path.join(base, sub))
        if os.path.commonpath([base, full]) == base and os.path.isfile(full):
            return full
    if url.startswith("/view?") or url.startswith("/api/view?"):
        import folder_paths
        q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        base = {"input": folder_paths.get_input_directory(), "temp": folder_paths.get_temp_directory()}.get(
            q.get("type", "output"), folder_paths.get_output_directory())
        full = os.path.normpath(os.path.join(base, q.get("subfolder", ""), q.get("filename", "")))
        if os.path.isfile(full):
            return full
    if os.path.isabs(url) and os.path.isfile(url):
        return url
    raise ProcessError(f"Cannot find the video file for {url[:120]} (blob/preview URLs must be uploaded to the project first)")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def _job_update(job_id: str, **kw):
    with _JOBS_LOCK:
        _JOBS[job_id].update(kw)


def get_job(job_id: str) -> Dict[str, Any]:
    with _JOBS_LOCK:
        return dict(_JOBS.get(job_id) or {"status": "not_found"})


def cancel_job(job_id: str) -> Dict[str, Any]:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id]["cancel"] = True
    try:
        from .process_runner import cancel_run
    except ImportError:  # pragma: no cover
        from process_runner import cancel_run
    try:
        cancel_run(run_tag=job_id)
    except Exception:
        pass
    return get_job(job_id)


def _cancelled(job_id: str) -> bool:
    with _JOBS_LOCK:
        return bool(_JOBS.get(job_id, {}).get("cancel"))


def start_job(kind: str, pid: str, vid: str, params: Dict[str, Any]) -> Dict[str, Any]:
    fn = {
        "prepare": _job_prepare, "depth": _job_preprocess, "canny": _job_preprocess, "pose": _job_preprocess,
        "detect_all": _job_detect_all, "track_object": _job_track_object,
        "corridor_key": _job_corridor_key, "undistort": _job_undistort, "track_camera": _job_track_camera,
        "ltx_alpha": _job_ltx_ic_lora, "ltx_ic_lora": _job_ltx_ic_lora,
        "fill": _job_fill, "render": _job_render, "layer_to_video": _job_layer_to_video,
        "mask_grow": _job_mask_grow, "first_frame": _job_first_frame, "text_mask": _job_text_mask, "composite": _job_composite, "relight_frame": _job_relight_frame,
    }.get(kind)
    if not fn:
        raise ProcessError(f"Unknown 2D job '{kind}'")
    job_id = f"vjob_{uuid.uuid4().hex[:10]}"
    with _JOBS_LOCK:
        _JOBS[job_id] = {"id": job_id, "kind": kind, "status": "running", "progress": 0, "message": "Starting…", "started": time.time()}

    def runner():
        try:
            ensure_plate_registered(pid, vid)
            res = fn(job_id, kind, pid, vid, params or {})
            _job_update(job_id, status="done", progress=100, message="Done", result=res)
        except Exception as e:
            stopped = _cancelled(job_id) or str(e) == "Stopped"
            _job_update(job_id, status="cancelled" if stopped else "error", message="Stopped" if stopped else str(e),
                        trace=None if stopped else traceback.format_exc()[-2000:])
        finally:
            if kind in ("ltx_ic_lora", "ltx_alpha"):
                _restore_reserved_vram()

    threading.Thread(target=runner, daemon=True).start()
    return get_job(job_id)


# --------------------------------------------------------------------------------------------- #
# OpenPose (COCO-18) layer from 3D characters: the studio projects mannequin joints through the  #
# shot camera and sends normalized 2D keypoints per frame; here they are drawn like DWPose.     #
# --------------------------------------------------------------------------------------------- #
OPENPOSE_LIMBS = [(1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10), (1, 11), (11, 12), (12, 13),
                  (1, 0), (0, 14), (14, 16), (0, 15), (15, 17)]
OPENPOSE_COLORS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0), (0, 255, 0),
                   (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255),
                   (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85)]


def draw_openpose(people, w: int, h: int) -> np.ndarray:
    """people: list of 18 [x, y] in 0..1 (or None). Returns BGRA, lines on transparent (like the pose layers)."""
    import math
    canvas = np.zeros((h, w, 3), np.uint8)
    stick = max(2, int(round(4 * min(w, h) / 512)))
    for pts in people or []:
        P = [(float(p[0]) * w, float(p[1]) * h) if (p is not None and len(p) >= 2) else None for p in (pts or [])[:18]]
        P += [None] * (18 - len(P))
        for i, (a, b) in enumerate(OPENPOSE_LIMBS):
            if P[a] is None or P[b] is None:
                continue
            (x1, y1), (x2, y2) = P[a], P[b]
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            length = math.hypot(x1 - x2, y1 - y2) / 2
            angle = math.degrees(math.atan2(y1 - y2, x1 - x2))
            poly = cv2.ellipse2Poly((int(mx), int(my)), (int(length), stick), int(angle), 0, 360, 1)
            layer = canvas.copy()
            r, g, b = OPENPOSE_COLORS[i]
            cv2.fillConvexPoly(layer, poly, (b, g, r))
            canvas = cv2.addWeighted(canvas, 0.4, layer, 0.6, 0)
        for i, pt in enumerate(P):
            if pt is None:
                continue
            r, g, b = OPENPOSE_COLORS[i]
            cv2.circle(canvas, (int(pt[0]), int(pt[1])), stick, (b, g, r), thickness=-1)
    m = canvas.max(axis=2)
    a = np.where(m < 12, 0, np.minimum(255, m.astype(np.int32) * 2)).astype(np.uint8)
    return np.dstack([canvas, a])


def pose_layer_from_3d(pid: str, vid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """data: {frames: [{"frame": i, "people": [[[x,y]|null]*18, ...]}], "name": str, "layer_id": optional}"""
    ensure_plate_registered(pid, vid)
    info = pps.get_info(node_id_for(vid)) or {}
    if not info.get("total_frames"):
        info = {}   # no extracted plate: the defaults (1920x1080) would be wrong
    # plate size if the frames are extracted, else the size of the tracked camera the studio sent
    w = int(info.get("width") or data.get("width") or 1920)
    h = int(info.get("height") or data.get("height") or 1080)
    frames = data.get("frames") or []
    lid = _safe(str(data.get("layer_id") or ""))
    man = load_manifest(pid, vid)
    layer = next((l for l in man["layers"] if l.get("id") == lid), None) if lid else None
    if layer is None:
        layer = _add_layer(pid, vid, "pose", str(data.get("name") or "Pose from 3D"), extra={"transparentBg": True, "source": "3d"})
    out_dir = _layer_dir(pid, vid, layer["id"])
    kps = {}
    for fr in frames:
        idx = int(fr.get("frame", 0))
        people = fr.get("people") or []
        cv2.imwrite(os.path.join(out_dir, f"frame_{idx:04d}.png"), draw_openpose(people, w, h))
        kps[idx] = people
    # keep the keypoints too: editable later, and usable as a control signal without re-detection
    kp_path = os.path.join(out_dir, "keypoints_3d.json")
    old = {}
    if os.path.isfile(kp_path):
        try:
            old = {int(k): v for k, v in json.load(open(kp_path, encoding="utf-8")).items()}
        except Exception:
            old = {}
    old.update(kps)
    with open(kp_path, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in sorted(old.items())}, f)
    return {"layer": layer, "frames": len(frames), "size": [w, h]}


def _layer_frame_bgr(pid: str, vid: str, lid: str, idx: int) -> Optional[np.ndarray]:
    """One frame of a layer as BGR; transparent layers (pose, canny) are put over black."""
    fp = frame_path(pid, vid, _safe(lid), idx)
    img = cv2.imread(fp, cv2.IMREAD_UNCHANGED) if fp else None
    if img is None:
        return None
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        a = img[..., 3:4].astype(np.float32) / 255.0
        return (img[..., :3].astype(np.float32) * a).astype(np.uint8)
    return img


def _png_data_url(img: np.ndarray) -> str:
    import base64
    ok, buf = cv2.imencode(".png", img)
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


FIRST_FRAME_PROMPT = ("Turn the grey 3D mannequin into {desc}. Keep exactly the same pose, limb positions, body "
                      "orientation, framing and camera angle. Plain white background.")
DESCRIBE_PROMPT = ("Describe the character in this image for an image generator, in one sentence of comma-separated "
                   "visual tags: gender and age look, hair (colour, length, style), eye colour, every clothing item with "
                   "colours, shoes, accessories, art style. No pose, no background, no extra words.")


def _ollama_describe(img_bgr: np.ndarray, model: str = "") -> str:
    """Short visual description of a reference image via a local Ollama vision model ('' when unavailable)."""
    import base64, json as _json, urllib.request
    base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if not base.startswith("http"):
        base = "http://" + base
    try:
        if not model:
            model = os.environ.get("KESHIKI_VISION_MODEL", "")
        if not model:
            with urllib.request.urlopen(base + "/api/tags", timeout=3) as r:
                models = [m["name"] for m in _json.load(r).get("models", [])]
            model = next((m for m in models if any(k in m.lower() for k in ("vl", "qwen3.5", "gemma3", "gemma4", "llava"))), "")
        if not model:
            return ""
        h, w = img_bgr.shape[:2]
        k = 768 / max(h, w)
        small = cv2.resize(img_bgr, (int(w * k), int(h * k))) if k < 1 else img_bgr
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 90])
        body = _json.dumps({"model": model, "stream": False, "think": False, "options": {"temperature": 0.2},
                            "messages": [{"role": "user", "content": DESCRIBE_PROMPT,
                                          "images": [base64.b64encode(buf.tobytes()).decode("ascii")]}]}).encode()
        req = urllib.request.Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            text = _json.load(r).get("message", {}).get("content", "")
        return " ".join(text.replace("\n", " ").split()).strip().rstrip(".")[:600]
    except Exception as e:  # noqa: BLE001
        print(f"[KESHIKI] first frame: no Ollama description ({e})")
        return ""


QWEN_FIRST_FRAME = ("Place the character from image 1 into the scene from image 2{pose}. Keep the character's face, "
                    "hair, outfit, colours and art style from image 1 exactly. Keep the camera angle, framing and the "
                    "position of every object from image 2, but turn the plain coloured 3D shapes into real objects{scene}. "
                    "One single frame, no skeleton lines, no text.")


def _save_first_frame(pid, vid, data_url):
    import base64
    raw = base64.b64decode(data_url.split(",", 1)[1])
    folder = os.path.join(get_project_dir(pid), "storyboard")
    os.makedirs(folder, exist_ok=True)
    name = f"first_frame_{_safe(vid)[:40]}_{time.strftime('%Y%m%d_%H%M%S')}.png"
    with open(os.path.join(folder, name), "wb") as f:
        f.write(raw)
    return {"url": f"/vfx/project_file/{pid}/storyboard/{name}", "title": name}


def _first_frame_qwen(job_id, pid, vid, p, scene_bgr, idx):
    """Qwen-Image-Edit-2511: image 1 = character reference, image 2 = 3D layout render (set dressing, camera),
    image 3 = pose skeleton of the same frame. Output in the frame of image 2."""
    try:
        from .image_generate_service import run_qwen_edit
    except ImportError:  # pragma: no cover
        from image_generate_service import run_qwen_edit
    ref = str(p.get("ref_image") or "")
    ch = cv2.imread(resolve_media_path(ref), cv2.IMREAD_COLOR) if ref else None
    if ch is None:
        raise ProcessError("Pick the character reference image")
    pose_txt = ""
    scene = scene_bgr.copy()
    if p.get("pose_layer"):
        pz = _layer_frame_bgr(pid, vid, p["pose_layer"], idx)
        if pz is not None:
            # the skeleton drawn over the layout: one image carries both the set and where/how she stands
            pz = cv2.resize(pz, (scene.shape[1], scene.shape[0]))
            m = pz.max(axis=2) > 30
            scene[m] = pz[m]
            pose_txt = (", replacing the coloured stick-figure skeleton: she stands exactly where the skeleton is, "
                        "in exactly its pose (same limb positions and size)")
    imgs = [_png_data_url(ch), _png_data_url(scene)]
    user = str(p.get("prompt") or "").strip()
    instruction = QWEN_FIRST_FRAME.format(pose=pose_txt, scene=f": {user}" if user else "")
    _job_update(job_id, message="Qwen Image Edit: first frame…", progress=15)
    res = run_qwen_edit(imgs, instruction, seed=p.get("seed") or None, size_from=1,
                        megapixels=float(p.get("megapixels", 0.6)), run_tag=job_id)
    return {"image": _save_first_frame(pid, vid, res["image"]), "seed": res["seed"], "model": "qwen",
            "seconds": res["seconds"], "prompt_used": instruction}


def _first_frame_klein_refs(job_id, pid, vid, p, scene_bgr, idx):
    """The recipe that keeps the character's look (tested on the car shot): image 1 = the scene (layout render,
    the base Klein keeps), image 2 = the skeleton alone (role pose), image 3 = the character (role reference),
    and an instruction to ADD the person from image 3 in the pose of image 2."""
    try:
        from .image_generate_service import run_image_generate
    except ImportError:  # pragma: no cover
        from image_generate_service import run_image_generate
    pz = _layer_frame_bgr(pid, vid, p["pose_layer"], idx)
    if pz is None:
        raise ProcessError(f"The pose layer has no frame {idx}")
    ch = cv2.imread(resolve_media_path(str(p["ref_image"])), cv2.IMREAD_COLOR)
    if ch is None:
        raise ProcessError("Cannot read the reference image")
    scene_txt = str(p.get("scene") or "").strip()
    extra = str(p.get("prompt") or "").strip()
    instruction = ("Add the person from the third image, in exactly the pose of the skeleton in the second image and at "
                   "the same place and size in the frame. Keep her face, hair, outfit, colours and drawing style from the "
                   "third image. " + (f"{extra} " if extra else "") +
                   (f"Turn the plain coloured 3D shapes into {scene_txt}, keeping their positions and the camera angle."
                    if scene_txt else "Keep the background unchanged."))
    _job_update(job_id, message="FLUX.3 Klein: first frame (scene + pose + character)…", progress=15)
    res = run_image_generate({
        "mode": "edit", "source": _png_data_url(scene_bgr), "megapixels": float(p.get("megapixels", 1.0)),
        "references": [{"data": _png_data_url(pz), "role": "pose", "name": "Skeleton"},
                       {"data": _png_data_url(ch), "role": "reference", "name": "Character"}],
        "prompt": instruction, "seed": p.get("seed") or None, "run_tag": job_id,
    })
    return {"image": _save_first_frame(pid, vid, res["image"]), "seed": res.get("seed"), "model": "klein_refs",
            "prompt_used": res.get("prompt_used")}


LIGHT_MIGRATION_PROMPT = "参考色调，移除图1原有的光照并参考图2的光照和色调对图1重新照明"


def _job_relight_frame(job_id, kind, pid, vid, p):
    """Relight one frame of a layer (usually a composite) to match a light reference (usually the new background)
    with Qwen-Image-Edit + the Light-Migration LoRA. The result is a still for the first-frame guide of an LTX
    refine pass, which carries the new light through the clip."""
    try:
        from .image_generate_service import run_qwen_edit
    except ImportError:  # pragma: no cover
        from image_generate_service import run_qwen_edit
    idx = int(p.get("frame", 0) or 0)
    src = _layer_frame_bgr(pid, vid, str(p.get("layer") or ""), idx)
    ref = _layer_frame_bgr(pid, vid, str(p.get("light_layer") or ""), idx)
    if src is None or ref is None:
        raise ProcessError("Choose the layer to relight and the light reference layer")
    lora = str(p.get("lora") or "Qwen\\Qwen-Image-Edit-2509-Light-Migration.safetensors")
    _job_update(job_id, message="Qwen Edit + Light-Migration: relighting the frame…", progress=15)
    res = run_qwen_edit([_png_data_url(src), _png_data_url(ref)], str(p.get("prompt") or LIGHT_MIGRATION_PROMPT),
                        seed=p.get("seed") or None, size_from=0, megapixels=float(p.get("megapixels", 1.0)),
                        run_tag=job_id, loras=[(lora, float(p.get("lora_strength", 1.0)))])
    return {"image": _save_first_frame(pid, vid, res["image"]), "seed": res["seed"], "seconds": res["seconds"]}


def _job_first_frame(job_id, kind, pid, vid, p):
    """Still for the video's first frame: FLUX.3 Klein edits the guide frame (3D render of the start pose) into the
    character. Klein copies a full reference image instead of reposing it, so the character goes in as text: the
    user's description, or one a local Ollama vision model writes from the reference image (cached next to it).
    The user approves the still before the video runs."""
    try:
        from .image_generate_service import run_image_generate
    except ImportError:  # pragma: no cover
        from image_generate_service import run_image_generate
    guide = p.get("guide_layer")
    if not guide:
        raise ProcessError("Choose the layer with the start pose (3D render or pose)")
    idx = int(p.get("frame", 0) or 0)
    g = _layer_frame_bgr(pid, vid, guide, idx)
    if g is None:
        raise ProcessError(f"The guide layer has no frame {idx}")
    if str(p.get("model") or "") == "qwen":
        return _first_frame_qwen(job_id, pid, vid, p, g, idx)
    if p.get("pose_layer") and p.get("ref_image") and not p.get("text_only"):
        return _first_frame_klein_refs(job_id, pid, vid, p, g, idx)
    desc = str(p.get("prompt") or "").strip()
    ref = str(p.get("ref_image") or "")
    if not desc and ref:
        ref_path = resolve_media_path(ref)
        cache = ref_path + ".desc.txt"
        if os.path.isfile(cache) and not p.get("redescribe"):
            desc = open(cache, encoding="utf-8").read().strip()
        else:
            ch = cv2.imread(ref_path, cv2.IMREAD_COLOR)
            if ch is None:
                raise ProcessError("Cannot read the reference image")
            _job_update(job_id, message="Describing the character (Ollama)…", progress=5)
            desc = _ollama_describe(ch, str(p.get("vision_model") or ""))
            if desc:
                try:
                    with open(cache, "w", encoding="utf-8") as f:
                        f.write(desc)
                except OSError:
                    pass
    if not desc:
        raise ProcessError("Describe the character in the prompt (no local vision model found to do it automatically)")
    gl = next((l for l in load_manifest(pid, vid).get("layers", []) if l.get("id") == guide), {})
    instruction = FIRST_FRAME_PROMPT.format(desc=desc)
    scene_txt = str(p.get("scene") or "").strip()
    if p.get("pose_layer"):
        # set dressing render + the skeleton drawn over it: draw the character where the skeleton stands
        pz = _layer_frame_bgr(pid, vid, p["pose_layer"], idx)
        if pz is not None:
            g = g.copy()
            pz = cv2.resize(pz, (g.shape[1], g.shape[0]))
            m = pz.max(axis=2) > 30
            g[m] = pz[m]
            instruction = (f"Replace the coloured stick-figure skeleton with {desc}: she stands exactly where the skeleton "
                           "is, in exactly its pose, limb positions and size. Turn the plain coloured 3D shapes into real "
                           f"objects{': ' + scene_txt if scene_txt else ''}, keeping their positions, sizes and the camera "
                           "angle. One coherent illustration in the same art style, no skeleton lines.")
    elif gl.get("type") == "pose":
        instruction = (f"Draw {desc}, full body, in exactly the pose of this OpenPose skeleton (same limb positions, "
                       "framing and camera angle). Plain white background, no skeleton lines.")
    _job_update(job_id, message="FLUX.3 Klein: first frame…", progress=15)
    res = run_image_generate({
        "mode": "edit", "source": _png_data_url(g), "megapixels": float(p.get("megapixels", 1.0)),
        "prompt": instruction, "seed": p.get("seed") or None, "run_tag": job_id,
    })
    import base64
    raw = base64.b64decode(res["image"].split(",", 1)[1])
    folder = os.path.join(get_project_dir(pid), "storyboard")
    os.makedirs(folder, exist_ok=True)
    name = f"first_frame_{_safe(vid)[:40]}_{time.strftime('%Y%m%d_%H%M%S')}.png"
    with open(os.path.join(folder, name), "wb") as f:
        f.write(raw)
    return {"image": {"url": f"/vfx/project_file/{pid}/storyboard/{name}", "title": name}, "seed": res.get("seed"),
            "description": desc}


def save_layer_frames(pid: str, vid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Frames rendered elsewhere (the 3D studio) -> a layer. data: {layer_id?, name, type, frames: [{frame, data}]}"""
    import base64
    lid = _safe(str(data.get("layer_id") or ""))
    man = load_manifest(pid, vid)
    layer = next((l for l in man["layers"] if l.get("id") == lid), None) if lid else None
    if layer is None:
        ltype = str(data.get("type") or "rgb")
        layer = _add_layer(pid, vid, ltype, str(data.get("name") or "3D render"), extra={"source": "3d"})
    out_dir = _layer_dir(pid, vid, layer["id"])
    n = 0
    for fr in data.get("frames") or []:
        raw = str(fr.get("data") or "")
        if "," in raw:
            raw = raw.split(",", 1)[1]
        img = cv2.imdecode(np.frombuffer(base64.b64decode(raw), np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        cv2.imwrite(os.path.join(out_dir, f"frame_{int(fr.get('frame', 0)):04d}.png"), img)
        n += 1
    return {"layer": layer, "frames": n}


def _require_plate(vid: str) -> Dict[str, Any]:
    info = pps.get_info(node_id_for(vid))
    if not info.get("total_frames"):
        raise ProcessError("Prepare the plate first (extract frames)")
    return info


def _job_prepare(job_id, kind, pid, vid, p):
    path = resolve_media_path(p.get("url", ""))
    info = pps.get_info(node_id_for(vid))
    if info.get("total_frames") and info.get("input_path") == path and not p.get("force"):
        return info
    _job_update(job_id, message="Extracting frames…", progress=10)
    return pps.load_input_source(path, node_id=node_id_for(vid))


def _job_undistort(job_id, kind, pid, vid, p):
    _require_plate(vid)
    if p.get("auto"):
        _job_update(job_id, message="Estimating lens distortion…", progress=20)
        k = pps.auto_calibrate_distortion(node_id=node_id_for(vid))
        k1, k2 = float(k.get("k1", 0)), float(k.get("k2", 0))
    else:
        k1, k2 = float(p.get("k1", 0)), float(p.get("k2", 0))
    _job_update(job_id, message=f"Undistorting (k1={k1:.3f}, k2={k2:.3f})…", progress=50)
    return pps.apply_undistortion(k1, k2, node_id=node_id_for(vid))


def _source_frames_dir(vid: str) -> str:
    d = pps.get_dirs(node_id_for(vid))
    return d["undist"] if _count_frames(d["undist"]) else d["raw"]


def _job_preprocess(job_id, kind, pid, vid, p):
    import folder_paths
    info = _require_plate(vid)
    total = int(info["total_frames"])
    src_dir = _source_frames_dir(vid)
    # depth engines: Depth Anything V2 (default) or Depth Anything 3 Mono-Large (ComfyUI core nodes)
    engine = str(p.get("engine") or "").lower()
    if kind == "depth" and engine in ("depth_pro", "depthpro", "pro"):
        return _depth_pro_video(job_id, pid, vid, p, total, src_dir)
    template = f"{kind}_da3_video" if (kind == "depth" and engine == "da3") else f"{kind}_video"
    title = "Depth (DA3)" if template == "depth_da3_video" else kind.capitalize()
    layer = _add_layer(pid, vid, kind, title, extra={"transparentBg": kind in ("canny", "pose"), "engine": engine or "v2"})
    out_dir = _layer_dir(pid, vid, layer["id"])
    chunk = int(p.get("chunk", 48))
    written = 0
    for start in range(0, total, chunk):
        if _cancelled(job_id):
            raise ProcessError("Stopped")
        _job_update(job_id, message=f"{kind}: frames {start}–{min(total, start + chunk) - 1} of {total}", progress=int(100 * start / total))
        prompt = load_template("deconstruct", template)
        q = run_process("deconstruct", prompt=prompt, client_id="keshiki_2d", run_tag=job_id, params={
            "Frames.directory": src_dir, "Frames.image_load_cap": chunk, "Frames.skip_first_images": start,
        })
        imgs = _wait_images(q["prompt_id"], job_id)
        for i, item in enumerate(imgs):
            src = os.path.join(folder_paths.get_output_directory(), item.get("subfolder", ""), item["filename"])
            img = cv2.imread(src, cv2.IMREAD_COLOR)
            if img is None:
                continue
            if kind in ("canny", "pose"):  # lines on black -> lines on transparent
                m = img.max(axis=2)
                a = np.where(m < 12, 0, np.minimum(255, m.astype(np.int32) * 2)).astype(np.uint8)
                img = np.dstack([img, a])
            cv2.imwrite(os.path.join(out_dir, f"frame_{start + i:04d}.png"), img)
            written += 1
    return {"layer": layer, "frames": written}


def _depth_pro_video(job_id, pid, vid, p, total, src_dir):
    """Apple Depth Pro (sharper, metric) via tools/depth_pro_runner.py in this Python env.
    Needs the ml-depth-pro repo + checkpoint: keshiki_local.json "depth_pro_root"."""
    import subprocess
    import sys
    try:
        from .local_config import local_setting
    except ImportError:  # pragma: no cover
        from local_config import local_setting
    root = local_setting("depth_pro_root")
    if not root or not os.path.isfile(os.path.join(root, "src", "depth_pro", "depth_pro.py")):
        raise ProcessError("Depth Pro is not set up: put the ml-depth-pro repo path into keshiki_local.json as depth_pro_root")
    try:  # free VRAM held by ComfyUI models (LTX etc.)
        import comfy.model_management as mm
        mm.unload_all_models()
        mm.soft_empty_cache()
    except Exception:
        pass
    f0 = max(0, int(p.get("start", 0) or 0))
    f1 = min(total - 1, int(p.get("end", total - 1)))
    layer = _add_layer(pid, vid, "depth", p.get("name") or "Depth (Depth Pro)", extra={"engine": "depth_pro"})
    out_dir = _layer_dir(pid, vid, layer["id"])
    runner = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "depth_pro_runner.py")
    cmd = [sys.executable, runner, "--root", root, "--frames", src_dir, "--out", out_dir,
           "--start", str(f0), "--end", str(f1), "--ema", str(float(p.get("ema", 0.6))),
           "--mapping", str(p.get("mapping") or "inverse")]
    ck = local_setting("depth_pro_checkpoint")
    if ck:
        cmd += ["--checkpoint", ck]
    proc = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    tail = []
    for line in proc.stdout:
        line = line.strip()
        if _cancelled(job_id):
            proc.kill()
            raise ProcessError("Stopped")
        if line.startswith("STATUS_UPDATE:"):
            try:
                m = json.loads(line[len("STATUS_UPDATE:"):])
                _job_update(job_id, message=str(m.get("status", ""))[:120], progress=min(99, int(m.get("progress", 0))))
            except ValueError:
                pass
        elif line:
            tail = (tail + [line])[-12:]
    if proc.wait() != 0:
        raise ProcessError("Depth Pro failed: " + " | ".join(tail[-4:]))
    return {"layer": layer, "frames": _count_frames(out_dir)}


def _job_text_mask(job_id, kind, pid, vid, p):
    """SAM 3.1 (ComfyUI core): masks of everything matching a text prompt ("person", "red car", "dog"),
    detected and tracked through the video in chunks -> one mask layer. SAM2 / YOLO stay available."""
    import folder_paths
    text = str(p.get("text") or "").strip()
    if not text:
        raise ProcessError("Write what to mask (e.g. 'person', 'red car')")
    info = _require_plate(vid)
    total = int(info["total_frames"])
    f0 = max(0, int(p.get("start", 0) or 0))
    f1 = min(total - 1, int(p.get("end", total - 1)))
    src_dir = _source_frames_dir(vid)
    tmp_dir = None
    if p.get("source_layer"):   # mask a generated layer instead of the footage (frames f0..f1 -> 0..n)
        tmp_dir = _layer_dir(pid, vid, "_tmp_" + job_id)
        os.makedirs(tmp_dir, exist_ok=True)
        w, h = int(info.get("width", 1920)), int(info.get("height", 1080))
        for i in range(f0, f1 + 1):
            fp = frame_path(pid, vid, _safe(str(p["source_layer"])), i)
            img = cv2.imread(fp, cv2.IMREAD_COLOR) if fp and os.path.isfile(fp) else None
            if img is None:
                img = np.zeros((h, w, 3), np.uint8)
            cv2.imwrite(os.path.join(tmp_dir, f"frame_{i - f0:04d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        src_dir = tmp_dir
    layer = _add_layer(pid, vid, "mask", p.get("name") or text[:40], color=p.get("color") or "#22d3ee",
                       extra={"engine": "sam3.1", "text": text, "source_layer": p.get("source_layer") or ""})
    out_dir = _layer_dir(pid, vid, layer["id"])
    chunk = int(p.get("chunk", 32))
    written = 0
    n = f1 - f0 + 1
    skip0 = 0 if tmp_dir else f0
    with _GPU_LOCK:
        for start in range(0, n, chunk):
            if _cancelled(job_id):
                raise ProcessError("Stopped")
            _job_update(job_id, message=f"SAM 3.1 «{text}»: frames {f0 + start}–{f0 + min(n, start + chunk) - 1} of {f1}",
                        progress=int(100 * start / n))
            prompt = load_template("deconstruct", "sam3_text_mask_video")
            prompt["3"]["inputs"]["text"] = text
            prompt["4"]["inputs"]["detection_threshold"] = float(p.get("threshold", 0.5))
            prompt["4"]["inputs"]["max_objects"] = int(p.get("max_objects", 8))
            q = run_process("deconstruct", prompt=prompt, client_id="keshiki_2d", run_tag=job_id, params={
                "Frames.directory": src_dir, "Frames.image_load_cap": min(chunk, n - start), "Frames.skip_first_images": skip0 + start,
            })
            imgs = _wait_images(q["prompt_id"], job_id)
            for i, item in enumerate(imgs):
                src = os.path.join(folder_paths.get_output_directory(), item.get("subfolder", ""), item["filename"])
                m = cv2.imread(src, cv2.IMREAD_GRAYSCALE)
                if m is None:
                    continue
                rgba = np.zeros((m.shape[0], m.shape[1], 4), np.uint8)
                rgba[..., :3] = _bgr(layer["color"])
                rgba[..., 3] = m
                cv2.imwrite(os.path.join(out_dir, f"frame_{f0 + start + i:04d}.png"), rgba)
                written += 1
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"layer": layer, "frames": written}


def _wait_images(prompt_id: str, job_id: str, timeout_s: float = 1800) -> List[Dict[str, Any]]:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = get_run_status(prompt_id)
        if st["status"] == "cancelled" or _cancelled(job_id):
            raise ProcessError("Stopped")
        if st["status"] == "error":
            msgs = [e.get("exception_message", "").strip() for e in (st.get("errors") or []) if isinstance(e, dict)]
            raise ProcessError("; ".join(m for m in msgs if m) or "ComfyUI error")
        if st["status"] == "done":
            return [o for o in st["outputs"] if o.get("kind") == "images" and o.get("type") == "output"]
        time.sleep(1.0)
    raise ProcessError("Timed out")


def _job_detect_all(job_id, kind, pid, vid, p):
    """All dynamic objects (YOLO classes, refined by SAM2) merged per frame -> one mask layer."""
    _require_plate(vid)
    classes = p.get("classes") or "person, car, truck, bus, motorcycle, bicycle"
    engine = "yolo" if str(p.get("engine", "sam2")).lower() == "yolo" else "sam2"
    _job_update(job_id, message=f"{'YOLO' if engine == 'yolo' else 'YOLO + SAM2'} on every frame ({classes})…", progress=5)
    with _GPU_LOCK:
        _free_comfy_vram()
        pps.apply_auto_mask(mask_classes=classes, detect_props=bool(p.get("detect_props", False)), engine=engine, node_id=node_id_for(vid))
    d = pps.get_dirs(node_id_for(vid))
    layer = _add_layer(pid, vid, "mask", p.get("name") or "Dynamic objects", color="#38bdf8")
    out_dir = _layer_dir(pid, vid, layer["id"])
    _write_tinted_masks(d["automask"], out_dir, layer["color"])
    return {"layer": layer}


def _free_comfy_vram():
    """In-process GPU work (SAM2, CorridorKey) runs outside ComfyUI's model manager: unload ComfyUI models
    first, otherwise a resident 22B LTX model pushes these jobs into shared memory (10-50x slower)."""
    try:
        from .progress_tracker import evict_heavy_llm_async
        evict_heavy_llm_async()
    except Exception:
        pass
    try:
        import comfy.model_management as mm
        mm.unload_all_models()
        mm.soft_empty_cache()
    except Exception as e:  # pragma: no cover
        print(f"[VFX-Suite] could not free ComfyUI VRAM: {e}")


# --- Layer operations: inverted display, fill, render (flatten), layer -> video ---------------------
MASK_LIKE = ("mask", "matte")


def bw_frame_png(path: str, invert: bool = False, as_alpha: bool = False) -> bytes:
    """A mask/matte frame as a plain black-and-white picture (white = the object), like a compositor's alpha view."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return b""
    if img.ndim == 2:
        a = img
    elif img.shape[2] == 4:
        a = img[..., 3]
    else:
        a = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if invert:
        a = 255 - a
    if as_alpha:  # white with the matte in the alpha channel: usable as a CSS mask-image
        out = np.full((a.shape[0], a.shape[1], 4), 255, np.uint8)
        out[..., 3] = a
        ok, buf = cv2.imencode(".png", out)
        return buf.tobytes() if ok else b""
    ok, buf = cv2.imencode(".png", a)
    return buf.tobytes() if ok else b""


def inverted_frame_png(path: str) -> bytes:
    """Same frame with its alpha inverted (masks/mattes shown as 'everything but the object')."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return b""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    if img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        img[..., 3] = 255
    a = img[..., 3]
    if img[..., :3].max() == 0 and a.max() > 0:
        img[..., :3] = 255
    else:
        # recolor fully-transparent pixels with the layer tint so the inverted area keeps its color
        vis = a > 0
        if vis.any():
            tint = np.median(img[..., :3][vis], axis=0).astype(np.uint8)
            img[..., :3] = tint
    img[..., 3] = 255 - a
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else b""


def _layer_alpha(pid, vid, layer, idx, h, w):
    """Alpha (0..1 float) of a layer at a frame, honoring its inverted flag; None if no frame."""
    fp = os.path.join(_layer_dir(pid, vid, layer["id"]), f"key_{idx:04d}.png")
    if not os.path.isfile(fp):
        fp = os.path.join(_layer_dir(pid, vid, layer["id"]), f"frame_{idx:04d}.png")
    if not os.path.isfile(fp):
        fp = _paint_key_for(_layer_dir(pid, vid, layer["id"]), idx) or fp
    img = cv2.imread(fp, cv2.IMREAD_UNCHANGED) if os.path.isfile(fp) else None
    if img is None:
        if layer.get("inverted") and layer.get("type") in MASK_LIKE:
            return np.ones((h, w), np.float32), None
        return None, None
    if img.shape[:2] != (h, w):
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
    if img.ndim == 2:
        a = img.astype(np.float32) / 255.0
        rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        a = img[..., 3].astype(np.float32) / 255.0
        rgb = img[..., :3]
    else:
        a = np.ones((h, w), np.float32)
        rgb = img
    if layer.get("inverted") and layer.get("type") in MASK_LIKE:
        a = 1.0 - a
    return a, rgb


def _hex_bgr(color):
    c = (color or "#00ff00").lstrip("#")
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        r, g, b = 0, 255, 0
    return (b, g, r)


def _job_fill(job_id, kind, pid, vid, p):
    """Solid color through a mask layer (or its inverse): e.g. a clean single-color background."""
    info = _require_plate(vid)
    total, w, h = int(info["total_frames"]), int(info["width"]), int(info["height"])
    man = load_manifest(pid, vid)
    src = next((l for l in man["layers"] if l["id"] == p.get("mask_layer_id")), None)
    invert = bool(p.get("invert", True))
    color = p.get("color") or "#00ff00"
    name = p.get("name") or (f"Fill {color}" + (f" · {'outside' if invert else 'inside'} {src['name']}" if src else ""))
    layer = _add_layer(pid, vid, "fill", name, color=color,
                       extra={"derived_from": src["id"] if src else None, "fill": {"color": color, "invert": invert}})
    out_dir = _layer_dir(pid, vid, layer["id"])
    bgr = _hex_bgr(color)
    probe = dict(src) if src else None
    if probe is not None:
        probe["inverted"] = bool(src.get("inverted")) ^ invert
        probe["type"] = "mask"
    for i in range(total):
        if _cancelled(job_id):
            raise ProcessError("Stopped")
        if i % 10 == 0:
            _job_update(job_id, message=f"Fill: frame {i + 1}/{total}", progress=int(100 * i / max(1, total)))
        if probe is None:
            a = np.ones((h, w), np.float32)
        else:
            a, _ = _layer_alpha(pid, vid, probe, i, h, w)
            if a is None:
                continue
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[..., :3] = bgr
        rgba[..., 3] = (a * 255).astype(np.uint8)
        cv2.imwrite(os.path.join(out_dir, f"frame_{i:04d}.png"), rgba)
    return {"layer": layer}


def _encode_mp4(frames_iter, out_path, fps, w, h):
    """H.264 mp4 (browser-playable) via the ffmpeg that ships with imageio-ffmpeg / VHS; mp4v as a fallback."""
    import subprocess
    exe = None
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
    if exe:
        cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps),
               "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "medium", out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        n = 0
        for fr in frames_iter:
            proc.stdin.write(np.ascontiguousarray(fr).tobytes())
            n += 1
        proc.stdin.close()
        proc.wait()
        if proc.returncode == 0:
            return n
    vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    n = 0
    for fr in frames_iter:
        vw.write(fr)
        n += 1
    vw.release()
    return n


def _new_video_path(pid, stem):
    folder = os.path.join(get_project_dir(pid), "source_video")
    os.makedirs(folder, exist_ok=True)
    name = f"{_safe(stem)[:60]}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    return os.path.join(folder, name), f"/vfx/project_file/{pid}/source_video/{name}"


def _job_composite(job_id, kind, pid, vid, p):
    """Cut a subject out of one source with a mask and put it over another background -> new rgb layer.

    fg: "plate" (the original footage) or a layer id; mask: a mask/matte layer (white = subject);
    bg: "plate" or a layer id. Soft edge: the mask is eroded a little and feathered, so the old background's
    fringe does not survive; a refine pass (e.g. the refine-details IC-LoRA) then blends lighting and edges.
    Sizes follow the plate; layers of another size (LTX output) are resized."""
    info = _require_plate(vid)
    total = int(info["total_frames"])
    w, h = int(info.get("width", 1920)), int(info.get("height", 1080))
    fg_id, bg_id, mask_id = str(p.get("fg") or "plate"), str(p.get("bg") or "plate"), str(p.get("mask") or "")
    if not mask_id:
        raise ProcessError("Choose the subject's mask layer")
    man = load_manifest(pid, vid)
    mlayer = next((l for l in man["layers"] if l["id"] == mask_id), None)
    if mlayer is None:
        raise ProcessError("Mask layer not found")
    erode, feather = int(p.get("erode", 2)), int(p.get("feather", 4))
    f0 = max(0, int(p.get("start", 0) or 0))
    f1 = min(total - 1, int(p.get("end", total - 1)))
    layer = _add_layer(pid, vid, "rgb", p.get("name") or "Composite", extra={"source": "composite", "fg": fg_id, "bg": bg_id, "mask": mask_id})
    out_dir = _layer_dir(pid, vid, layer["id"])

    def rgb(src, i):
        fp = frame_path(pid, vid, "plate_undist" if src == "plate" and _count_frames(pps.get_dirs(node_id_for(vid))["undist"]) else
                        ("plate_raw" if src == "plate" else _safe(src)), i)
        img = cv2.imread(fp, cv2.IMREAD_UNCHANGED) if fp and os.path.isfile(fp) else None
        if img is None:
            return None
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            img = img[..., :3]
        return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA) if img.shape[:2] != (h, w) else img

    k = np.ones((3, 3), np.uint8)
    written = 0
    harmonize = float(p.get("harmonize", 0) or 0)      # 0..1: pull the subject's colour/light toward the background
    light_wrap = float(p.get("light_wrap", 0) or 0)    # 0..1: background light bleeding over the subject's edge
    stats = None
    if harmonize > 0:   # one transform for the whole shot (per-frame stats flicker)
        acc = []
        for i in range(f0, f1 + 1, max(1, (f1 - f0 + 1) // 8)):
            fg, bg = rgb(fg_id, i), rgb(bg_id, i)
            a, _ = _layer_alpha(pid, vid, mlayer, i, h, w)
            if fg is None or bg is None or a is None or (a > 0.5).sum() < 500:
                continue
            fl, bl = cv2.cvtColor(fg, cv2.COLOR_BGR2LAB).astype(np.float32), cv2.cvtColor(bg, cv2.COLOR_BGR2LAB).astype(np.float32)
            m = a > 0.5
            acc.append((fl[m].mean(0), fl[m].std(0) + 1e-3, bl[~m].mean(0), bl[~m].std(0) + 1e-3))
        if acc:
            stats = [np.mean([x[j] for x in acc], 0) for j in range(4)]

    def harmonized(fg):
        if stats is None:
            return fg
        fm, fs, bm, bs = stats
        lab = cv2.cvtColor(fg, cv2.COLOR_BGR2LAB).astype(np.float32)
        # Reinhard transfer, softened: luminance contrast is kept closer to the original than colour
        scale = 1.0 + harmonize * (bs / fs - 1.0) * np.array([0.5, 1.0, 1.0], np.float32)
        tgt = (lab - fm) * scale + fm + harmonize * (bm - fm) * np.array([0.6, 1.0, 1.0], np.float32)
        return cv2.cvtColor(tgt.clip(0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

    for i in range(f0, f1 + 1):
        if _cancelled(job_id):
            raise ProcessError("Stopped")
        if (i - f0) % 12 == 0:
            _job_update(job_id, message=f"Composite: frame {i} of {f1}", progress=int(100 * (i - f0) / max(1, f1 - f0 + 1)))
        fg, bg = rgb(fg_id, i), rgb(bg_id, i)
        if fg is None or bg is None:
            continue
        a, _ = _layer_alpha(pid, vid, mlayer, i, h, w)
        if a is None:
            a = np.zeros((h, w), np.float32)
        a8 = (a * 255).astype(np.uint8)
        if erode > 0:
            a8 = cv2.erode(a8, k, iterations=erode)
        a = a8.astype(np.float32) / 255.0
        if feather > 0:
            a = cv2.GaussianBlur(a, (0, 0), feather)
        fg = harmonized(fg)
        fgf = fg.astype(np.float32)
        if light_wrap > 0:
            # light wrap: the blurred background glows into the subject near its edge
            edge = (a * (1.0 - cv2.GaussianBlur(a, (0, 0), 6 + 10 * light_wrap)))[..., None]
            bgb = cv2.GaussianBlur(bg, (0, 0), 12).astype(np.float32)
            fgf = fgf * (1 - edge * light_wrap * 2.0).clip(0, 1) + bgb * (edge * light_wrap * 2.0).clip(0, 1)
        out = fgf * a[..., None] + bg.astype(np.float32) * (1.0 - a[..., None])
        cv2.imwrite(os.path.join(out_dir, f"frame_{i:04d}.png"), out.clip(0, 255).astype(np.uint8))
        written += 1
    return {"layer": layer, "frames": written}


def _job_render(job_id, kind, pid, vid, p):
    """Flattens the plate + all visible layers (order, opacity, inversion) into a new video."""
    info = _require_plate(vid)
    total, w, h = int(info["total_frames"]), int(info["width"]), int(info["height"])
    fps = float(info.get("fps") or 24) or 24
    man = load_manifest(pid, vid)
    layers = [l for l in man["layers"] if l.get("visible", True)]
    src_dir = _source_frames_dir(vid)
    include_plate = bool(p.get("include_plate", True))
    f0 = max(0, int(p.get("start", 0) or 0))
    f1 = min(total - 1, int(p.get("end", total - 1)))

    def frames():
        for i in range(f0, f1 + 1):
            if _cancelled(job_id):
                raise ProcessError("Stopped")
            if i % 5 == 0:
                _job_update(job_id, message=f"Render: frame {i + 1}/{f1 + 1}", progress=int(100 * (i - f0) / max(1, f1 - f0 + 1)))
            base = cv2.imread(os.path.join(src_dir, f"frame_{i:04d}.jpg")) if include_plate else None
            acc = base.astype(np.float32) if base is not None else np.zeros((h, w, 3), np.float32)
            if acc.shape[:2] != (h, w):
                acc = cv2.resize(acc, (w, h))
            for l in layers:
                a, rgb = _layer_alpha(pid, vid, l, i, h, w)
                if a is None:
                    continue
                if rgb is None:  # inverted mask without a frame: fill with its color
                    rgb = np.zeros((h, w, 3), np.uint8); rgb[:] = _hex_bgr(l.get("color") or "#ffffff")
                elif l.get("inverted") and l.get("type") in MASK_LIKE:
                    rgb = np.zeros((h, w, 3), np.uint8); rgb[:] = _hex_bgr(l.get("color") or "#ffffff")
                a = (a * float(l.get("opacity", 1.0)))[..., None]
                acc = acc * (1 - a) + rgb.astype(np.float32) * a
            yield np.clip(acc, 0, 255).astype(np.uint8)

    out_path, url = _new_video_path(pid, p.get("name") or "render")
    n = _encode_mp4(frames(), out_path, fps, w, h)
    return {"video": {"url": url, "frames": n, "fps": fps, "width": w, "height": h,
                      "layers": [l["name"] for l in layers], "include_plate": include_plate}}


def _job_layer_to_video(job_id, kind, pid, vid, p):
    """One layer as its own video: masks/mattes as a white-on-black matte, color layers over black."""
    info = _require_plate(vid)
    total, w, h = int(info["total_frames"]), int(info["width"]), int(info["height"])
    fps = float(info.get("fps") or 24) or 24
    man = load_manifest(pid, vid)
    layer = next((l for l in man["layers"] if l["id"] == p.get("layer_id")), None)
    if not layer:
        raise ProcessError("Layer not found")
    as_matte = layer.get("type") in MASK_LIKE

    def frames():
        for i in range(total):
            if _cancelled(job_id):
                raise ProcessError("Stopped")
            if i % 10 == 0:
                _job_update(job_id, message=f"Export: frame {i + 1}/{total}", progress=int(100 * i / max(1, total)))
            a, rgb = _layer_alpha(pid, vid, layer, i, h, w)
            if a is None:
                yield np.zeros((h, w, 3), np.uint8)
                continue
            if as_matte or rgb is None:
                yield cv2.cvtColor((a * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
            else:
                yield (rgb.astype(np.float32) * a[..., None]).astype(np.uint8)

    out_path, url = _new_video_path(pid, layer.get("name") or "layer")
    n = _encode_mp4(frames(), out_path, fps, w, h)
    return {"video": {"url": url, "frames": n, "fps": fps, "width": w, "height": h, "layer": layer["name"], "matte": as_matte}}


def _job_mask_grow(job_id, kind, pid, vid, p):
    """Grow (+px) or shrink (-px) a mask/matte layer, optional feather. Re-applies from the original
    frames every time, so the slider can go back and forth without degrading the mask."""
    _require_plate(vid)
    man = load_manifest(pid, vid)
    layer = next((l for l in man["layers"] if l["id"] == p.get("layer_id")), None)
    if not layer:
        raise ProcessError("Layer not found")
    px = int(p.get("px", 0))
    feather = max(0, int(p.get("feather", 0)))
    d = _layer_dir(pid, vid, layer["id"])
    orig = os.path.join(d, "_orig")
    if not os.path.isdir(orig):
        os.makedirs(orig)
        for fn in os.listdir(d):
            if fn.startswith("frame_"):
                shutil.copy2(os.path.join(d, fn), os.path.join(orig, fn))
    files = sorted(fn for fn in os.listdir(orig) if fn.startswith("frame_"))
    k = None
    if px:
        r = abs(px)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    for i, fn in enumerate(files):
        if _cancelled(job_id):
            raise ProcessError("Stopped")
        if i % 10 == 0:
            _job_update(job_id, message=f"{'Grow' if px >= 0 else 'Shrink'} {abs(px)}px: {i + 1}/{len(files)}", progress=int(100 * i / max(1, len(files))))
        img = cv2.imread(os.path.join(orig, fn), cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA); img[..., 3] = img[..., 0]
        a = img[..., 3]
        if k is not None:
            a = cv2.dilate(a, k) if px > 0 else cv2.erode(a, k)
        if feather:
            a = cv2.GaussianBlur(a, (0, 0), feather / 2.0)
        img[..., 3] = a
        if px > 0:  # newly covered pixels get the layer color
            img[..., :3] = _bgr(layer.get("color") or "#ffffff") if layer.get("type") == "mask" else img[..., :3]
        cv2.imwrite(os.path.join(d, fn), img)
    layer["grow"] = {"px": px, "feather": feather}
    save_manifest(pid, vid, man)
    return {"layer": layer, "frames": len(files)}


def _release_inprocess_gpu():
    """Drops our own cached GPU models (SAM2 image/video, CorridorKey) so ComfyUI gets the VRAM back."""
    global _SAM2_VIDEO
    import sys as _sys
    try:
        _SAM2_VIDEO = None
    except Exception:
        pass
    try:
        pps._SAM2_PREDICTOR = None
    except Exception:
        pass
    for name, mod in list(_sys.modules.items()):
        if name.endswith("corridor_key.engine") and hasattr(mod, "_ENGINE_CACHE"):
            mod._ENGINE_CACHE.clear()
    try:
        import gc, torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _bgr(color: str):
    c = (color or "#ffffff").lstrip("#")
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        r, g, b = 255, 255, 255
    return (b, g, r)


def _write_tinted_masks(src_dir: str, out_dir: str, color: str = "#ffffff"):
    for fn in sorted(os.listdir(src_dir)):
        if not fn.startswith("frame_"):
            continue
        m = cv2.imread(os.path.join(src_dir, fn), cv2.IMREAD_GRAYSCALE)
        if m is None:
            continue
        rgba = np.zeros((m.shape[0], m.shape[1], 4), np.uint8)
        rgba[..., :3] = _bgr(color)
        rgba[..., 3] = m
        cv2.imwrite(os.path.join(out_dir, os.path.splitext(fn)[0] + ".png"), rgba)


# --- LTX-2.5 IC-LoRA video-to-video (Alpha-Gen and other IC-LoRAs) -------------------
LTX_IC_LORAS = {
    # kind -> (lora file, layer type, layer name, output is a grayscale matte)
    "alpha": ("LTX-2.5\\ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors", "matte", "LTX Alpha matte", True),
}


# Known IC-LoRAs: keyword in the file name -> (title RU, title EN, what it does RU, output: rgb|matte|upscale, needs)
IC_LORA_INFO = [
    ("alpha-gen", "Альфа (матт)", "Alpha matte", "RGB → чёрно-белый матт объекта на переднем плане", "matte", ""),
    ("clean-plate", "Чистый плейт", "Clean plate", "Убирает объект и восстанавливает фон", "rgb", ""),
    ("layout-to-render", "Лейаут → рендер", "Layout to render", "Серый лейаут/плейбласт → финальный шот. Нужен первый кадр-образец (палитра, свет, материалы), сделанный из первого кадра лейаута", "rgb", "first_frame"),
    ("refine-details", "Рефайн деталей", "Refine details", "Дорисовывает мелкие детали, сохраняя движение", "rgb", ""),
    ("pixel-spatial-upscaler", "Апскейл ×2", "Upscale ×2", "Увеличивает разрешение в 2 раза", "upscale", ""),
    ("restore", "Реставрация", "Restore", "Чистит шум, царапины и артефакты старого видео", "rgb", ""),
    ("deblur", "Убрать размытие", "Deblur", "Убирает смаз и расфокус", "rgb", ""),
    ("decompression", "Убрать артефакты сжатия", "Decompression", "Убирает блоки и артефакты кодека", "rgb", ""),
    ("colorization", "Колоризация", "Colorization", "Раскрашивает чёрно-белое видео", "rgb", ""),
    ("day-to-night", "День → ночь", "Day to night", "Переводит дневную сцену в ночную", "rgb", ""),
    ("sdr-to-hdr", "SDR → HDR", "SDR to HDR", "Расширяет динамический диапазон (вывод — 8-бит превью)", "rgb", ""),
    ("water-simulation", "Симуляция воды", "Water simulation", "Добавляет/переделывает воду по промпту", "rgb", ""),
    ("ingredients", "Ингредиенты", "Ingredients", "Видео по референс-листу (персонажи, предметы, локации на чёрном фоне). Промпт: «Reference sheet: … / Generated video: …»", "rgb", "ref_sheet"),
    ("union-control", "Union control", "Union control", "Управление по depth/pose/canny-видео (LTX-2.3, экспериментально на 2.5)", "rgb", "control"),
    ("motion-track", "Motion track", "Motion track", "Управление движением по треку (LTX-2.3, экспериментально)", "rgb", "control"),
    ("hdr", "HDR (2.3)", "HDR (2.3)", "HDR IC-LoRA от LTX-2.3 (экспериментально)", "rgb", ""),
]


def list_ic_loras():
    """Every IC-LoRA file on disk (LTX-2.5 first, then LTX-2.3 ones that may work on 2.5). New files show up automatically;
    unknown ones are listed by file name."""
    import folder_paths
    out = []
    try:
        names = folder_paths.get_filename_list("loras")
    except Exception:
        names = []
    for n in names:
        low = n.replace("\\", "/").lower()
        base = low.rsplit("/", 1)[-1]
        if not ("ic-lora" in base or "ic_lora" in base) or "ltx" not in low:
            continue
        if re.search(r"step_\d+|_000\d0", base) or "scene-emb" in base:  # training checkpoints / helper files
            continue
        fam = "2.5" if ("2.5" in low or "ltx25" in low) else ("2.3" if ("2.3" in low or "ltx23" in low) else "?")
        info = next((i for i in IC_LORA_INFO if i[0] in base and not (i[0] == "hdr" and "sdr-to-hdr" in base)), None)
        stem = base.rsplit(".", 1)[0]
        out.append({
            "file": n, "family": fam,
            "key": info[0] if info else stem,
            "title_ru": (info[1] if info else stem) + ("" if fam == "2.5" else f" · LTX-{fam}"),
            "title_en": (info[2] if info else stem) + ("" if fam == "2.5" else f" · LTX-{fam}"),
            "desc_ru": info[3] if info else "IC-LoRA без описания (добавлен вручную)",
            "output": info[4] if info else "rgb",
            "needs": info[5] if info else "",
        })
    out.sort(key=lambda x: (x["family"] != "2.5", x["title_ru"]))
    return out


def _ref_image_to_input(url: str, dst_name: str) -> str:
    """Copies a project image (project URL, /view URL, data URL or absolute path) into ComfyUI/input."""
    import base64
    import folder_paths
    dst = os.path.join(folder_paths.get_input_directory(), dst_name)
    if url.startswith("data:"):
        with open(dst, "wb") as f:
            f.write(base64.b64decode(url.split(",", 1)[1]))
    else:
        src = resolve_media_path(url)
        img = cv2.imread(src, cv2.IMREAD_COLOR)
        if img is None:
            raise ProcessError("Cannot read the reference image")
        cv2.imwrite(dst, img)
    return dst_name


def _add_second_ic_lora(prompt: dict, lora2: str, clip2: str, strength: float = 1.0, attention: float = 1.0) -> str:
    """Stacks a second IC-LoRA with its own control video (e.g. layout-to-render + pose).
    Returns the id of the node whose (positive, negative, latent) now feed the sampler."""
    prompt["kl2"] = {"class_type": "LTXICLoRALoaderModelOnly", "_meta": {"title": "IC LoRA 2"},
                     "inputs": {"lora_name": lora2, "strength_model": float(strength), "model": ["5004:5606", 0]}}
    prompt["5516:4828"]["inputs"]["model"] = ["kl2", 0]
    prompt["k2_src"] = {"class_type": "LoadVideo", "inputs": {"file": clip2}, "_meta": {"title": "Control Video 2"}}
    prompt["k2_comp"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["k2_src", 0]}}
    prompt["k2_scale"] = {"class_type": "ImageScale", "inputs": {"image": ["k2_comp", 0], "upscale_method": "lanczos",
                          "width": ["5548:5029", 0], "height": ["5548:5029", 1], "crop": "disabled"}}
    prompt["k2_guide"] = {"class_type": "LTXAddVideoICLoRAGuideAdvanced", "_meta": {"title": "IC-LoRA Guide 2"}, "inputs": {
        "positive": ["9002:5012", 0], "negative": ["9002:5012", 1], "vae": ["5004:5601", 0], "latent": ["9002:5012", 2],
        "image": ["k2_scale", 0], "frame_idx": 0, "strength": 1.0, "latent_downscale_factor": ["kl2", 1],
        "crop": "disabled", "use_tiled_encode": False, "tile_size": 256, "tile_overlap": 64,
        "attention_strength": float(attention), "use_streaming": False, "tile_frames": 97}}
    prompt["9002:4528"]["inputs"]["video_latent"] = ["k2_guide", 2]
    for nid in ("5516:4828", "5516:5000"):
        prompt[nid]["inputs"]["positive"] = ["k2_guide", 0]
        prompt[nid]["inputs"]["negative"] = ["k2_guide", 1]
    return "k2_guide"


def _add_first_frame_guide(prompt: dict, image_name: str, strength: float = 1.0, after: str = "9002:5012") -> None:
    """Adds an image guide at frame 0 after the IC-LoRA video guide(s) (LTXVAddGuide) and rewires the sampler to it."""
    prompt["kref_load"] = {"class_type": "LoadImage", "inputs": {"image": image_name}, "_meta": {"title": "Reference Image"}}
    prompt["kref_scale"] = {"class_type": "ImageScale", "inputs": {"image": ["kref_load", 0], "upscale_method": "lanczos",
                            "width": ["5548:5029", 0], "height": ["5548:5029", 1], "crop": "center"}}
    prompt["kref_guide"] = {"class_type": "LTXVAddGuide", "inputs": {
        "positive": [after, 0], "negative": [after, 1], "vae": ["5004:5601", 0], "latent": [after, 2],
        "image": ["kref_scale", 0], "frame_idx": 0, "strength": float(strength)}}
    prompt["9002:4528"]["inputs"]["video_latent"] = ["kref_guide", 2]
    for nid in ("5516:4828", "5516:5000"):
        prompt[nid]["inputs"]["positive"] = ["kref_guide", 0]
        prompt[nid]["inputs"]["negative"] = ["kref_guide", 1]


_RESERVE_DEFAULT = None


def _reserve_vram_for_ltx(gb: float):
    """Raise ComfyUI's reserved VRAM (like --reserve-vram) so big models load partially and leave room to run."""
    global _RESERVE_DEFAULT
    try:
        import comfy.model_management as mm
        if _RESERVE_DEFAULT is None:
            _RESERVE_DEFAULT = mm.EXTRA_RESERVED_VRAM
        mm.EXTRA_RESERVED_VRAM = max(_RESERVE_DEFAULT, int(gb * 1024 ** 3))
        return mm.EXTRA_RESERVED_VRAM
    except Exception:
        return None


def _restore_reserved_vram():
    try:
        import comfy.model_management as mm
        if _RESERVE_DEFAULT is not None:
            mm.EXTRA_RESERVED_VRAM = _RESERVE_DEFAULT
    except Exception:
        pass


def _job_ltx_ic_lora(job_id, kind, pid, vid, p):
    """Runs the plate through LTX-2.5 + an IC-LoRA in chunks of 8n+1 frames (the model's frame rule).

    Alpha-Gen returns a grayscale matte (white = foreground) of the same length; it is resized back to
    the plate and stored as a mask-style layer. Chunks keep VRAM in check on 24 GB cards.
    """
    import folder_paths
    info = _require_plate(vid)
    _release_inprocess_gpu()  # the 22B model needs every GB we hold
    # Keep VRAM headroom for activations: when ComfyUI loads the 22B transformer completely (~20.5 GB on a 24 GB card)
    # a 33-frame chunk takes 5-9 min instead of ~2.4 min with a little of it offloaded. Restored when the job ends.
    # Up to `max_one_pass` frames (97) go in ONE pass - no seams, and ~3x faster than overlapping chunks
    # (tested: 97 frames in 3.3 min on a 3090 Ti with 5 GB reserved). Longer clips: 33-frame chunks + overlap.
    _one_pass = "chunk" not in p
    _reserve = _reserve_vram_for_ltx(float(p.get("reserve_vram_gb", 5.0 if _one_pass else 3.0)))
    total = int(info["total_frames"])
    fps = float(info.get("fps") or 24) or 24
    w, h = int(info.get("width", 1920)), int(info.get("height", 1080))
    preset = p.get("preset") or ("alpha" if kind == "ltx_alpha" else "")
    lora, ltype, lname, is_matte = LTX_IC_LORAS.get(preset, (p.get("lora"), "matte" if p.get("output") == "matte" else "rgb",
                                                            p.get("name") or "LTX IC-LoRA", p.get("output") == "matte"))
    keep_out_size = p.get("output") == "upscale"
    if not lora:
        raise ProcessError("No IC-LoRA chosen")
    chunk = int(p.get("chunk", 33))
    chunk = max(9, ((chunk - 1) // 8) * 8 + 1)
    _span = min(total, int(p.get("end", total - 1)) + 1) - max(0, int(p.get("start", 0) or 0))
    if _one_pass and 0 < _span <= int(p.get("max_one_pass", 97)):
        chunk = ((_span - 1 + 7) // 8) * 8 + 1   # the whole range at once (padded to 8n+1)
    src_dir = _source_frames_dir(vid)
    frames = sorted(f for f in os.listdir(src_dir) if f.startswith("frame_"))[:total]
    ref_url = str(p.get("ref_image") or "")
    needs = p.get("needs") or ""
    ref_name = _ref_image_to_input(ref_url, f"keshiki_ref_{job_id}.png") if ref_url else None
    if needs == "first_frame" and not ref_name:
        raise ProcessError("This mode needs a first-frame image (press + and pick one)")
    if needs == "ref_sheet" and not ref_name:
        raise ProcessError("This mode needs a reference sheet image (press + and pick one)")
    src_layer = p.get("source_layer")
    for old in os.listdir(os.path.dirname(_layer_dir(pid, vid, "x"))) if os.path.isdir(os.path.dirname(_layer_dir(pid, vid, "x"))) else []:
        if old.startswith(("_tmp_vjob_", "_tmp2_vjob_")):
            shutil.rmtree(os.path.join(os.path.dirname(_layer_dir(pid, vid, "x")), old), ignore_errors=True)
    if needs == "ref_sheet":  # the sheet is fed as a static video of the same length
        import folder_paths
        sheet = cv2.imread(os.path.join(folder_paths.get_input_directory(), ref_name))
        sheet = cv2.resize(sheet, (w, h), interpolation=cv2.INTER_AREA)
        tmp_src = _layer_dir(pid, vid, "_tmp_" + job_id)
        os.makedirs(tmp_src, exist_ok=True)
        made = []
        for i in range(len(frames)):
            name = f"frame_{i:04d}.jpg"
            if i == 0:
                cv2.imwrite(os.path.join(tmp_src, name), sheet, [cv2.IMWRITE_JPEG_QUALITY, 95])
            else:
                shutil.copyfile(os.path.join(tmp_src, "frame_0000.jpg"), os.path.join(tmp_src, name))
            made.append(name)
        src_dir, frames, src_layer = tmp_src, made, "__sheet__"
    elif src_layer:  # use a layer (layout, mask, paint…) as the input video: RGBA over black, held frames filled in
        if not os.path.isdir(_layer_dir(pid, vid, _safe(src_layer))):
            raise ProcessError("Source layer not found")
        tmp_src = _layer_dir(pid, vid, "_tmp_" + job_id)
        os.makedirs(tmp_src, exist_ok=True)
        made = []
        for i in range(len(frames)):
            fp = frame_path(pid, vid, _safe(src_layer), i)
            img = cv2.imread(fp, cv2.IMREAD_UNCHANGED) if fp else None
            if img is None:
                img = np.zeros((h, w, 3), np.uint8)
            elif img.ndim == 3 and img.shape[2] == 4:
                a = img[..., 3:4].astype(np.float32) / 255.0
                img = (img[..., :3].astype(np.float32) * a).astype(np.uint8)
            elif img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            if img.shape[1] != w or img.shape[0] != h:
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            name = f"frame_{i:04d}.jpg"
            cv2.imwrite(os.path.join(tmp_src, name), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            made.append(name)
        src_dir, frames = tmp_src, made
    # Optional second IC-LoRA with its own control layer (e.g. layout-to-render on a 3D render + pose)
    lora2 = str(p.get("lora2") or "")
    src2_dir = None
    if lora2:
        src2 = p.get("source_layer2")
        if not src2 or not os.path.isdir(_layer_dir(pid, vid, _safe(src2))):
            raise ProcessError("Choose the layer for the second control")
        src2_dir = _layer_dir(pid, vid, "_tmp2_" + job_id)
        os.makedirs(src2_dir, exist_ok=True)
        for i in range(len(frames)):
            img = _layer_frame_bgr(pid, vid, src2, i)
            if img is None:
                img = np.zeros((h, w, 3), np.uint8)
            if img.shape[1] != w or img.shape[0] != h:
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(src2_dir, frames[i] if frames[i].endswith(".jpg") else f"frame_{i:04d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    # Optional range (timeline in/out); frame files keep their absolute numbers.
    r0 = max(0, int(p.get("start", 0) or 0))
    r1 = min(len(frames) - 1, int(p.get("end", len(frames) - 1)))
    frames = frames[r0:r1 + 1]
    layer = _add_layer(pid, vid, ltype, lname,
                       color="#f472b6" if is_matte else "", extra={"source": "ltx_ic_lora", "lora": lora, **({"lora2": lora2} if lora2 else {})})
    out_dir = _layer_dir(pid, vid, layer["id"])
    in_dir = folder_paths.get_input_directory()
    written = 0
    # The VAE / IC-LoRA guide need sizes divisible by 64 (by 128 when the LoRA encodes its guide at half size, "ref0.5"),
    # otherwise the guide encode fails with "can't divide axis ... in chunks of 2".
    mult = 128 if ("ref0.5" in str(lora).lower() or "ref0.5" in lora2.lower()) else 64
    th = 512 if mult == 128 else 576
    tw = max(mult, int(round(w * th / max(1, h) / mult)) * mult)
    if h > w:  # portrait: fix the width instead
        tw = th
        th = max(mult, int(round(h * tw / max(1, w) / mult)) * mult)
    chunk_ref = ref_name  # later chunks start from a frame we already generated, so the look carries over
    # Chunks overlap: chunk k starts `overlap` frames before chunk k-1 ends, is guided by the frame already
    # generated there, and the overlapping frames are cross-faded - no visible jump at the chunk borders.
    overlap = int(p.get("overlap", 8)) if len(frames) > chunk else 0
    overlap = max(0, min(overlap, chunk // 2))
    starts, st0 = [], 0
    while True:
        starts.append(st0)
        if st0 + chunk >= len(frames):
            break
        st0 += chunk - overlap
    continuity = not is_matte   # rgb outputs: guide every later chunk by the frame already generated at its start
    for ci, start in enumerate(starts):
        if _cancelled(job_id):
            raise ProcessError("Stopped")
        part = frames[start:start + chunk]
        n_real = len(part)
        n_pad = ((n_real - 1 + 7) // 8) * 8 + 1  # pad to 8n+1 by holding the last frame
        part = part + [part[-1]] * (n_pad - n_real)
        _job_update(job_id, message=f"LTX: frames {r0 + start}–{r0 + start + n_real - 1} ({start + n_real}/{len(frames)}; first run loads the 22B model)",
                    progress=int(100 * start / max(1, len(frames))))
        clip_name = f"keshiki_{job_id}_{ci:03d}.mp4"
        clip_path = os.path.join(in_dir, clip_name)
        vw = cv2.VideoWriter(clip_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (tw, th))
        n_ok = 0
        for fn in part:
            im = cv2.imread(os.path.join(src_dir, fn))
            if im is None:
                continue
            vw.write(cv2.resize(im, (tw, th), interpolation=cv2.INTER_AREA))
            n_ok += 1
        vw.release()
        cap_chk = cv2.VideoCapture(clip_path)
        n_clip = int(cap_chk.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap_chk.release()
        clip2_name = None
        if src2_dir:
            clip2_name = f"keshiki_{job_id}_{ci:03d}_c2.mp4"
            vw2 = cv2.VideoWriter(os.path.join(in_dir, clip2_name), cv2.VideoWriter_fourcc(*"mp4v"), fps, (tw, th))
            for fn in part:
                im2 = cv2.imread(os.path.join(src2_dir, fn))
                vw2.write(cv2.resize(im2, (tw, th), interpolation=cv2.INTER_AREA) if im2 is not None else np.zeros((th, tw, 3), np.uint8))
            vw2.release()
        if n_ok == 0 or n_clip == 0:
            raise ProcessError(f"Could not build the source clip for frames {r0 + start}–{r0 + start + n_real - 1} (no readable frames)")
        seed = int(p.get("seed", 42))
        try:
            for attempt in range(2):
                prompt = load_template("video_fx", "ltx25_ic_lora_v2v")
                try:
                    prompt["5548:5026"]["inputs"]["resize_type.shorter_size"] = min(tw, th)
                except KeyError:
                    pass
                last_guide = "9002:5012"
                if clip2_name:
                    last_guide = _add_second_ic_lora(prompt, lora2, clip2_name, float(p.get("lora2_strength", 1.0)),
                                                     float(p.get("lora2_attention", 1.0)))
                if needs == "first_frame" or (continuity and ci > 0 and chunk_ref):
                    _add_first_frame_guide(prompt, chunk_ref, float(p.get("ref_strength", 1.0)), after=last_guide)
                q = run_process("video_fx", prompt=prompt, client_id="keshiki_2d", run_tag=job_id, params={
                    "Source Video.file": clip_name, "IC LoRA.lora_name": lora,
                    "Prompt.value": p.get("prompt", ""), "Seed.noise_seed": seed + attempt,
                    "Save.filename_prefix": f"keshiki/video/{job_id}",
                })
                outs = _wait_images(q["prompt_id"], job_id, timeout_s=3600)
                vids = [o for o in outs if str(o.get("filename", "")).lower().endswith((".mp4", ".webm", ".mov"))]
                if not vids:
                    raise ProcessError("LTX returned no video")
                o = vids[0]
                cap = cv2.VideoCapture(os.path.join(folder_paths.get_output_directory(), o.get("subfolder", ""), o["filename"]))
                got = []
                while len(got) < n_real:
                    ok, fr = cap.read()
                    if not ok:
                        break
                    got.append(fr if keep_out_size else cv2.resize(fr, (w, h), interpolation=cv2.INTER_CUBIC))
                cap.release()
                # Alpha-Gen sometimes decides nothing is foreground on ambiguous shots: retry once with another seed.
                if is_matte and got and float(np.mean([g.mean() for g in got])) < 2.0 and attempt == 0:
                    _job_update(job_id, message=f"LTX: empty matte on frames {start}–{start + n_real - 1}, retrying with seed {seed + 1}")
                    continue
                break
        finally:
            for cp in (clip_path, os.path.join(in_dir, clip2_name) if clip2_name else None):
                try:
                    if cp:
                        os.remove(cp)
                except OSError:
                    pass
        ov = overlap if ci > 0 else 0
        for i, fr in enumerate(got):
            dst = os.path.join(out_dir, f"frame_{r0 + start + i:04d}.png")
            if i < ov and os.path.isfile(dst):   # cross-fade with the previous chunk's frames
                prev = cv2.imread(dst, cv2.IMREAD_UNCHANGED)
                t = (i + 1) / (ov + 1)
                t = t * t * (3 - 2 * t)   # smoothstep: no kink at either end of the cross-fade
                if is_matte and prev is not None and prev.ndim == 3 and prev.shape[2] == 4:
                    m = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
                    prev[..., 3] = (prev[..., 3].astype(np.float32) * (1 - t) + m * t).astype(np.uint8)
                    cv2.imwrite(dst, prev)
                    continue
                if prev is not None and prev.shape[:2] == fr.shape[:2] and prev.ndim == 3 and prev.shape[2] == 3:
                    fr = (prev.astype(np.float32) * (1 - t) + fr.astype(np.float32) * t).astype(np.uint8)
                    cv2.imwrite(dst, fr)
                    continue
            if is_matte:
                m = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                rgba = np.zeros((h, w, 4), np.uint8)
                rgba[..., :3] = _bgr(layer.get("color") or "#ffffff")
                rgba[..., 3] = m
                cv2.imwrite(dst, rgba)
            else:
                cv2.imwrite(dst, fr)
            written += 1
        if (needs == "first_frame" or continuity) and got and ci + 1 < len(starts):
            import folder_paths
            nxt = starts[ci + 1] - start          # the next chunk's first frame, already generated by this chunk
            ref_fr = cv2.imread(os.path.join(out_dir, f"frame_{r0 + start + nxt:04d}.png")) if 0 <= nxt < len(got) else None
            chunk_ref = f"keshiki_ref_{job_id}_c{ci + 1}.png"
            cv2.imwrite(os.path.join(folder_paths.get_input_directory(), chunk_ref), ref_fr if ref_fr is not None else got[-1])
    if src_layer:
        shutil.rmtree(src_dir, ignore_errors=True)
    if src2_dir:
        shutil.rmtree(src2_dir, ignore_errors=True)
    return {"layer": layer, "frames": written}


# --- SAM2 video tracking of one object from clicks ------------------------------
_SAM2_VIDEO = None


def _get_sam2_video_model():
    global _SAM2_VIDEO
    if _SAM2_VIDEO is not None:
        return _SAM2_VIDEO
    import importlib
    import sys
    import torch
    import folder_paths
    comfy_dir = getattr(folder_paths, "base_path", None) or os.path.dirname(folder_paths.models_dir)
    if comfy_dir not in sys.path:
        sys.path.insert(0, comfy_dir)
    mod = importlib.import_module("custom_nodes.ComfyUI-segment-anything-2.load_model")
    model_path = os.path.join(folder_paths.models_dir, "sam2", "sam2_hiera_base_plus.safetensors")
    cfg = os.path.join(comfy_dir, "custom_nodes", "ComfyUI-segment-anything-2", "sam2_configs", "sam2_hiera_b+.yaml")
    if not os.path.isfile(model_path):
        cands = [f for f in os.listdir(os.path.join(folder_paths.models_dir, "sam2")) if f.endswith((".safetensors", ".pt"))]
        if not cands:
            raise ProcessError("No SAM2 weights in models/sam2")
        model_path = os.path.join(folder_paths.models_dir, "sam2", cands[0])
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if (device == "cuda" and torch.cuda.is_bf16_supported()) else (torch.float16 if device == "cuda" else torch.float32)
    _SAM2_VIDEO = (mod.load_model(model_path, cfg, segmentor="video", dtype=dtype, device=device), device, dtype)
    return _SAM2_VIDEO


def _job_track_object(job_id, kind, pid, vid, p):
    import torch
    info = _require_plate(vid)
    total = int(info["total_frames"])
    src_dir = _source_frames_dir(vid)
    points = p.get("points") or []
    if not points:
        raise ProcessError("Click the object first")
    frame_idx = int(p.get("frame", 0))
    start = int(p.get("start", 0))
    end = int(p.get("end", total - 1))
    end = max(start, min(total - 1, end))
    frame_idx = max(start, min(end, frame_idx))
    with _GPU_LOCK:
        _free_comfy_vram()
        model, device, dtype = _get_sam2_video_model()
        size = model.image_size
        _job_update(job_id, message=f"Loading frames {start}–{end}…", progress=5)
        frames = []
        H = W = None
        for i in range(start, end + 1):
            im = cv2.imread(os.path.join(src_dir, f"frame_{i:04d}.jpg"))
            if im is None:
                raise ProcessError(f"Missing frame {i}")
            H, W = im.shape[:2]
            im = cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2RGB), (size, size), interpolation=cv2.INTER_LINEAR)
            frames.append(torch.from_numpy(im).to(torch.float16) / 255.0)
        images = torch.stack(frames).permute(0, 3, 1, 2).contiguous()
        coords = np.array([[float(q["x"]), float(q["y"])] for q in points], dtype=np.float32)
        labels = np.array([1 if q.get("positive", True) else 0 for q in points], dtype=np.int32)
        with torch.inference_mode(), torch.autocast(device, dtype=dtype):
            state = model.init_state(images, H, W, device=device, offload_video_to_cpu=True, offload_state_to_cpu=False)
            model.add_new_points_or_box(inference_state=state, frame_idx=frame_idx - start, obj_id=1, points=coords, labels=labels, clear_old_points=True)
            masks: Dict[int, np.ndarray] = {}
            n = end - start + 1
            for direction in (False, True):
                for out_idx, _ids, logits in model.propagate_in_video(state, start_frame_idx=frame_idx - start, reverse=direction):
                    if _cancelled(job_id):
                        raise ProcessError("Stopped")
                    masks[out_idx] = (logits[0] > 0.0).cpu().numpy()[0]
                    _job_update(job_id, message=f"Tracking object… {len(masks)}/{n} frames", progress=int(10 + 85 * len(masks) / n))
            model.reset_state(state)
        del images
        torch.cuda.empty_cache()
    layer = _add_layer(pid, vid, "mask", p.get("name") or "Object", color=p.get("color") or "#f43f5e",
                       extra={"tracked_from": {"frame": frame_idx, "points": points}})
    out_dir = _layer_dir(pid, vid, layer["id"])
    for i, m in masks.items():
        m8 = (m.astype(np.uint8) * 255)
        if m8.shape != (H, W):
            m8 = cv2.resize(m8, (W, H), interpolation=cv2.INTER_NEAREST)
        rgba = np.zeros((H, W, 4), np.uint8)
        rgba[..., :3] = _bgr(layer["color"])
        rgba[..., 3] = m8
        cv2.imwrite(os.path.join(out_dir, f"frame_{start + i:04d}.png"), rgba)
    return {"layer": layer, "frames": len(masks)}


def tracking_readiness() -> Dict[str, Any]:
    """What the GLOMAP tracker needs and what is missing (python packages + the two executables)."""
    import importlib.util
    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    colmap_cands = [os.environ.get("VFX_COLMAP", ""), os.path.join(tools, "colmap", "bin", "colmap.exe"),
                    os.path.join(tools, "colmap", "COLMAP.bat"), r"C:\COLMAP\bin\colmap.exe", r"C:\COLMAP\COLMAP.bat",
                    shutil.which("colmap") or ""]
    colmap = next((c for c in colmap_cands if c and os.path.isfile(c)), "")
    glomap = os.path.join(tools, "glomap", "bin", "glomap.exe")
    pk = {m: importlib.util.find_spec(m) is not None for m in ("pycolmap", "open3d", "ultralytics", "scipy")}
    missing = [m for m, ok in pk.items() if not ok]
    if not colmap:
        missing.append("COLMAP (colmap.exe)")
    glomap_cands = [os.environ.get("VFX_GLOMAP", ""), glomap, shutil.which("glomap") or ""]
    glomap = next((c for c in glomap_cands if c and os.path.isfile(c)), glomap)
    if not os.path.isfile(glomap):
        missing.append("GLOMAP 1.2 (tools/glomap/bin/glomap.exe - run install.bat)")
    import sys as _sys
    return {"ready": not missing, "missing": missing, "packages": pk, "colmap": colmap, "glomap": glomap if os.path.isfile(glomap) else "",
            "python": _sys.executable}


def _job_track_camera(job_id, kind, pid, vid, p):
    """GLOMAP camera solve (vfx_nodes/tracker_node.CameraTrackerNode: COLMAP SIFT + sequential matching + GLOMAP mapper)
    on the plate's In–Out range. Moving objects are excluded with the chosen mask layers (or YOLO); the solved camera
    and point cloud are saved as the project's 3D scene (passes/scene_data.json) and next to the video."""
    import torch
    rd = tracking_readiness()
    if not rd["ready"]:
        raise ProcessError("Tracking is not installed yet, missing: " + ", ".join(rd["missing"]))
    info = _require_plate(vid)
    d = pps.get_dirs(node_id_for(vid))
    src = d["undist"] if _count_frames(d.get("undist", "")) else d["raw"]
    names = sorted(f for f in os.listdir(src) if f.startswith("frame_"))
    r0 = max(0, int(p.get("start", 0) or 0))
    r1 = min(len(names) - 1, int(p.get("end", len(names) - 1)))
    names = names[r0:r1 + 1]
    if len(names) < 3:
        raise ProcessError("Camera tracking needs at least 3 frames")
    tmp = os.path.join(video_root(pid, vid), "track", "_frames")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    for i, n in enumerate(names):
        shutil.copyfile(os.path.join(src, n), os.path.join(tmp, f"frame_{i:04d}.jpg"))
    # dynamic-object mask: union of the chosen layers, quarter size (the tracker scales it back)
    mask_ids = [_safe(x) for x in (p.get("mask_layers") or []) if x]
    mode = p.get("mask_mode") or ("layers" if mask_ids else "yolo")
    tracking_mask = None
    if mode == "layers" and mask_ids:
        W, H = int(info.get("width", 1920)) // 4, int(info.get("height", 1080)) // 4
        arr = np.zeros((len(names), H, W), np.float16)
        for i in range(len(names)):
            acc = np.zeros((H, W), np.uint8)
            for lid in mask_ids:
                fp = frame_path(pid, vid, lid, r0 + i)
                im = cv2.imread(fp, cv2.IMREAD_UNCHANGED) if fp else None
                if im is None:
                    continue
                a = im[..., 3] if im.ndim == 3 and im.shape[2] == 4 else (cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) if im.ndim == 3 else im)
                acc = np.maximum(acc, cv2.resize(a, (W, H), interpolation=cv2.INTER_NEAREST))
            grow = int(p.get("mask_grow", 8)) // 4
            if grow > 0:
                acc = cv2.dilate(acc, np.ones((2 * grow + 1, 2 * grow + 1), np.uint8))
            arr[i] = acc.astype(np.float16) / 255.0
        tracking_mask = torch.from_numpy(arr)
    mask_mode = {"layers": "Exclude Masked (Dynamic Objects)", "yolo": "Auto YOLO Detect", "none": "Bypass Mask (Full Frame)"}.get(mode, "Auto YOLO Detect")
    density = {"draft": "Draft (2048 pts)", "standard": "Standard (4096 pts)", "dense": "Dense (8192 pts)", "ultra": "Ultra (16384 pts)"}.get(p.get("density", "standard"), "Standard (4096 pts)")
    _job_update(job_id, message=f"Tracking {len(names)} frames: COLMAP features → matching → GLOMAP (this takes a while, no per-step progress)", progress=10)
    try:
        from .tracker_node import CameraTrackerNode
    except ImportError:  # pragma: no cover
        from tracker_node import CameraTrackerNode
    with _GPU_LOCK:
        _free_comfy_vram()
        _, cam, pts, _, report = CameraTrackerNode().track_camera(
            point_cloud_density=density,
            focal_length_mode="Fixed / Known Focal" if p.get("focal_fixed") else "Auto Estimate (Refined)",
            focal_length_mm=float(p.get("focal_mm", 28.0) or 28.0),
            mask_mode=mask_mode, camera_height_m=float(p.get("camera_height", 1.55) or 1.55),
            smooth_camera=bool(p.get("smooth", False)), tracking_mask=tracking_mask, input_path=tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    if "Static camera fallback" in str(report):
        raise ProcessError("Tracking failed: GLOMAP could not solve the camera (not enough parallax / features, or COLMAP/GLOMAP not found)")
    cam = dict(cam or {})
    cam["fps"] = float(info.get("fps") or cam.get("fps") or 24)
    cam["start_frame"] = r0
    scene = {"camera": cam, "points": pts.get("points", []), "colors": pts.get("colors", []), "total_frames": cam.get("total_frames", len(names)),
             "has_cloud": bool(pts.get("points")), "source_video": vid, "report": report}
    for dst in (os.path.join(get_project_dir(pid), "passes", "scene_data.json"), os.path.join(video_root(pid, vid), "track", "scene_data.json")):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(scene, f)
    return {"report": report, "frames": len(names), "points": len(scene["points"]), "focal_mm": cam.get("focal_mm")}


def _ck_progress(job_id, n, t):
    if _cancelled(job_id):  # the Stop button: abort between frames
        raise ProcessError("Stopped")
    _job_update(job_id, message=f"CorridorKey: frame {n + 1}/{t}", progress=15 + int(80 * n / max(1, t)))


def _ck_run(p, vid, mask_frames, job_id):
    with _GPU_LOCK:
        _free_comfy_vram()
        pps.apply_corridor_key(
            despill_strength=float(p.get("despill", 1.0)), refiner_strength=float(p.get("refiner", 1.0)),
            auto_despeckle="On", despeckle_size=int(p.get("despeckle", 400)), node_id=node_id_for(vid),
            frames=mask_frames, progress_cb=lambda n, t: _ck_progress(job_id, n, t))


def _job_corridor_key(job_id, kind, pid, vid, p):
    """CorridorKey refines a rough mask layer into a clean matte + premultiplied foreground."""
    _require_plate(vid)
    src_lid = p.get("mask_layer_id")
    if not src_lid:
        raise ProcessError("Choose a mask layer as the rough key")
    d = pps.get_dirs(node_id_for(vid))
    src_dir = _layer_dir(pid, vid, src_lid)
    if not _count_frames(src_dir):
        raise ProcessError("The chosen mask layer has no frames")
    for key in ("finalmask", "cutout"):
        for fn in os.listdir(d[key]):
            os.remove(os.path.join(d[key], fn))
    mask_frames = []
    for fn in sorted(os.listdir(src_dir)):
        if fn.startswith("frame_"):
            try:
                mask_frames.append(int(fn[6:10]))
            except ValueError:
                pass
    # Step 1 — draft: CorridorKey is trained on a coarse alpha hint ("rough, blurry, slightly eroded"),
    # so a sharp SAM2 / hand mask is eroded and blurred first. The draft is kept as its own (hidden) layer.
    erode = max(0, int(p.get("hint_erode", 3)))
    blur = max(0, int(p.get("hint_blur", 8)))
    src_name = next((l.get("name") for l in load_manifest(pid, vid)["layers"] if l["id"] == src_lid), src_lid)
    hint = _add_layer(pid, vid, "mask", f"{src_name} · черновая маска (CorridorKey)", color="#fbbf24",
                      extra={"derived_from": src_lid, "process": "corridor_hint", "visible": False, "hint": {"erode": erode, "blur": blur}})
    hint_dir = _layer_dir(pid, vid, hint["id"])
    k_er = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode + 1, 2 * erode + 1)) if erode else None
    _job_update(job_id, message="CorridorKey: draft mask (erode + blur)…", progress=5)
    for fn in sorted(os.listdir(src_dir)):
        if not fn.startswith("frame_"):
            continue
        rgba = cv2.imread(os.path.join(src_dir, fn), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            continue
        alpha = rgba[..., 3] if rgba.ndim == 3 and rgba.shape[2] == 4 else (cv2.cvtColor(rgba, cv2.COLOR_BGR2GRAY) if rgba.ndim == 3 else rgba)
        if k_er is not None:
            alpha = cv2.erode(alpha, k_er)
        if blur:
            alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=blur / 2.0)
        cv2.imwrite(os.path.join(d["finalmask"], fn), alpha)
        tint = np.zeros((alpha.shape[0], alpha.shape[1], 4), np.uint8)
        tint[..., :3] = _bgr(hint["color"])
        tint[..., 3] = alpha
        cv2.imwrite(os.path.join(hint_dir, os.path.splitext(fn)[0] + ".png"), tint)
    _job_update(job_id, message="CorridorKey: refining the matte…", progress=15)
    try:
        _ck_run(p, vid, mask_frames, job_id)
    except Exception:
        man = load_manifest(pid, vid)  # stopped or failed: drop the half-made draft layer
        man["layers"] = [l for l in man["layers"] if l["id"] != hint["id"]]
        save_manifest(pid, vid, man)
        shutil.rmtree(hint_dir, ignore_errors=True)
        raise
    matte = _add_layer(pid, vid, "matte", f"{src_name} · CorridorKey matte", color="#a3e635", extra={"derived_from": src_lid, "process": "corridor_key"})
    _write_tinted_masks(d["finalmask"], _layer_dir(pid, vid, matte["id"]), matte["color"])
    fg = _add_layer(pid, vid, "alpha", f"{src_name} · CorridorKey foreground", extra={"derived_from": src_lid, "process": "corridor_key"})
    for fn in sorted(os.listdir(d["cutout"])):
        shutil.copy2(os.path.join(d["cutout"], fn), os.path.join(_layer_dir(pid, vid, fg["id"]), fn))
    return {"layers": [hint, matte, fg]}
