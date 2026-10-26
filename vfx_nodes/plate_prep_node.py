"""
VFX Plate Prep Node: Lens Undistortion & AI/Manual Masking
===========================================================
Modular first step of the VFX matchmoving pipeline.
All footage ingestion, lens undistortion calibration, and AI/manual mask creation
are managed interactively inside Plate Prep Studio.
The node face provides a live preview card and outputs tensors to downstream nodes:
- undistorted_plate (IMAGE)
- tracking_mask (MASK)
- rough_actor_matte (MASK)
- prep_report (STRING)
"""

import os
import sys
import cv2
import numpy as np
import torch

class VFXPlatePrepNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {},
            "optional": {},
            "hidden": {
                "unique_id": "UNIQUE_ID"
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "MASK", "STRING")
    RETURN_NAMES = ("undistorted_plate", "tracking_mask", "rough_actor_matte", "prep_report")
    FUNCTION = "prepare_plate"
    CATEGORY = "VFX Studio/Prep"

    @classmethod
    def IS_CHANGED(s, unique_id="default", **kwargs):
        try:
            from . import plate_prep_service as pps
        except (ImportError, ValueError):
            import plate_prep_service as pps

        node_id = str(unique_id or "default")
        d = pps.get_dirs(node_id)
        info_path = d.get("info", "")
        if not os.path.exists(info_path) and node_id != "default":
            d_def = pps.get_dirs("default")
            if os.path.exists(d_def.get("info", "")):
                info_path = d_def.get("info", "")
                node_id = "default"

        if os.path.exists(info_path):
            mtime = os.path.getmtime(info_path)
            try:
                info = pps.get_info(node_id)
                inv = bool(info.get("invert_mask", False))
                tot = info.get("total_frames", 0)
                k1 = info.get("k1", 0.0)
                th = info.get("thickness", 20)
                eng = info.get("mask_engine", "sam2")
                kfs = len(info.get("keyframes", []))
                return f"{node_id}_{mtime}_{inv}_{tot}_{k1}_{th}_{eng}_{kfs}"
            except Exception:
                return f"{node_id}_{mtime}"
        return float("NaN")

    def prepare_plate(self, unique_id="default", **kwargs):
        try:
            from . import plate_prep_service as pps
        except (ImportError, ValueError):
            import plate_prep_service as pps

        node_id = str(unique_id or "default")
        d = pps.get_dirs(node_id)
        info = pps.get_info(node_id)

        cached_total = info.get("total_frames", 0)
        # Seamless fallback to default cache if specific node_id is empty (for legacy workflows)
        if cached_total == 0 and node_id != "default":
            default_info = pps.get_info("default")
            if default_info.get("total_frames", 0) > 0:
                d = pps.get_dirs("default")
                info = default_info
                cached_total = info.get("total_frames", 0)

        if cached_total == 0 or not info.get("has_undist", False):
            raise ValueError(
                f"VFXPlatePrep [Node {node_id}]: Шот не подготовлен!\n"
                f"Нажмите кнопку '🎞️ Open Plate Prep Studio' на ноде, загрузите видео или сиквенс кадров, "
                f"настройте маски и дисторсию, затем повторите запуск workflow."
            )

        w = info.get("width", 1920)
        h = info.get("height", 1080)
        k1 = info.get("k1", 0.0)
        k2 = info.get("k2", 0.0)

        undist_plate_tensor = np.zeros((cached_total, h, w, 3), dtype=np.float32)
        tracking_mask_tensor = np.zeros((cached_total, h, w), dtype=np.float32)
        actor_matte_tensor = np.zeros((cached_total, h, w), dtype=np.float32)

        for fid in range(cached_total):
            u_path = os.path.join(d["undist"], f"frame_{fid:04d}.jpg")
            if not os.path.exists(u_path):
                u_path = os.path.join(d["raw"], f"frame_{fid:04d}.jpg")
            if os.path.exists(u_path):
                fr_bgr = cv2.imread(u_path)
                if fr_bgr is not None:
                    if fr_bgr.shape[:2] != (h, w):
                        fr_bgr = cv2.resize(fr_bgr, (w, h))
                    undist_plate_tensor[fid] = cv2.cvtColor(fr_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0

            m_path = os.path.join(d["finalmask"], f"frame_{fid:04d}.png")
            if os.path.exists(m_path):
                m_gray = cv2.imread(m_path, cv2.IMREAD_GRAYSCALE)
                if m_gray is not None:
                    if m_gray.ndim == 3:
                        m_gray = m_gray[:, :, 0]
                    if m_gray.shape[:2] != (h, w):
                        m_gray = cv2.resize(m_gray, (w, h), interpolation=cv2.INTER_NEAREST)
                    if m_gray.ndim == 3:
                        m_gray = m_gray[:, :, 0]
                    tracking_mask_tensor[fid] = m_gray.astype(np.float32) / 255.0

            c_path = os.path.join(d["cutout"], f"frame_{fid:04d}.png")
            if os.path.exists(c_path):
                c_img = cv2.imread(c_path, cv2.IMREAD_UNCHANGED)
                if c_img is not None and c_img.ndim == 3 and c_img.shape[2] == 4:
                    matte = c_img[:, :, 3]
                    if matte.ndim == 3:
                        matte = matte[:, :, 0]
                    if matte.shape[:2] != (h, w):
                        matte = cv2.resize(matte, (w, h), interpolation=cv2.INTER_NEAREST)
                    if matte.ndim == 3:
                        matte = matte[:, :, 0]
                    actor_matte_tensor[fid] = matte.astype(np.float32) / 255.0
                else:
                    actor_matte_tensor[fid] = tracking_mask_tensor[fid]
            else:
                actor_matte_tensor[fid] = tracking_mask_tensor[fid]

        invert = bool(info.get("invert_mask", False))
        if invert:
            tracking_mask_tensor = 1.0 - tracking_mask_tensor
            actor_matte_tensor = 1.0 - actor_matte_tensor

        plate_out = torch.from_numpy(undist_plate_tensor)
        track_mask_out = torch.from_numpy(tracking_mask_tensor)
        actor_matte_out = torch.from_numpy(actor_matte_tensor)
        report = (
            f"VFX Plate Prep [Node {node_id}]: {cached_total} frames ({w}x{h}), "
            f"Lens k1={k1:.3f}, k2={k2:.3f}, InvertMask={invert}. Studio cache loaded successfully."
        )

        return (plate_out, track_mask_out, actor_matte_out, report)
