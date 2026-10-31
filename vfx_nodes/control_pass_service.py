# -*- coding: utf-8 -*-
"""
Control-pass cleanup jobs (2D video layers)
===========================================
Clean the control passes rendered from the 3D scene before they drive LTX IC-LoRAs.
Lessons from the "sci-fi bridge" demo shot:

* ``pose_cleanup``   - a skeleton render (Mixamo / FBX mannequin) often contains the root bone drawn from the
  hips to the world origin. The model reads it as an extra limb and draws a second character. Long straight
  bone segments (longer than ``max_bone_px``) are removed; the result is an OpenPose-style pose layer on black.
* ``layout_cleanup`` - the layout pass must not contain the actor or the skeleton, otherwise layout-to-render
  generates a person there. The actor (matte layer + optional dark cut-out plane) and the skeleton are
  painted over with the row's background colour; bright set pieces (e.g. a white console box) are protected.
  Also writes a first-frame guide (clean layout + clean pose at the first frame) for FLUX.3 Klein Edit.

Sources accept a layer id of the same video, ``other_video_id/layer_id``, an absolute folder with
``frame_0000.png`` ..., or ``layout_render`` (output_cache/layout_renders, the 3D layout render).
Both jobs are registered in ``video_layers_service.start_job``.
"""

import os
from typing import Any, Dict, Optional

import cv2
import numpy as np

try:
    from . import video_layers_service as vls
    from .process_runner import ProcessError
except ImportError:  # pragma: no cover
    import video_layers_service as vls
    from process_runner import ProcessError

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYOUT_RENDER_DIR = os.path.join(BASE_DIR, "output_cache", "layout_renders")


def _source_dir(pid: str, vid: str, src: str) -> str:
    src = str(src or "").strip()
    if not src:
        raise ProcessError("Source layer is not set")
    if src.lower() in ("layout_render", "3d_layout_render", "layout"):
        return LAYOUT_RENDER_DIR
    if os.path.isabs(src) and os.path.isdir(src):
        return src
    if "/" in src:
        other_vid, lid = src.split("/", 1)
        return vls._layer_dir(pid, other_vid, lid)
    return vls._layer_dir(pid, vid, src)


def _read_frame(d: str, idx: int, w: int, h: int, flags=cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    for name in (f"frame_{idx:04d}.png", f"key_{idx:04d}.png"):
        fp = os.path.join(d, name)
        if os.path.isfile(fp):
            img = cv2.imread(fp, flags)
            if img is None:
                return None
            if img.shape[:2] != (h, w):
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
            return img
    return None


def _frame_range(vid: str, p: Dict[str, Any]):
    info = vls._require_plate(vid)
    total, w, h = int(info["total_frames"]), int(info.get("width", 1920)), int(info.get("height", 1080))
    f0 = max(0, int(p.get("start", 0) or 0))
    f1 = min(total - 1, int(p.get("end", total - 1) if p.get("end") not in (None, "") else total - 1))
    return f0, f1, w, h


def _skeleton_mask(skel: np.ndarray, bg_tol: int = 18) -> np.ndarray:
    """Everything that differs from the render's background colour (median of the image border)."""
    border = np.concatenate([skel[0], skel[-1], skel[:, 0], skel[:, -1]], axis=0)
    bg = np.median(border, axis=0).astype(np.int16)
    diff = np.max(np.abs(skel.astype(np.int16) - bg[None, None, :]), axis=2)
    return (diff > bg_tol).astype(np.uint8) * 255


def clean_pose_frame(skel: np.ndarray, max_bone_px: float = 110.0) -> (np.ndarray, np.ndarray):
    """Returns (pose_bgr_on_black, clean_mask). Removes bone segments longer than max_bone_px."""
    skel_mask = _skeleton_mask(skel)
    b, g, r = (skel[..., i].astype(np.int16) for i in range(3))
    # joints are drawn as yellow dots: cut bones at the joints, measure each segment separately
    joints = ((skel_mask > 0) & (r - b > 40) & (g - b > 30)).astype(np.uint8) * 255
    joints_dil = cv2.dilate(joints, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    bones = cv2.bitwise_and(skel_mask, cv2.bitwise_not(joints_dil))
    num, labels, stats, _ = cv2.connectedComponentsWithStats(bones)
    bad = np.zeros_like(skel_mask)
    for lbl in range(1, num):
        bw, bh = stats[lbl, cv2.CC_STAT_WIDTH], stats[lbl, cv2.CC_STAT_HEIGHT]
        if (bw ** 2 + bh ** 2) ** 0.5 > max_bone_px:
            bad[labels == lbl] = 255
    bad = cv2.dilate(bad, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    clean = cv2.bitwise_or(cv2.bitwise_and(skel_mask, cv2.bitwise_not(bad)), joints)

    thick = cv2.dilate(clean, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    vivid = cv2.dilate(skel, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    pose = np.zeros_like(skel)
    pose[thick > 0] = vivid[thick > 0]
    hsv = cv2.cvtColor(pose, cv2.COLOR_BGR2HSV)
    on = thick > 0
    hsv[..., 1] = np.where(on, np.clip(hsv[..., 1].astype(np.int16) + 100, 0, 255), 0).astype(np.uint8)
    hsv[..., 2] = np.where(on, np.clip(hsv[..., 2].astype(np.int16) + 80, 0, 255), 0).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR), skel_mask


def _job_pose_cleanup(job_id, kind, pid, vid, p):
    """params: source_layer (skeleton render), max_bone_px=110, start, end, name."""
    f0, f1, w, h = _frame_range(vid, p)
    src_dir = _source_dir(pid, vid, p.get("source_layer"))
    max_bone = float(p.get("max_bone_px", 110) or 110)
    layer = vls._add_layer(pid, vid, "pose", p.get("name") or "Pose from 3D (clean)", color="#34d399",
                           extra={"source": "pose_cleanup", "derived_from": p.get("source_layer"), "max_bone_px": max_bone})
    out_dir = vls._layer_dir(pid, vid, layer["id"])
    written = 0
    for i in range(f0, f1 + 1):
        if vls._cancelled(job_id):
            raise ProcessError("Stopped")
        if (i - f0) % 10 == 0:
            vls._job_update(job_id, message=f"Pose cleanup: frame {i} of {f1}", progress=int(100 * (i - f0) / max(1, f1 - f0 + 1)))
        skel = _read_frame(src_dir, i, w, h)
        if skel is None:
            continue
        pose, _ = clean_pose_frame(skel, max_bone)
        cv2.imwrite(os.path.join(out_dir, f"frame_{i:04d}.png"), pose)
        written += 1
    if not written:
        raise ProcessError(f"No frames found in {src_dir}")
    layer["frames"] = written
    return {"layer": layer, "frames": written}


def _matte_alpha(img: Optional[np.ndarray], w: int, h: int) -> np.ndarray:
    if img is None:
        return np.zeros((h, w), np.uint8)
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[..., 3]
    elif img.ndim == 3:
        a = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        a = img
    if a.shape[:2] != (h, w):
        a = cv2.resize(a, (w, h), interpolation=cv2.INTER_NEAREST)
    return a


def _job_layout_cleanup(job_id, kind, pid, vid, p):
    """params: layout_source ('layout_render' or layer), skeleton_source (layer, optional),
    matte_layer (actor matte, optional), pose_layer (clean pose for the first-frame guide, optional),
    erase_dark=True (also erase a dark actor cut-out plane), protect_bright=True (keep white set pieces),
    grow_px=23, start, end, name.
    Result: new rgb layer + `image` = first-frame guide PNG (clean layout + pose at frame `start`)."""
    f0, f1, w, h = _frame_range(vid, p)
    lay_dir = _source_dir(pid, vid, p.get("layout_source") or "layout_render")
    skel_dir = _source_dir(pid, vid, p["skeleton_source"]) if p.get("skeleton_source") else None
    matte_dir = _source_dir(pid, vid, p["matte_layer"]) if p.get("matte_layer") else None
    erase_dark = str(p.get("erase_dark", True)).lower() not in ("0", "false", "no")
    protect_bright = str(p.get("protect_bright", True)).lower() not in ("0", "false", "no")
    grow = max(1, int(p.get("grow_px", 23) or 23))
    k_grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow))

    layer = vls._add_layer(pid, vid, "rgb", p.get("name") or "3D Layout (clean)", color="#38bdf8",
                           extra={"source": "layout_cleanup", "derived_from": p.get("layout_source") or "layout_render"})
    out_dir = vls._layer_dir(pid, vid, layer["id"])
    written = 0
    for i in range(f0, f1 + 1):
        if vls._cancelled(job_id):
            raise ProcessError("Stopped")
        if (i - f0) % 10 == 0:
            vls._job_update(job_id, message=f"Layout cleanup: frame {i} of {f1}", progress=int(100 * (i - f0) / max(1, f1 - f0 + 1)))
        lay = _read_frame(lay_dir, i, w, h)
        if lay is None:
            continue
        erase = np.zeros((h, w), np.uint8)
        if matte_dir:
            erase |= (_matte_alpha(_read_frame(matte_dir, i, w, h, cv2.IMREAD_UNCHANGED), w, h) > 0).astype(np.uint8) * 255
        if erase_dark:
            erase |= ((lay[..., 0] < 108) & (lay[..., 1] < 108) & (lay[..., 2] < 115)).astype(np.uint8) * 255
        if skel_dir:
            skel = _read_frame(skel_dir, i, w, h)
            if skel is not None:
                erase |= _skeleton_mask(skel)
        keep = ((lay[..., 0] > 128) & (lay[..., 1] > 128) & (lay[..., 2] > 128)) if protect_bright else np.zeros((h, w), bool)
        erase[keep] = 0
        erase = cv2.dilate(erase, k_grow)
        erase[keep] = 0
        out = lay.copy()
        ys = np.unique(np.where(erase > 0)[0])
        x0, x1 = min(20, w - 1), max(min(150, w), min(20, w - 1) + 1)
        for y in ys:   # paint with the row's background colour from the left edge (floor / wall)
            out[y, erase[y] > 0] = np.median(lay[y, x0:x1, :], axis=0).astype(np.uint8)
        cv2.imwrite(os.path.join(out_dir, f"frame_{i:04d}.png"), out)
        written += 1
    if not written:
        raise ProcessError(f"No layout frames found in {lay_dir}")
    layer["frames"] = written

    res: Dict[str, Any] = {"layer": layer, "frames": written}
    # First-frame guide for FLUX.3 Klein Edit: clean layout + clean pose at the first frame
    guide = cv2.imread(os.path.join(out_dir, f"frame_{f0:04d}.png"))
    if guide is not None:
        if p.get("pose_layer"):
            pose = _read_frame(_source_dir(pid, vid, p["pose_layer"]), f0, w, h)
            if pose is not None:
                on = pose.max(axis=2) > 30
                guide[on] = pose[on]
        gdir = os.path.join(vls.video_root(pid, vid), "guides")
        os.makedirs(gdir, exist_ok=True)
        gpath = os.path.join(gdir, f"{layer['id']}_first_frame_guide.png")
        cv2.imwrite(gpath, guide)
        res["image"] = gpath
    return res


JOBS = {"pose_cleanup": _job_pose_cleanup, "layout_cleanup": _job_layout_cleanup}
