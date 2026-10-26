"""
VFX Harmonizer & Compositor Node
================================
Seamless Multi-Layer Compositing:
Blends Foreground Subject (from Alpha-Gen) over Generated Background (from Layout-To-Render)
with Edge Feathering, Light Wrapping, and Color Temperature Harmonization.
Supports both video and legacy image/mask pin names for 100% backwards compatibility.
"""

import torch
import numpy as np
import cv2

class VFXCompositorNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "edge_feather_px": ("FLOAT", {"default": 2.0, "min": 0.0, "max": 20.0, "step": 0.5, "tooltip": "Edge softness for subpixel hair and motion blur"}),
                "light_wrap_strength": ("FLOAT", {"default": 0.20, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "Bleed background rim lighting into foreground edges"}),
                "color_harmonize": ("BOOLEAN", {"default": True, "tooltip": "Harmonize foreground tones to match background lighting"}),
            },
            "optional": {
                "background_video": ("IMAGE", {"tooltip": "Generated environment from LTX Layout-To-Render"}),
                "foreground_video": ("IMAGE", {"tooltip": "Original camera subject"}),
                "alpha_matte": ("MASK", {"tooltip": "Sub-pixel alpha matte from LTX Alpha-Gen"}),
                "background_image": ("IMAGE", {"tooltip": "Legacy alias for background_video"}),
                "foreground_image": ("IMAGE", {"tooltip": "Legacy alias for foreground_video"}),
                "foreground_mask": ("MASK", {"tooltip": "Legacy alias for alpha_matte"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("composite_video", "combined_mask")
    FUNCTION = "composite_layers"
    CATEGORY = "VFX Studio/Compositing"

    def composite_layers(self, edge_feather_px, light_wrap_strength, color_harmonize,
                         background_video=None, foreground_video=None, alpha_matte=None,
                         background_image=None, foreground_image=None, foreground_mask=None):
        # Resolve inputs with legacy backwards-compatibility fallback
        bg_tensor = background_video if background_video is not None else background_image
        fg_tensor = foreground_video if foreground_video is not None else foreground_image
        alpha_tensor = alpha_matte if alpha_matte is not None else foreground_mask

        if bg_tensor is None:
            raise ValueError("VFXCompositor: Missing background input! Connect background_video or background_image.")
        if fg_tensor is None:
            raise ValueError("VFXCompositor: Missing foreground input! Connect foreground_video or foreground_image.")
        if alpha_tensor is None:
            alpha_tensor = torch.ones((fg_tensor.shape[0], fg_tensor.shape[1], fg_tensor.shape[2]), dtype=torch.float32)

        # Convert tensors: [B, H, W, C]
        bg_np = bg_tensor.cpu().numpy()
        fg_np = fg_tensor.cpu().numpy()
        alpha_np = alpha_tensor.cpu().numpy()

        num_frames = min(bg_np.shape[0], fg_np.shape[0])
        h, w = bg_np.shape[1], bg_np.shape[2]

        out_frames = np.zeros((num_frames, h, w, 3), dtype=np.float32)
        out_masks = np.zeros((num_frames, h, w), dtype=np.float32)

        for i in range(num_frames):
            bg = bg_np[i]
            fg = fg_np[i]
            a = alpha_np[i] if i < alpha_np.shape[0] else alpha_np[-1]

            # Match sizes if needed
            if fg.shape[:2] != (h, w):
                fg = cv2.resize(fg, (w, h), interpolation=cv2.INTER_LINEAR)
            if a.shape[:2] != (h, w):
                a = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)

            # Feather edges
            if edge_feather_px > 0:
                k_size = int(edge_feather_px * 2) | 1
                a = cv2.GaussianBlur(a, (k_size, k_size), edge_feather_px)

            a_3d = np.clip(a[:, :, None], 0.0, 1.0)

            # Color harmonization (Reinhard tone transfer)
            if color_harmonize:
                bg_mean, bg_std = bg.mean(axis=(0, 1)), bg.std(axis=(0, 1)) + 1e-5
                fg_mean, fg_std = fg.mean(axis=(0, 1)), fg.std(axis=(0, 1)) + 1e-5
                fg_norm = (fg - fg_mean) / fg_std
                fg_harmonized = np.clip(fg_norm * bg_std + bg_mean, 0.0, 1.0)
                fg = (fg * 0.7) + (fg_harmonized * 0.3)

            # Light Wrap: Bleed blurred background into edges of foreground
            if light_wrap_strength > 0:
                bg_blurred = cv2.GaussianBlur(bg, (31, 31), 10.0)
                edge_mask = np.clip(cv2.Laplacian(a, cv2.CV_32F), 0.0, 1.0)[:, :, None]
                fg = np.clip(fg + (bg_blurred * edge_mask * light_wrap_strength), 0.0, 1.0)

            # Alpha over blending
            comp = (fg * a_3d) + (bg * (1.0 - a_3d))
            out_frames[i] = np.clip(comp, 0.0, 1.0)
            out_masks[i] = a

        comp_tensor = torch.from_numpy(out_frames).float()
        mask_tensor = torch.from_numpy(out_masks).float()

        return (comp_tensor, mask_tensor)
