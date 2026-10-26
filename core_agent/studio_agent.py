"""KESHIKI studio agent (v2): understands the whole conversation and the project, then acts.

Pieces
------
* ACTIONS      — what the agent can do in the studio (name, params, cost, how the UI executes it).
* AgentMemory  — per-project memory on disk: full dialogue log, rolling summary, remembered facts,
                 an action waiting for the user's "go".
* build_system_prompt — role + studio knowledge + action catalogue + live project state + memory.
* plan_turn    — one dialogue turn: big-context LLM call, JSON decision, validation, studio actions.

The LLM answers in prose and ends with one JSON block:
    {"action": "<id>|null", "params": {...}, "confirm": true|false, "remember": ["..."]}
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.path.join(_HERE, "knowledge_studio")

# --------------------------------------------------------------------------- #
# Action catalogue                                                            #
# --------------------------------------------------------------------------- #
ACTIONS: Dict[str, Dict[str, Any]] = {
    "generate_image": {
        "title": "Сгенерировать изображение (FLUX.3 Klein)",
        "params": {"prompt": "детальный английский промпт сцены", "title": "короткое имя кадра (рус)"},
        "heavy": False,
    },
    "generate_video": {
        "title": "Сгенерировать видео (LTX-2.5 22B)",
        "params": {"prompt": "английский промпт: сцена, действие, движение камеры, звук",
                   "mode": "t2v | i2v (из картинки-референса) | flf2v (первый и последний кадр)",
                   "image_id": "для i2v: id картинки из файлов проекта (первый кадр)",
                   "end_image_id": "для flf2v: id последнего кадра",
                   "title": "короткое имя шота (рус)"},
        "heavy": True,
    },
    "edit_image": {
        "title": "Изменить текущее изображение (FLUX edit по маскам/референсам) — откроет панель генерации с промптом",
        "params": {"prompt": "английский промпт правки", "image_id": "id изображения (по умолчанию открытое)"},
        "heavy": False,
    },
    "video_process": {
        "title": "Процесс над открытым видео в 2D (результат — новый слой). kind и params по таблице «Процессы 2D» из инструкции",
        "params": {"kind": "prepare | depth | canny | pose | text_mask | detect_yolo | detect_all | track_camera | ltx_alpha | corridor_key | "
                           "undistort | first_frame | ltx_ic_lora | composite | relight_frame | render | layer_to_video",
                   "params": "объект параметров процесса, ids слоёв — из «Состояние проекта» (слои видео с id)",
                   "video_id": "id видео (по умолчанию открытое)"},
        "heavy_kinds": ["ltx_alpha", "ltx_ic_lora", "relight_frame", "track_camera"],
        "heavy": False,
    },
    "dubbing": {
        "title": "Дубляж / клон голоса / озвучка текста (Whisper + F5-TTS / VibeVoice / Qwen3-TTS, отдельное окружение)",
        "params": {"mode": "dub | voice | narration",
                   "config": "dub: {player:{video_path,target_lang}, translation:{source_language,target_language,translator:'ollama'}}; "
                             "voice: {tts_general:{engine:'F5-TTS',speaker_ref,target_text}}; narration: {text, speaker_ref}",
                   "start_at_stage": "0..4 (только dub, продолжить с этапа)"},
        "heavy": True,
    },
    "mesh3d": {
        "title": "3D-модель из картинки или текстура для меша (TRELLIS.2)",
        "params": {"engine": "trellis2", "mode": "generate | texture", "image_source": "url картинки из проекта",
                   "mesh_source": "для texture: url меша из библиотеки", "actor_name": "имя объекта",
                   "resolution_key": "512 | 1024 | 1536", "texture_size": "1024 | 2048 | 4096"},
        "heavy": True,
    },
    "deconstruct": {
        "title": "Настроить пассы деконструкции (маски, глубина, поза, трекинг)",
        "params": {"passes": "список: tracking_glomap, mask_sam2_rough, mask_corridor_key, mask_ltx_alpha_lora, pass_depth, pass_canny, pass_pose"},
        "heavy": False,
    },
    "music": {"title": "Сгенерировать музыку (ACE-Step)", "params": {"prompt": "описание музыки по-английски"}, "heavy": True},
    "voice": {"title": "Озвучка текста (TTS)", "params": {"text": "текст реплики", "voice": "голос"}, "heavy": False},
    "show_workflow": {"title": "Показать граф нод ComfyUI для процесса", "params": {"process_id": "storyboard | video_gen"}, "heavy": False},
}

GO_WORDS = {"запускай", "запусти", "запуск", "давай", "поехали", "го", "да", "ок", "окей", "делай", "генерируй",
            "погнали", "вперед", "вперёд", "жми", "go", "run", "yes", "ok", "start", "конечно", "согласен", "подтверждаю"}
FILLER = {"уже", "его", "это", "плиз", "пожалуйста", "please", "now", "сейчас", "ну", "тогда", "так"}


def is_go_command(msg: str) -> bool:
    t = re.sub(r"[^\w\s]", " ", (msg or "").lower()).split()
    return bool(t) and len(t) <= 4 and all(w in GO_WORDS or w in FILLER for w in t) and any(w in GO_WORDS for w in t)


# --------------------------------------------------------------------------- #
# Memory                                                                      #
# --------------------------------------------------------------------------- #
class AgentMemory:
    _locks: Dict[str, threading.Lock] = {}

    def __init__(self, project_dir: str):
        self.dir = os.path.join(project_dir, "agent")
        os.makedirs(self.dir, exist_ok=True)
        self.log_path = os.path.join(self.dir, "dialogue.jsonl")
        self.state_path = os.path.join(self.dir, "memory.json")
        self.lock = AgentMemory._locks.setdefault(self.dir, threading.Lock())

    def load_log(self) -> List[Dict[str, Any]]:
        out = []
        if os.path.isfile(self.log_path):
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
        return out

    def append(self, role: str, text: str, **extra) -> None:
        with self.lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"role": role, "text": text, "ts": time.time(), **extra}, ensure_ascii=False) + "\n")

    def state(self) -> Dict[str, Any]:
        if os.path.isfile(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"summary": "", "summarized_upto": 0, "facts": [], "pending": None}

    def save_state(self, st: Dict[str, Any]) -> None:
        with self.lock, open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- #
# Context                                                                     #
# --------------------------------------------------------------------------- #
def load_knowledge(max_chars: int = 22000) -> str:
    parts = []
    if os.path.isdir(KNOWLEDGE_DIR):
        for fn in sorted(os.listdir(KNOWLEDGE_DIR)):
            if fn.endswith(".md"):
                with open(os.path.join(KNOWLEDGE_DIR, fn), "r", encoding="utf-8") as f:
                    parts.append(f.read().strip())
    text = "\n\n".join(parts)
    return text[:max_chars]


GUIDE_PATHS = [os.path.join(_HERE, "..", "docs", "USER_GUIDE_RU.md"), os.path.join(_HERE, "..", "docs", "USER_GUIDE.md")]
HELP_WORDS = ("как ", "как?", "почему", "что делать", "не получается", "не работает", "инструкц", "помоги", "подскажи",
              "объясни", "что такое", "зачем", "how ", "why", "help", "guide", "what is", "doesn't work", "не выходит", "ошибк")


def _guide_sections() -> List[Tuple[str, str]]:
    """(heading, text) for every ## / ### section of the user guide."""
    for path in GUIDE_PATHS:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            secs, head, buf = [], "", []
            for line in text.splitlines():
                if line.startswith("## ") or line.startswith("### "):
                    if buf:
                        secs.append((head, "\n".join(buf).strip()))
                    head, buf = line.lstrip("# ").strip(), [line]
                else:
                    buf.append(line)
            if buf:
                secs.append((head, "\n".join(buf).strip()))
            return secs
    return []


def guide_snippets(question: str, k: int = 3, max_chars: int = 6000) -> str:
    """The guide sections that best match the user's words: 5-letter stems weighted by rarity (IDF)."""
    import math
    stop = {"как", "что", "поче", "заче", "сдел", "можн", "нужн", "чтоб", "мне", "теб", "тобо", "это", "для", "или",
            "how", "what", "why", "can", "the", "and", "with", "make", "into", "пожа", "плиз", "есть", "был", "буде", "свою", "свой"}
    words = {w[:4] for w in re.findall(r"[\wё]{3,}", (question or "").lower())} - stop
    secs = [(h, b) for h, b in _guide_sections() if len(b) >= 160]   # bare headings carry no instructions
    if not words or not secs:
        return ""
    stems = [{t[:4] for t in re.findall(r"[\wё]{3,}", (h + " " + b).lower())} for h, b in secs]
    idf = {w: math.log((1 + len(secs)) / (1 + sum(1 for st in stems if w in st))) + 1.0 for w in words}
    scored = []
    for (head, body), st in zip(secs, stems):
        head_t = {w[:4] for w in re.findall(r"[\wё]{3,}", head.lower())}
        score = sum(idf[w] for w in words if w in st) + sum(idf[w] for w in words & head_t)
        if score > 0:
            scored.append((score, head, body))
    scored.sort(key=lambda x: -x[0])
    out, used = [], 0
    for _, head, body in scored[:k]:
        if used + len(body) > max_chars:
            body = body[: max(0, max_chars - used)]
        out.append(body)
        used += len(body)
        if used >= max_chars:
            break
    return "\n\n".join(out)


def is_help_question(msg: str) -> bool:
    m = (msg or "").lower()
    return any(w in m for w in HELP_WORDS)


def installed_loras() -> List[str]:
    try:
        import folder_paths
        return [n for n in folder_paths.get_filename_list("loras") if "ltx-2.5" in n.lower() or "ltx2.5" in n.lower()][:40]
    except Exception:
        return []


def describe_studio_context(ctx: Dict[str, Any]) -> str:
    """Live project state sent by the UI, as compact readable text."""
    if not ctx:
        return "(нет данных о проекте)"
    L = []
    p = ctx.get("project") or {}
    L.append(f"Проект: «{p.get('name', '')}», длина {p.get('frames', '?')} кадров @ {p.get('fps', '?')} fps.")
    L.append(f"Сейчас открыто: окно {ctx.get('view', '?')}, вкладка {ctx.get('active_tab', '?')}.")
    ai = ctx.get("active_image")
    if ai:
        L.append(f"Открытое изображение: «{ai.get('title')}» (id {ai.get('id')})" + (f", заметки: {ai.get('notes')[:300]}" if ai.get("notes") else ""))
        if ai.get("layers"):
            L.append("  слои: " + "; ".join(f"{x.get('name')} [{x.get('type')}{', скрыт' if x.get('hidden') else ''}]" for x in ai["layers"][:20]))
        if ai.get("made_by"):
            L.append(f"  создано: {ai['made_by']}")
    av = ctx.get("active_video")
    if av:
        L.append(f"Открытое видео: «{av.get('title')}» (id {av.get('id')}), {av.get('frames', '?')} кадров" + (f", заметки: {av.get('notes')[:300]}" if av.get("notes") else ""))
        if av.get("layers"):
            L.append("  слои видео: " + "; ".join(f"«{x.get('name')}» id={x.get('id')} [{x.get('type')}{', скрыт' if x.get('hidden') else ''}]" for x in av["layers"][:30]))
    assets = ctx.get("assets") or []
    if assets:
        L.append(f"Файлы проекта ({len(assets)}):")
        for a in assets[:60]:
            line = f"  - {a.get('type')} «{a.get('title')}» (id {a.get('id')})"
            if a.get("folder"):
                line += f" в папке «{a['folder']}»"
            if a.get("notes"):
                line += f": {str(a['notes'])[:140]}"
            L.append(line)
    sb = ctx.get("storyboard") or []
    if sb:
        L.append(f"Раскадровка ({len(sb)} шотов):")
        for c in sb[:30]:
            L.append(f"  - {c.get('title')}: {str(c.get('story_note') or c.get('flux_prompt') or '')[:160]}")
    refs = ctx.get("attached") or []
    if refs:
        L.append("Пользователь прикрепил к сообщению: " + ", ".join(f"«{r.get('title')}» ({r.get('type')})" for r in refs))
    return "\n".join(L)


def action_catalogue() -> str:
    lines = []
    for aid, a in ACTIONS.items():
        ps = ", ".join(f"{k}: {v}" for k, v in a["params"].items())
        lines.append(f"- {aid}: {a['title']}{' [тяжёлое — нужно подтверждение]' if a.get('heavy') else ''}. Параметры: {ps}")
    return "\n".join(lines)


OUTPUT_RULES = """
## Формат ответа

Сначала ответь пользователю обычным текстом (по-русски, коротко, как коллега).
В самом конце ОБЯЗАТЕЛЬНО добавь один блок:

```json
{"action": null, "params": {}, "confirm": false, "remember": []}
```

- action: id действия из каталога или null, если сейчас нужно только поговорить / уточнить.
- params: параметры действия. Промпты для моделей — по-английски, развёрнутые.
- confirm: true — если действие тяжёлое или ты не уверен, что пользователь хочет его прямо сейчас.
  Тогда в тексте опиши, что сделаешь, и спроси «запускаю?». Действие будет ждать его согласия.
  false — выполнить сразу.
- remember: новые важные факты о проекте и предпочтениях пользователя (коротко), или [].
""".strip()


def build_system_prompt(studio_ctx: Dict[str, Any], mem_state: Dict[str, Any], user_message: str = "") -> str:
    parts = [load_knowledge()]
    snip = guide_snippets(user_message) if user_message else ""
    if snip:
        parts.append("## Выдержки из инструкции пользователя (docs/USER_GUIDE) по этому вопросу\n"
                     "Если пользователь спрашивает «как сделать» — объясни по этим шагам, своими словами, коротко, "
                     "и предложи запустить нужный шаг. Ссылайся на раздел инструкции по названию.\n\n" + snip)
    loras = installed_loras()
    if loras:
        parts.append("## Установленные LoRA LTX-2.5\n" + ", ".join(os.path.basename(x) for x in loras))
    parts.append("## Каталог действий\n" + action_catalogue())
    parts.append("## Состояние проекта сейчас\n" + describe_studio_context(studio_ctx))
    if mem_state.get("summary"):
        parts.append("## Краткая история проекта (сжатые ранние разговоры)\n" + mem_state["summary"])
    if mem_state.get("facts"):
        parts.append("## Что ты помнишь о проекте и пользователе\n" + "\n".join(f"- {f}" for f in mem_state["facts"][-80:]))
    pend = mem_state.get("pending")
    if pend:
        parts.append(f"## Ждёт подтверждения пользователя\nТы предложил: {pend.get('action')} {json.dumps(pend.get('params', {}), ensure_ascii=False)[:600]}")
    parts.append(OUTPUT_RULES)
    return "\n\n".join(p for p in parts if p)


def dialogue_messages(log: List[Dict[str, Any]], start: int, budget_chars: int = 26000) -> List[Dict[str, str]]:
    """Newest turns verbatim within the budget; older ones (after the summary point) as one-line digests."""
    turns = log[start:]
    verbatim, used = [], 0
    for e in reversed(turns):
        txt = str(e.get("text", "")).strip()
        if e.get("action"):
            txt += f"\n[выполнено действие: {e['action']} {json.dumps(e.get('params', {}), ensure_ascii=False)[:300]}]"
        if used + len(txt) > budget_chars and verbatim:
            break
        verbatim.append({"role": "user" if e.get("role") == "user" else "assistant", "content": txt})
        used += len(txt)
    verbatim.reverse()
    older = turns[: len(turns) - len(verbatim)]
    msgs = []
    if older:
        digest = "\n".join(f"{'Пользователь' if e.get('role') == 'user' else 'Ты'}: {str(e.get('text', ''))[:160]}" for e in older[-60:])
        msgs.append({"role": "user", "content": f"(Более ранние реплики, кратко)\n{digest}"})
        msgs.append({"role": "assistant", "content": "Помню."})
    return msgs + verbatim


# --------------------------------------------------------------------------- #
# LLM                                                                         #
# --------------------------------------------------------------------------- #
def _comfy_url() -> str:
    """The ComfyUI server KESHIKI runs in (its real port), see vfx_nodes/process_runner.py."""
    try:
        from vfx_nodes.process_runner import COMFY_URL
        return COMFY_URL
    except Exception:
        pass
    try:
        import importlib
        for name, mod in list(sys.modules.items()):
            if name.endswith("vfx_nodes.process_runner") and hasattr(mod, "COMFY_URL"):
                return mod.COMFY_URL
    except Exception:
        pass
    return os.environ.get("KESHIKI_COMFY_URL", "http://127.0.0.1:8188")


LOCAL_PROVIDERS = ("ollama", "llama_cpp", "")
# Old saved configs may still name removed providers; treat them as local Ollama.
LEGACY_LOCAL_PROVIDERS = ("gemma4_12b", "gemma4_ltx", "antigravity", "antigravity_sdk", "google_antigravity")
MICRO_RE = re.compile(r"(0\.[58]b|1\.5b|:1b|:2b|:3b|-1b|-3b)", re.I)
SIMPLE_ACTIONS = ("video_process", "show_workflow")


def _ollama_names(agent_config: Dict[str, Any]) -> List[str]:
    try:
        url = re.sub(r"/(v1|api)$", "", (agent_config.get("api_url") or "http://127.0.0.1:11434").rstrip("/"))
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as r:
            return [m.get("name", "") for m in json.loads(r.read().decode()).get("models", [])]
    except Exception:
        return []


def micro_model(agent_config: Dict[str, Any]) -> Optional[str]:
    want = str(agent_config.get("micro_model") or "").strip()
    if want:
        return want
    small = [n for n in _ollama_names(agent_config) if MICRO_RE.search(n)]
    return small[0] if small else None


def gpu_busy() -> Optional[str]:
    """What is using the GPU right now (ComfyUI run or a 2D job), or None."""
    try:
        with urllib.request.urlopen(_comfy_url() + "/vfx/process/progress", timeout=3) as r:
            d = json.loads(r.read().decode())
        if d.get("running"):
            run = d["running"]
            return f"{run.get('label') or run.get('node_title') or 'генерация'} — {round(run.get('percent') or 0)}%"
        if d.get("video_jobs"):
            j = d["video_jobs"][0]
            return f"{j.get('kind')} — {round(j.get('progress') or 0)}%"
        if d.get("pending"):
            return f"в очереди {d['pending']}"
    except Exception:
        pass
    return None


def free_comfy_vram() -> None:
    """Models ComfyUI keeps idle in VRAM go to RAM, so the thinking model has room (they come back fast)."""
    try:
        req = urllib.request.Request(_comfy_url() + "/free", data=json.dumps({"unload_models": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass


MICRO_SYSTEM = """Ты быстрый диспетчер студии. Реши, можно ли выполнить сообщение пользователя ОДНОЙ простой командой без размышлений.
Простые команды:
- video_process: params.kind = depth | canny | pose | detect_yolo | detect_all | corridor_key | undistort | ltx_alpha (над открытым видео)
- show_workflow: params.process_id = storyboard | video_gen
Всё остальное (вопросы, обсуждение, идеи, генерация картинок или видео, правки, неясные просьбы) — need_thinking = true.
Ответь только JSON: {"need_thinking": true} или {"need_thinking": false, "action": "...", "params": {...}, "reply": "коротко, что делаю"}"""


MICRO_PHRASES = {"depth": "Строю карту глубины", "canny": "Выделяю контуры (Canny)", "pose": "Извлекаю позу (DWPose)",
                 "detect_yolo": "Ищу объекты (YOLO)", "detect_all": "Ищу и маскирую объекты (SAM2)",
                 "corridor_key": "Чищу маску (CorridorKey)", "undistort": "Убираю дисторсию",
                 "ltx_alpha": "Строю альфу через LTX"}


def micro_phrase(act: str, prm: Dict[str, Any]) -> str:
    if act == "video_process":
        return MICRO_PHRASES.get(str((prm or {}).get("kind")), "Запускаю обработку видео")
    if act == "show_workflow":
        return "Открываю схему процесса"
    return ACTIONS.get(act, {}).get("title", "Выполняю")


RULE_KINDS = [
    ("depth", r"глубин|\bdepth\b"),
    ("canny", r"контур|\bcanny\b|кэнни|\bedges?\b"),
    ("pose", r"\bпоз[аыуе]\b|dwpose|скелет|\bpose\b"),
    ("corridor_key", r"corridor|коридор"),
    ("undistort", r"дисторс|undistort"),
    ("ltx_alpha", r"альф|\balpha\b"),
    ("detect_all", r"sam ?2|сегмент|замаск\w* (все|объект)"),
    ("detect_yolo", r"\byolo\b|найди объект|детект"),
]
RULE_VERB = r"^(пожалуйста\s+)?(сделай|сделать|построй|запусти|извлеки|выдели|найди|примени|сними|получи|прогони|make|run|extract|build|detect|get|do)\b"
RULE_BLOCK = r"\?|изображени|картин|фото|сгенер|генер|созда|нарисуй|промт|промпт|prompt|как |почему|зачем|что |можно ли|объясни|расскажи|\bhow\b|\bwhy\b|\bwhat\b"


def rule_route(message: str, studio_ctx: Dict[str, Any]) -> Optional[str]:
    """A short imperative command about the open video with exactly one known process -> that process; else None."""
    m = (message or "").strip().lower()
    if not m or len(m) > 90 or not (studio_ctx or {}).get("active_video"):
        return None
    if not re.search(RULE_VERB, m) or re.search(RULE_BLOCK, m):
        return None
    hits = [k for k, rx in RULE_KINDS if re.search(rx, m)]
    return hits[0] if len(hits) == 1 else None


def micro_route(model: str, message: str, studio_ctx: Dict[str, Any], agent_config: Dict[str, Any]) -> Dict[str, Any]:
    av = (studio_ctx or {}).get("active_video")
    hint = f"Открыто видео «{av.get('title')}»." if av else "Видео не открыто."
    try:
        out = ollama_chat(model, [{"role": "system", "content": MICRO_SYSTEM}, {"role": "user", "content": f"{hint}\nСообщение: {message}"}],
                          agent_config, num_ctx=2048, max_tokens=200, keep_alive=-1, fmt="json", temperature=0.1)
        d = json.loads(out)
        return d if isinstance(d, dict) else {"need_thinking": True}
    except Exception:
        return {"need_thinking": True}


def pick_model(agent_config: Dict[str, Any]) -> str:
    want = str(agent_config.get("model_name") or "").strip()
    if want and want not in ("default", "auto") and not want.endswith(".safetensors") and not re.search(r"0\.[58]b|1\.5b", want.lower()):
        return want
    try:
        url = (agent_config.get("api_url") or "http://127.0.0.1:11434").rstrip("/")
        url = re.sub(r"/(v1|api)$", "", url)
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as r:
            names = [m.get("name", "") for m in json.loads(r.read().decode()).get("models", [])]
        heavy = [n for n in names if re.search(r"qwen3\.8|qwen38|27b|32b|30b|70b", n.lower())]
        mid = [n for n in names if re.search(r"(7|8|9|12|14)b", n.lower())]
        return (heavy or mid or names or ["qwen3.5:0.8b"])[0]
    except Exception:
        return want or "qwen3.8:27b"


def ollama_chat(model: str, messages: List[Dict[str, Any]], agent_config: Dict[str, Any], num_ctx: int, max_tokens: int = 1500,
                keep_alive: Any = "5m", fmt: Optional[str] = None, temperature: float = 0.6) -> str:
    url = (agent_config.get("api_url") or "http://127.0.0.1:11434").rstrip("/")
    url = re.sub(r"/(v1|api)$", "", url) + "/api/chat"
    payload = {"model": model, "messages": messages, "stream": False, "keep_alive": keep_alive, "think": False,
               "options": {"num_ctx": num_ctx, "num_predict": max_tokens, "temperature": temperature}}
    if fmt:
        payload["format"] = fmt
    def call(p):
        req = urllib.request.Request(url, data=json.dumps(p).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        res = call(payload)
    except urllib.error.HTTPError:
        payload.pop("think", None)  # older Ollama without the think switch
        res = call(payload)
    msg = res.get("message", {}) or {}
    content = str(msg.get("content", "")).strip()
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    return content


def parse_decision(text: str) -> Tuple[str, Dict[str, Any]]:
    """Splits the reply into prose and the trailing JSON decision."""
    decision: Dict[str, Any] = {}
    blocks = list(re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S))
    raw = blocks[-1].group(1) if blocks else None
    if raw is None:
        m = None
        for m in re.finditer(r"\{[^{}]*\"action\"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.S):
            pass
        raw = m.group(0) if m else None
    if raw:
        try:
            decision = json.loads(raw)
        except Exception:
            try:
                decision = json.loads(re.sub(r",\s*}", "}", raw.replace("'", '"')))
            except Exception:
                decision = {}
        prose = text.replace(blocks[-1].group(0), "") if blocks else text.replace(raw, "")
    else:
        prose = text
    return prose.strip(), decision if isinstance(decision, dict) else {}


# --------------------------------------------------------------------------- #
# Turning a decision into studio actions                                      #
# --------------------------------------------------------------------------- #
def to_studio_actions(action: str, params: Dict[str, Any], studio_ctx: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params = params or {}
    extra: Dict[str, Any] = {}
    acts: List[Dict[str, Any]] = []
    if action == "generate_image":
        prompt = str(params.get("prompt") or "").strip()
        title = str(params.get("title") or "Кадр")[:40]
        acts += [
            {"type": "create_storyboard_card", "title": title, "story_note": params.get("title") or "", "mode": "image", "flux_prompt": prompt, "ltx_prompt": ""},
            {"type": "trigger_generation", "mode": "image", "workflow_type": "flux_image", "title": f"FLUX: {title}", "prompt": prompt, "custom_workflow": "FLUX.3_Klein_Text2Image.json"},
        ]
        extra["flux_prompt"] = prompt
    elif action == "generate_video":
        prompt = str(params.get("prompt") or "").strip()
        mode = str(params.get("mode") or "t2v").lower()
        wf = {"i2v": "LTX-2.5/video_ltx2_5_i2v.json", "flf2v": "LTX-2.5/video_ltx2_5_flf2v.json"}.get(mode, "LTX-2.5/video_ltx2_5_t2v.json")
        title = str(params.get("title") or "Видео")[:40]
        acts += [
            {"type": "create_storyboard_card", "title": f"🎬 {title}", "story_note": title, "mode": "video", "flux_prompt": "", "ltx_prompt": prompt},
            {"type": "trigger_generation", "mode": "video", "workflow_type": "ltx_video", "title": f"LTX: {title}", "prompt": prompt, "custom_workflow": wf,
             "image_ids": [x for x in (params.get("image_id"), params.get("end_image_id")) if x] if mode in ("i2v", "flf2v") else []},
        ]
        extra.update(ltx_prompt=prompt, is_video=True)
    elif action == "edit_image":
        acts.append({"type": "open_image_generate", "mode": "edit", "prompt": params.get("prompt", ""),
                     "image_id": params.get("image_id") or (studio_ctx.get("active_image") or {}).get("id")})
    elif action == "video_process":
        acts.append({"type": "video_job", "kind": params.get("kind", ""), "video_id": params.get("video_id") or (studio_ctx.get("active_video") or {}).get("id"),
                     "params": params.get("params") or {}})
    elif action == "dubbing":
        acts.append({"type": "dubbing_job", "mode": params.get("mode") or "dub", "config": params.get("config") or {},
                     "start_at_stage": int(params.get("start_at_stage") or 0)})
    elif action == "mesh3d":
        acts.append({"type": "mesh3d_job", "params": {k: v for k, v in params.items() if v not in (None, "")}})
    elif action == "deconstruct":
        passes = params.get("passes") or []
        if isinstance(passes, str):
            passes = [x.strip() for x in passes.split(",")]
        acts.append({"type": "configure_deconstruction", "passes": {p: True for p in passes}})
    elif action == "music":
        acts.append({"type": "generate_music", "engine": "ace_step", "prompt": params.get("prompt", "")})
    elif action == "voice":
        acts.append({"type": "prepare_tts", "text": params.get("text", ""), "voice": params.get("voice") or "dmitry"})
    elif action == "show_workflow":
        acts.append({"type": "visualize_workflow", "process_id": params.get("process_id") or "storyboard", "workflow_name": "FLUX.3_Klein_Text2Image.json"})
    return acts, extra


def is_heavy(action: str, params: Dict[str, Any]) -> bool:
    a = ACTIONS.get(action) or {}
    return bool(a.get("heavy")) or (params or {}).get("kind") in (a.get("heavy_kinds") or [])


# --------------------------------------------------------------------------- #
# One dialogue turn                                                           #
# --------------------------------------------------------------------------- #
def maybe_summarize(mem: AgentMemory, st: Dict[str, Any], log: List[Dict[str, Any]], model: str, agent_config: Dict[str, Any], keep_recent: int = 40) -> None:
    """Every ~30 new turns the old part of the dialogue is folded into a written summary."""
    upto = int(st.get("summarized_upto", 0))
    if len(log) - upto < keep_recent + 30:
        return
    chunk = log[upto: len(log) - keep_recent]
    text = "\n".join(f"{'Пользователь' if e.get('role') == 'user' else 'Агент'}: {str(e.get('text', ''))[:600]}" for e in chunk)
    prev = st.get("summary", "")
    try:
        summary = ollama_chat(model, [
            {"role": "system", "content": "Ты ведёшь журнал кинопроекта. Сожми разговор в связный конспект на русском: цели, решения, стиль, персонажи, локации, что сгенерировано и что не понравилось. Без воды, до 2500 знаков."},
            {"role": "user", "content": (f"Предыдущий конспект:\n{prev}\n\n" if prev else "") + f"Новый фрагмент разговора:\n{text}"}
        ], agent_config, num_ctx=16384, max_tokens=1200)
        if summary:
            st["summary"] = summary.strip()
            st["summarized_upto"] = len(log) - keep_recent
    except Exception:
        pass


def plan_turn(user_message: str, project_dir: str, studio_ctx: Dict[str, Any], agent_config: Dict[str, Any],
              ui_history: Optional[List[Dict[str, Any]]] = None, llm_fallback=None) -> Dict[str, Any]:
    agent_config = agent_config or {}
    mem = AgentMemory(project_dir)
    st = mem.state()
    log = [e for e in mem.load_log() if e.get("role") in ("user", "assistant")]
    if not log and ui_history:  # first run on an existing chat: import what the UI still has
        for h in ui_history:
            if str(h.get("text", "")).strip():
                mem.append("user" if h.get("role") == "user" else "assistant", str(h.get("text")))
        log = [e for e in mem.load_log() if e.get("role") in ("user", "assistant")]

    # "давай" with something waiting → do exactly that, no second guessing
    pend = st.get("pending")
    if pend and is_go_command(user_message):
        acts, extra = to_studio_actions(pend["action"], pend.get("params", {}), studio_ctx)
        reply = f"Запускаю: {ACTIONS.get(pend['action'], {}).get('title', pend['action'])}."
        mem.append("user", user_message)
        mem.append("assistant", reply, action=pend["action"], params=pend.get("params", {}))
        st["pending"] = None
        mem.save_state(st)
        return {"response_ru": reply, "studio_actions": acts, "decision": {"action": pend["action"], "params": pend.get("params", {})}, **extra}

    provider = str(agent_config.get("provider") or "ollama")
    local = provider in LOCAL_PROVIDERS or provider in LEGACY_LOCAL_PROVIDERS
    if local:
        micro = None
        # 1) plain one-line commands ("сделай глубину") are recognised by rules, without any model:
        #    the 0.8B dispatcher misread real requests and launched wrong jobs, so it is gone.
        cmd = rule_route(user_message, studio_ctx)
        if cmd:
            act, prm = "video_process", {"kind": cmd}
            if is_heavy(act, prm):
                st["pending"] = {"action": act, "params": prm, "ts": time.time()}
                reply = f"{micro_phrase(act, prm)}. Это тяжёлый процесс — запускаю?"
                acts, extra = [], {}
            else:
                reply = micro_phrase(act, prm) + "…"
                acts, extra = to_studio_actions(act, prm, studio_ctx)
                st["pending"] = None
            mem.append("user", user_message)
            mem.append("assistant", reply, **({"action": act, "params": prm} if acts else {}))
            mem.save_state(st)
            return {"response_ru": reply, "studio_actions": acts, "decision": {"action": act, "params": prm, "confirm": not acts},
                    "model_used": "rules", "seconds": 0, **extra}
        # 2) the big model never loads while a generation holds the GPU (that is what overflowed VRAM)
        busy = gpu_busy()
        if busy:
            reply = (f"Сейчас видеокарта занята ({busy}). Чтобы не переполнить память, подробно отвечу, когда генерация закончится — "
                     f"напиши мне снова или нажми ■, если её нужно остановить.")
            mem.append("user", user_message)
            mem.append("assistant", reply)
            st["deferred"] = user_message
            mem.save_state(st)
            return {"response_ru": reply, "studio_actions": [], "decision": {"action": None}, "model_used": micro or "", "seconds": 0}
        # 3) idle ComfyUI models step aside for the thinker
        free_comfy_vram()

    model = pick_model(agent_config)
    num_ctx = int(agent_config.get("context_tokens") or 24576)
    maybe_summarize(mem, st, log, model, agent_config)
    system = build_system_prompt(studio_ctx, st, user_message)
    messages = [{"role": "system", "content": system}] + dialogue_messages(log, int(st.get("summarized_upto", 0)))
    messages.append({"role": "user", "content": user_message})

    t0 = time.time()
    if local:
        text = ollama_chat(model, messages, agent_config, num_ctx=num_ctx)
    elif llm_fallback:
        text = llm_fallback(system, messages)
    else:
        text = ""
    if not text:
        raise RuntimeError("LLM returned nothing")
    prose, decision = parse_decision(text)
    if "action" not in decision:
        # the model talked but forgot the decision block: ask for it alone (cheap, same context cached)
        try:
            fix = ollama_chat(model, messages + [{"role": "assistant", "content": text},
                {"role": "user", "content": "Служебно: выведи ТОЛЬКО JSON-блок решения для своего последнего ответа "
                 "в формате {\"action\": ..., \"params\": {...}, \"confirm\": ..., \"remember\": [...]}. Если ты пообещал что-то запустить — "
                 "укажи это действие и полный английский промпт."}], agent_config, num_ctx=num_ctx, max_tokens=700)
            _, decision = parse_decision(fix if "```" in fix else f"```json\n{fix.strip()}\n```")
        except Exception:
            decision = {}
        if "action" not in decision:
            mem.append("debug", text[:4000])
    action = decision.get("action")
    action = action if action in ACTIONS else None
    params = decision.get("params") if isinstance(decision.get("params"), dict) else {}
    # heavy actions wait for a "go" unless the model explicitly decided the user already asked for them
    confirm = bool(decision.get("confirm", is_heavy(action, params) if action else False)) if action else False

    for f in decision.get("remember") or []:
        f = str(f).strip()
        if f and f not in st.get("facts", []):
            st.setdefault("facts", []).append(f[:300])

    acts, extra = [], {}
    if action and confirm:
        st["pending"] = {"action": action, "params": params, "ts": time.time()}
    elif action:
        acts, extra = to_studio_actions(action, params, studio_ctx)
        st["pending"] = None
    mem.append("user", user_message)
    mem.append("assistant", prose, **({"action": action, "params": params} if action and not confirm else {}))
    mem.save_state(st)
    return {"response_ru": prose or "…", "studio_actions": acts, "decision": {"action": action, "params": params, "confirm": confirm},
            "model_used": model, "seconds": round(time.time() - t0, 1), **extra}
