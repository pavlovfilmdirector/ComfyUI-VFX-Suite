"""
Camera Tracker Node (GLOMAP Global Structure-from-Motion)
==========================================================
Autonomous 3D Camera Tracking with AI Dynamic Object Masking,
Metric Scaling, and Ground Plane Alignment (Z = 0.000 m).
Graceful fallback for single images / static previews.
"""

import os
import sys
import re
import cv2
import time
import shutil
import subprocess
import numpy as np
import torch
import pycolmap
import open3d as o3d
from scipy.spatial.transform import Rotation, Slerp
from scipy.interpolate import PchipInterpolator
import json

# Standard tools locations
TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
def _find_colmap():
    # env override > the build install.py put into tools/ > a system-wide install
    cands = [os.environ.get("VFX_COLMAP", ""),
             os.path.join(TOOLS_DIR, "colmap", "bin", "colmap.exe"), os.path.join(TOOLS_DIR, "colmap", "COLMAP.bat"),
             r"C:\COLMAP\bin\colmap.exe", r"C:\COLMAP\COLMAP.bat",
             shutil.which("colmap") or ""]
    return next((c for c in cands if c and os.path.isfile(c)), r"C:\COLMAP\bin\colmap.exe")


COLMAP_BIN = _find_colmap()
def _find_glomap():
    cands = [os.environ.get("VFX_GLOMAP", ""), os.path.join(TOOLS_DIR, "glomap", "bin", "glomap.exe"),
             shutil.which("glomap") or ""]
    return next((c for c in cands if c and os.path.isfile(c)), cands[1])


# GLOMAP 1.2.0 standalone + COLMAP 3.13 (features/matching) is the combination the tracker's accuracy was tuned on.
GLOMAP_BIN = _find_glomap()

class CameraTrackerNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "point_cloud_density": (["Draft (2048 pts)", "Standard (4096 pts)", "Dense (8192 pts)", "Ultra (16384 pts)", ""], {
                    "default": "Draft (2048 pts)",
                    "tooltip": "Плотность облака точек SfM (число ключевых ориентиров SIFT на кадр)"
                }),
                "focal_length_mode": (["Auto Estimate (Refined)", "Fixed / Known Focal", ""], {
                    "default": "Auto Estimate (Refined)",
                    "tooltip": "Режим объектива: автоматический подбор фокуса с оптимизацией или фиксированный объектив"
                }),
                "focal_length_mm": ("FLOAT", {
                    "default": 28.0, "min": 8.0, "max": 300.0, "step": 0.5,
                    "tooltip": "Фокусное расстояние объектива в эквиваленте 35мм (точное значение для Fixed, либо начальное приближение для Auto)"
                }),
                "mask_mode": (["Exclude Masked (Dynamic Objects)", "Auto YOLO Detect", "Track Subject Only (Inverted)", "Bypass Mask (Full Frame)", ""], {
                    "default": "Exclude Masked (Dynamic Objects)",
                    "tooltip": "Действие маски: исключать динамические объекты (маска из Plate Prep / YOLO), трекать только объект, или отключить маску"
                }),
            },
            "optional": {
                "images": ("IMAGE", {"tooltip": "Входная секвенция кадров из Plate Prep"}),
                "tracking_mask": ("MASK", {"tooltip": "Маска динамических объектов из Plate Prep"}),
                "manual_mask": ("MASK", {"tooltip": "Дополнительная ручная маска из MaskEditor"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "CAMERA_DATA", "TRACK_POINTS", "MASK", "STRING")
    RETURN_NAMES = ("undistorted_images", "camera_data", "track_points", "tracking_mask", "summary_report")
    FUNCTION = "track_camera"
    CATEGORY = "VFX Studio/Tracking"

    @classmethod
    def IS_CHANGED(s, input_path="", **kwargs):
        if input_path and os.path.exists(input_path):
            return str(os.path.getmtime(input_path))
        return ""

    def track_camera(
        self,
        point_cloud_density="Draft (2048 pts)",
        focal_length_mode="Auto Estimate (Refined)",
        focal_length_mm=28.0,
        mask_mode="Exclude Masked (Dynamic Objects)",
        camera_height_m=1.55,
        smooth_camera=False,
        images=None,
        tracking_mask=None,
        manual_mask=None,
        input_path="",
        **kwargs
    ):
        # Backward compatibility and sanitization for older graphs
        if "focal_mm" in kwargs and (focal_length_mm == 28.0 or focal_length_mm is None):
            try:
                v = float(kwargs["focal_mm"])
                if not np.isnan(v) and v > 0:
                    focal_length_mm = v
            except (ValueError, TypeError):
                pass

        try:
            focal_length_mm = float(focal_length_mm)
            if np.isnan(focal_length_mm) or focal_length_mm <= 0:
                focal_length_mm = 28.0
        except (ValueError, TypeError):
            focal_length_mm = 28.0

        if not point_cloud_density or point_cloud_density not in ["Draft (2048 pts)", "Standard (4096 pts)", "Dense (8192 pts)", "Ultra (16384 pts)"]:
            point_cloud_density = "Draft (2048 pts)"

        if not focal_length_mode or focal_length_mode not in ["Auto Estimate (Refined)", "Fixed / Known Focal"]:
            focal_length_mode = "Auto Estimate (Refined)"

        if not mask_mode or mask_mode not in ["Exclude Masked (Dynamic Objects)", "Auto YOLO Detect", "Track Subject Only (Inverted)", "Bypass Mask (Full Frame)"]:
            mask_mode = "Exclude Masked (Dynamic Objects)"

        t_start = time.time()
        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output_cache")
        os.makedirs(output_dir, exist_ok=True)

        img_dir = os.path.join(output_dir, "images")
        mask_dir = os.path.join(output_dir, "masks")
        db_path = os.path.join(output_dir, "database.db")
        glomap_dir = os.path.join(output_dir, "glomap_sparse")

        for d in [img_dir, mask_dir]:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)

        if os.path.exists(db_path):
            os.remove(db_path)
        if os.path.exists(glomap_dir):
            shutil.rmtree(glomap_dir)

        # 1. Load Images
        fps = 25.0
        if images is not None:
            total_frames = images.shape[0]
            h, w = images.shape[1], images.shape[2]
            for i in range(total_frames):
                frame_np = (images[i].cpu().numpy() * 255.0).astype(np.uint8)
                frame_bgr = cv2.cvtColor(frame_np, cv2.COLOR_RGB2BGR)
                cv2.imwrite(os.path.join(img_dir, f"frame_{i:04d}.jpg"), frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 98])
        elif input_path and os.path.isdir(input_path):
            exts = ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp')
            files = sorted([f for f in os.listdir(input_path) if f.lower().endswith(exts)])
            total_frames = len(files)
            first = cv2.imread(os.path.join(input_path, files[0]))
            h, w = first.shape[:2]
            for i, f in enumerate(files):
                shutil.copyfile(os.path.join(input_path, f), os.path.join(img_dir, f"frame_{i:04d}.jpg"))
        elif input_path and os.path.isfile(input_path):
            cap = cv2.VideoCapture(input_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            count = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                cv2.imwrite(os.path.join(img_dir, f"frame_{count:04d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 98])
                count += 1
            cap.release()
            total_frames = count
        else:
            raise ValueError(f"CameraTracker: Invalid input. Connect 'images' pin or provide valid 'input_path' (got '{input_path}').")

        # 2. Dynamic Masking
        dynamic_mask_tensor = np.zeros((total_frames, h, w), dtype=np.float32)

        if mask_mode == "Bypass Mask (Full Frame)":
            # Masking disabled completely: track entire frame
            for fid in range(total_frames):
                img_name = f"frame_{fid:04d}.jpg"
                valid_mask = np.ones((h, w), dtype=np.uint8) * 255
                cv2.imwrite(os.path.join(mask_dir, f"{img_name}.png"), valid_mask)
                cv2.imwrite(os.path.join(mask_dir, f"frame_{fid:04d}.png"), valid_mask)
        elif mask_mode == "Track Subject Only (Inverted)":
            # Track ONLY the masked subject (invert mask: inside mask is valid 255, outside is 0)
            for fid in range(total_frames):
                img_name = f"frame_{fid:04d}.jpg"
                valid_mask = np.zeros((h, w), dtype=np.uint8)
                m_np = np.zeros((h, w), dtype=np.uint8)
                if tracking_mask is not None:
                    m_idx = min(fid, tracking_mask.shape[0] - 1)
                    m_np = (tracking_mask[m_idx].cpu().numpy() * 255.0).astype(np.uint8)
                    if m_np.shape[:2] != (h, w):
                        m_np = cv2.resize(m_np, (w, h), interpolation=cv2.INTER_NEAREST)
                if manual_mask is not None:
                    u_idx = min(fid, manual_mask.shape[0] - 1)
                    u_np = (manual_mask[u_idx].cpu().numpy() * 255.0).astype(np.uint8)
                    u_np = cv2.resize(u_np, (w, h), interpolation=cv2.INTER_NEAREST)
                    m_np = np.maximum(m_np, u_np)
                dynamic_mask_tensor[fid] = m_np.astype(np.float32) / 255.0
                valid_mask[m_np > 127] = 255
                cv2.imwrite(os.path.join(mask_dir, f"{img_name}.png"), valid_mask)
                cv2.imwrite(os.path.join(mask_dir, f"frame_{fid:04d}.png"), valid_mask)
        elif mask_mode == "Auto YOLO Detect" or (tracking_mask is None and mask_mode == "Exclude Masked (Dynamic Objects)"):
            mask_classes = kwargs.get("mask_classes", "person, car, truck, bus, motorcycle")
            mask_dilation = int(kwargs.get("mask_dilation", 20))
            detect_props = bool(kwargs.get("detect_props", True))

            target_classes_map = {"person": 0, "bicycle": 1, "car": 2, "motorcycle": 3, "bus": 5, "train": 6, "truck": 7}
            classes_list = [c.strip().lower() for c in mask_classes.split(",")]
            target_ids = [target_classes_map[c] for c in classes_list if c in target_classes_map]

            from ultralytics import YOLO  # lazy: only for "Auto YOLO Detect"
            model = YOLO("yolov8n-seg.pt")
            kernel = np.ones((mask_dilation, mask_dilation), np.uint8) if mask_dilation > 0 else None

            for fid in range(total_frames):
                img_name = f"frame_{fid:04d}.jpg"
                frame = cv2.imread(os.path.join(img_dir, img_name))
                valid_mask = np.ones((h, w), dtype=np.uint8) * 255
                dynamic_mask = np.zeros((h, w), dtype=np.uint8)
                person_bbox_mask = np.zeros((h, w), dtype=np.uint8)

                user_m = None
                if manual_mask is not None:
                    m_idx = min(fid, manual_mask.shape[0] - 1)
                    raw_user_m = (manual_mask[m_idx].cpu().numpy() * 255.0).astype(np.uint8)
                    user_m = cv2.resize(raw_user_m, (w, h), interpolation=cv2.INTER_NEAREST)

                results = model(frame, conf=0.20, classes=target_ids, device="cuda" if torch.cuda.is_available() else "cpu", verbose=False)
                if len(results) > 0 and results[0].masks is not None:
                    for i, m in enumerate(results[0].masks.data):
                        m_np = cv2.resize((m.cpu().numpy() * 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
                        dynamic_mask = np.maximum(dynamic_mask, m_np)
                        if results[0].boxes is not None and i < len(results[0].boxes):
                            b = results[0].boxes.xyxy[i].cpu().numpy().astype(int)
                            x1, y1 = max(0, b[0] - 250), max(0, b[1] - 250)
                            x2, y2 = min(w, b[2] + 250), min(h, b[3] + 250)
                            person_bbox_mask[y1:y2, x1:x2] = 255

                if detect_props and np.any(person_bbox_mask > 0):
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    green_mask = cv2.inRange(hsv, np.array([35, 50, 50]), np.array([85, 255, 255]))
                    prop_mask = cv2.bitwise_and(green_mask, person_bbox_mask)
                    dynamic_mask = np.maximum(dynamic_mask, prop_mask)

                if user_m is not None:
                    dynamic_mask = np.maximum(dynamic_mask, user_m)

                if kernel is not None and np.any(dynamic_mask > 0):
                    dynamic_mask = cv2.dilate(dynamic_mask, kernel, iterations=1)

                dynamic_mask_tensor[fid] = dynamic_mask.astype(np.float32) / 255.0
                valid_mask[dynamic_mask > 0] = 0
                cv2.imwrite(os.path.join(mask_dir, f"{img_name}.png"), valid_mask)
                cv2.imwrite(os.path.join(mask_dir, f"frame_{fid:04d}.png"), valid_mask)
        else: # "Exclude Masked (Dynamic Objects)" default with tracking_mask
            for fid in range(total_frames):
                img_name = f"frame_{fid:04d}.jpg"
                valid_mask = np.ones((h, w), dtype=np.uint8) * 255
                m_np = np.zeros((h, w), dtype=np.uint8)
                if tracking_mask is not None:
                    m_idx = min(fid, tracking_mask.shape[0] - 1)
                    m_np = (tracking_mask[m_idx].cpu().numpy() * 255.0).astype(np.uint8)
                    if m_np.shape[:2] != (h, w):
                        m_np = cv2.resize(m_np, (w, h), interpolation=cv2.INTER_NEAREST)
                if manual_mask is not None:
                    u_idx = min(fid, manual_mask.shape[0] - 1)
                    u_np = (manual_mask[u_idx].cpu().numpy() * 255.0).astype(np.uint8)
                    u_np = cv2.resize(u_np, (w, h), interpolation=cv2.INTER_NEAREST)
                    m_np = np.maximum(m_np, u_np)
                dynamic_mask_tensor[fid] = m_np.astype(np.float32) / 255.0
                valid_mask[m_np > 127] = 0
                cv2.imwrite(os.path.join(mask_dir, f"{img_name}.png"), valid_mask)
                cv2.imwrite(os.path.join(mask_dir, f"frame_{fid:04d}.png"), valid_mask)

        # 3. COLMAP & GLOMAP Structure-from-Motion
        glomap_success = False
        solved_focal_mm = float(focal_length_mm)
        final_cams = None
        final_rots = None
        pts_colors = []
        final_pts = np.empty((0, 3))
        report = ""

        # Point cloud density preset mappings to COLMAP SIFT extraction parameters
        DENSITY_PRESETS = {
            "Draft (2048 pts)": {"max_features": 2048, "peak_threshold": 0.008},
            "Standard (4096 pts)": {"max_features": 4096, "peak_threshold": 0.006},
            "Dense (8192 pts)": {"max_features": 8192, "peak_threshold": 0.004},
            "Ultra (16384 pts)": {"max_features": 16384, "peak_threshold": 0.002},
        }
        density_cfg = DENSITY_PRESETS.get(point_cloud_density, DENSITY_PRESETS["Draft (2048 pts)"])
        max_num_features_str = str(density_cfg["max_features"])
        peak_threshold_str = str(density_cfg["peak_threshold"])

        if total_frames >= 3:
            try:
                focal_px = (float(focal_length_mm) / 36.0) * w
                cmd_feat = [
                    COLMAP_BIN, "feature_extractor",
                    "--database_path", db_path,
                    "--image_path", img_dir,
                    "--ImageReader.mask_path", mask_dir,
                    "--ImageReader.camera_model", "SIMPLE_RADIAL",
                    "--ImageReader.single_camera", "1",
                    "--ImageReader.default_focal_length_factor", str(focal_px / max(w, h)),
                    "--FeatureExtraction.use_gpu", "1" if torch.cuda.is_available() else "0",
                    "--SiftExtraction.max_num_features", max_num_features_str,
                    "--SiftExtraction.peak_threshold", peak_threshold_str
                ]
                subprocess.run(cmd_feat, check=True)

                cmd_match = [
                    COLMAP_BIN, "sequential_matcher",
                    "--database_path", db_path,
                    "--SequentialMatching.overlap", "25",
                    "--SequentialMatching.quadratic_overlap", "1",
                    "--FeatureMatching.use_gpu", "1" if torch.cuda.is_available() else "0"
                ]
                subprocess.run(cmd_match, check=True)

                os.makedirs(glomap_dir, exist_ok=True)
                if os.path.isfile(GLOMAP_BIN):  # legacy standalone GLOMAP build
                    cmd_glomap = [GLOMAP_BIN, "mapper", "--database_path", db_path, "--output_path", glomap_dir]
                else:  # fallback only: COLMAP 4.x ships GLOMAP as "global_mapper" (not the tuned setup)
                    print("[CameraTracker] glomap.exe not found - falling back to colmap global_mapper (less tested)")
                    cmd_glomap = [COLMAP_BIN, "global_mapper", "--database_path", db_path,
                                  "--image_path", img_dir, "--output_path", glomap_dir]
                sub_res = subprocess.run(cmd_glomap, capture_output=True, text=True)

                if sub_res.returncode == 0:
                    rec_path = os.path.join(glomap_dir, "0") if os.path.exists(os.path.join(glomap_dir, "0")) else glomap_dir
                    if os.path.exists(os.path.join(rec_path, "cameras.bin")) or os.path.exists(os.path.join(rec_path, "cameras.txt")):
                        rec = pycolmap.Reconstruction(rec_path)
                        if len(rec.cameras) > 0 and len(rec.images) > 0:
                            glomap_success = True
            except Exception as e:
                print(f"[CameraTracker] GLOMAP SfM execution note: {e}")

        if glomap_success:
            cam = list(rec.cameras.values())[0]
            fx = cam.params[0]
            cx = cam.params[1] if len(cam.params) > 1 else w / 2.0
            cy = cam.params[2] if len(cam.params) > 2 else h / 2.0
            k1 = cam.params[3] if len(cam.params) > 3 else 0.0

            K = np.array([[fx, 0, cx], [0, fx, cy], [0, 0, 1]], dtype=np.float32)
            dist = np.array([k1, 0, 0, 0, 0], dtype=np.float32)
            map1, map2 = cv2.initUndistortRectifyMap(K, dist, None, K, (w, h), cv2.CV_32FC1)

            undist_frames = []
            for fid in range(total_frames):
                fr = cv2.imread(os.path.join(img_dir, f"frame_{fid:04d}.jpg"))
                undist = cv2.remap(fr, map1, map2, cv2.INTER_LINEAR)
                undist_rgb = cv2.cvtColor(undist, cv2.COLOR_BGR2RGB)
                undist_frames.append(undist_rgb)

            undist_tensor = torch.from_numpy(np.array(undist_frames)).float() / 255.0

            glomap_poses = {}
            for img_id, im in rec.images.items():
                m = re.search(r'frame_(\d+)\.jpg', im.name)
                if m:
                    fid = int(m.group(1))
                    R = im.cam_from_world().rotation.matrix()
                    C = np.array(im.projection_center())
                    rot_obj = Rotation.from_matrix(R)
                    glomap_poses[fid] = (R, C, rot_obj)

            reg_fids = sorted(glomap_poses.keys())
            raw_pts_glomap = np.array([p.xyz for p in rec.points3D.values()])
            pts_colors = np.array([p.color / 255.0 for p in rec.points3D.values()])

            reg_C_raw = np.array([glomap_poses[f][1] for f in reg_fids])
            if len(reg_fids) >= 2:
                px = PchipInterpolator(reg_fids, reg_C_raw[:, 0])
                py = PchipInterpolator(reg_fids, reg_C_raw[:, 1])
                pz = PchipInterpolator(reg_fids, reg_C_raw[:, 2])
            else:
                px = lambda f: reg_C_raw[0, 0]
                py = lambda f: reg_C_raw[0, 1]
                pz = lambda f: reg_C_raw[0, 2]

            all_C = np.zeros((total_frames, 3))
            all_rot = []
            for fid in range(total_frames):
                if fid in glomap_poses:
                    all_C[fid] = glomap_poses[fid][1]
                    all_rot.append(glomap_poses[fid][2])
                else:
                    all_C[fid] = [float(px(fid)), float(py(fid)), float(pz(fid))]
                    left = [f for f in reg_fids if f < fid]
                    right = [f for f in reg_fids if f > fid]
                    ref_fid = left[-1] if left else right[0]
                    all_rot.append(glomap_poses[ref_fid][2])

            # Convert raw COLMAP frame (X-right, Y-down, Z-forward) to Blender/VFX Z-up frame (X-right, Y-forward, Z-up)
            M_w2b = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float)
            R_conv = np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=float)

            raw_cams_b = np.array([M_w2b @ all_C[i] for i in range(total_frames)])
            raw_rots_b = [M_w2b @ all_rot[i].as_matrix().T @ R_conv for i in range(total_frames)]
            raw_pts_b = np.column_stack([raw_pts_glomap[:, 0], raw_pts_glomap[:, 2], -raw_pts_glomap[:, 1]])

            # Estimate camera up vector in Z-up world. For handheld/tripod video, cameras are upright (local +Y is up).
            avg_cam_up = np.mean([R[:, 1] for R in raw_rots_b], axis=0)
            norm_up = np.linalg.norm(avg_cam_up)
            if norm_up > 1e-6:
                avg_cam_up /= norm_up
            else:
                avg_cam_up = np.array([0.0, 0.0, 1.0])

            # In VFX matchmoving, camera gravity (avg_cam_up) is the primary reference for the world vertical axis.
            # Aligning avg_cam_up strictly to [0, 0, 1] guarantees the camera horizon in 3D is perfectly level (0 deg roll),
            # preventing noisy point cloud patches or slanted terrain from tilting the camera sideways.
            rot_align, _ = Rotation.align_vectors([[0, 0, 1]], [avg_cam_up])
            R_align = rot_align.as_matrix()

            cams_aligned = (R_align @ raw_cams_b.T).T
            rots_aligned = [R_align @ R for R in raw_rots_b]
            pts_aligned = (R_align @ raw_pts_b.T).T

            # Metric scale estimation along true vertical Z axis
            target_h = float(camera_height_m) if (camera_height_m and float(camera_height_m) > 0) else 1.55
            cam_z_mean = float(cams_aligned[:, 2].mean())
            pts_below = pts_aligned[pts_aligned[:, 2] < cam_z_mean]

            if len(pts_below) >= 30:
                # 20th percentile of points below camera gives robust floor level without outliers
                floor_est = float(np.percentile(pts_below[:, 2], 20))
                cam_h_raw = abs(cam_z_mean - floor_est)
            else:
                cam_h_raw = target_h

            S_metric = target_h / max(cam_h_raw, 0.05) if cam_h_raw > 0.05 else 1.0

            cams_scaled = cams_aligned * S_metric
            pts_scaled = pts_aligned * S_metric

            # Place the 3D ground plane (Z = 0.000m) exactly target_h below camera
            ground_z = cams_scaled[:, 2].mean() - target_h

            cam_center = cams_scaled[:, :2].mean(axis=0)
            T_world = np.array([-cam_center[0], -cam_center[1], -ground_z])

            final_cams = cams_scaled + T_world
            final_pts = pts_scaled + T_world
            final_rots = rots_aligned

            # Focal length resolution based on mode
            if focal_length_mode == "Fixed / Known Focal":
                solved_focal_mm = float(focal_length_mm)
            else:
                solved_focal_mm = (fx / w) * 36.0

            # Optional camera trajectory smoothing to filter out high-frequency jitter
            if smooth_camera and total_frames >= 5:
                try:
                    from scipy.ndimage import gaussian_filter1d
                    final_cams = gaussian_filter1d(final_cams, sigma=1.5, axis=0)
                    quats = np.array([Rotation.from_matrix(R).as_quat() for R in final_rots])
                    for qi in range(1, len(quats)):
                        if np.dot(quats[qi], quats[qi - 1]) < 0.0:
                            quats[qi] = -quats[qi]
                    smoothed_quats = gaussian_filter1d(quats, sigma=1.5, axis=0)
                    norms = np.linalg.norm(smoothed_quats, axis=1, keepdims=True)
                    smoothed_quats = smoothed_quats / np.maximum(norms, 1e-8)
                    final_rots = [Rotation.from_quat(q).as_matrix() for q in smoothed_quats]
                except Exception as e:
                    print(f"[CameraTracker] Note: Smoothing skipped ({e})")

            report = (
                f"GLOMAP SfM solved {len(reg_fids)}/{total_frames} frames. "
                f"Focal: {solved_focal_mm:.1f}mm ({focal_length_mode}). "
                f"Points: {len(final_pts)} ({point_cloud_density}). "
                f"Ground aligned to Z=0.000m (Height: {camera_height_m:.2f}m)."
            )
        else:
            # Fallback static camera for single image or failed tracking
            undist_frames = []
            for fid in range(total_frames):
                fr = cv2.imread(os.path.join(img_dir, f"frame_{fid:04d}.jpg"))
                undist_rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
                undist_frames.append(undist_rgb)
            undist_tensor = torch.from_numpy(np.array(undist_frames)).float() / 255.0

            final_cams = np.array([[0.0, -3.5, 1.5] for _ in range(total_frames)])
            final_rots = [np.eye(3) for _ in range(total_frames)]

            grid_list = []
            grid_cols = []
            for gx in range(-5, 6):
                for gy in range(-5, 6):
                    grid_list.append([float(gx), float(gy), 0.0])
                    grid_cols.append([0.3, 0.5, 0.8])
            final_pts = np.array(grid_list)
            pts_colors = np.array(grid_cols)
            report = f"CameraTracker: Processed {total_frames} frames. Static camera fallback generated (SfM requires a multi-frame video sequence for parallax)."

        matrices_4x4 = []
        for i in range(total_frames):
            mat = np.eye(4)
            mat[:3, :3] = final_rots[i]
            mat[:3, 3] = final_cams[i]
            matrices_4x4.append(mat.tolist())

        camera_data = {
            "fps": fps,
            "width": w,
            "height": h,
            "focal_mm": float(solved_focal_mm),
            "sensor_width_mm": 36.0,
            "total_frames": total_frames,
            "matrices": matrices_4x4,
            "positions": final_cams.tolist(),
        }

        track_points_data = {
            "points": final_pts.tolist(),
            "colors": pts_colors.tolist() if isinstance(pts_colors, np.ndarray) else pts_colors,
            "count": len(final_pts),
        }

        # Cache scene data for WebGL 3D Layout Editor
        try:
            scene_cache = {
                "camera": camera_data,
                "points": track_points_data["points"],
                "colors": track_points_data["colors"],
                "total_frames": total_frames,
                "has_cloud": len(track_points_data["points"]) > 0
            }
            with open(os.path.join(output_dir, "scene_data.json"), "w", encoding="utf-8") as f:
                json.dump(scene_cache, f)
        except Exception as e:
            print(f"[CameraTracker] Note: Failed to cache scene_data.json: {e}")

        mask_out_tensor = torch.from_numpy(dynamic_mask_tensor)
        return (undist_tensor, camera_data, track_points_data, mask_out_tensor, report)
