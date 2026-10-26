"""
Single-image object segmentation for the 1D workspace
=====================================================
- detect_objects():   YOLOv8-seg finds objects (any COCO class), each refined by SAM2 from its box.
- segment_by_points(): SAM2 click segmentation (positive / negative points, optional box).

Masks are returned as PNG data URLs (white RGB, alpha = mask) so the UI can tint
them with the layer's ID color and store each object as its own mask layer.
"""

import base64
import hashlib
import os
import threading
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

try:
    import torch
except Exception:  # pragma: no cover
    torch = None

try:
    from .plate_prep_service import get_sam2_predictor
except ImportError:  # pragma: no cover
    from plate_prep_service import get_sam2_predictor

_LOCK = threading.Lock()
_YOLO = None
_SAM2_IMAGE_KEY = None


def _decode_image(data_url: str) -> np.ndarray:
    if not isinstance(data_url, str) or "," not in data_url:
        raise ValueError("Expected an image data URL")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image")
    return img


def _mask_to_data_url(mask: np.ndarray) -> str:
    h, w = mask.shape[:2]
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., :3] = 255
    rgba[..., 3] = (mask > 0).astype(np.uint8) * 255
    ok, buf = cv2.imencode(".png", rgba)
    if not ok:
        raise RuntimeError("PNG encode failed")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _device():
    return "cuda" if (torch is not None and torch.cuda.is_available()) else "cpu"


def _autocast():
    dev = _device()
    if torch is None or dev != "cuda":
        import contextlib
        return contextlib.nullcontext()
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    return torch.autocast(dev, dtype=dtype)


def _get_yolo():
    global _YOLO
    if _YOLO is not None:
        return _YOLO
    from ultralytics import YOLO
    candidates = []
    try:
        import folder_paths
        candidates.append(os.path.join(folder_paths.models_dir, "ultralytics", "segm", "yolov8n-seg.pt"))
        candidates.append(os.path.join(os.path.dirname(folder_paths.models_dir), "yolov8n-seg.pt"))
    except Exception:
        pass
    path = next((c for c in candidates if os.path.isfile(c)), "yolov8n-seg.pt")  # name -> auto-download
    _YOLO = YOLO(path)
    return _YOLO


def _sam2_set_image(predictor, img_bgr: np.ndarray):
    """Embeds the image once; repeated clicks on the same image reuse the embedding."""
    global _SAM2_IMAGE_KEY
    key = hashlib.sha1(img_bgr.tobytes()[:: max(1, img_bgr.size // 200000)]).hexdigest() + f"{img_bgr.shape}"
    if key != _SAM2_IMAGE_KEY:
        predictor.set_image(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
        _SAM2_IMAGE_KEY = key


def _fit(mask: np.ndarray, h: int, w: int) -> np.ndarray:
    m = mask.astype(np.uint8)
    if m.shape[:2] != (h, w):
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
    return m


def detect_objects(image_data_url: str, classes: Optional[List[str]] = None, conf: float = 0.25,
                   refine_with_sam2: bool = True, max_objects: int = 20) -> Dict[str, Any]:
    img = _decode_image(image_data_url)
    h, w = img.shape[:2]
    with _LOCK:
        model = _get_yolo()
        names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
        class_ids = None
        if classes:
            wanted = {c.strip().lower() for c in classes if c and c.strip()}
            class_ids = [i for i, n in names.items() if n.lower() in wanted] or None
        res = model(img, conf=conf, classes=class_ids, device=_device(), verbose=False)[0]
        objects = []
        if res.boxes is None or len(res.boxes) == 0:
            return {"engine": "yolo", "width": w, "height": h, "objects": []}

        predictor = get_sam2_predictor() if refine_with_sam2 else None
        engine = "yolo+sam2" if predictor is not None else "yolo"
        if predictor is not None:
            with _autocast():
                _sam2_set_image(predictor, img)

        order = np.argsort(-res.boxes.conf.cpu().numpy())[:max_objects]
        counters: Dict[str, int] = {}
        for idx in order:
            cls_name = names.get(int(res.boxes.cls[idx]), "object")
            box = res.boxes.xyxy[idx].cpu().numpy()
            mask = None
            if predictor is not None:
                try:
                    with _autocast():
                        m, _, _ = predictor.predict(box=box, multimask_output=False)
                    if m is not None and len(m):
                        mask = _fit(m[0], h, w)
                except Exception as e:
                    print(f"[VFX-Suite] SAM2 refine failed, using YOLO mask: {e}")
            if mask is None and res.masks is not None:
                mask = _fit(res.masks.data[idx].cpu().numpy() > 0.5, h, w)
            if mask is None or not mask.any():
                continue
            counters[cls_name] = counters.get(cls_name, 0) + 1
            objects.append({
                "label": f"{cls_name} {counters[cls_name]}",
                "class": cls_name,
                "confidence": round(float(res.boxes.conf[idx]), 3),
                "box": [round(float(v), 1) for v in box],
                "mask": _mask_to_data_url(mask),
            })
    return {"engine": engine, "width": w, "height": h, "objects": objects}


def segment_by_points(image_data_url: str, points: List[Dict[str, Any]], box: Optional[List[float]] = None) -> Dict[str, Any]:
    img = _decode_image(image_data_url)
    h, w = img.shape[:2]
    if not points and not box:
        raise ValueError("Give at least one point or a box")
    with _LOCK:
        predictor = get_sam2_predictor()
        if predictor is None:
            raise RuntimeError("SAM2 is not available: install ComfyUI-segment-anything-2 and put sam2 weights in models/sam2")
        with _autocast():
            _sam2_set_image(predictor, img)
            kwargs: Dict[str, Any] = {"multimask_output": len(points) == 1 and not box}
            if points:
                kwargs["point_coords"] = np.array([[float(p["x"]), float(p["y"])] for p in points], dtype=np.float32)
                kwargs["point_labels"] = np.array([1 if p.get("positive", True) else 0 for p in points], dtype=np.int32)
            if box:
                kwargs["box"] = np.array(box, dtype=np.float32)
            masks, scores, _ = predictor.predict(**kwargs)
    best = int(np.argmax(scores)) if scores is not None and len(scores) > 1 else 0
    mask = _fit(masks[best], h, w)
    return {"engine": "sam2", "width": w, "height": h, "score": round(float(scores[best]), 3), "mask": _mask_to_data_url(mask)}
