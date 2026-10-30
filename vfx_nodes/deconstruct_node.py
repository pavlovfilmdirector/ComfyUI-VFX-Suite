"""
VFX Deconstruct & Nuke Exporter Node
===================================
Exports all project deconstruction items (masks, video layers, 3D camera tracks, passes)
as dynamic output pins directly in the ComfyUI Graph.
Also compiles the complete Foundry Nuke (.nk) script and camera .chan path on execution.
"""

import os
import json
import torch
import numpy as np

try:
    from .deconstruct_service import get_project_deconstruction, generate_nuke_export
    from .project_manager import list_projects, get_project_dir
except ImportError:
    from deconstruct_service import get_project_deconstruction, generate_nuke_export
    from project_manager import list_projects, get_project_dir


class VFXDeconstructNukeNode:
    @classmethod
    def INPUT_TYPES(s):
        try:
            projs = [p.get("id") for p in list_projects()]
        except Exception:
            projs = []
        if not projs:
            projs = ["New_Movie_Shot", "sandbox_playground"]

        return {
            "required": {
                "project_id": (projs, {"default": projs[0] if projs else "New_Movie_Shot"}),
                "auto_generate_nuke_script": ("BOOLEAN", {"default": True, "tooltip": "Automatically compile .nk and .chan script on execution"}),
            },
            "optional": {
                "custom_override_image": ("IMAGE", {"tooltip": "Optional direct image input to pass-through"}),
                "custom_override_mask": ("MASK", {"tooltip": "Optional direct mask input to pass-through"}),
            }
        }

    # 12 outputs covering Images, Masks, Camera data, Nuke script and Project Manifest
    RETURN_TYPES = (
        "IMAGE", "IMAGE", "IMAGE", "IMAGE",
        "MASK", "MASK", "MASK", "MASK",
        "VFX_CAMERA", "STRING", "STRING", "DICT"
    )
    RETURN_NAMES = (
        "pass_1", "pass_2", "pass_3", "pass_4",
        "mask_1", "mask_2", "mask_3", "mask_4",
        "camera_track", "nuke_script_path", "camera_chan_path", "project_manifest"
    )
    FUNCTION = "export_deconstruction"

    FUNCTION = "export_deconstruction"

    def process(self, *args, **kwargs):
        return self.export_deconstruction(*args, **kwargs)
    CATEGORY = "VFX Studio/Deconstruct"

    def export_deconstruction(self, project_id, auto_generate_nuke_script=True, custom_override_image=None, custom_override_mask=None):
        dec_info = get_project_deconstruction(project_id)
        items = dec_info.get("items", [])
        enabled_items = [it for it in items if it.get("enabled", True)]

        nuke_script_path = ""
        chan_path = ""
        if auto_generate_nuke_script:
            try:
                res = generate_nuke_export(project_id, enabled_items)
                nuke_script_path = res.get("nuke_script_path", "")
                chan_path = res.get("chan_path", "")
            except Exception as e:
                print(f"[VFXDeconstructNuke] Nuke export warning: {e}")

        # Placeholders
        dummy_img = custom_override_image if custom_override_image is not None else torch.zeros((1, 64, 64, 3), dtype=torch.float32)
        dummy_mask = custom_override_mask if custom_override_mask is not None else torch.zeros((1, 64, 64), dtype=torch.float32)
        dummy_cam = {"project_id": project_id, "chan_path": chan_path}

        img_passes = [dummy_img, dummy_img, dummy_img, dummy_img]
        mask_passes = [dummy_mask, dummy_mask, dummy_mask, dummy_mask]
        cam_out = dummy_cam

        img_idx = 0
        mask_idx = 0
        for it in enabled_items:
            dtype = it.get("data_type", "IMAGE").upper()
            fp = it.get("file_path") or ""
            if dtype == "MASK":
                if mask_idx < 4:
                    if fp and os.path.exists(fp):
                        try:
                            import cv2
                            m_raw = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
                            if m_raw is not None:
                                mask_passes[mask_idx] = torch.from_numpy(m_raw.astype(np.float32) / 255.0).unsqueeze(0)
                        except Exception:
                            pass
                    mask_idx += 1
            elif dtype in ("VFX_CAMERA", "CAMERA") or it.get("source_type") == "camera_3d":
                cam_out = {
                    "project_id": project_id,
                    "chan_path": chan_path,
                    "meta": it.get("meta", {})
                }
            else:
                if img_idx < 4:
                    if fp and os.path.exists(fp):
                        try:
                            import cv2
                            im_raw = cv2.imread(fp)
                            if im_raw is not None:
                                im_rgb = cv2.cvtColor(im_raw, cv2.COLOR_BGR2RGB)
                                img_passes[img_idx] = torch.from_numpy(im_rgb.astype(np.float32) / 255.0).unsqueeze(0)
                        except Exception:
                            pass
                    img_idx += 1

        return (
            img_passes[0], img_passes[1], img_passes[2], img_passes[3],
            mask_passes[0], mask_passes[1], mask_passes[2], mask_passes[3],
            cam_out, nuke_script_path, chan_path, dec_info
        )
