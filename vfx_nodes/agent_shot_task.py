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


def _run_child(job_id: str, kind: str, pid: str, vid: str, params: Dict[str, Any], steps, i) -> Dict[str, Any]:
    """Starts a normal studio job and waits for it, showing its progress inside this step."""
    v = _vls()
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


def _dancers_text(d: Optional[Dict[str, Any]]) -> str:
    if not d:
        return ""
    n = int(d.get("count") or 2)
    who = str(d.get("who") or "people").strip()
    act = str(d.get("action") or "dancing").strip()
    return f" In the background {n} {who} are {act}."


def run_shot_task(job_id: str, kind: str, pid: str, vid: str, p: Dict[str, Any]) -> Dict[str, Any]:
    v = _vls()
    t_start = time.time()
    info = v.pps.get_info(v.node_id_for(vid)) or {}
    variants = [x for x in (p.get("variants") or []) if x in FIRST_FRAME_TEXT]
    dancers = p.get("dancers") or None
    subject = str(p.get("subject") or "person").strip()
    steps: List[Dict[str, Any]] = []
    add = lambda key, title: steps.append({"key": key, "title": title, "status": "todo"})
    if not info.get("total_frames"):
        add("prepare", "Подготовка плейта (кадры)")
    add("mask", f"Маска персонажа «{subject}» (SAM)")
    if p.get("track"):
        add("track", "Трекинг камеры (GLOMAP)")
    add("pose", "Поза персонажа (DWPose)")
    if "char" in variants:
        add("depth", "Карта глубины")
    if dancers:
        add("dancers", f"Скелеты в 3D: {int(dancers.get('count') or 2)} × {dancers.get('action') or 'танец'} → Pose to Gen")
    for var in variants:
        add(f"ff_{var}", f"Первый кадр: {VARIANT_TITLE[var]} (FLUX)")
        add(f"ltx_{var}", f"Видео: {VARIANT_TITLE[var]} (LTX union-control)")
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
        _log(job_id, steps, i, "done", f"уже есть: {m.get('name')}")
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
                _log(job_id, steps, i, "done", "камера уже решена для этого диапазона — беру готовую")
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
        _log(job_id, steps, i, "done", f"уже есть: {pose.get('name')}")
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
            _log(job_id, steps, i, "done", f"уже есть: {depth.get('name')}")
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
        found = None
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
                 f"жду слой Pose to Gen из 3D (ещё ~{left} мин): 3D → добавьте {int(dancers.get('count') or 2)} скелета с анимацией, "
                 f"поставьте их за персонажем в кадре трекинг-камеры → кнопка Pose to Gen")
            time.sleep(5)
        if found:
            time.sleep(3)   # let the last frames land
            merged = _merge_pose_layers(pid, vid, [pose.get("id"), found["id"]], f"{subject} + dancers · pose", start, end)
            control_pose = merged["id"]
            out["layers"]["dancers"] = found["id"]
            out["layers"]["pose_with_dancers"] = merged["id"]
            _log(job_id, steps, i, "done", f"скелеты добавлены: {found.get('name')}")
        else:
            dancers = None
            _log(job_id, steps, i, "skipped", "слой из 3D не пришёл — продолжаю без скелетов")

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
            "start": start, "end": end, "name": f"Agent · {VARIANT_TITLE[var]}",
        }, steps, i)
        lay = r.get("layer") or {}
        out["videos"].append({"variant": var, "layer": lay.get("id"), "name": lay.get("name"), "first_frame": url})
        _log(job_id, steps, i, "done", lay.get("name") or "")
    v._job_update(job_id, steps=[dict(s) for s in steps])
    return out
