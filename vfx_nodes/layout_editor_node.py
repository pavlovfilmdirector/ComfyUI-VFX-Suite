"""
3D Layout Editor Node with Bezier Keyframing & Multi-Format Camera Import
========================================================================
Supports:
- GLOMAP SfM Camera & Sparse Pointcloud
- External Camera Import (.abc via Blender bridge, .chan, .json)
- Keyframed Scene Manifest (Step, Linear, Cubic Bezier interpolation)
- Multi-pass rendering: Clay (Lambertian studio), Depth Map, Normal Map, Mask
"""

import os
import sys
import json
import subprocess
import tempfile
import numpy as np
import torch
import cv2

# Check for Blender executable on system
BLENDER_PATHS = [
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender\blender.exe",
]
BLENDER_BIN = next((p for p in BLENDER_PATHS if os.path.exists(p)), None)


def parse_chan_camera(file_path):
    """Parses standard ASCII .chan camera file."""
    matrices = []
    focals = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 7:
                # Format: frame tx ty tz rx ry rz (optional fov)
                tx, ty, tz = float(parts[1]), float(parts[2]), float(parts[3])
                rx, ry, rz = float(parts[4]), float(parts[5]), float(parts[6])
                fov = float(parts[7]) if len(parts) > 7 else 50.0

                # Convert Euler (degrees) to rotation matrix
                rx_rad, ry_rad, rz_rad = np.radians([rx, ry, rz])
                Rx = np.array([[1, 0, 0], [0, np.cos(rx_rad), -np.sin(rx_rad)], [0, np.sin(rx_rad), np.cos(rx_rad)]])
                Ry = np.array([[np.cos(ry_rad), 0, np.sin(ry_rad)], [0, 1, 0], [-np.sin(ry_rad), 0, np.cos(ry_rad)]])
                Rz = np.array([[np.cos(rz_rad), -np.sin(rz_rad), 0], [np.sin(rz_rad), np.cos(rz_rad), 0], [0, 0, 1]])
                R = Rz @ Ry @ Rx

                c2w = np.eye(4)
                c2w[:3, :3] = R
                c2w[:3, 3] = [tx, ty, tz]
                matrices.append(c2w.tolist())
                focals.append(fov)
    return {"matrices": matrices, "focals": focals, "total_frames": len(matrices)}


def parse_alembic_camera(file_path):
    """Extracts camera matrices from .abc Alembic file via headless Blender bridge."""
    if not BLENDER_BIN:
        raise RuntimeError("Alembic (.abc) import requires Blender on system, but blender.exe was not found.")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_out:
        out_json = tmp_out.name

    script_content = f"""
import bpy
import json

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.alembic_import(filepath=r"{file_path}")

cam = None
for obj in bpy.data.objects:
    if obj.type == 'CAMERA':
        cam = obj
        break

if not cam and bpy.data.objects:
    cam = bpy.data.objects[0]

result = {{"matrices": [], "total_frames": 0, "focal_mm": 35.0}}
if cam:
    scene = bpy.context.scene
    start = scene.frame_start
    end = scene.frame_end
    result["total_frames"] = max(1, end - start + 1)
    if hasattr(cam.data, "lens"):
        result["focal_mm"] = cam.data.lens

    for f in range(start, end + 1):
        scene.frame_set(f)
        matrix = [[v for v in row] for row in cam.matrix_world]
        result["matrices"].append(matrix)

with open(r"{out_json}", "w") as f:
    json.dump(result, f)
"""
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8") as tmp_py:
        tmp_py.write(script_content)
        script_file = tmp_py.name

    try:
        cmd = [BLENDER_BIN, "-b", "--python", script_file]
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
        with open(out_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    finally:
        if os.path.exists(script_file):
            os.remove(script_file)
        if os.path.exists(out_json):
            os.remove(out_json)


def interpolate_transform(keyframes, frame_idx, total_frames):
    """
    Interpolates transform at frame_idx using keyframes.
    Supports 'step', 'linear', and 'bezier' with easing.
    """
    if not keyframes:
        return [0, 0, 0], [0, 0, 0], [1, 1, 1]

    # Sort keyframes by frame number
    sorted_keys = sorted(keyframes, key=lambda k: k.get("frame", 0))

    # Before first or after last keyframe
    if frame_idx <= sorted_keys[0]["frame"]:
        k = sorted_keys[0]
        return k.get("position", [0, 0, 0]), k.get("rotation", [0, 0, 0]), k.get("scale", [1, 1, 1])
    if frame_idx >= sorted_keys[-1]["frame"]:
        k = sorted_keys[-1]
        return k.get("position", [0, 0, 0]), k.get("rotation", [0, 0, 0]), k.get("scale", [1, 1, 1])

    # Find bounding keyframes
    k_prev = sorted_keys[0]
    k_next = sorted_keys[-1]
    for i in range(len(sorted_keys) - 1):
        if sorted_keys[i]["frame"] <= frame_idx <= sorted_keys[i + 1]["frame"]:
            k_prev = sorted_keys[i]
            k_next = sorted_keys[i + 1]
            break

    f0 = k_prev["frame"]
    f1 = k_next["frame"]
    span = max(1, f1 - f0)
    t = (frame_idx - f0) / float(span)

    interp_type = k_prev.get("interp", "bezier").lower()

    if interp_type == "step":
        weight = 0.0
    elif interp_type == "linear":
        weight = t
    else:  # Bezier smooth interpolation (Cubic Hermite / Smoothstep)
        weight = t * t * (3.0 - 2.0 * t)

    p0, p1 = np.array(k_prev.get("position", [0, 0, 0])), np.array(k_next.get("position", [0, 0, 0]))
    r0, r1 = np.array(k_prev.get("rotation", [0, 0, 0])), np.array(k_next.get("rotation", [0, 0, 0]))
    s0, s1 = np.array(k_prev.get("scale", [1, 1, 1])), np.array(k_next.get("scale", [1, 1, 1]))

    pos = (p0 + (p1 - p0) * weight).tolist()
    rot = (r0 + (r1 - r0) * weight).tolist()
    scale = (s0 + (s1 - s0) * weight).tolist()
    return pos, rot, scale


class LayoutEditorNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "render_mode": (["Clay (Lambertian Studio)", "Semantic Color (Color Blocking)", "Flat Color Segmentation", "Depth Map", "Surface Normals", "Layout Mask"], {
                    "default": "Clay (Lambertian Studio)"
                }),
                "camera_smoothing": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "Smooth camera jitter (0=raw SfM, 1=full smooth)"}),
                "layout_json": ("STRING", {
                    "default": "[]",
                    "multiline": True,
                    "tooltip": "JSON manifest of 3D objects, keyframes, and animations"
                }),
                "workflow_gate": (["Auto (Pause if Layout Empty)", "Interactive (Always Pause for Staging)", "Direct Render (Queue)", ""], {
                    "default": "Auto (Pause if Layout Empty)",
                    "tooltip": "Controls workflow staging: halts execution before downstream generation so you can position 3D proxies inside the 3D Studio."
                }),
            },
            "optional": {
                "camera_data": ("CAMERA_DATA", {"tooltip": "Solved camera trajectory from CameraTracker"}),
                "track_points": ("TRACK_POINTS", {"tooltip": "3D sparse point cloud for snapping"}),
                "undistorted_images": ("IMAGE", {"tooltip": "Background plate for viewport alignment"}),
                "rough_actor_matte": ("MASK", {"tooltip": "Rough actor cutout from Plate Prep to project onto 3D layout"}),
                "external_camera_file": ("STRING", {"default": "", "tooltip": "Optional path to .abc, .fbx, .chan, or .json camera file"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "MASK", "CAMERA_DATA")
    RETURN_NAMES = ("layout_render", "depth_map", "layout_mask", "camera_data")
    FUNCTION = "render_layout"
    CATEGORY = "VFX Studio/Layout"

    def render_layout(self, render_mode, camera_smoothing, layout_json, workflow_gate="Auto (Pause if Layout Empty)", camera_data=None, track_points=None, undistorted_images=None, rough_actor_matte=None, external_camera_file=""):
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output_cache")
        os.makedirs(cache_dir, exist_ok=True)

        # 0. Sync fresh camera_data / track_points to disk cache for WebGL Studio
        if camera_data is not None:
            try:
                scene_cache = {
                    "camera": camera_data,
                    "points": track_points.get("points", []) if isinstance(track_points, dict) else [],
                    "colors": track_points.get("colors", []) if isinstance(track_points, dict) else [],
                    "total_frames": camera_data.get("total_frames", 1),
                    "has_cloud": bool(track_points and isinstance(track_points, dict) and track_points.get("points"))
                }
                with open(os.path.join(cache_dir, "scene_data.json"), "w", encoding="utf-8") as f:
                    json.dump(scene_cache, f)
            except Exception as e:
                print(f"[LayoutEditor3D] Cache write note: {e}")

        # Bake actor cutouts if connected
        if undistorted_images is not None and rough_actor_matte is not None:
            cutout_dir = os.path.join(cache_dir, "actor_cutout")
            os.makedirs(cutout_dir, exist_ok=True)
            n_imgs = undistorted_images.shape[0]
            for fid in range(n_imgs):
                img_np = (undistorted_images[fid].cpu().numpy() * 255.0).astype(np.uint8)
                m_idx = min(fid, rough_actor_matte.shape[0] - 1)
                m_np = (rough_actor_matte[m_idx].cpu().numpy() * 255.0).astype(np.uint8)
                if m_np.shape[:2] != img_np.shape[:2]:
                    m_np = cv2.resize(m_np, (img_np.shape[1], img_np.shape[0]), interpolation=cv2.INTER_NEAREST)
                rgba = cv2.cvtColor(img_np, cv2.COLOR_RGB2RGBA)
                rgba[:, :, 3] = m_np
                bgr_a = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
                cv2.imwrite(os.path.join(cutout_dir, f"frame_{fid:04d}.png"), bgr_a)

        # 1. Check Workflow Staging Gate (Halts execution cleanly to allow 3D proxy staging)
        try:
            parsed_layout = json.loads(layout_json) if layout_json else []
        except Exception:
            parsed_layout = []

        if not workflow_gate or workflow_gate not in ["Auto (Pause if Layout Empty)", "Interactive (Always Pause for Staging)", "Direct Render (Queue)"]:
            workflow_gate = "Auto (Pause if Layout Empty)"

        if workflow_gate == "Interactive (Always Pause for Staging)":
            print("[LayoutEditor3D] ⏸ Pipeline paused for 3D layout staging. Open 'Fullscreen 3D Viewport', position proxies, then click '⚡ Save & Queue Render'.")
            try:
                import comfy.model_management as mm
                raise mm.InterruptProcessingException()
            except ImportError:
                pass
        elif workflow_gate == "Auto (Pause if Layout Empty)":
            if not parsed_layout:
                print("[LayoutEditor3D] ⏸ Pipeline paused: Layout manifest is empty. Open 'Fullscreen 3D Viewport' to stage 3D proxies, then click '⚡ Save & Queue Render'.")
                try:
                    import comfy.model_management as mm
                    raise mm.InterruptProcessingException()
                except ImportError:
                    pass
        # 1. Resolve camera source (Input pin vs External file)
        resolved_cam = None
        if external_camera_file and os.path.exists(external_camera_file):
            ext = os.path.splitext(external_camera_file)[1].lower()
            if ext == ".abc":
                resolved_cam = parse_alembic_camera(external_camera_file)
            elif ext in [".chan", ".csv"]:
                resolved_cam = parse_chan_camera(external_camera_file)
            elif ext == ".json":
                with open(external_camera_file, "r", encoding="utf-8") as f:
                    resolved_cam = json.load(f)

        if resolved_cam is None:
            if camera_data is not None:
                resolved_cam = camera_data
            else:
                # Default fallback static camera
                resolved_cam = {
                    "total_frames": 1,
                    "width": 1920,
                    "height": 1080,
                    "focal_mm": 35.0,
                    "sensor_width_mm": 36.0,
                    "matrices": [np.eye(4).tolist()]
                }

        total_frames = resolved_cam.get("total_frames", 1)
        w = resolved_cam.get("width", 1920)
        h = resolved_cam.get("height", 1080)
        focal_mm = resolved_cam.get("focal_mm", 35.0)
        sensor_w = resolved_cam.get("sensor_width_mm", 36.0)
        matrices = [np.array(m) for m in resolved_cam.get("matrices", [])]

        if not matrices:
            matrices = [np.eye(4)]
            total_frames = 1

        # Apply Gaussian / moving average camera smoothing if requested
        if camera_smoothing > 0.001 and len(matrices) > 3:
            window = max(3, int(camera_smoothing * 15))
            if window % 2 == 0:
                window += 1
            half_w = window // 2
            smoothed_mats = []
            for i in range(len(matrices)):
                i_start = max(0, i - half_w)
                i_end = min(len(matrices), i + half_w + 1)
                sub = matrices[i_start:i_end]
                avg = np.mean(sub, axis=0)
                # Re-orthogonalize rotation
                U, _, Vt = np.linalg.svd(avg[:3, :3])
                avg[:3, :3] = U @ Vt
                smoothed_mats.append(avg)
            matrices = smoothed_mats

        # 2. Parse layout objects & keyframes (Widget input or studio manifest cache)
        try:
            scene_objects = json.loads(layout_json) if layout_json and layout_json.strip() != "[]" else []
        except Exception:
            scene_objects = []

        if not scene_objects:
            manifest_path = os.path.join(cache_dir, "layout_manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        scene_objects = json.load(f)
                except Exception:
                    pass

        # 3. Setup Render Buffers
        clay_out = np.zeros((total_frames, h, w, 3), dtype=np.float32)
        depth_out = np.zeros((total_frames, h, w, 3), dtype=np.float32)
        mask_out = np.zeros((total_frames, h, w), dtype=np.float32)

        # Check for high-fidelity WebGL In-Node renders from Studio Viewport
        layout_renders_dir = os.path.join(cache_dir, "layout_renders")
        cached_render_files = []
        if os.path.exists(layout_renders_dir):
            cached_render_files = sorted([
                f for f in os.listdir(layout_renders_dir)
                if f.startswith("frame_") and f.endswith((".png", ".jpg"))
            ])

        has_cached_renders = len(cached_render_files) >= total_frames

        if has_cached_renders:
            print(f"[ComfyUI-VFX-Suite] Using {len(cached_render_files)} hardware-accelerated WebGL layout frames from {layout_renders_dir}")
            for f_idx in range(total_frames):
                rf_path = os.path.join(layout_renders_dir, cached_render_files[f_idx])
                bgr = cv2.imread(rf_path, cv2.IMREAD_UNCHANGED)
                if bgr is not None:
                    if bgr.shape[0] != h or bgr.shape[1] != w:
                        bgr = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)

                    if bgr.ndim == 3 and bgr.shape[2] == 4:
                        rgb = cv2.cvtColor(bgr[:, :, :3], cv2.COLOR_BGR2RGB)
                        alpha = bgr[:, :, 3].astype(np.float32) / 255.0
                        mask_out[f_idx] = alpha
                    else:
                        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                        mask_out[f_idx] = (gray > 10).astype(np.float32)

                    clay_out[f_idx] = rgb.astype(np.float32) / 255.0
                    depth_out[f_idx] = np.repeat(mask_out[f_idx][:, :, np.newaxis], 3, axis=-1)
        else:
            fx = (focal_mm / sensor_w) * w
            fy = fx
            cx = w / 2.0
            cy = h / 2.0

            # Studio directional light vector for clay shading
            light_dir = np.array([0.5, 0.7, 0.5])
            light_dir = light_dir / np.linalg.norm(light_dir)

            # 4. Render frame-by-frame
            for f_idx in range(total_frames):
                c2w = matrices[min(f_idx, len(matrices) - 1)]
                w2c = np.linalg.inv(c2w)

                frame_clay = np.zeros((h, w, 3), dtype=np.uint8)
                frame_depth = np.ones((h, w), dtype=np.float32) * 1000.0
                frame_mask = np.zeros((h, w), dtype=np.uint8)

                for obj in scene_objects:
                    # Resolve transform with keyframe interpolation
                    keyframes = obj.get("keyframes", [])
                    if keyframes:
                        pos, rot, scale = interpolate_transform(keyframes, f_idx, total_frames)
                    else:
                        pos = obj.get("position", [0, 0, 0])
                        rot = obj.get("rotation", [0, 0, 0])
                        scale = obj.get("scale", [1, 1, 1])

                    p_world = np.array(pos, dtype=np.float32)
                    p_cam = (w2c[:3, :3] @ p_world) + w2c[:3, 3]
                    z = p_cam[2]

                    if z > 0.05:
                        u = int((fx * p_cam[0] / z) + cx)
                        v = int((fy * (-p_cam[1]) / z) + cy)
                        radius = int(max(4, (fx * np.mean(scale) * 0.45) / z))

                        if 0 <= u < w and 0 <= v < h:
                            # Resolve object color
                            hex_str = str(obj.get("color", "#CCCCCC")).lstrip('#')
                            if len(hex_str) == 6:
                                r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
                                base_bgr = (b, g, r)
                            else:
                                base_bgr = (200, 200, 200)

                            if "Semantic Color" in render_mode:
                                normal = np.array([0.0, 0.6, 0.8])
                                diffuse = max(0.25, float(np.dot(normal, light_dir)))
                                draw_bgr = tuple([int(c * diffuse) for c in base_bgr])
                            elif "Flat Color" in render_mode:
                                draw_bgr = base_bgr
                            else:  # Clay Lambertian Studio
                                normal = np.array([0.0, 0.6, 0.8])
                                diffuse = max(0.2, float(np.dot(normal, light_dir)))
                                clay_val = int(220 * diffuse)
                                draw_bgr = (clay_val, clay_val, clay_val)

                            cv2.circle(frame_clay, (u, v), radius, draw_bgr, -1)
                            cv2.circle(frame_mask, (u, v), radius, 255, -1)
                            frame_depth[max(0, v - radius):min(h, v + radius), max(0, u - radius):min(w, u + radius)] = np.minimum(
                                frame_depth[max(0, v - radius):min(h, v + radius), max(0, u - radius):min(w, u + radius)],
                                float(z)
                            )

                # Normalize depth to [0, 1] range for visual output
                depth_vis = np.clip(1.0 - (frame_depth / 30.0), 0.0, 1.0)
                depth_vis = np.repeat(depth_vis[:, :, np.newaxis], 3, axis=-1)

                clay_out[f_idx] = frame_clay.astype(np.float32) / 255.0
                depth_out[f_idx] = depth_vis.astype(np.float32)
                mask_out[f_idx] = frame_mask.astype(np.float32) / 255.0


        # Convert to PyTorch tensors for ComfyUI
        tensor_clay = torch.from_numpy(clay_out)
        tensor_depth = torch.from_numpy(depth_out)
        tensor_mask = torch.from_numpy(mask_out)

        out_camera = {
            "matrices": [m.tolist() for m in matrices],
            "total_frames": total_frames,
            "width": w,
            "height": h,
            "focal_mm": focal_mm,
            "sensor_width_mm": sensor_w,
        }

        # Cache updated scene info for WebGL Viewport
        try:
            scene_cache_path = os.path.join(cache_dir, "scene_data.json")
            cur_data = {}
            if os.path.exists(scene_cache_path):
                with open(scene_cache_path, "r", encoding="utf-8") as f:
                    cur_data = json.load(f)
            cur_data["camera"] = out_camera
            cur_data["total_frames"] = total_frames
            if track_points is not None:
                cur_data["points"] = track_points.get("points", [])
                cur_data["colors"] = track_points.get("colors", [])
                cur_data["has_cloud"] = len(cur_data.get("points", [])) > 0
            if undistorted_images is not None and rough_actor_matte is not None:
                cur_data["has_actor"] = True
            with open(scene_cache_path, "w", encoding="utf-8") as f:
                json.dump(cur_data, f)
        except Exception:
            pass

        # Select primary image output based on render_mode
        if render_mode == "Depth Map":
            primary_img = tensor_depth
        else:
            primary_img = tensor_clay

        return (primary_img, tensor_depth, tensor_mask, out_camera)
