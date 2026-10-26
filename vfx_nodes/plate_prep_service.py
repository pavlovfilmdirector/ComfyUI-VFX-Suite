"""
Plate Prep Processing Service
=============================
Handles independent staged processes for Plate Prep with Per-Node Isolation:
1. Ingestion & Frame Extraction (Video, Image Sequence, Direct Tensor)
2. Lens Undistortion (k1, k2) & Auto-Calibration
3. YOLOv8 Dynamic Actor & Prop Masking
4. Mask Thickness (Dilation / Erosion with Elliptic Anti-Aliasing)
5. Manual Brush Paint (Add / Subtract with SDF Keyframe Tracking)
6. Mask Overlay & Actor Cutout Generation
"""

import os
import sys
import json
import base64
import shutil
import cv2
import numpy as np
import torch
try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "output_cache", "plate")
os.makedirs(CACHE_DIR, exist_ok=True)


# node_id -> plate root registered by the 2D workspace (frames live inside the project folder)
PLATE_ROOTS = {}


def register_plate_root(node_id, root):
    PLATE_ROOTS[str(node_id)] = root


def get_dirs(node_id="default"):
    nid = str(node_id).strip().replace("/", "_").replace("\\", "_")
    if not nid or nid == "default":
        nid = "default"

    folder_name = nid if nid.startswith("node_") else f"node_{nid}"
    base = PLATE_ROOTS.get(nid) or os.path.join(CACHE_DIR, folder_name)

    # Seamless backward compatibility with legacy cache root
    legacy_info = os.path.join(CACHE_DIR, "info.json")
    if nid == "default" and os.path.exists(os.path.join(CACHE_DIR, "raw", "frame_0000.jpg")):
        os.makedirs(base, exist_ok=True)
        try:
            for item in ["raw", "undist", "automask", "finalmask", "cutout"]:
                src = os.path.join(CACHE_DIR, item)
                dst = os.path.join(base, item)
                if os.path.exists(src) and not os.path.exists(os.path.join(dst, "frame_0000.jpg")) and not os.path.exists(os.path.join(dst, "frame_0000.png")):
                    if os.path.exists(dst):
                        shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(src, dst)
            if os.path.exists(legacy_info):
                shutil.copy2(legacy_info, os.path.join(base, "info.json"))
        except Exception:
            pass

    dirs = {
        "root": base,
        "raw": os.path.join(base, "raw"),
        "undist": os.path.join(base, "undist"),
        "automask": os.path.join(base, "automask"),
        "finalmask": os.path.join(base, "finalmask"),
        "cutout": os.path.join(base, "cutout"),
        "fg": os.path.join(base, "fg"),
        "crypto": os.path.join(base, "crypto"),
        "info": os.path.join(base, "info.json")
    }
    for k, p in dirs.items():
        if k != "info":
            os.makedirs(p, exist_ok=True)
    return dirs


def get_info(node_id="default"):
    d = get_dirs(node_id)
    info_path = d["info"]
    if os.path.exists(info_path):
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "invert_mask" not in data:
                    data["invert_mask"] = False
                if "mask_engine" not in data:
                    data["mask_engine"] = "sam2"
                return data
        except Exception:
            pass
    return {
        "node_id": str(node_id),
        "total_frames": 0,
        "width": 1920,
        "height": 1080,
        "fps": 25.0,
        "k1": 0.0,
        "k2": 0.0,
        "thickness": 20,
        "has_raw": False,
        "has_undist": False,
        "has_mask": False,
        "input_path": "",
        "invert_mask": False,
        "mask_engine": "sam2",
        "keyframes": []
    }


def save_info(info_dict, node_id="default"):
    d = get_dirs(node_id)
    cur = get_info(node_id)
    cur.update(info_dict)
    cur["node_id"] = str(node_id)
    with open(d["info"], "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)
    return cur


def save_uploaded_video(filename, file_bytes, node_id="default"):
    d = get_dirs(node_id)
    for f in os.listdir(d["raw"]):
        try:
            os.remove(os.path.join(d["raw"], f))
        except Exception:
            pass

    temp_path = os.path.join(d["root"], f"temp_{filename}")
    with open(temp_path, "wb") as f:
        f.write(file_bytes)

    cap = cv2.VideoCapture(temp_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        cv2.imwrite(os.path.join(d["raw"], f"frame_{total:04d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 98])
        cv2.imwrite(os.path.join(d["undist"], f"frame_{total:04d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 98])
        total += 1
    cap.release()

    try:
        os.remove(temp_path)
    except Exception:
        pass

    if total == 0:
        raise ValueError(f"Could not extract frames from video: {filename}")

    return save_info({
        "input_path": f"[Video] {filename}",
        "total_frames": total,
        "width": w,
        "height": h,
        "fps": float(fps),
        "has_raw": True,
        "has_undist": True,
        "k1": 0.0,
        "k2": 0.0,
        "keyframes": []
    }, node_id=node_id)


def save_uploaded_sequence(files_dict, node_id="default"):
    d = get_dirs(node_id)
    for f in os.listdir(d["raw"]):
        try:
            os.remove(os.path.join(d["raw"], f))
        except Exception:
            pass

    sorted_names = sorted(files_dict.keys())
    if not sorted_names:
        raise ValueError("No files provided in sequence upload")

    first_bytes = files_dict[sorted_names[0]]
    first_img = cv2.imdecode(np.frombuffer(first_bytes, np.uint8), cv2.IMREAD_COLOR)
    h, w = first_img.shape[:2]

    total = 0
    for name in sorted_names:
        b = files_dict[name]
        img = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            cv2.imwrite(os.path.join(d["raw"], f"frame_{total:04d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 98])
            cv2.imwrite(os.path.join(d["undist"], f"frame_{total:04d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 98])
            total += 1

    return save_info({
        "input_path": f"[Sequence] {len(sorted_names)} frames",
        "total_frames": total,
        "width": w,
        "height": h,
        "fps": 25.0,
        "has_raw": True,
        "has_undist": True,
        "k1": 0.0,
        "k2": 0.0,
        "keyframes": []
    }, node_id=node_id)


def save_uploaded_mask_video(filename, file_bytes, node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    total_frames = info.get("total_frames", 0)
    w = info.get("width", 1920)
    h = info.get("height", 1080)
    if total_frames == 0:
        raise ValueError("Сначала загрузите видео или кадры шота (Шаг 1), перед загрузкой масок.")

    temp_path = os.path.join(d["root"], f"temp_mask_{filename}")
    with open(temp_path, "wb") as f:
        f.write(file_bytes)

    cap = cv2.VideoCapture(temp_path)
    count = 0
    while True:
        ret, frame = cap.read()
        if not ret or count >= total_frames:
            break
        if frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        if gray.shape[:2] != (h, w):
            gray = cv2.resize(gray, (w, h), interpolation=cv2.INTER_NEAREST)

        cv2.imwrite(os.path.join(d["finalmask"], f"frame_{count:04d}.png"), gray)
        cv2.imwrite(os.path.join(d["automask"], f"frame_{count:04d}.png"), gray)

        undist_p = os.path.join(d["undist"], f"frame_{count:04d}.jpg")
        if os.path.exists(undist_p):
            fr = cv2.imread(undist_p)
            rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
            rgba[:, :, 3] = gray
            cv2.imwrite(os.path.join(d["cutout"], f"frame_{count:04d}.png"), rgba)
        count += 1
    cap.release()

    try:
        os.remove(temp_path)
    except Exception:
        pass

    if count == 0:
        raise ValueError("Не удалось прочитать кадры из загруженного видео масок.")

    save_info({"has_mask": True}, node_id=node_id)
    return {"status": "ok", "frames_count": count}


def save_uploaded_mask_sequence(files_dict, node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    total_frames = info.get("total_frames", 0)
    w = info.get("width", 1920)
    h = info.get("height", 1080)
    if total_frames == 0:
        raise ValueError("Сначала загрузите видео или кадры шота (Шаг 1), перед загрузкой масок.")

    sorted_names = sorted(files_dict.keys())
    if not sorted_names:
        raise ValueError("Файлы масок не переданы.")

    count = 0
    for name in sorted_names:
        if count >= total_frames:
            break
        b = files_dict[name]
        nparr = np.frombuffer(b, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        if img.ndim == 3 and img.shape[2] == 4:
            gray = img[:, :, 3]
        elif img.ndim == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img

        if gray.shape[:2] != (h, w):
            gray = cv2.resize(gray, (w, h), interpolation=cv2.INTER_NEAREST)

        cv2.imwrite(os.path.join(d["finalmask"], f"frame_{count:04d}.png"), gray)
        cv2.imwrite(os.path.join(d["automask"], f"frame_{count:04d}.png"), gray)

        undist_p = os.path.join(d["undist"], f"frame_{count:04d}.jpg")
        if os.path.exists(undist_p):
            fr = cv2.imread(undist_p)
            rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
            rgba[:, :, 3] = gray
            cv2.imwrite(os.path.join(d["cutout"], f"frame_{count:04d}.png"), rgba)
        count += 1

    save_info({"has_mask": True}, node_id=node_id)
    return {"status": "ok", "frames_count": count}


def save_uploaded_mask_single(filename, file_bytes, frame_idx=0, apply_all=False, node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    total_frames = info.get("total_frames", 0)
    w = info.get("width", 1920)
    h = info.get("height", 1080)
    if total_frames == 0:
        raise ValueError("Сначала загрузите видео или кадры шота (Шаг 1), перед загрузкой масок.")

    nparr = np.frombuffer(file_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Не удалось декодировать изображение маски.")

    if img.ndim == 3 and img.shape[2] == 4:
        gray = img[:, :, 3]
    elif img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    if gray.shape[:2] != (h, w):
        gray = cv2.resize(gray, (w, h), interpolation=cv2.INTER_NEAREST)

    keyframes = set(info.get("keyframes", []))

    if apply_all:
        for fid in range(total_frames):
            cv2.imwrite(os.path.join(d["finalmask"], f"frame_{fid:04d}.png"), gray)
            cv2.imwrite(os.path.join(d["automask"], f"frame_{fid:04d}.png"), gray)
            undist_p = os.path.join(d["undist"], f"frame_{fid:04d}.jpg")
            if os.path.exists(undist_p):
                fr = cv2.imread(undist_p)
                rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
                rgba[:, :, 3] = gray
                cv2.imwrite(os.path.join(d["cutout"], f"frame_{fid:04d}.png"), rgba)
    else:
        fid = int(frame_idx)
        cv2.imwrite(os.path.join(d["finalmask"], f"frame_{fid:04d}.png"), gray)
        undist_p = os.path.join(d["undist"], f"frame_{fid:04d}.jpg")
        if os.path.exists(undist_p):
            fr = cv2.imread(undist_p)
            rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
            rgba[:, :, 3] = gray
            cv2.imwrite(os.path.join(d["cutout"], f"frame_{fid:04d}.png"), rgba)
        keyframes.add(fid)

    save_info({"has_mask": True, "keyframes": sorted(list(keyframes))}, node_id=node_id)
    return {"status": "ok", "frame": frame_idx, "apply_all": apply_all, "keyframes": sorted(list(keyframes))}



def auto_calibrate_distortion(node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    total = info.get("total_frames", 0)
    w = info.get("width", 1920)
    h = info.get("height", 1080)
    if total == 0:
        return {"k1": -0.06, "k2": 0.0}

    f0_path = os.path.join(d["raw"], "frame_0000.jpg")
    if not os.path.exists(f0_path):
        f0_path = os.path.join(d["undist"], "frame_0000.jpg")
    if not os.path.exists(f0_path):
        return {"k1": -0.06, "k2": 0.0}

    img = cv2.imread(f0_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=80, maxLineGap=10)

    best_k1 = -0.06
    if lines is not None and len(lines) > 2:
        cx, cy = w / 2.0, h / 2.0
        norm_r = np.sqrt(cx**2 + cy**2)
        outer_count = 0
        for l in lines:
            pts = np.asarray(l).reshape(-1)
            if len(pts) < 4:
                continue
            x1, y1, x2, y2 = int(pts[0]), int(pts[1]), int(pts[2]), int(pts[3])
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            r_mid = np.sqrt((mx - cx)**2 + (my - cy)**2) / norm_r
            if r_mid > 0.35:
                outer_count += 1
        if outer_count > 6:
            best_k1 = -0.09
        elif outer_count > 2:
            best_k1 = -0.05
        else:
            best_k1 = -0.03

    return {"k1": round(float(best_k1), 3), "k2": 0.0}


def load_input_source(input_path, node_id="default"):
    if not input_path or not os.path.exists(input_path):
        raise ValueError(f"Input path does not exist: {input_path}")

    d = get_dirs(node_id)
    for f in os.listdir(d["raw"]):
        try:
            os.remove(os.path.join(d["raw"], f))
        except Exception:
            pass

    fps = 25.0
    if os.path.isdir(input_path):
        exts = ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp')
        files = sorted([f for f in os.listdir(input_path) if f.lower().endswith(exts)])
        if not files:
            raise ValueError(f"No valid images found in: {input_path}")
        first = cv2.imread(os.path.join(input_path, files[0]))
        h, w = first.shape[:2]
        for i, f in enumerate(files):
            shutil_src = os.path.join(input_path, f)
            shutil_dst = os.path.join(d["raw"], f"frame_{i:04d}.jpg")
            img = cv2.imread(shutil_src)
            cv2.imwrite(shutil_dst, img, [cv2.IMWRITE_JPEG_QUALITY, 98])
        total = len(files)
    elif os.path.isfile(input_path):
        cap = cv2.VideoCapture(input_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            cv2.imwrite(os.path.join(d["raw"], f"frame_{total:04d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 98])
            total += 1
        cap.release()
        if total == 0:
            raise ValueError(f"Could not read any frames from video: {input_path}")
    else:
        raise ValueError(f"Invalid path: {input_path}")

    for i in range(total):
        src = os.path.join(d["raw"], f"frame_{i:04d}.jpg")
        dst = os.path.join(d["undist"], f"frame_{i:04d}.jpg")
        if os.path.exists(src):
            cv2.imwrite(dst, cv2.imread(src))

    return save_info({
        "input_path": input_path,
        "total_frames": total,
        "width": w,
        "height": h,
        "fps": float(fps),
        "has_raw": True,
        "has_undist": True,
        "k1": 0.0,
        "k2": 0.0
    }, node_id=node_id)


def apply_undistortion(k1, k2, node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    total = info.get("total_frames", 0)
    w = info.get("width", 1920)
    h = info.get("height", 1080)
    if total == 0:
        raise ValueError("No frames loaded yet.")

    cx, cy = w / 2.0, h / 2.0
    focal = max(w, h)
    K = np.array([[focal, 0, cx], [0, focal, cy], [0, 0, 1]], dtype=np.float32)
    dist = np.array([k1, k2, 0, 0, 0], dtype=np.float32)

    for i in range(total):
        raw_p = os.path.join(d["raw"], f"frame_{i:04d}.jpg")
        if not os.path.exists(raw_p):
            continue
        fr = cv2.imread(raw_p)
        if abs(k1) > 1e-4 or abs(k2) > 1e-4:
            undist = cv2.undistort(fr, K, dist)
        else:
            undist = fr
        cv2.imwrite(os.path.join(d["undist"], f"frame_{i:04d}.jpg"), undist, [cv2.IMWRITE_JPEG_QUALITY, 98])

    return save_info({"k1": float(k1), "k2": float(k2), "has_undist": True}, node_id=node_id)


_SAM2_PREDICTOR = None

def get_sam2_predictor():
    global _SAM2_PREDICTOR
    if _SAM2_PREDICTOR is not None:
        return _SAM2_PREDICTOR

    try:
        # Locate the ComfyUI root (the suite may be symlinked into custom_nodes, so __file__ is unreliable)
        comfy_dir = None
        try:
            import folder_paths
            comfy_dir = getattr(folder_paths, "base_path", None) or os.path.dirname(folder_paths.models_dir)
        except Exception:
            pass
        if not comfy_dir or not os.path.isdir(os.path.join(comfy_dir, "custom_nodes")):
            import folder_paths as _fp; comfy_dir = _fp.base_path
        if comfy_dir not in sys.path:
            sys.path.insert(0, comfy_dir)
        import importlib
        mod = importlib.import_module("custom_nodes.ComfyUI-segment-anything-2.load_model")

        # every folder ComfyUI knows for "sam2" (incl. extra_model_paths.yaml) + the default one
        sam2_dirs = []
        try:
            import folder_paths as _fp2
            sam2_dirs = list(_fp2.get_folder_paths("sam2"))
        except Exception:
            pass
        sam2_dirs.append(os.path.join(comfy_dir, "models", "sam2"))
        sam2_dirs = [d for i, d in enumerate(sam2_dirs) if d and d not in sam2_dirs[:i]]
        model_path = next((os.path.join(d, "sam2_hiera_base_plus.safetensors") for d in sam2_dirs
                           if os.path.isfile(os.path.join(d, "sam2_hiera_base_plus.safetensors"))), "")
        custom_nodes_dir = os.path.join(comfy_dir, "custom_nodes", "ComfyUI-segment-anything-2")
        model_cfg = os.path.join(custom_nodes_dir, "sam2_configs", "sam2_hiera_b+.yaml")

        if not model_path:
            for d in sam2_dirs:
                if os.path.isdir(d):
                    hit = next((f for f in sorted(os.listdir(d)) if f.endswith((".safetensors", ".pt"))), None)
                    if hit:
                        model_path = os.path.join(d, hit)
                        break

        if not os.path.exists(model_path) or not os.path.exists(model_cfg):
            return None

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if (device == "cuda" and torch.cuda.is_bf16_supported()) else (torch.float16 if device == "cuda" else torch.float32)
        predictor = mod.load_model(model_path, model_cfg, segmentor="single_image", dtype=dtype, device=device)
        _SAM2_PREDICTOR = predictor
        print(f"[ComfyUI-VFX-Suite] Initialized SAM 2.1 Predictor on {device} ({dtype})")
        return _SAM2_PREDICTOR
    except Exception as e:
        print(f"[ComfyUI-VFX-Suite] Note: SAM 2 loader skipped ({e})")
        return None


def apply_auto_mask(mask_classes="person, car, truck, bus, motorcycle", detect_props=True, engine="sam2", node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    total = info.get("total_frames", 0)
    w = info.get("width", 1920)
    h = info.get("height", 1080)
    if total == 0:
        raise ValueError("No frames loaded yet.")

    target_map = {"person": 0, "bicycle": 1, "car": 2, "motorcycle": 3, "bus": 5, "train": 6, "truck": 7}
    target_ids = [target_map[c.strip().lower()] for c in mask_classes.split(",") if c.strip().lower() in target_map]

    use_sam2 = (str(engine).lower() == "sam2")
    sam2_predictor = None
    if use_sam2:
        sam2_predictor = get_sam2_predictor()
        if sam2_predictor is None:
            print("[ComfyUI-VFX-Suite] SAM 2 predictor unavailable, falling back to YOLOv8.")
            use_sam2 = False

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if (device == "cuda" and torch.cuda.is_bf16_supported()) else (torch.float16 if device == "cuda" else torch.float32)
    if YOLO is None:
        raise RuntimeError("ultralytics is not installed. Please run: pip install ultralytics")
    yolo_model = YOLO("yolov8n-seg.pt")

    for i in range(total):
        src = os.path.join(d["undist"], f"frame_{i:04d}.jpg")
        if not os.path.exists(src):
            continue
        fr = cv2.imread(src)
        dynamic_m = np.zeros((h, w), dtype=np.uint8)
        person_bbox_m = np.zeros((h, w), dtype=np.uint8)

        results = yolo_model(fr, conf=0.20, classes=target_ids, device=device, verbose=False)
        candidate_boxes = []
        if len(results) > 0:
            if results[0].boxes is not None and len(results[0].boxes) > 0:
                for b_item in results[0].boxes.xyxy:
                    b_np = b_item.cpu().numpy()
                    candidate_boxes.append(b_np)
                    x1, y1 = max(0, int(b_np[0]) - 250), max(0, int(b_np[1]) - 250)
                    x2, y2 = min(w, int(b_np[2]) + 250), min(h, int(b_np[3]) + 250)
                    person_bbox_m[y1:y2, x1:x2] = 255

            if not use_sam2:
                if results[0].masks is not None:
                    for m in results[0].masks.data:
                        m_np = cv2.resize((m.cpu().numpy() * 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
                        dynamic_m = np.maximum(dynamic_m, m_np)

        if use_sam2 and sam2_predictor is not None and len(candidate_boxes) > 0:
            fr_rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
            try:
                with torch.autocast(device, dtype=dtype):
                    sam2_predictor.set_image(fr_rgb)
                    for bx in candidate_boxes:
                        out_masks, scores, _ = sam2_predictor.predict(box=bx, multimask_output=False)
                        if out_masks is not None and len(out_masks) > 0:
                            m_bin = out_masks[0]
                            if m_bin.shape[:2] != (h, w):
                                m_bin = cv2.resize(m_bin.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
                            dynamic_m = np.maximum(dynamic_m, (m_bin * 255).astype(np.uint8))
            except Exception as e:
                print(f"[ComfyUI-VFX-Suite] SAM 2 frame {i} error ({e}), falling back to YOLO.")
                if results[0].masks is not None:
                    for m in results[0].masks.data:
                        m_np = cv2.resize((m.cpu().numpy() * 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
                        dynamic_m = np.maximum(dynamic_m, m_np)

        if detect_props and np.any(person_bbox_m > 0):
            hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
            green_m = cv2.inRange(hsv, np.array([35, 50, 50]), np.array([85, 255, 255]))
            prop_m = cv2.bitwise_and(green_m, person_bbox_m)
            dynamic_m = np.maximum(dynamic_m, prop_m)

        cv2.imwrite(os.path.join(d["automask"], f"frame_{i:04d}.png"), dynamic_m)
        cv2.imwrite(os.path.join(d["finalmask"], f"frame_{i:04d}.png"), dynamic_m)

        # Update actor cutout
        rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
        rgba[:, :, 3] = dynamic_m
        cv2.imwrite(os.path.join(d["cutout"], f"frame_{i:04d}.png"), rgba)

    return save_info({"has_mask": True, "thickness": 0, "mask_engine": "sam2" if use_sam2 else "yolo"}, node_id=node_id)


def apply_corridor_key(despill_strength=1.0, refiner_strength=1.0, auto_despeckle="On", despeckle_size=400, node_id="default", frames=None, progress_cb=None):
    """
    Refines rough mask using CorridorKey (GreenFormer neural green-screen keyer).
    Inputs:
      - Plate frames from dirs["undist"] (or raw)
      - Rough mask from dirs["finalmask"] (SAM2 / YOLO / Roto)
    Outputs:
      - Refined high-detail alpha matte in dirs["finalmask"]
      - Despilled actor cutout in dirs["cutout"]
      - Clean straight foreground in dirs["fg"]
    """
    d = get_dirs(node_id)
    info = get_info(node_id)
    total = info.get("total_frames", 0)
    w = info.get("width", 1920)
    h = info.get("height", 1080)
    if total == 0:
        raise ValueError("No frames loaded yet.")

    try:
        import folder_paths as _fp; comfy_dir = _fp.base_path
        corridor_dir = os.path.join(comfy_dir, "custom_nodes", "ComfyUI-CorridorKey")
        if corridor_dir not in sys.path:
            sys.path.insert(0, corridor_dir)
        from corridor_key import CorridorKeyProcessor, CorridorKeySettings
    except Exception as e:
        raise RuntimeError(f"CorridorKey is not available: {e}")

    processor = CorridorKeyProcessor()
    settings = CorridorKeySettings(
        gamma_space="sRGB",
        despill_strength=float(despill_strength),
        refiner_strength=float(refiner_strength),
        auto_despeckle=str(auto_despeckle),
        despeckle_size=int(despeckle_size)
    )

    todo = sorted(int(i) for i in frames) if frames is not None else list(range(total))
    for n_done, i in enumerate(todo):
        if progress_cb:
            progress_cb(n_done, len(todo))
        src_p = os.path.join(d["undist"], f"frame_{i:04d}.jpg")
        if not os.path.exists(src_p):
            src_p = os.path.join(d["raw"], f"frame_{i:04d}.jpg")
        if not os.path.exists(src_p):
            continue

        mask_p = os.path.join(d["finalmask"], f"frame_{i:04d}.png")
        if not os.path.exists(mask_p):
            mask_p = os.path.join(d["automask"], f"frame_{i:04d}.png")

        fr_bgr = cv2.imread(src_p)
        fr_rgb = cv2.cvtColor(fr_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img_t = torch.from_numpy(fr_rgb).unsqueeze(0)

        if os.path.exists(mask_p):
            m_gray = cv2.imread(mask_p, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
        else:
            m_gray = np.ones((h, w), dtype=np.float32)
        mask_t = torch.from_numpy(m_gray).unsqueeze(0)

        fg_t, matte_t, proc_t, _ = processor.refine(img_t, mask_t, settings)

        refined_matte = (matte_t[0].cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
        proc_np = (proc_t[0].cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
        fg_np = (fg_t[0].cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)

        cv2.imwrite(os.path.join(d["finalmask"], f"frame_{i:04d}.png"), refined_matte)

        proc_bgr = cv2.cvtColor(proc_np, cv2.COLOR_RGB2BGR)
        rgba = cv2.cvtColor(proc_bgr, cv2.COLOR_BGR2BGRA)
        rgba[:, :, 3] = refined_matte
        cv2.imwrite(os.path.join(d["cutout"], f"frame_{i:04d}.png"), rgba)

        fg_bgr = cv2.cvtColor(fg_np, cv2.COLOR_RGB2BGR)
        cv2.imwrite(os.path.join(d["fg"], f"frame_{i:04d}.png"), fg_bgr)

    return save_info({"has_corridor_key": True, "has_mask": True}, node_id=node_id)


def apply_thickness(thickness_px, node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    total = info.get("total_frames", 0)
    if total == 0:
        raise ValueError("No frames loaded yet.")

    k_size = abs(thickness_px)
    if k_size > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_size, k_size))
    else:
        kernel = None

    for i in range(total):
        auto_p = os.path.join(d["automask"], f"frame_{i:04d}.png")
        if not os.path.exists(auto_p):
            continue
        m = cv2.imread(auto_p, cv2.IMREAD_GRAYSCALE)

        if kernel is not None and np.any(m > 0):
            if thickness_px > 0:
                m = cv2.dilate(m, kernel, iterations=1)
            elif thickness_px < 0:
                m = cv2.erode(m, kernel, iterations=1)

            if k_size >= 2:
                blur = cv2.GaussianBlur(m, (5, 5), 1.2)
                _, m = cv2.threshold(blur, 127, 255, cv2.THRESH_BINARY)

        cv2.imwrite(os.path.join(d["finalmask"], f"frame_{i:04d}.png"), m)

        undist_p = os.path.join(d["undist"], f"frame_{i:04d}.jpg")
        if os.path.exists(undist_p):
            fr = cv2.imread(undist_p)
            rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
            rgba[:, :, 3] = m
            cv2.imwrite(os.path.join(d["cutout"], f"frame_{i:04d}.png"), rgba)

    return save_info({"thickness": int(thickness_px)}, node_id=node_id)


def apply_manual_paint(frame_idx, mode, mask_png_bytes, node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    w = info.get("width", 1920)
    h = info.get("height", 1080)

    nparr = np.frombuffer(mask_png_bytes, np.uint8)
    user_img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    if user_img is None:
        return {"status": "empty", "frame": frame_idx}

    # Analyze painted pixels by color channels (Red=Add, Blue=Subtract)
    add_mask = np.zeros((user_img.shape[0], user_img.shape[1]), dtype=np.uint8)
    sub_mask = np.zeros((user_img.shape[0], user_img.shape[1]), dtype=np.uint8)

    if user_img.ndim == 3 and user_img.shape[2] >= 3:
        b = user_img[:, :, 0].astype(int)
        g = user_img[:, :, 1].astype(int)
        r = user_img[:, :, 2].astype(int)
        if user_img.shape[2] == 4:
            alpha = user_img[:, :, 3]
            painted = alpha > 10
        else:
            painted = (b > 15) | (g > 15) | (r > 15)

        # Red brush: rgba(255, 59, 92) -> R > B + 25
        # Blue brush: rgba(0, 162, 255) -> B > R + 25
        is_red = painted & (r > b + 25)
        is_blue = painted & (b > r + 25)

        add_mask[is_red] = 255
        sub_mask[is_blue] = 255

        # Neutral or monochromatic fallback
        other_painted = painted & ~is_red & ~is_blue
        if np.any(other_painted):
            if mode == "subtract":
                sub_mask[other_painted] = 255
            else:
                add_mask[other_painted] = 255
    else:
        painted = user_img > 10
        if mode == "subtract":
            sub_mask[painted] = 255
        else:
            add_mask[painted] = 255

    if add_mask.shape[:2] != (h, w):
        add_mask = cv2.resize(add_mask, (w, h), interpolation=cv2.INTER_NEAREST)
    if sub_mask.shape[:2] != (h, w):
        sub_mask = cv2.resize(sub_mask, (w, h), interpolation=cv2.INTER_NEAREST)

    final_p = os.path.join(d["finalmask"], f"frame_{frame_idx:04d}.png")
    if os.path.exists(final_p):
        cur_m = cv2.imread(final_p, cv2.IMREAD_GRAYSCALE)
    else:
        cur_m = np.zeros((h, w), dtype=np.uint8)

    if cur_m is None:
        cur_m = np.zeros((h, w), dtype=np.uint8)
    if cur_m.ndim == 3:
        cur_m = cur_m[:, :, 0]
    if cur_m.shape[:2] != (h, w):
        cur_m = cv2.resize(cur_m, (w, h), interpolation=cv2.INTER_NEAREST)

    # Apply additions and subtractions simultaneously
    if np.any(add_mask > 0):
        cur_m = np.maximum(cur_m, add_mask)
    if np.any(sub_mask > 0):
        cur_m = np.clip(cur_m.astype(int) - sub_mask.astype(int), 0, 255).astype(np.uint8)

    cv2.imwrite(final_p, cur_m)

    # Track as keyframe
    keyframes = set(info.get("keyframes", []))
    keyframes.add(int(frame_idx))
    save_info({"keyframes": sorted(list(keyframes))}, node_id=node_id)

    # Update actor cutout
    undist_p = os.path.join(d["undist"], f"frame_{frame_idx:04d}.jpg")
    if os.path.exists(undist_p):
        fr = cv2.imread(undist_p)
        rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
        rgba[:, :, 3] = cur_m
        cv2.imwrite(os.path.join(d["cutout"], f"frame_{frame_idx:04d}.png"), rgba)

    return {"status": "ok", "frame": frame_idx, "keyframes": sorted(list(keyframes))}


def clear_frame_mask(frame_idx, node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    auto_p = os.path.join(d["automask"], f"frame_{frame_idx:04d}.png")
    final_p = os.path.join(d["finalmask"], f"frame_{frame_idx:04d}.png")

    if os.path.exists(auto_p):
        cur_m = cv2.imread(auto_p, cv2.IMREAD_GRAYSCALE)
    else:
        cur_m = np.zeros((info.get("height", 1080), info.get("width", 1920)), dtype=np.uint8)

    if cur_m is None:
        cur_m = np.zeros((info.get("height", 1080), info.get("width", 1920)), dtype=np.uint8)
    if cur_m.ndim == 3:
        cur_m = cur_m[:, :, 0]

    cv2.imwrite(final_p, cur_m)

    keyframes = set(info.get("keyframes", []))
    if int(frame_idx) in keyframes:
        keyframes.remove(int(frame_idx))
    save_info({"keyframes": sorted(list(keyframes))}, node_id=node_id)

    # Update actor cutout
    undist_p = os.path.join(d["undist"], f"frame_{frame_idx:04d}.jpg")
    if os.path.exists(undist_p):
        fr = cv2.imread(undist_p)
        rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
        rgba[:, :, 3] = cur_m
        cv2.imwrite(os.path.join(d["cutout"], f"frame_{frame_idx:04d}.png"), rgba)

    return {"status": "ok", "frame": frame_idx, "keyframes": sorted(list(keyframes))}


def reset_all_masks(node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    total = info.get("total_frames", 0)
    for i in range(total):
        auto_p = os.path.join(d["automask"], f"frame_{i:04d}.png")
        final_p = os.path.join(d["finalmask"], f"frame_{i:04d}.png")
        if os.path.exists(auto_p):
            cur_m = cv2.imread(auto_p, cv2.IMREAD_GRAYSCALE)
        else:
            cur_m = np.zeros((info.get("height", 1080), info.get("width", 1920)), dtype=np.uint8)

        if cur_m is None:
            cur_m = np.zeros((info.get("height", 1080), info.get("width", 1920)), dtype=np.uint8)
        if cur_m.ndim == 3:
            cur_m = cur_m[:, :, 0]

        cv2.imwrite(final_p, cur_m)

        undist_p = os.path.join(d["undist"], f"frame_{i:04d}.jpg")
        if os.path.exists(undist_p):
            fr = cv2.imread(undist_p)
            rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
            rgba[:, :, 3] = cur_m
            cv2.imwrite(os.path.join(d["cutout"], f"frame_{i:04d}.png"), rgba)

    return save_info({"keyframes": []}, node_id=node_id)


def _compute_sdt(mask):
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    _, binary = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
    dist_inside = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    dist_outside = cv2.distanceTransform(255 - binary, cv2.DIST_L2, 5)
    return dist_inside - dist_outside


def _get_centroid(mask):
    M = cv2.moments(mask)
    h, w = mask.shape[:2]
    if M["m00"] == 0:
        return np.array([w / 2.0, h / 2.0])
    return np.array([M["m10"] / M["m00"], M["m01"] / M["m00"]])


def _shift_mask(mask, dx, dy):
    h, w = mask.shape[:2]
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(mask, M, (w, h), flags=cv2.INTER_NEAREST)


def interpolate_keyframes(node_id="default"):
    d = get_dirs(node_id)
    info = get_info(node_id)
    keyframes = sorted(info.get("keyframes", []))
    if len(keyframes) < 2:
        return {"status": "need_at_least_2_keyframes", "keyframes": keyframes}

    total_interpolated = 0

    for idx in range(len(keyframes) - 1):
        f_start = keyframes[idx]
        f_end = keyframes[idx + 1]
        span = f_end - f_start
        if span <= 1:
            continue

        p_start = os.path.join(d["finalmask"], f"frame_{f_start:04d}.png")
        p_end = os.path.join(d["finalmask"], f"frame_{f_end:04d}.png")
        if not os.path.exists(p_start) or not os.path.exists(p_end):
            continue

        m_start = cv2.imread(p_start, cv2.IMREAD_GRAYSCALE)
        m_end = cv2.imread(p_end, cv2.IMREAD_GRAYSCALE)

        c_start = _get_centroid(m_start)
        c_end = _get_centroid(m_end)

        for step in range(1, span):
            cur_fid = f_start + step
            alpha = float(step) / float(span)

            c_interp = c_start * (1.0 - alpha) + c_end * alpha
            m_s_aligned = _shift_mask(m_start, c_interp[0] - c_start[0], c_interp[1] - c_start[1])
            m_e_aligned = _shift_mask(m_end, c_interp[0] - c_end[0], c_interp[1] - c_end[1])

            sdt_s = _compute_sdt(m_s_aligned)
            sdt_e = _compute_sdt(m_e_aligned)
            sdt_interp = sdt_s * (1.0 - alpha) + sdt_e * alpha

            m_morph = np.where(sdt_interp > 0, 255, 0).astype(np.uint8)
            cv2.imwrite(os.path.join(d["finalmask"], f"frame_{cur_fid:04d}.png"), m_morph)

            undist_p = os.path.join(d["undist"], f"frame_{cur_fid:04d}.jpg")
            if os.path.exists(undist_p):
                fr = cv2.imread(undist_p)
                rgba = cv2.cvtColor(fr, cv2.COLOR_BGR2BGRA)
                rgba[:, :, 3] = m_morph
                cv2.imwrite(os.path.join(d["cutout"], f"frame_{cur_fid:04d}.png"), rgba)

            total_interpolated += 1

    return {"status": "ok", "interpolated_frames": total_interpolated, "keyframes": keyframes}


def generate_tinted_mask_overlay_png(frame_idx, node_id="default"):
    """
    Returns bytes of a true RGBA PNG where white mask pixels are semi-transparent ruby,
    and black background is 100% transparent. Eliminates client-side CORS & tainted canvas crashes.
    """
    d = get_dirs(node_id)
    f_path = os.path.join(d["finalmask"], f"frame_{frame_idx:04d}.png")
    if not os.path.exists(f_path):
        f_path = os.path.join(d["finalmask"], "frame_0000.png")

    info = get_info(node_id)
    w = info.get("width", 1920)
    h = info.get("height", 1080)

    if os.path.exists(f_path):
        m_gray = cv2.imread(f_path, cv2.IMREAD_GRAYSCALE)
        if m_gray is not None:
            if m_gray.shape[:2] != (h, w):
                m_gray = cv2.resize(m_gray, (w, h), interpolation=cv2.INTER_NEAREST)
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            invert = bool(info.get("invert_mask", False))
            mask_pts = (m_gray <= 20) if invert else (m_gray > 20)
            # Ruby Tint in BGRA
            rgba[mask_pts, 0] = 80   # B
            rgba[mask_pts, 1] = 45   # G
            rgba[mask_pts, 2] = 255  # R
            rgba[mask_pts, 3] = 140  # Alpha (~55%)
            success, enc = cv2.imencode(".png", rgba)
            if success:
                return enc.tobytes()

    # Fallback transparent PNG
    empty_rgba = np.zeros((h, w, 4), dtype=np.uint8)
    _, enc = cv2.imencode(".png", empty_rgba)
    return enc.tobytes()
