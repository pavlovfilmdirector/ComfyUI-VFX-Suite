"""
1D image generation (FLUX.3 Klein)
==================================
One entry point for everything the 1D workspace generates:

- mode "t2i":  text -> image (optional boxes from masks drawn on an empty canvas are ignored)
- mode "edit": source image + instruction, with
    * mask layers   -> named bounding boxes (FLUX.3 layout prompt) and, optionally,
                       "change only inside masks" (result composited back onto the source)
    * depth / canny / pose layers and other images -> extra reference images
    * frame notes   -> scene context appended to the prompt

Templates: workflows/api/storyboard/flux3_klein_t2i.json and flux3_klein_edit.json
(extra references are added to the edit template on the fly).
"""

import base64
import hashlib
import os
import random
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

try:
    from .process_runner import ProcessError, get_run_status, load_template, run_process
except ImportError:  # pragma: no cover
    from process_runner import ProcessError, get_run_status, load_template, run_process

PROCESS_ID = "storyboard"
ROLE_HINTS = {
    "depth": "is a depth map: keep its spatial layout, camera and object distances",
    "canny": "is an edge map: keep these outlines and composition",
    "pose": "is a pose skeleton: the person takes exactly this pose",
    "reference": "is a reference: take from it what the instruction asks for",
    "mask": "is a mask: white marks the area the instruction refers to",
    "annotation": ("is image 1 with hand-drawn notes (arrows, circles, scribbles, written words) from the director: "
                   "apply what the marks ask for to image 1; the result must not contain any of these marks"),
}

# Drawings on the image (paint layers) in edit mode:
#   sketch     - rough strokes are baked into the source; Klein turns them into real objects in the scene's style
#   annotation - an annotated copy goes in as a reference: arrows / circles / words are instructions, not content
#   collage    - pasted cut-outs are baked into the source; Klein blends them in (light, shadows, perspective)
DRAWING_PROMPTS = {
    "sketch": ("The rough hand-drawn strokes in image 1 ({name}) are a sketch: render them as real, photographic objects "
               "that belong to the scene, matching its light, perspective and style; remove every visible stroke."),
    "collage": ("Image 1 is a collage: the pasted cut-outs ({name}) must be blended naturally into the scene - match light, "
                "colour, shadows, perspective and grain so nothing looks pasted."),
}
ASPECTS = {"16:9": (1344, 768), "1:1": (1024, 1024), "9:16": (768, 1344), "4:3": (1184, 880), "2.39:1": (1536, 640)}


def _decode(data_url: str) -> bytes:
    if not isinstance(data_url, str) or "," not in data_url:
        raise ProcessError("Expected an image data URL")
    return base64.b64decode(data_url.split(",", 1)[1])


def _to_input(data_url: str, tag: str) -> str:
    import folder_paths
    raw = _decode(data_url)
    ext = ".png" if data_url.startswith("data:image/png") else ".jpg"
    name = f"keshiki_{tag}_{hashlib.sha1(raw).hexdigest()[:16]}{ext}"
    path = os.path.join(folder_paths.get_input_directory(), name)
    if not os.path.isfile(path):
        with open(path, "wb") as f:
            f.write(raw)
    return name


def _overlay(base_png: bytes, layer_data_url: str) -> bytes:
    """Draws an RGBA layer (same aspect, any size) over the base image; returns PNG bytes."""
    base = cv2.imdecode(np.frombuffer(base_png, np.uint8), cv2.IMREAD_COLOR)
    lay = cv2.imdecode(np.frombuffer(_decode(layer_data_url), np.uint8), cv2.IMREAD_UNCHANGED)
    if base is None or lay is None:
        raise ProcessError("Could not decode a drawing layer")
    h, w = base.shape[:2]
    lay = cv2.resize(lay, (w, h), interpolation=cv2.INTER_AREA)
    if lay.ndim == 2:
        lay = cv2.cvtColor(lay, cv2.COLOR_GRAY2BGRA)
    elif lay.shape[2] == 3:
        lay = np.dstack([lay, np.full((h, w), 255, np.uint8)])
    a = lay[..., 3:4].astype(np.float32) / 255.0
    out = (lay[..., :3].astype(np.float32) * a + base.astype(np.float32) * (1 - a)).astype(np.uint8)
    ok, buf = cv2.imencode(".png", out)
    return buf.tobytes()


def _png_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _mask_alpha(data_url: str) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(_decode(data_url), np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ProcessError("Could not decode mask")
    if img.ndim == 3 and img.shape[2] == 4:
        return img[..., 3]
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return gray


def _box_from_mask(alpha: np.ndarray) -> Optional[Dict[str, int]]:
    ys, xs = np.where(alpha > 16)
    if len(xs) == 0:
        return None
    h, w = alpha.shape[:2]
    x0, x1 = xs.min() / w, (xs.max() + 1) / w
    y0, y1 = ys.min() / h, (ys.max() + 1) / h
    return {"x": int(x0 * 1000), "y": int(y0 * 1000), "width": max(1, int((x1 - x0) * 1000)), "height": max(1, int((y1 - y0) * 1000))}


def _output_file(item: Dict[str, Any]) -> str:
    import folder_paths
    base = folder_paths.get_output_directory() if item.get("type", "output") == "output" else folder_paths.get_temp_directory()
    return os.path.join(base, item.get("subfolder", ""), item["filename"])


def _wait(prompt_id: str, timeout_s: float) -> bytes:
    started = time.time()
    while time.time() - started < timeout_s:
        st = get_run_status(prompt_id)
        if st["status"] == "cancelled":
            raise ProcessError("Stopped")
        if st["status"] == "error":
            msgs = [e.get("exception_message", "").strip() for e in (st.get("errors") or []) if isinstance(e, dict)]
            raise ProcessError("Generation failed: " + ("; ".join(m for m in msgs if m) or str(st.get("errors"))))
        if st["status"] == "done":
            outs = [o for o in st["outputs"] if o.get("kind") == "images" and o.get("type") == "output"]
            if not outs:
                raise ProcessError("Workflow finished without an image")
            with open(_output_file(outs[-1]), "rb") as f:
                return f.read()
        time.sleep(1.0)
    raise ProcessError(f"Timed out after {int(timeout_s)} s (prompt {prompt_id} may still be running)")


def _composite_inside_masks(source_png: bytes, result_png: bytes, masks: List[np.ndarray], feather_px: int = 6) -> bytes:
    src = cv2.imdecode(np.frombuffer(source_png, np.uint8), cv2.IMREAD_COLOR)
    res = cv2.imdecode(np.frombuffer(result_png, np.uint8), cv2.IMREAD_COLOR)
    h, w = res.shape[:2]
    src = cv2.resize(src, (w, h), interpolation=cv2.INTER_AREA)
    union = np.zeros((h, w), np.float32)
    for m in masks:
        union = np.maximum(union, cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0)
    if feather_px > 0:
        k = feather_px * 2 + 1
        union = cv2.GaussianBlur(union, (k, k), feather_px)
    a = union[..., None]
    out = (res.astype(np.float32) * a + src.astype(np.float32) * (1 - a)).astype(np.uint8)
    ok, buf = cv2.imencode(".png", out)
    return buf.tobytes()


def run_image_generate(params: Dict[str, Any], timeout_s: float = 900.0) -> Dict[str, Any]:
    mode = params.get("mode", "edit")
    instruction = str(params.get("prompt") or "").strip()
    context = str(params.get("context") or "").strip()
    if not instruction and not context:
        raise ProcessError("Write what to generate")
    seed = int(params.get("seed") or random.randint(1, 2**31 - 1))
    masks = [m for m in (params.get("masks") or []) if m.get("data")]
    refs = [r for r in (params.get("references") or []) if r.get("data")][:4]

    boxes, mask_arrays = [], []
    for m in masks:
        alpha = _mask_alpha(m["data"])
        box = _box_from_mask(alpha)
        if not box:
            continue
        mask_arrays.append(alpha)
        desc = str(m.get("name") or "object")
        if m.get("desc"):
            desc += f": {m['desc']}"
        boxes.append({**box, "metadata": {"type": "obj", "desc": desc, "palette": []}})

    orig_source = params.get("source")
    drawings = [d for d in (params.get("drawings") or []) if d.get("data")] if mode == "edit" else []
    drawing_lines = []
    if drawings:
        if not params.get("source"):
            raise ProcessError("Edit mode needs a source image")
        src_png = _decode(params["source"])
        baked = False
        for d in drawings:
            how, name = str(d.get("mode") or "sketch"), str(d.get("name") or "drawing")
            if how == "annotation":
                refs = [{"role": "annotation", "name": name, "data": _png_data_url(_overlay(src_png, d["data"]))}] + refs
            else:
                src_png = _overlay(src_png, d["data"])
                baked = True
                drawing_lines.append(DRAWING_PROMPTS.get(how, DRAWING_PROMPTS["sketch"]).format(name=name))
            if d.get("note"):
                drawing_lines.append(f"Note on {name}: {d['note']}")
        if baked:
            params = {**params, "source": _png_data_url(src_png)}
        refs = refs[:4]

    lines = [instruction] if instruction else []
    lines += drawing_lines
    if mode == "edit" and refs:
        for i, r in enumerate(refs, start=2):
            hint = ROLE_HINTS.get(r.get("role", "reference"), ROLE_HINTS["reference"])
            lines.append(f"Image {i} ({r.get('name') or r.get('role')}) {hint}.")
    if boxes:
        lines.append("Named regions: " + "; ".join(b["metadata"]["desc"] for b in boxes) + ".")
    if context:
        lines.append(f"Scene context: {context}")
    full_prompt = "\n".join(lines)

    run_params: Dict[str, Any] = {
        "Prompt.value": full_prompt,
        "Sampler.seed": seed,
        "Boxes.editor_state": {"__value__": boxes if mode == "edit" else []},
    }

    if mode == "t2i":
        w, h = ASPECTS.get(str(params.get("aspect") or "16:9"), ASPECTS["16:9"])
        run_params.update({"Output size.width": w, "Output size.height": h})
        prompt = load_template(PROCESS_ID, "flux3_klein_t2i")
        source_bytes = None
    else:
        source = params.get("source")
        if not source:
            raise ProcessError("Edit mode needs a source image")
        source_bytes = _decode(source)
        run_params["Source Image.image"] = _to_input(source, "src")
        run_params["Scale.megapixels"] = float(params.get("megapixels") or 2)
        prompt = load_template(PROCESS_ID, "flux3_klein_edit")
        for i, r in enumerate(refs, start=1):
            nid = f"ref{i}"
            prompt[nid] = {"class_type": "LoadImage", "inputs": {"image": _to_input(r["data"], "ref")}, "_meta": {"title": f"Reference {i}"}}
            prompt["136"]["inputs"][f"images.image{i}"] = [nid, 0]

    queued = run_process(PROCESS_ID, prompt=prompt, params=run_params, client_id="keshiki_1d_generate", run_tag=params.get("run_tag"))
    started = time.time()
    result = _wait(queued["prompt_id"], timeout_s)

    only_inside = bool(params.get("only_inside_masks")) and mode == "edit" and mask_arrays
    if only_inside:
        result = _composite_inside_masks(_decode(orig_source) if orig_source else source_bytes, result, mask_arrays)

    return {
        "image": "data:image/png;base64," + base64.b64encode(result).decode("ascii"),
        "prompt_used": full_prompt,
        "seed": seed,
        "mode": mode,
        "boxes": len(boxes),
        "references": len(refs),
        "only_inside_masks": bool(only_inside),
        "seconds": round(time.time() - started, 1),
        "prompt_id": queued["prompt_id"],
    }


def run_qwen_edit(images: List[str], prompt: str, seed: Optional[int] = None, size_from: int = 0,
                  megapixels: float = 1.0, run_tag: Optional[str] = None, timeout_s: float = 1200.0,
                  loras: Optional[List[tuple]] = None) -> Dict[str, Any]:
    """Qwen-Image-Edit-2511 (Apache 2.0) with up to 3 images (Lightning 4 steps). Keeps a character's identity and
    a scene far better than Klein, but does not follow a drawn skeleton. The output takes the size of
    images[size_from]. Weights load as bf16 ("default"): ComfyUI then offloads part of the 40 GB to RAM and runs
    at ~4.4 s/step on a 24 GB card; casting to fp8 made it load "completely" and spill into shared memory."""
    images = [i for i in images if i][:3]
    if not images:
        raise ProcessError("Qwen edit needs at least one image")
    seed = int(seed or random.randint(1, 2**31 - 1))
    p = load_template(PROCESS_ID, "qwen_image_edit_2511")
    for i, data in enumerate(images, start=1):
        load_id, scale_id = f"2{i}", f"3{i}"
        p[load_id] = {"class_type": "LoadImage", "inputs": {"image": _to_input(data, "qref")}, "_meta": {"title": f"Image {i}"}}
        p[scale_id] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": [load_id, 0], "upscale_method": "lanczos",
                       "megapixels": float(megapixels), "resolution_steps": 16}, "_meta": {"title": f"Scale {i}"}}
        for enc in ("10", "11"):
            p[enc]["inputs"][f"image{i}"] = [scale_id, 0]
        # reference latents at our own size (the encode node would scale every image to 1 MP, which does not fit
        # a 24 GB card with several references)
        p[f"6{i}"] = {"class_type": "VAEEncode", "inputs": {"pixels": [scale_id, 0], "vae": ["3", 0]}, "_meta": {"title": f"Ref latent {i}"}}
        for side, prev in (("p", "10"), ("n", "11")):
            nid = f"7{i}{side}"
            before = f"7{i-1}{side}" if i > 1 else prev
            p[nid] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": [before, 0], "latent": [f"6{i}", 0]}, "_meta": {"title": f"Reference {i}"}}
    last = len(images)
    p["12"]["inputs"]["conditioning"] = [f"7{last}p", 0]
    p["13"]["inputs"]["conditioning"] = [f"7{last}n", 0]
    for enc in ("10", "11"):
        p[enc]["inputs"].pop("vae", None)   # vision tokens only; latents come from the ReferenceLatent chain
    # extra LoRAs (e.g. Light-Migration) chained after the Lightning LoRA
    prev = "4"
    for k, (lname, lstr) in enumerate(loras or []):
        nid = f"4x{k}"
        p[nid] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": [prev, 0], "lora_name": lname, "strength_model": float(lstr)},
                  "_meta": {"title": f"LoRA {k + 1}"}}
        prev = nid
    p["5"]["inputs"]["model"] = [prev, 0]
    p["10"]["inputs"]["prompt"] = prompt
    p["40"]["inputs"]["image"] = [f"3{min(size_from, len(images) - 1) + 1}", 0]
    p["50"]["inputs"]["seed"] = seed
    started = time.time()
    queued = run_process(PROCESS_ID, prompt=p, params={}, client_id="keshiki_1d_generate", run_tag=run_tag)
    result = _wait(queued["prompt_id"], timeout_s)
    return {"image": "data:image/png;base64," + base64.b64encode(result).decode("ascii"), "seed": seed,
            "model": "qwen_image_edit_2511", "seconds": round(time.time() - started, 1)}
