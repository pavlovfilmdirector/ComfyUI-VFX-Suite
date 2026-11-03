"""Plain-text shot tasks -> one `shot_task` job (vfx_nodes/agent_shot_task.py).

"Сделай трекинг этого шота, замени фон на гетто, добавь двух танцоров капоэйры и дай три варианта: замена фона,
замена персонажа, фон + персонаж" becomes a plan the server runs step by step. The request is read with rules
(no model has to load while the GPU is needed for the generation itself); the free-text parts (the new place,
the new character) are translated with a small phrase book and otherwise passed on as written.
"""
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

PHRASES = [  # Russian -> English prompt text (first match per group wins)
    (r"гет+о", "a gritty urban ghetto street: graffiti-covered brick walls, chain-link fences, cracked asphalt, old parked cars, trash on the sidewalk"),
    (r"киберпанк", "a neon-lit cyberpunk city street at night, wet asphalt, holographic signs"),
    (r"трущоб", "a run-down slum alley with crumbling walls and tangled wires"),
    (r"(городск\w*|город\w*)\s+(сред|улиц)", "a busy city street with apartment buildings and shop fronts"),
    (r"лес", "a misty forest with tall trees"),
    (r"пляж|мор[ея]", "a sunny beach by the sea"),
    (r"пустын", "a sandy desert under a harsh sun"),
    (r"горы|горах|горн", "a mountain valley"),
    (r"снег|зим", "a snowy winter landscape"),
    (r"космос|космич", "the interior of a spaceship"),
    (r"замок|средневек", "a medieval castle courtyard"),
    (r"офис", "a modern open-plan office"),
    (r"ночн|ночь", "at night"),
]
WHO = [
    (r"аф[рт]о-?амери\w*|темнокож\w*|чернокож\w*", "African-American men"),
    (r"девуш\w*|женщин\w*", "women"),
    (r"мужчин\w*|парн\w*|парен\w*", "men"),
    (r"дет[ейи]", "children"),
]
ACTIONS = [
    (r"капо[эе]йр\w*", "dancing capoeira"),
    (r"брейк|breakdance", "breakdancing"),
    (r"хип-?хоп", "dancing hip-hop"),
    (r"драк|дерут", "fighting"),
    (r"танц\w*", "dancing"),
    (r"бег|бегут", "running"),
]
NUM = {"пару": 2, "пара": 2, "два": 2, "двух": 2, "двое": 2, "три": 3, "трех": 3, "трёх": 3, "трое": 3, "четыре": 4, "четырех": 4}
DEFAULT_CHARACTER = "a young man with short dark hair, wearing a denim jacket and a white t-shirt"


def _first(table, text: str, default: str = "") -> str:
    for rx, en in table:
        if re.search(rx, text):
            return en
    return default


def _translate(ru: str) -> str:
    """A place / person description: phrase book hits + the original words (FLUX and LTX read some Russian)."""
    ru = ru.strip(" .,;:")
    hits = [en for rx, en in PHRASES if re.search(rx, ru.lower())]
    place = next((h for h in hits if h != "at night"), "")
    if place:
        return place + (", at night" if "at night" in hits and "night" not in place else "")
    return ru


def looks_like_shot_task(msg: str) -> bool:
    m = (msg or "").lower()
    groups = [
        bool(re.search(r"трек", m)),
        bool(re.search(r"(замен\w*|помен\w*|смен\w*|другой|новы\w*)\s+(\w+\s+)?фон|фон\w*\s+на\s", m)),
        bool(re.search(r"(замен\w*|помен\w*|смен\w*|друго\w*|новы\w*)\s+(\w+\s+)?(персонаж|героин|героя|актер|актёр)", m)),
        bool(re.search(r"скелет|танцор|танцу|танцев", m)),
        bool(re.search(r"вариант", m)),
    ]
    action = bool(re.search(r"сделай|сделаем|давай|добав|замени|заменим|нужно|хочу|пусть|запусти|выполни", m))
    # a new 3D scene built around the moves (performance transfer / reskin) is the 3D layout flow, not a shot task
    if re.search(r"проецир|рескин|reskin|перенос\w*\s+(движ|перформ)|в\s+новой\s+сцене|созда\w*\s+сцен", m):
        return False
    return sum(groups) >= 2 and action


def resolve_video(msg: str, project_dir: str, studio_ctx: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    vids: List[Dict[str, Any]] = []
    try:
        with open(os.path.join(project_dir, "project.json"), encoding="utf-8") as f:
            vids = json.load(f).get("mediaVideos2D") or []
    except Exception:
        pass
    m = (msg or "").lower()
    for v in vids:
        if v.get("id") and str(v["id"]).lower() in m:
            return v, vids
    best, best_len = None, 0
    for v in vids:
        t = str(v.get("title") or "").lower().strip()
        for cand in (t, re.sub(r"[-_ ]?(au|audio)$", "", t)):
            if len(cand) >= 6 and cand in m and len(cand) > best_len:
                best, best_len = v, len(cand)
    if best:
        return best, vids
    av = (studio_ctx or {}).get("active_video") or {}
    if av.get("id"):
        return next((v for v in vids if v.get("id") == av["id"]), {"id": av["id"], "title": av.get("title") or av["id"]}), vids
    return None, vids


def build_plan(msg: str) -> Dict[str, Any]:
    m = (msg or "").lower()
    plan: Dict[str, Any] = {"track": bool(re.search(r"трек", m))}
    # who is in the shot
    plan["subject"] = "woman" if re.search(r"девуш|женщин|героин", m) else ("man" if re.search(r"мужчин|парн|парен|героя\b", m) else "person")
    # new place
    bg = re.search(r"(?:фон\w*|окружени\w*|локаци\w*)\s+(?:на|в)\s+(.+?)(?=\s+(?:и\s+)?(?:добав|пусть|а\s+также|сдела|дай|замени|персонаж)|[.;!\n,]|$)", m)
    if not bg and re.search(r"гет+о|киберпанк|трущоб", m):
        bg = re.search(r"((?:городск\w+\s+)?\w*\s*(?:гет+о|киберпанк\w*|трущоб\w*))", m)
    if bg:
        plan["background"] = _translate(bg.group(1))
        plan["background_ru"] = bg.group(1).strip()
    # new character
    ch = re.search(r"(?:персонаж\w*|героин\w*|героя|актер\w*|актёр\w*)\s+на\s+(.+?)(?=[.;!\n,]|\s+(?:и\s+)?(?:сдела|фон|добав|дай|пусть|а\s+также)|$)", m)
    if ch:
        desc = ch.group(1).strip()
        plan["character"] = _translate(desc) if _translate(desc) != desc else desc
        plan["character_ru"] = desc
    # extra dancers / people from skeletons
    if re.search(r"скелет|танцор|танцу|танцев", m):
        cnt = 2
        mc = re.search(r"\b(\d+)\s+(?:скелет|танцор|челов)", m)
        if mc:
            cnt = int(mc.group(1))
        else:
            for w, n in NUM.items():
                if re.search(rf"\b{w}\b\s+(?:\w+\s+)?(?:скелет|танцор|челов|афро|парн|девуш)", m):
                    cnt = n
                    break
        plan["dancers"] = {"count": cnt, "who": _first(WHO, m, "people"), "action": _first(ACTIONS, m, "dancing")}
    # variants
    var: List[str] = []
    rest = m
    if re.search(r"фон\w*\s+и\s+персонаж|персонаж\w*\s+и\s+фон", rest):
        var.append("both")
        rest = re.sub(r"фон\w*\s+и\s+персонаж\w*|персонаж\w*\s+и\s+фон\w*", " ", rest)
    if re.search(r"вариант", m):
        if re.search(r"замен\w*\s+фон", rest):
            var.insert(0, "bg")
        if re.search(r"замен\w*\s+персонаж|замен\w*\s+героин", rest):
            var.insert(1 if "bg" in var else 0, "char")
    if not var:
        if plan.get("background") and plan.get("character"):
            var = ["both"]
        elif plan.get("background"):
            var = ["bg"]
        elif plan.get("character") or re.search(r"(замен\w*|друго\w*|новы\w*)\s+персонаж", m):
            var = ["char"]
    order = {"bg": 0, "char": 1, "both": 2}
    plan["variants"] = sorted(dict.fromkeys(var), key=lambda x: order[x])
    if any(v in ("char", "both") for v in plan["variants"]) and not plan.get("character"):
        plan["character"] = DEFAULT_CHARACTER
        plan["character_default"] = True
    if any(v in ("bg", "both") for v in plan["variants"]) and not plan.get("background"):
        plan["background"] = "a different location"
    rng = re.search(r"кадр\w*\s+(\d+)\s*[-–—]\s*(\d+)", m)
    if rng:
        plan["start"], plan["end"] = int(rng.group(1)), int(rng.group(2))
    else:
        plan["start"], plan["end"] = 0, 96
    return plan


VAR_RU = {"bg": "замена фона", "char": "замена персонажа", "both": "фон + персонаж"}


def describe_plan(video: Dict[str, Any], plan: Dict[str, Any]) -> str:
    lines = [f"🎬 **Беру задачу на шот «{video.get('title') or video.get('id')}»**, кадры {plan['start']}–{plan['end']} "
             f"(один проход LTX — до 97 кадров). План:"]
    n = 1
    lines.append(f"{n}. Маска персонажа ({plan['subject']}) — чтобы трекинг не цеплялся за него."); n += 1
    if plan.get("track"):
        lines.append(f"{n}. Трекинг камеры (GLOMAP) → камера и облако точек в 3D."); n += 1
    lines.append(f"{n}. Поза персонажа (DWPose) — управление движением для видео."); n += 1
    if "char" in plan["variants"]:
        lines.append(f"{n}. Карта глубины — держит старый фон в варианте с новым персонажем."); n += 1
    d = plan.get("dancers")
    if d:
        lines.append(f"{n}. **Скелеты на фоне ({d['count']} × {d['who']}, {d['action']}) — нужен ваш шаг в 3D**, жду до 40 мин."); n += 1
    for v in plan["variants"]:
        lines.append(f"{n}. {VAR_RU[v]}: первый кадр (FLUX) → видео (LTX union-control)."); n += 1
    if plan.get("background"):
        lines.append(f"\nНовое окружение: _{plan['background']}_")
    if plan.get("character"):
        lines.append(f"Новый персонаж: _{plan['character']}_" + (" (вы не описали — взял нейтрального, напишите своего, если нужно)" if plan.get("character_default") else ""))
    if d:
        lines.append(
            "\n**Что сделать вам, когда закончится трекинг:**\n"
            "1. Откройте вкладку **3D** — там уже будет камера трекинга и облако точек.\n"
            f"2. Добавьте {d['count']} скелета и дайте им анимацию (например, Capoeira из папки characters проекта).\n"
            "3. Поставьте их на землю **за** персонажем, чтобы они были видны в кадре трекинг-камеры.\n"
            "4. Нажмите кнопку **Pose to Gen** (иконка картинки в панели 3D).\n"
            "Я сам замечу новый слой и продолжу. Если скелеты не нужны — напишите «пропусти скелеты».")
    lines.append("\nХод выполнения — спросите «статус». Остановить — «стоп задачу».")
    return "\n".join(lines)


def _vls():
    for name, mod in list(sys.modules.items()):
        if name.endswith("video_layers_service") and hasattr(mod, "start_job") and hasattr(mod, "_JOBS"):
            return mod
    return None


def status_text(job: Dict[str, Any]) -> str:
    icon = {"done": "✅", "skipped": "⏭", "running": "⏳", "waiting_user": "🙋", "todo": "▫️"}
    steps = job.get("steps") or []
    st = job.get("status")
    head = {"running": "Задача идёт", "done": "Задача готова", "error": "Задача упала", "cancelled": "Задача остановлена"}.get(st, st)
    lines = [f"**{head}** ({job.get('progress', 0)}%)"]
    for i, s in enumerate(steps):
        note = f" — {s['note']}" if s.get("note") and s.get("status") != "done" or (s.get("note") and s.get("status") == "skipped") else ""
        lines.append(f"{icon.get(s.get('status'), '•')} {i + 1}. {s.get('title')}{note}")
    if st == "error":
        lines.append(f"\nОшибка: {job.get('message')}")
    if st == "done":
        res = job.get("result") or {}
        for v in res.get("videos") or []:
            lines.append(f"🎞 {v.get('name')} — слой в 2D-видео шота")
    return "\n".join(lines)


def handle(user_message: str, project_dir: str, pid: str, studio_ctx: Dict[str, Any], st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Returns an agent reply dict, or None when the message is not about a shot task."""
    m = (user_message or "").lower().strip()
    vls = _vls()
    job_id = st.get("shot_task_job")
    if job_id and vls is not None:
        job = vls.get_job(job_id)
        if job.get("status") != "not_found":
            if re.search(r"пропусти\w*\s+скелет|без\s+скелет|скелеты\s+не\s+нужн", m):
                with vls._JOBS_LOCK:
                    vls._JOBS[job_id]["skip_wait"] = True
                return {"response_ru": "Ок, скелеты пропускаю — иду к вариантам.", "studio_actions": [], "decision": {"action": None}}
            if re.search(r"^(стоп|останови)\w*\s+задач|отмени\w*\s+задач", m):
                vls.cancel_job(job_id)
                return {"response_ru": "Останавливаю задачу.", "studio_actions": [], "decision": {"action": None}}
            if re.search(r"статус|как\s+дела|прогресс|что\s+с\s+задач|готов[оа]?\s*\?|на\s+каком\s+шаге|где\s+мы", m):
                return {"response_ru": status_text(job), "studio_actions": [], "decision": {"action": None}, "shot_task": {"job_id": job_id}}
    if not looks_like_shot_task(user_message):
        return None
    video, vids = resolve_video(user_message, project_dir, studio_ctx)
    if not video:
        names = ", ".join(str(v.get("title")) for v in vids[:8]) or "—"
        return {"response_ru": f"Понял задачу, но не понял, какой шот. Назовите видео (например: {names}) или откройте его в 2D.",
                "studio_actions": [], "decision": {"action": None}}
    plan = build_plan(user_message)
    plan["video_url"] = video.get("videoUrl") or ""
    if not plan["variants"] and not plan.get("track"):
        return None
    reply = describe_plan(video, plan)
    if vls is None:
        return {"response_ru": reply + "\n\n⚠ Не нашёл сервис видео-задач — запустите студию через ComfyUI.", "studio_actions": [],
                "decision": {"action": None}}
    try:
        job = vls.start_job("shot_task", pid, video["id"], plan)
    except Exception as e:
        return {"response_ru": reply + f"\n\n⚠ Не смог запустить: {e}", "studio_actions": [], "decision": {"action": None}}
    st["shot_task_job"] = job["id"]
    st["shot_task_video"] = video["id"]
    return {"response_ru": reply + f"\n\n▶ Запустил (задача `{job['id']}`).", "studio_actions": [],
            "decision": {"action": "shot_task", "params": plan, "confirm": False}, "shot_task": {"job_id": job["id"], "video_id": video["id"]},
            "model_used": "rules"}


def _task_module():
    vls = _vls()
    if vls is None:
        return None
    import importlib
    pkg = vls.__name__.rsplit(".", 1)[0] if "." in vls.__name__ else ""
    try:
        return importlib.import_module((pkg + "." if pkg else "") + "agent_shot_task")
    except Exception:
        return None


def start_free_plan(params: Dict[str, Any], pid: str, project_dir: str, studio_ctx: Dict[str, Any], st: Dict[str, Any],
                    user_message: str = "") -> Dict[str, Any]:
    """The model's own plan (action run_plan) -> one server job. Returns {job_id, video} or {errors}."""
    mod, vls = _task_module(), _vls()
    if mod is None or vls is None:
        return {"errors": ["the video job service is not loaded (start the studio through ComfyUI)"]}
    steps = params.get("steps")
    errs = mod.validate_plan(steps)
    want = str(params.get("video_id") or "")
    video, vids = resolve_video(want or user_message, project_dir, studio_ctx)
    if want and not any(v.get("id") == want for v in vids):
        hit = next((v for v in vids if str(v.get("title") or "").lower() == want.lower()), None)
        video = hit or video
    if not video:
        errs.append("video_id: which video? (" + ", ".join(f"{v.get('id')} = {v.get('title')}" for v in vids[:10]) + ")")
    if errs:
        return {"errors": errs}
    job = vls.start_job("shot_task", pid, video["id"], {"steps": steps, "video_url": video.get("videoUrl") or "",
                                                        "title": params.get("title") or ""})
    st["shot_task_job"] = job["id"]
    st["shot_task_video"] = video["id"]
    return {"job_id": job["id"], "video": video}


def plan_lines(steps: List[Dict[str, Any]]) -> str:
    return "\n".join(f"{n}. {s.get('title') or s.get('kind')} (`{s.get('kind')}`)" for n, s in enumerate(steps or [], 1))
