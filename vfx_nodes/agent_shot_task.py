"""A whole shot task in one job: the studio agent turns a plain-text request ("track the shot, put it in a
ghetto street, add two capoeira dancers, give me three variants...") into a step plan, and this job runs the
steps one after another on the server, feeding each step's result (mask, pose, first frame...) into the next.

Plan (params of the `shot_task` video job):
    start, end            frame range (one LTX pass: up to 97 frames)
    subject               SAM text for the main character ("woman", "person"...)
    track                 solve the camera (GLOMAP) with the subject masked out
    background            new environment (English prompt text) - for the "bg" / "both" variants
    character             new character (English prompt text) - for the "char" / "both" variants
    dancers               {"count": 2, "action": "dancing capoeira", "who": "..."}: extra people in the background.
                          They come from 3D (skeletons through the tracked camera -> Pose to Gen layer); the job
                          waits for that layer (`dancers_wait_min`), then goes on without them if it never comes.
    variants              ["bg", "char", "both"]
    motion                camera move words for the video prompt
Every step reuses what the video already has (an existing mask / pose / depth layer of the same kind).
"""
import os
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

UNION = "LTX-2.3\\ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors"


def _vls():
    try:
        from . import video_layers_service as v
    except ImportError:  # pragma: no cover
        import video_layers_service as v
    return v


def _log(job_id: str, steps: List[Dict[str, Any]], i: int, status: str, note: str = "", progress: Optional[int] = None):
    v = _vls()
    steps[i]["status"] = status
    if note:
        steps[i]["note"] = note
    done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
    v._job_update(job_id, steps=[dict(s) for s in steps],
                  progress=progress if progress is not None else int(100 * done / max(1, len(steps))),
                  message=f"{i + 1}/{len(steps)} · {steps[i]['title']}" + (f" — {note}" if note else ""))


def _unload_ollama():
    """The chat model (Ollama, up to ~20 GB) must not sit in VRAM while FLUX / LTX run."""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=2) as r:
            models = [m.get("name") for m in _json.loads(r.read().decode()).get("models", [])]
        for m in models:
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=_json.dumps({"model": m, "keep_alive": 0}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


HEAVY = {"ltx_ic_lora", "ltx_alpha", "track_camera", "relight_frame", "first_frame", "depth", "text_mask", "corridor_key"}


def _run_child(job_id: str, kind: str, pid: str, vid: str, params: Dict[str, Any], steps, i) -> Dict[str, Any]:
    """Starts a normal studio job and waits for it, showing its progress inside this step."""
    v = _vls()
    if kind in HEAVY:
        _unload_ollama()
    child = v.start_job(kind, pid, vid, params)
    cid = child["id"]
    steps[i]["job_id"] = cid
    while True:
        if v._cancelled(job_id):
            v.cancel_job(cid)
            raise v.ProcessError("Stopped")
        j = v.get_job(cid)
        if j.get("status") not in ("running", "queued", "starting"):
            break
        _log(job_id, steps, i, "running", f"{j.get('message', '')}")
        time.sleep(3)
    if j.get("status") != "done":
        raise v.ProcessError(f"{steps[i]['title']}: {j.get('message') or j.get('status')}")
    return j.get("result") or {}


def _layers(pid, vid) -> List[Dict[str, Any]]:
    return (_vls().load_manifest(pid, vid) or {}).get("layers", [])


def _find_layer(pid, vid, pred) -> Optional[Dict[str, Any]]:
    for l in reversed(_layers(pid, vid)):
        try:
            if pred(l):
                return l
        except Exception:
            pass
    return None


def _merge_pose_layers(pid, vid, lids: List[str], name: str, start: int, end: int) -> Dict[str, Any]:
    """Several pose layers -> one (the character + the dancers): per pixel the brighter stroke wins."""
    v = _vls()
    layer = v._add_layer(pid, vid, "pose", name, extra={"transparentBg": True, "source": "agent_merge", "merged_from": lids})
    out = v._layer_dir(pid, vid, layer["id"])
    n = 0
    for f in range(start, end + 1):
        acc = None
        for lid in lids:
            fp = v.frame_path(pid, vid, lid, f)
            im = cv2.imread(fp, cv2.IMREAD_UNCHANGED) if fp else None
            if im is None:
                continue
            if im.ndim == 2:
                im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGRA)
            elif im.shape[2] == 3:
                a = (im.max(axis=2) > 20).astype(np.uint8) * 255
                im = np.dstack([im, a])
            if acc is None:
                acc = im.copy()
            else:
                if im.shape[:2] != acc.shape[:2]:
                    im = cv2.resize(im, (acc.shape[1], acc.shape[0]), interpolation=cv2.INTER_NEAREST)
                m = im[..., 3] > acc[..., 3]
                acc[m] = im[m]
        if acc is not None:
            cv2.imwrite(os.path.join(out, f"frame_{f:04d}.png"), acc)
            n += 1
    man = v.load_manifest(pid, vid)
    for l in man["layers"]:
        if l["id"] == layer["id"]:
            l["frames"] = end + 1
    v.save_manifest(pid, vid, man)
    layer["frames"] = n
    return layer


FIRST_FRAME_TEXT = {
    "bg": ("Keep the {subject} exactly as is - same face, hair, clothes, pose, position, size and lighting direction. "
           "Replace everything behind {subject_it} with {background}.{dancers} Same camera angle, lens and framing. "
           "Photorealistic film still."),
    "char": ("Replace the {subject} with {character}, in exactly the same pose, position, size and framing. "
             "Keep the background and the objects around exactly the same. Photorealistic film still."),
    "both": ("Replace the {subject} with {character}, in exactly the same pose, position, size and framing, and replace "
             "everything behind with {background}.{dancers} Same camera angle and lens. Photorealistic film still."),
}
VARIANT_TITLE = {"bg": "замена фона", "char": "замена персонажа", "both": "фон + персонаж"}
VARIANT_TITLE_EN = {"bg": "new background", "char": "new character", "both": "background + character"}


def _tr(p: Dict[str, Any]):
    """Messages in the language the user wrote the task in (p[\"lang\"]: ru / en)."""
    ru = str(p.get("lang") or "ru") == "ru"
    return lambda r, e: r if ru else e


def _dancers_text(d: Optional[Dict[str, Any]]) -> str:
    if not d:
        return ""
    n = int(d.get("count") or 2)
    who = str(d.get("who") or "people").strip()
    act = str(d.get("action") or "dancing").strip()
    return f" In the background {n} {who} are {act}."


def run_shot_task(job_id: str, kind: str, pid: str, vid: str, p: Dict[str, Any]) -> Dict[str, Any]:
    v = _vls()
    if p.get("steps"):
        return run_free_plan(job_id, pid, vid, p)
    T = _tr(p)
    VT = VARIANT_TITLE if str(p.get("lang") or "ru") == "ru" else VARIANT_TITLE_EN
    t_start = time.time()
    info = v.pps.get_info(v.node_id_for(vid)) or {}
    variants = [x for x in (p.get("variants") or []) if x in FIRST_FRAME_TEXT]
    dancers = p.get("dancers") or None
    subject = str(p.get("subject") or "person").strip()
    steps: List[Dict[str, Any]] = []
    add = lambda key, title: steps.append({"key": key, "title": title, "status": "todo"})
    if not info.get("total_frames"):
        add("prepare", T("Подготовка плейта (кадры)", "Prepare the plate (frames)"))
    add("mask", T(f"Маска персонажа «{subject}» (SAM)", f"Mask of the {subject} (SAM)"))
    if p.get("track"):
        add("track", T("Трекинг камеры (GLOMAP)", "Camera track (GLOMAP)"))
    add("pose", T("Поза персонажа (DWPose)", "Character pose (DWPose)"))
    if "char" in variants:
        add("depth", T("Карта глубины", "Depth map"))
    if dancers:
        add("dancers", T(f"Скелеты в 3D: {int(dancers.get('count') or 2)} × {dancers.get('action') or 'танец'} → Pose to Gen",
                         f"Skeletons in 3D: {int(dancers.get('count') or 2)} × {dancers.get('action') or 'dancing'} → Pose to Gen"))
    for var in variants:
        add(f"ff_{var}", T("Первый кадр", "First frame") + f": {VT[var]} (FLUX)")
        add(f"ltx_{var}", T("Видео", "Video") + f": {VT[var]} (LTX union-control)")
    v._job_update(job_id, steps=[dict(s) for s in steps], plan=p)
    idx = {s["key"]: i for i, s in enumerate(steps)}
    out: Dict[str, Any] = {"layers": {}, "images": {}, "videos": []}

    # 0 plate
    if "prepare" in idx:
        i = idx["prepare"]
        _log(job_id, steps, i, "running")
        _run_child(job_id, "prepare", pid, vid, {"url": p.get("video_url", "")}, steps, i)
        info = v.pps.get_info(v.node_id_for(vid)) or {}
        _log(job_id, steps, i, "done")
    total = int(info.get("total_frames") or 0)
    if not total:
        raise v.ProcessError("The plate has no frames")
    start = max(0, int(p.get("start", 0) or 0))
    end = min(total - 1, int(p.get("end", start + 96) if p.get("end") is not None else start + 96))
    if end - start + 1 > 97:
        end = start + 96
    out["range"] = [start, end]

    # 1 mask of the main character
    i = idx["mask"]
    _log(job_id, steps, i, "running")
    generic = subject.lower() in ("person", "people", "human", "character")
    m = _find_layer(pid, vid, lambda l: l.get("type") in ("mask", "matte") and (
        str(l.get("text") or l.get("name") or "").strip().lower() == subject.lower()
        or (generic and str(l.get("engine") or "").startswith("sam") and l.get("text"))))
    if m:
        _log(job_id, steps, i, "done", T("уже есть", "already there") + f": {m.get('name')}")
    else:
        m = _run_child(job_id, "text_mask", pid, vid, {"text": subject}, steps, i).get("layer") or {}
        _log(job_id, steps, i, "done")
    mask_id = m.get("id")
    out["layers"]["mask"] = mask_id

    # 2 camera
    if "track" in idx:
        i = idx["track"]
        _log(job_id, steps, i, "running")
        prev = os.path.join(v.video_root(pid, vid), "track", "scene_data.json")
        try:
            import json as _json
            with open(prev, encoding="utf-8") as f:
                old = _json.load(f)
            cam = old.get("camera") or {}
            if int(cam.get("start_frame", -1)) == start and int(old.get("total_frames") or 0) >= end - start and not p.get("retrack"):
                # the shot is already solved: put it back as the project's 3D scene and go on
                import shutil
                dst = os.path.join(v.get_project_dir(pid), "passes", "scene_data.json")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(prev, dst)
                out["track"] = {"report": old.get("report"), "reused": True}
                _log(job_id, steps, i, "done", T("камера уже решена для этого диапазона — беру готовую", "camera already solved for this range - reusing it"))
                prev = None
        except Exception:
            pass
    if "track" in idx and steps[idx["track"]]["status"] != "done":
        i = idx["track"]
        r = _run_child(job_id, "track_camera", pid, vid, {"start": start, "end": end, "mask_layers": [mask_id] if mask_id else [],
                                                            "mask_mode": "layers" if mask_id else "yolo", "density": "standard"}, steps, i)
        out["track"] = {k: r.get(k) for k in ("report", "frames", "points", "focal_mm")}
        _log(job_id, steps, i, "done", str(r.get("report") or "")[:120])

    # 3 pose (+ depth)
    i = idx["pose"]
    _log(job_id, steps, i, "running")
    pose = _find_layer(pid, vid, lambda l: l.get("type") == "pose" and l.get("source") not in ("3d", "agent_merge"))
    if pose:
        _log(job_id, steps, i, "done", T("уже есть", "already there") + f": {pose.get('name')}")
    else:
        pose = _run_child(job_id, "pose", pid, vid, {"name": f"{subject} pose"}, steps, i).get("layer") or {}
        _log(job_id, steps, i, "done")
    out["layers"]["pose"] = pose.get("id")
    depth = None
    if "depth" in idx:
        i = idx["depth"]
        _log(job_id, steps, i, "running")
        depth = _find_layer(pid, vid, lambda l: l.get("type") == "depth")
        if depth:
            _log(job_id, steps, i, "done", T("уже есть", "already there") + f": {depth.get('name')}")
        else:
            depth = _run_child(job_id, "depth", pid, vid, {"engine": "v2"}, steps, i).get("layer") or {}
            _log(job_id, steps, i, "done")
        out["layers"]["depth"] = depth.get("id")

    # 4 dancers: skeletons placed in 3D through the tracked camera -> a Pose to Gen layer on this video
    control_pose = pose.get("id")
    if "dancers" in idx:
        i = idx["dancers"]
        wait_s = float(p.get("dancers_wait_min", 40)) * 60
        t0 = time.time()
        # a Pose to Gen layer the user already made for this shot (e.g. the task is run again) is used right away
        found = _find_layer(pid, vid, lambda l: l.get("type") == "pose" and l.get("source") == "3d")
        if found:
            wait_s = 0
        while time.time() - t0 < wait_s:
            if v._cancelled(job_id):
                raise v.ProcessError("Stopped")
            if (v.get_job(job_id) or {}).get("skip_wait"):
                break
            found = _find_layer(pid, vid, lambda l: l.get("type") == "pose" and l.get("source") == "3d"
                                and float(l.get("created_at") or 0) >= t_start - 5)
            if found:
                break
            left = int((wait_s - (time.time() - t0)) // 60)
            _log(job_id, steps, i, "waiting_user",
                 T(f"жду слой Pose to Gen из 3D (ещё ~{left} мин): 3D → добавьте {int(dancers.get('count') or 2)} скелета с анимацией, "
                   f"поставьте их за персонажем в кадре трекинг-камеры → кнопка Pose to Gen",
                   f"waiting for a Pose to Gen layer from 3D (~{left} min left): 3D → add {int(dancers.get('count') or 2)} animated skeletons, "
                   f"put them behind the character in the tracked camera's view → Pose to Gen button"))
            time.sleep(5)
        if found:
            time.sleep(3)   # let the last frames land
            merged = _merge_pose_layers(pid, vid, [pose.get("id"), found["id"]], f"{subject} + dancers · pose", start, end)
            control_pose = merged["id"]
            out["layers"]["dancers"] = found["id"]
            out["layers"]["pose_with_dancers"] = merged["id"]
            _log(job_id, steps, i, "done", T("скелеты добавлены", "skeletons added") + f": {found.get('name')}")
        else:
            dancers = None
            _log(job_id, steps, i, "skipped", T("слой из 3D не пришёл — продолжаю без скелетов", "no layer came from 3D - going on without skeletons"))

    # 5 variants: first frame (FLUX edit of the plate's first frame) -> LTX union-control video
    try:
        from .image_generate_service import run_image_generate
    except ImportError:  # pragma: no cover
        from image_generate_service import run_image_generate
    import base64
    plate0 = v._layer_frame_bgr(pid, vid, "plate_raw", start)
    subject_it = "her" if subject.lower() in ("woman", "girl", "lady") else ("him" if subject.lower() in ("man", "boy", "guy") else "them")
    bg = str(p.get("background") or "a different location").strip()
    ch = str(p.get("character") or "a different person").strip()
    motion = str(p.get("motion") or "slow cinematic camera push-in").strip()
    folder = os.path.join(v.get_project_dir(pid), "storyboard")
    os.makedirs(folder, exist_ok=True)
    for var in variants:
        i = idx[f"ff_{var}"]
        _log(job_id, steps, i, "running")
        instr = FIRST_FRAME_TEXT[var].format(subject=subject, subject_it=subject_it, background=bg, character=ch,
                                             dancers=_dancers_text(dancers))
        _unload_ollama()
        res = run_image_generate({"mode": "edit", "source": v._png_data_url(plate0), "megapixels": 1.0, "prompt": instr,
                                  "seed": p.get("seed") or None, "run_tag": job_id})
        name = f"agent_{var}_{v._safe(vid)[:30]}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        with open(os.path.join(folder, name), "wb") as f:
            f.write(base64.b64decode(res["image"].split(",", 1)[1]))
        url = f"/vfx/project_file/{pid}/storyboard/{name}"
        out["images"][var] = url
        steps[i]["image"] = url
        _log(job_id, steps, i, "done")

        i = idx[f"ltx_{var}"]
        _log(job_id, steps, i, "running")
        who = ch if var in ("char", "both") else f"the same {subject}"
        where = bg if var in ("bg", "both") else "the same place as in the first frame"
        prompt = (f"{who}, {where}.{_dancers_text(dancers) if var != 'char' else ''} The character keeps the original "
                  f"performance and movement. {motion}. Photorealistic, natural light, film grain.")
        ctrl = depth.get("id") if (var == "char" and depth) else control_pose
        r = _run_child(job_id, "ltx_ic_lora", pid, vid, {
            "lora": UNION, "needs": "control", "source_layer": ctrl, "ref_image": url, "prompt": prompt,
            "start": start, "end": end, "name": f"Agent · {VT[var]}",
        }, steps, i)
        lay = r.get("layer") or {}
        out["videos"].append({"variant": var, "layer": lay.get("id"), "name": lay.get("name"), "first_frame": url})
        _log(job_id, steps, i, "done", lay.get("name") or "")
    v._job_update(job_id, steps=[dict(s) for s in steps])
    return out


# --------------------------------------------------------------------------------------------- #
# Free plans: any chain of studio jobs the agent writes itself.                                  #
#   steps: [{"id": "s1", "kind": "text_mask", "params": {"text": "person"}, "title": "..."},       #
#           {"id": "s2", "kind": "track_camera", "params": {"mask_layers": ["$s1.layer"]}}, ...]  #
#   "$s1.layer" = the layer id a step made, "$s1.image" = the still it made (url).                 #
# Extra kinds besides the video jobs: image_edit (FLUX edit of a plate frame / image),            #
# wait_3d_pose (wait for a Pose to Gen layer from 3D), merge_pose (several pose layers -> one).   #
# --------------------------------------------------------------------------------------------- #
PSEUDO_KINDS = {"image_edit", "wait_3d_pose", "merge_pose"}
KNOWN_KINDS = ("prepare", "depth", "normal", "canny", "pose", "detect_all", "track_object", "corridor_key", "undistort", "redistort",
               "track_camera", "ltx_alpha", "ltx_ic_lora", "fill", "render", "video_cutout", "layer_to_video", "mask_grow",
               "merge_masks", "first_frame", "text_mask", "composite", "relight_frame", "montage")
NO_PLAN_KINDS = {"shot_task", "montage"}
REF_RX = __import__("re").compile(r"^\$(\w+)\.(layer|image|video)$")


def job_kinds() -> List[str]:
    import inspect
    import re
    try:
        found = set(re.findall(r'"(\w+)":\s*_job_', inspect.getsource(_vls().start_job)))
    except Exception:
        found = set()
    return sorted((found or set(KNOWN_KINDS)) | {"pose_cleanup", "layout_cleanup"})


def validate_plan(steps: Any) -> List[str]:
    """Problems a model's plan has (empty = runnable). Shown back to the model so it can fix the plan."""
    errs: List[str] = []
    if not isinstance(steps, list) or not steps:
        return ["steps must be a non-empty list"]
    if len(steps) > 25:
        errs.append("at most 25 steps")
    kinds = set(job_kinds()) | PSEUDO_KINDS
    seen: List[str] = []
    for n, st in enumerate(steps, 1):
        if not isinstance(st, dict):
            errs.append(f"step {n}: must be an object")
            continue
        sid = str(st.get("id") or f"s{n}")
        k = st.get("kind")
        if k not in kinds or k in NO_PLAN_KINDS:
            errs.append(f"step {sid}: unknown kind '{k}' (allowed: {', '.join(sorted(kinds - NO_PLAN_KINDS))})")
        prm = st.get("params") or {}
        if not isinstance(prm, dict):
            errs.append(f"step {sid}: params must be an object")
            prm = {}

        def walk(x):
            if isinstance(x, str) and x.startswith("$"):
                m = REF_RX.match(x)
                if not m:
                    errs.append(f"step {sid}: bad reference '{x}' (use $<step id>.layer or $<step id>.image)")
                elif m.group(1) not in seen:
                    errs.append(f"step {sid}: '{x}' points to a step that has not run before it")
            elif isinstance(x, list):
                for y in x:
                    walk(y)
            elif isinstance(x, dict):
                for y in x.values():
                    walk(y)
        walk(prm)
        if k == "ltx_ic_lora" and not prm.get("lora"):
            errs.append(f"step {sid}: ltx_ic_lora needs params.lora")
        if k == "text_mask" and not prm.get("text"):
            errs.append(f"step {sid}: text_mask needs params.text (English object name)")
        if k == "image_edit" and not prm.get("prompt"):
            errs.append(f"step {sid}: image_edit needs params.prompt")
        seen.append(sid)
    return errs


def _resolve(x, results: Dict[str, Dict[str, Any]]):
    if isinstance(x, str):
        m = REF_RX.match(x)
        if m:
            r = results.get(m.group(1)) or {}
            val = r.get(m.group(2))
            if val is None:
                raise _vls().ProcessError(f"{x}: that step made no {m.group(2)}")
            return val
        return x
    if isinstance(x, list):
        return [_resolve(y, results) for y in x]
    if isinstance(x, dict):
        return {k: _resolve(y, results) for k, y in x.items()}
    return x


def _norm_result(kind: str, r: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    lay = r.get("layer")
    if isinstance(lay, dict) and lay.get("id"):
        out["layer"] = lay["id"]
    img = r.get("image")
    if isinstance(img, dict) and img.get("url"):
        out["image"] = img["url"]
    elif isinstance(img, str):
        out["image"] = img
    if r.get("video_url") or r.get("url"):
        out["video"] = r.get("video_url") or r.get("url")
    return out


def run_free_plan(job_id: str, pid: str, vid: str, p: Dict[str, Any]) -> Dict[str, Any]:
    v = _vls()
    raw = p.get("steps") or []
    errs = validate_plan(raw)
    if errs:
        raise v.ProcessError("Plan is not runnable: " + "; ".join(errs[:6]))
    t_start = time.time()
    steps = [{"key": str(s.get("id") or f"s{n}"), "title": str(s.get("title") or s["kind"]), "status": "todo", "kind": s["kind"]}
             for n, s in enumerate(raw, 1)]
    v._job_update(job_id, steps=[dict(s) for s in steps], plan=p)
    results: Dict[str, Dict[str, Any]] = {}
    info = v.pps.get_info(v.node_id_for(vid)) or {}
    for i, st in enumerate(raw):
        sid = steps[i]["key"]
        k = st["kind"]
        prm = _resolve(st.get("params") or {}, results)
        _log(job_id, steps, i, "running")
        if k != "prepare" and not info.get("total_frames"):
            _run_child(job_id, "prepare", pid, vid, {"url": p.get("video_url", "")}, steps, i)
            info = v.pps.get_info(v.node_id_for(vid)) or {}
        if k == "image_edit":
            try:
                from .image_generate_service import run_image_generate
            except ImportError:  # pragma: no cover
                from image_generate_service import run_image_generate
            import base64
            if prm.get("image"):
                path = v.resolve_media_path(str(prm["image"]))
                src = cv2.imread(path, cv2.IMREAD_COLOR)
            else:
                src = v._layer_frame_bgr(pid, vid, str(prm.get("layer") or "plate_raw"), int(prm.get("frame", 0) or 0))
            if src is None:
                raise v.ProcessError(f"{sid}: no source image")
            _unload_ollama()
            res = run_image_generate({"mode": "edit", "source": v._png_data_url(src), "megapixels": float(prm.get("megapixels", 1.0)),
                                      "prompt": str(prm["prompt"]), "seed": prm.get("seed") or None, "run_tag": job_id})
            folder = os.path.join(v.get_project_dir(pid), "storyboard")
            os.makedirs(folder, exist_ok=True)
            name = f"agent_{sid}_{time.strftime('%Y%m%d_%H%M%S')}.png"
            with open(os.path.join(folder, name), "wb") as f:
                f.write(base64.b64decode(res["image"].split(",", 1)[1]))
            results[sid] = {"image": f"/vfx/project_file/{pid}/storyboard/{name}"}
            steps[i]["image"] = results[sid]["image"]
        elif k == "wait_3d_pose":
            wait_s = float(prm.get("wait_min", 40)) * 60
            t0, found = time.time(), None
            while time.time() - t0 < wait_s:
                if v._cancelled(job_id):
                    raise v.ProcessError("Stopped")
                if (v.get_job(job_id) or {}).get("skip_wait"):
                    break
                found = _find_layer(pid, vid, lambda l: l.get("type") == "pose" and l.get("source") == "3d"
                                    and float(l.get("created_at") or 0) >= t_start - 5)
                if found:
                    break
                _log(job_id, steps, i, "waiting_user", str(prm.get("ask") or _tr(p)("жду слой Pose to Gen из 3D", "waiting for a Pose to Gen layer from 3D")))
                time.sleep(5)
            if found:
                time.sleep(3)
                results[sid] = {"layer": found["id"]}
            else:
                results[sid] = {}
                _log(job_id, steps, i, "skipped", _tr(p)("слой из 3D не пришёл", "no layer came from 3D"))
                continue
        elif k == "merge_pose":
            lids = [x for x in (prm.get("layers") or []) if x]
            total = int(info.get("total_frames") or 0)
            lay = _merge_pose_layers(pid, vid, lids, str(prm.get("name") or "merged pose"), int(prm.get("start", 0) or 0),
                                     min(total - 1, int(prm.get("end", total - 1))))
            results[sid] = {"layer": lay["id"]}
        else:
            results[sid] = _norm_result(k, _run_child(job_id, k, pid, vid, prm, steps, i))
            if k == "prepare":
                info = v.pps.get_info(v.node_id_for(vid)) or {}
        steps[i]["result"] = results[sid]
        _log(job_id, steps, i, "done")
    return {"results": results}
