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

        "title": "Сгенерировать изображение (FLUX.3 Klein / Qwen Edit) — по тексту, скетчу, позе (OpenPose/DWPose) или референсу",

        "params": {

            "prompt": "детальный английский промпт сцены",

            "title": "короткое имя кадра (рус)",

            "image_id": "опционально: id открытого изображения (для ControlNet/Inpaint/Edit)",

            "pose_layer": "опционально: имя или id слоя позы (например Skeleton 1) для точного контроля позы",

            "depth_layer": "опционально: имя слоя глубины (Depth)",

            "canny_layer": "опционально: имя слоя контуров (Canny)",

            "mask_layer": "опционально: имя слоя маски",

            "engine": "flux | qwen"

        },

        "heavy": False,

    },

    "generate_video": {

        "title": "Подготовить и сгенерировать видео (LTX-2.5 22B) — откроет окно настроек шота перед запуском",

        "params": {"prompt": "английский промпт: сцена, действие, движение камеры, освещение, звук",

                   "mode": "t2v (текст в видео) | i2v (быстрый I2V из картинки) | flf2v (интерполяция первого и последнего кадров) | pro_i2v (кинематографичный Pro I2V) | layout_to_render (3D-лейаут в рендер) | v2v_ic_lora (трансфер видео) | tiled_fusion_4k (апскейл 4K/8K) | inpaint_sam2 (инпейнтинг SAM2) | inpaint_sam3 (инпейнтинг SAM3)",

                   "workflow": "опциональное точное имя воркфлоу из каталога LTX-2.5",

                   "image_id": "для i2v/pro_i2v: id картинки первого кадра",

                   "end_image_id": "для flf2v: id картинки последнего кадра",

                   "video_id": "для v2v, inpaint или tiled_fusion: id видео",

                   "title": "короткое имя шота (рус)"},

        "heavy": True,

    },

    "edit_image": {

        "title": "Изменить/перегенерировать текущее изображение по промпту, позе или маске (FLUX.3 / Qwen Edit)",

        "params": {

            "prompt": "английский промпт правки/генерации",

            "image_id": "id изображения (по умолчанию открытое)",

            "pose_layer": "опционально: имя слоя позы (например Skeleton 1)",

            "engine": "flux | qwen"

        },

        "heavy": False,

    },

    "video_process": {

        "title": "Процесс над открытым ВИДЕО в 2D (новый слой). Только для ВИДЕО, НЕ использовать для статичных изображений 1D (для фото используй generate_image или edit_image)!",

        "params": {"kind": "prepare | depth | canny | pose | text_mask | detect_yolo | detect_all | track_camera | ltx_alpha | corridor_key | "

                           "undistort | first_frame | ltx_ic_lora | composite | relight_frame | render | layer_to_video",

                   "params": "объект параметров процесса; для ltx_ic_lora НЕ передавай chunk (однопроходный режим one_pass до 97 кадров с резервом 5 ГБ VRAM запускается автоматически)",

                   "video_id": "id видео (по умолчанию открытое)"},

        "heavy_kinds": ["ltx_alpha", "ltx_ic_lora", "relight_frame", "track_camera"],

        "heavy": False,

    },

    "dubbing": {

        "title": "Дубляж / клон голоса / озвучка текста (Whisper + F5-TTS, отдельное окружение)",

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

        "heavy": False,

    },

    "setup_3d_layout": {

        "title": "Расстановка 3D сцены (Three.js): кубы-прокси, плоскость пола и камера облета",

        "params": {

            "cubes": "список кубов: [{'name': '...', 'position': [x,y,z], 'scale': [sx,sy,sz], 'color': '#...'}]",

            "camera_preset": "orbit_half | dolly_in | truck_right | orbit_arc | from_view",

            "camera_opts": "{'arcDeg': 180, 'radius': 5.2, 'lift': 1.1, 'frames': 49}"

        },

        "heavy": False,

    },

    "replace_proxy_cube": {

        "title": "Замена прокси-кубов в 3D сцене на 3D модели (GLB)",

        "params": {

            "target_ids": "список имен или id кубов для замены",

            "glb_url": "url или путь к 3d модели"

        },

        "heavy": False,

    },

    "add_skeleton": {

        "title": "Добавить скелет манекена (UE Mannequin) в 3D сцену",

        "params": {

            "name": "имя скелета (по умолчанию UE_Mannequin)",

            "position": "[x, y, z]"

        },

        "heavy": False,

    },

    "project_pose": {

        "title": "Спроецировать 3D позу персонажа через камеру шота в 2D слой OpenPose",

        "params": {},

        "heavy": False,

    },

    "deconstruct": {

        "title": "Настроить пассы деконструкции (маски, глубина, поза, трекинг)",

        "params": {"passes": "список: tracking_glomap, mask_sam2_rough, mask_corridor_key, mask_ltx_alpha_lora, pass_depth, pass_canny, pass_pose"},

        "heavy": False,

    },

    "music": {"title": "Сгенерировать музыку (ACE-Step 1.5)", "params": {"tags": "стиль по-английски: жанр, настроение, инструменты, голос",

              "lyrics": "текст песни со структурой [verse]/[chorus] или пусто (без слов)", "duration": "секунды (10-240)", "bpm": "темп"}, "heavy": True},

    "voice": {"title": "Закадровый голос: открыть редактор с текстом", "params": {"text": "текст для озвучки (абзацы через пустую строку)"}, "heavy": False},

    "run_tool": {"title": "Запустить инструмент пользователя («Мои инструменты»)", "params": {

        "tool_id": "id инструмента из списка ниже", "values": "{id входа: url медиа проекта, id параметра: значение}"}, "heavy": True},

    "show_workflow": {"title": "Показать граф нод ComfyUI для процесса", "params": {"process_id": "storyboard | video_gen"}, "heavy": False},

    "create_blank_canvas": {

        "title": "Создать чистый холст (1D фото или 2D видео) для рисования набросков и генерации",

        "params": {

            "kind": "image (1D изображение) | video (2D видеоряд)",

            "title": "короткое имя холста (рус)",

            "width": "ширина в px (например 1280)",

            "height": "высота в px (например 720)",

            "fps": "для video: частота кадров (24.0)",

            "total_frames": "для video: количество кадров (25, 33, 49, 87)",

            "color": "цвет фона (#0c0c0e, #000000, #ffffff, #00ff00)",

        },

        "heavy": False,

    },

    "switch_tab": {

        "title": "Переключить вкладку студии (mesh_gen, details, storyboard, studio_agent, tts_stage1, music, projects)",

        "params": {"tab_id": "mesh_gen | details | storyboard | studio_agent | tts_stage1 | music | projects"},

        "heavy": False,

    },

    "switch_dimension": {

        "title": "Переключить главное измерение рабочей области (1D, 2D, 3D, Storyboard, Director)",

        "params": {"dimension": "1D | 2D | 3D | storyboard | director"},

        "heavy": False,

    },

    "open_details": {

        "title": "Открыть инспектор деталей с Three.js turntable просмотром и картами PBR текстур",

        "params": {"target": "mesh3d | asset_id"},

        "heavy": False,

    },

    "save_pipeline": {

        "title": "Сохранить текущую выполненную последовательность этапов в Мои Пайплайны",

        "params": {"name": "понятное название пайплайна (рус)", "description": "описание цепочки и её назначение"},

        "heavy": False,

    },

    "save_tool": {

        "title": "Сохранить отдельный этап процесса как инструмент в Мои Инструменты",

        "params": {"name": "понятное название инструмента (рус)", "tab": "video | photo | 3d | audio", "description": "описание инструмента"},

        "heavy": False,

    },

}



GO_STEMS = (

    "запуск", "старт", "погнал", "поехал", "генерир", "делай", "делайте",

    "продолж", "утвержд", "начин", "выполн", "приступ", "действуй", "согласен",

    "вперед", "вперёд", "рендери"

)

GO_WORDS = {

    "запускай", "запускайъ", "запусти", "запустиъ", "запускайте", "запуск", "пуск",

    "давай", "давайте", "поехали", "го", "гоу", "да", "ок", "окей", "делай", "делайте",

    "генерируй", "генерируйте", "погнали", "вперед", "вперёд", "жми", "жмите", "go", "run",

    "yes", "ok", "start", "конечно", "согласен", "подтверждаю", "приступай", "приступайте",

    "продолжай", "продолжайте", "продолжи", "утверждаю", "начинай", "начинайте",

    "детализация", "покажи", "выполняй", "выполняйте", "плюс", "плюсую", "ага", "угу"

}

FILLER = {"уже", "его", "это", "плиз", "пожалуйста", "please", "now", "сейчас", "ну", "тогда", "так", "же", "дальше", "всё"}



IMPERATIVE_TRIGGERS = {

    "сделай", "сгенерируй", "запусти", "запускай", "создай", "нарисуй", "построй",

    "отрендери", "примени", "добавь", "поменяй", "замени", "отредактируй", "удали",

    "вырежи", "выдели", "посчитай", "протрекай", "трекай", "делай", "поставь",

    "включи", "переключи", "анимируй", "оживи", "озвучь", "переведи", "дублируй",

    "попробуй", "выполни", "старт", "пуск", "начни", "сделайте", "сгенерируйте",

    "танцует", "захват", "позы", "сохраняя",

    "compose", "generate", "render", "create", "make", "run", "start", "track",

    "apply", "build", "do"

}





def _clean_token(w: str) -> str:

    return re.sub(r"[^\w]", "", w.lower()).rstrip("ъь")





def is_go_command(msg: str) -> bool:

    raw_tokens = (msg or "").strip().lower().split()

    if not raw_tokens or len(raw_tokens) > 5:

        return False

    tokens = [_clean_token(w) for w in raw_tokens if _clean_token(w)]

    if not tokens:

        return False

    is_go_tok = lambda w: w in GO_WORDS or any(w.startswith(stem) for stem in GO_STEMS)

    return any(is_go_tok(w) for w in tokens) and all(is_go_tok(w) or w in FILLER for w in tokens)





def has_imperative_intent(msg: str) -> bool:

    if is_go_command(msg):

        return True

    raw_words = re.sub(r"[^\w\s]", " ", (msg or "").lower()).split()

    tokens = [_clean_token(w) for w in raw_words if _clean_token(w)]

    return any(w in IMPERATIVE_TRIGGERS or any(w.startswith(stem) for stem in GO_STEMS) for w in tokens)





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





def _guide_sections(lang: str = "ru") -> List[Tuple[str, str]]:

    """(heading, text) for every ## / ### section of the user guide in that language (ru / en)."""

    paths = GUIDE_PATHS if lang == "ru" else list(reversed(GUIDE_PATHS))

    for path in paths:

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

    lang = "ru" if re.search(r"[а-яё]", (question or "").lower()) else "en"

    secs = [(h, b) for h, b in _guide_sections(lang) if len(b) >= 160]   # bare headings carry no instructions

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

    s3d = ctx.get("scene_3d")

    if s3d and isinstance(s3d, dict):

        L.append("3D Сцена сейчас (Three.js):")

        if s3d.get("level"):

            L.append(f"  - Активный уровень: «{s3d['level']}»")

        if s3d.get("camera"):

            sc = s3d["camera"]

            L.append(f"  - Кинокамера: «{sc.get('name', 'Camera')}» ({sc.get('frames', 1)} кадров)")

        objs = s3d.get("objects") or []

        if objs:

            L.append("  - Объекты в 3D сцене: " + ", ".join(f"«{o.get('name')}» [{o.get('type')}]" for o in objs[:25]))

        if s3d.get("hasSkeleton"):

            L.append("  - В сцене присутствует скелет/персонаж с анимацией для захвата позы (OpenPose).")

    return "\n".join(L)





def action_catalogue() -> str:

    lines = []

    for aid, a in ACTIONS.items():

        ps = ", ".join(f"{k}: {v}" for k, v in a["params"].items())

        lines.append(f"- {aid}: {a['title']}. Параметры: {ps}")

    tools = user_tools()

    if tools:

        lines.append("Инструменты пользователя (для run_tool):")

        for t in tools:

            ins = ", ".join(f"{i['id']} ({i['kind']}: {i['label']})" for i in t.get("inputs", []))

            prs = ", ".join(f"{p['id']} ({p['label']}, по умолчанию {str(p.get('default'))[:40]})" for p in t.get("params", []))

            lines.append(f"  - {t['id']}: {t.get('name')} — {t.get('description') or 'без описания'}. Входы: {ins or 'нет'}. Параметры: {prs or 'нет'}")

    return "\n".join(lines)





def user_tools() -> List[Dict[str, Any]]:

    """Passports of the user's own workflows (user_tools/<id>/tool.json), read straight from disk."""

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_tools")

    out = []

    if os.path.isdir(root):

        for d in sorted(os.listdir(root)):

            p = os.path.join(root, d, "tool.json")

            try:

                with open(p, encoding="utf-8") as f:

                    out.append(json.load(f))

            except (OSError, ValueError):

                continue

    return out





OUTPUT_RULES = """

## Формат ответа



Сначала ответь пользователю обычным текстом (по-русски, коротко, энергично, деловито).

СТРОЖАЙШЕ ЗАПРЕЩЕНО:

- Говорить: «Это сложный и масштабный процесс», «Поскольку это тяжелый процесс», «Это трудно сделать сразу».

- Жаловаться на сложность или многоэтапность, вздыхать, задавать тормозящие вопросы («С чего мне начать?»).

- Если пользователь дал команду (расставить кубы, сгенерировать 3D, запустить видео/лейаут) — ТЫ СРАЗУ ВЫПОЛНЯЕШЬ ЕЁ И АКТИВИРУЕШЬ ДЕЙСТВИЯ.



В самом конце ОБЯЗАТЕЛЬНО добавь один блок:



```json

{"action": null, "params": {}, "confirm": false, "remember": []}

```



- action: id действия из каталога или null, если сейчас только общий диалог.

- params: параметры действия. Промпты для моделей — по-английски, развёрнутые.

- confirm: false ВСЕГДА, когда пользователь дал команду сделать/сгенерировать/расставить/запустить. Выполняй действие сразу! Никогда не ставь confirm: true и не выпрашивай подтверждения.

- Выполняй РОВНО ТО, что попросил пользователь, не перехватывая инициативу и не запуская незапрошенных процессов:
  * Если пользователь просит 3D-меш (сгенерируй 3д меш, создай 3d модель и т.д.) — вызывай mesh3d (engine: "trellis2", image_source: ссылка или имя референса).
  * Если пользователь просит генерацию изображения — вызывай generate_image.
  * Если пользователь просит генерацию видео — вызывай generate_video (mode: layout_to_render, t2v или i2v).
  * Если пользователь просит расставить 3D-сцену — вызывай setup_3d_layout (не сбрасывай сцену, если пользователь просил сохранить существующие объекты).
  * Для генерации видео LTX-2.5 диффузия выполняется за один проход до 97 кадров без швов.

- remember: новые важные факты о проекте и предпочтениях пользователя (коротко), или [].



ВАЖНО: НИКОГДА не пиши в тексте служебные скобки [выполнено действие: ...]. Действие задаётся ИСКЛЮЧИТЕЛЬНО в блоке ```json ... ``` в самом конце сообщения.

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
        clean_facts = [
            f for f in mem_state["facts"][-80:]
            if not any(w in str(f).lower() for w in ["танцу", "пляж", "два куба", "hero_prop_3d"])
        ]
        if clean_facts:
            parts.append("## Что ты помнишь о проекте и пользователе\n" + "\n".join(f"- {f}" for f in clean_facts))

    pend = mem_state.get("pending")

    if pend:

        parts.append(f"## Ждёт подтверждения пользователя\nТы предложил: {pend.get('action')} {json.dumps(pend.get('params', {}), ensure_ascii=False)[:600]}")

    parts.append(OUTPUT_RULES)

    return "\n\n".join(p for p in parts if p)





def dialogue_messages(log: List[Dict[str, Any]], start: int, budget_chars: int = 350000) -> List[Dict[str, str]]:
    """Newest turns verbatim within the 80k context budget; older ones as digests."""
    turns = log[start:]
    verbatim, used = [], 0
    for e in reversed(turns):
        txt = str(e.get("text", "")).strip()
        txt = re.sub(r"\[(?:выполнено\s+)?действие:\s*[a-zA-Z0-9_]+\s*\{.*?\}\]", "", txt, flags=re.S).strip()
        if e.get("action"):
            txt += f"\n```json\n{{\"action\": \"{e['action']}\", \"params\": {json.dumps(e.get('params', {}), ensure_ascii=False)}}}\n```"
        if used + len(txt) > budget_chars and verbatim:
            break
        verbatim.append({"role": "user" if e.get("role") == "user" else "assistant", "content": txt})
        used += len(txt)
    verbatim.reverse()
    older = turns[: len(turns) - len(verbatim)]
    msgs = []
    if older:
        digest = "\n".join(f"{'Пользователь' if e.get('role') == 'user' else 'Ты'}: {str(e.get('text', ''))[:2000]}" for e in older[-60:])
        msgs.append({"role": "user", "content": f"(Более ранние реплики, контекст)\n{digest}"})
        msgs.append({"role": "assistant", "content": "Помню все предыдущие договоренности и контекст проекта."})
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





def get_agent_tools() -> List[Dict[str, Any]]:

    import sys

    mcp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")

    if mcp_dir not in sys.path:

        sys.path.insert(0, mcp_dir)

    tools = []

    try:

        import keshiki_mcp as km

        for t in km.TOOLS:

            tools.append({

                "type": "function",

                "function": {

                    "name": t[0],

                    "description": t[1],

                    "parameters": t[2]

                }

            })

    except Exception:

        pass



    extra_tools = [

        {

            "type": "function",

            "function": {

                "name": "generate_video",

                "description": "Generate video with LTX-2.5 22B: t2v, i2v, flf2v, or 3D scene layout-to-render.",

                "parameters": {

                    "type": "object",

                    "properties": {

                        "prompt": {"type": "string", "description": "Cinematic visual prompt in English"},

                        "mode": {"type": "string", "enum": ["layout_to_render", "t2v", "i2v", "flf2v"]},

                        "custom_workflow": {"type": "string"}

                    },

                    "required": ["prompt"]

                }

            }

        },

        {

            "type": "function",

            "function": {

                "name": "project_pose",

                "description": "Extract 3D skeleton animation into a 2D OpenPose sequence layer.",

                "parameters": {

                    "type": "object",

                    "properties": {

                        "name": {"type": "string"}

                    }

                }

            }

        }

    ]

    seen = {t["function"]["name"] for t in tools}

    for et in extra_tools:

        if et["function"]["name"] not in seen:

            tools.append(et)

            seen.add(et["function"]["name"])

    return tools





def ollama_chat(model: str, messages: List[Dict[str, Any]], agent_config: Dict[str, Any], num_ctx: int, max_tokens: int = 1500,

                keep_alive: Any = "5m", fmt: Optional[str] = None, temperature: float = 0.6,

                tools: Optional[List[Dict[str, Any]]] = None) -> Any:

    if agent_config.get("provider") == "mock" or agent_config.get("mock"):
        return ("", [])

    url = (agent_config.get("api_url") or "http://127.0.0.1:11434").rstrip("/")

    url = re.sub(r"/(v1|api)$", "", url) + "/api/chat"

    payload = {"model": model, "messages": messages, "stream": False, "keep_alive": keep_alive, "think": False,

               "options": {"num_ctx": num_ctx, "num_predict": max_tokens, "temperature": temperature}}

    if fmt:

        payload["format"] = fmt

    if tools:

        payload["tools"] = tools

    def call(p):

        req = urllib.request.Request(url, data=json.dumps(p).encode("utf-8"), headers={"Content-Type": "application/json"})

        with urllib.request.urlopen(req, timeout=600) as r:

            return json.loads(r.read().decode("utf-8"))

    try:

        res = call(payload)

    except urllib.error.HTTPError:

        payload.pop("think", None)

        try:

            res = call(payload)

        except urllib.error.HTTPError:

            payload.pop("tools", None)

            res = call(payload)

    msg = res.get("message", {}) or {}

    text_content = str(msg.get("content", "")).strip()

    text_content = re.sub(r"<think>.*?</think>", "", text_content, flags=re.S).strip()

    tool_calls = msg.get("tool_calls") or []

    if tools is not None:

        return text_content, tool_calls

    return text_content





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



    # Fallback: extract if the model wrote `[выполнено действие: <action> <json_params>]` or `[действие: ...]`

    if not decision or not decision.get("action"):

        m_act = re.search(r"\[(?:выполнено\s+)?действие:\s*([a-zA-Z0-9_]+)\s*(\{.*?\})\]", text, re.S)

        if m_act:

            act_name = m_act.group(1).strip()

            try:

                prm = json.loads(m_act.group(2))

            except Exception:

                try:

                    prm = json.loads(re.sub(r",\s*}", "}", m_act.group(2).replace("'", '"')))

                except Exception:

                    prm = {}

            decision = {"action": act_name, "params": prm, "confirm": False}

            prose = re.sub(r"\[(?:выполнено\s+)?действие:\s*[a-zA-Z0-9_]+\s*\{.*?\}\]", "", prose, flags=re.S)



    return prose.strip(), decision if isinstance(decision, dict) else {}





# --------------------------------------------------------------------------- #

# Turning a decision into studio actions                                      #

# --------------------------------------------------------------------------- #

def to_studio_actions(action: str, params: Dict[str, Any], studio_ctx: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:

    params = params or {}

    extra: Dict[str, Any] = {}

    acts: List[Dict[str, Any]] = []

    if action in ("generate_image", "edit_image"):

        prompt = str(params.get("prompt") or "").strip()

        title = str(params.get("title") or "Shot")[:40]

        img_id = params.get("image_id") or (studio_ctx.get("active_image") or {}).get("id")

        pose = params.get("pose_layer") or params.get("pose")

        controls = params.get("controls") or []

        engine = params.get("engine") or "flux"

        refs = params.get("references") or []

        if pose and not any((r.get("name") == pose or r.get("id") == pose) for r in refs if isinstance(r, dict)):

            refs.append({"role": "pose", "name": str(pose), "id": str(pose)})



        acts += [

            {"type": "create_storyboard_card", "title": title, "story_note": params.get("title") or title, "mode": "image", "flux_prompt": prompt, "ltx_prompt": ""},

            {"type": "trigger_generation", "mode": "image", "workflow_type": "flux_image", "title": f"FLUX: {title}", "prompt": prompt,

             "image_id": img_id, "pose_layer": pose, "controls": controls, "references": refs, "engine": engine,

             "custom_workflow": "FLUX.3_Klein_Text2Image.json"},

        ]

        extra["flux_prompt"] = prompt

    elif action == "generate_video":

        prompt = str(params.get("prompt") or "").strip()

        mode = str(params.get("mode") or "t2v").lower()

        requested_wf = str(params.get("workflow") or "").strip()

        workflow_map = {

            "t2v": "LTX-2.5/video_ltx2_5_t2v.json",

            "i2v": "LTX-2.5/video_ltx2_5_i2v.json",

            "flf2v": "LTX-2.5/video_ltx2_5_flf2v.json",

            "pro_i2v": "LTX-2.5/LTX-2.5_Pro_Image_to_Video.json",

            "layout_to_render": "LTX-2.5/LTX-2.5_ICLoRA_Layout_To_Render_Two_Stage_Distilled.json",

            "v2v_ic_lora": "LTX-2.5/LTX-2.5_V2V_ICLoRA_Single_Stage_Distilled.json",

            "tiled_fusion_4k": "LTX-2.5/LTX-2.5_V2V_TiledFusion_Native_4K_8K.json",

            "inpaint_sam2": "LTX-2.5/LTX-2.3_-_V2V_inpainting_Inpaint-T2V-lora_Sam2-Point-Masking.json",

            "inpaint_sam3": "LTX-2.5/LTX-2.3_-_V2V_inpainting_Inpaint-T2V-lora_Sam3-Masking.json",

        }

        wf = requested_wf if requested_wf else workflow_map.get(mode, "LTX-2.5/video_ltx2_5_t2v.json")

        title = str(params.get("title") or "Video")[:40]

        start_iid = params.get("image_id") or (studio_ctx.get("active_image") or {}).get("id")

        end_iid = params.get("end_image_id")

        iids = [x for x in (start_iid, end_iid) if x] if mode in ("i2v", "flf2v", "pro_i2v") else []

        vid_id = params.get("video_id") or (studio_ctx.get("active_video") or {}).get("id")

        if mode == "layout_to_render" and not vid_id:

            vids = (studio_ctx.get("videos") or []) if isinstance(studio_ctx, dict) else []

            for v in vids:

                if v.get("id") == "baked_layout_sequence" or "layout" in str(v.get("title", "")).lower():

                    vid_id = v.get("id")

                    break

            if not vid_id:

                vid_id = "baked_layout_sequence"

        if mode == "layout_to_render":
            acts.append({"type": "render_3d_layout", "frames": 49})
        acts += [

            {"type": "create_storyboard_card", "title": f"🎬 {title}", "story_note": title, "mode": "video", "flux_prompt": "", "ltx_prompt": prompt},

            {"type": "trigger_generation", "mode": "video", "workflow_type": "ltx_video", "title": f"LTX: {title}", "prompt": prompt, "custom_workflow": wf,

             "image_ids": iids, "video_id": vid_id, "source_video": vid_id, "generation_mode": mode},

        ]

        extra.update(ltx_prompt=prompt, is_video=True, workflow=wf, mode=mode)

    elif action == "video_process":

        vid_id = params.get("video_id") or (studio_ctx.get("active_video") or {}).get("id")

        prms = dict(params.get("params") or {})

        kind = params.get("kind", "")

        # If the agent erroneously called video_process first_frame when a 1D image is active (no video):

        if kind == "first_frame" and not vid_id and (studio_ctx.get("active_image") or {}).get("id"):

            prompt = str(prms.get("prompt") or "").strip()

            title = str(prms.get("scene") or "First Frame")[:40]

            img_id = (studio_ctx.get("active_image") or {}).get("id")

            pose = prms.get("pose_layer")

            refs = []

            if pose:

                refs.append({"role": "pose", "name": str(pose), "id": str(pose)})

            acts += [

                {"type": "create_storyboard_card", "title": title, "story_note": title, "mode": "image", "flux_prompt": prompt, "ltx_prompt": ""},

                {"type": "trigger_generation", "mode": "image", "workflow_type": "flux_image", "title": f"FLUX First Frame: {title}", "prompt": prompt,

                 "image_id": img_id, "pose_layer": pose, "references": refs, "custom_workflow": "FLUX.3_Klein_Text2Image.json"}

            ]

            extra["flux_prompt"] = prompt

            return acts, extra

        if kind == "ltx_ic_lora":

            prms.pop("chunk", None)

            if not prms.get("lora") and (vid_id == "baked_layout_sequence" or "layout" in str(vid_id or "").lower()):

                prms["lora"] = r"LTX-2.5\ltx-2.5-22b-ic-lora-layout-to-render-1.0.safetensors"

        lora_name = str(prms.get("lora") or "").lower()

        if "clean-plate" in lora_name or "clean_plate" in lora_name or "clean-plate" in str(prms.get("name") or "").lower():

            prms.pop("source_layer", None)

            if not prms.get("lora"):

                prms["lora"] = "clean-plate"

        elif "source_layer" in prms:

            sl = str(prms["source_layer"]).strip()

            layers = (studio_ctx.get("active_video") or {}).get("layers") or []

            existing_ids = {l.get("id"): l for l in layers if l.get("id")}

            if sl not in existing_ids:

                mask_layers = [l for l in layers if l.get("type") in ("mask", "matte")]

                if mask_layers:

                    prms["source_layer"] = mask_layers[-1]["id"]

                else:

                    prms.pop("source_layer", None)

        acts.append({"type": "video_job", "kind": kind, "video_id": vid_id, "params": prms})

    elif action == "dubbing":

        acts.append({"type": "dubbing_job", "mode": params.get("mode") or "dub", "config": params.get("config") or {},

                     "start_at_stage": int(params.get("start_at_stage") or 0)})

    elif action == "mesh3d":

        acts.append({"type": "mesh3d_job", "params": {k: v for k, v in params.items() if v not in (None, "")}})

    elif action == "setup_3d_layout":

        acts.append({

            "type": "setup_3d_layout",

            "cubes": params.get("cubes") or [

                {"name": "Cube_Actor_1", "position": [-2.0, 0.0, 0.5], "scale": [1.2, 1.2, 1.2], "color": "#ff5533"},

                {"name": "Cube_Actor_2", "position": [2.0, 0.0, 0.5], "scale": [1.2, 1.2, 1.2], "color": "#3388ff"}

            ],

            "camera_preset": params.get("camera_preset", "orbit_half"),

            "camera_opts": params.get("camera_opts") or {"arcDeg": 180, "radius": 5.2, "lift": 1.1, "frames": 49}

        })

    elif action == "replace_proxy_cube":

        acts.append({

            "type": "replace_proxy_cube",

            "target_ids": params.get("target_ids") or [params.get("target_id") or "Cube_Actor_1"],

            "glb_url": params.get("glb_url", "")

        })

    elif action == "add_skeleton":

        acts.append({

            "type": "add_character_skeleton",

            "name": params.get("name", "UE_Mannequin"),

            "position": params.get("position", [0.0, 0.0, 0.0])

        })

    elif action == "project_pose":

        acts.append({

            "type": "project_skeleton_to_pose"

        })

        p_prompt = str(params.get("prompt") or params.get("ltx_prompt") or "").strip()

        if p_prompt:

            p_title = str(params.get("title") or "Видео по 3D позе")[:40]

            acts += [

                {"type": "switch_dimension", "dimension": "2D"},

                {"type": "create_storyboard_card", "title": f"🎬 {p_title}", "story_note": p_title, "mode": "video", "flux_prompt": "", "ltx_prompt": p_prompt},

                {"type": "trigger_generation", "mode": "video", "workflow_type": "ltx_video", "title": f"LTX: {p_title}", "prompt": p_prompt,

                 "custom_workflow": "LTX-2.5/LTX-2.5_ICLoRA_Layout_To_Render_Two_Stage_Distilled.json", "pose_layer": "Pose from 3D", "generation_mode": "layout_to_render", "source_video": "baked_layout_sequence", "video_id": "baked_layout_sequence"}

            ]

            extra.update(ltx_prompt=p_prompt, is_video=True)

    elif action == "deconstruct":

        passes = params.get("passes") or []

        if isinstance(passes, str):

            passes = [x.strip() for x in passes.split(",")]

        acts.append({"type": "configure_deconstruction", "passes": {p: True for p in passes}})

    elif action == "music":

        acts.append({"type": "music_job", "params": {"tags": params.get("tags") or params.get("prompt", ""), "lyrics": params.get("lyrics", ""),

                                                     "duration": params.get("duration") or 60, "bpm": params.get("bpm") or 100}})

    elif action == "voice":

        acts.append({"type": "open_voiceover", "text": params.get("text", "")})

    elif action == "run_tool":

        acts.append({"type": "run_user_tool", "tool_id": str(params.get("tool_id") or ""), "values": params.get("values") or {}})

    elif action == "show_workflow":

        acts.append({"type": "visualize_workflow", "process_id": params.get("process_id") or "storyboard", "workflow_name": "FLUX.3_Klein_Text2Image.json"})

    elif action == "create_blank_canvas":

        acts.append({

            "type": "create_blank_canvas",

            "kind": params.get("kind") or "image",

            "title": params.get("title") or ("Video Canvas" if params.get("kind") == "video" else "Image Canvas"),

            "width": int(params.get("width") or 1280),

            "height": int(params.get("height") or 720),

            "fps": float(params.get("fps") or 24.0),

            "total_frames": int(params.get("total_frames") or 25),

            "color": str(params.get("color") or "#0c0c0e")

        })

    elif action == "switch_tab":

        acts.append({"type": "switch_tab", "tab_id": params.get("tab_id") or "mesh_gen"})

    elif action == "switch_dimension":

        acts.append({"type": "switch_dimension", "dimension": params.get("dimension") or "3D"})

    elif action == "open_details":

        acts.append({"type": "open_details", "target": params.get("target") or "mesh3d"})

    elif action == "save_pipeline":

        acts.append({

            "type": "save_pipeline_preset",

            "name": str(params.get("name") or "Мой Пайплайн"),

            "description": str(params.get("description") or "Пайплайн сохраненный ИИ-Режиссером")

        })

    elif action == "save_tool":

        acts.append({

            "type": "save_stage_as_tool",

            "name": str(params.get("name") or "Мой Инструмент"),

            "tab": str(params.get("tab") or "video"),

            "description": str(params.get("description") or "Кастомный инструмент сохраненный ИИ-Режиссером")

        })

    return acts, extra





def is_heavy(action: str, params: Dict[str, Any]) -> bool:

    a = ACTIONS.get(action) or {}

    return bool(a.get("heavy")) or (params or {}).get("kind") in (a.get("heavy_kinds") or [])





# --------------------------------------------------------------------------- #

# One dialogue turn                                                           #

# --------------------------------------------------------------------------- #

def maybe_summarize(mem: AgentMemory, st: Dict[str, Any], log: List[Dict[str, Any]], model: str, agent_config: Dict[str, Any], keep_recent: int = 100) -> None:
    """With 80k context window, keep recent turns fully verbatim in memory and summarize only very distant history."""
    upto = int(st.get("summarized_upto", 0))
    if len(log) - upto < keep_recent + 50:
        return
    chunk = log[upto: len(log) - keep_recent]
    text = "\n".join(f"{'Пользователь' if e.get('role') == 'user' else 'Агент'}: {str(e.get('text', ''))[:1200]}" for e in chunk)
    prev = st.get("summary", "")
    try:
        summary = ollama_chat(model, [
            {"role": "system", "content": "Ты ведёшь журнал кинопроекта. Сожми разговор в связный конспект на русском: цели, решения, стиль, персонажи, локации, что сгенерировано и что не понравилось. Без воды, до 3500 знаков."},
            {"role": "user", "content": (f"Предыдущий конспект:\n{prev}\n\n" if prev else "") + f"Новый фрагмент разговора:\n{text}"}
        ], agent_config, num_ctx=32768, max_tokens=1200)
        if summary:
            st["summary"] = summary.strip()
            st["summarized_upto"] = len(log) - keep_recent
    except Exception:
        pass





def synthesize_intent_actions(user_message: str, studio_ctx: Dict[str, Any], st: Dict[str, Any], log: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:

    msg_l = (user_message or "").strip().lower()

    attached = (studio_ctx or {}).get("attached") or []



    # 1. Autonomous mode request

    if any(phrase in msg_l for phrase in ["работай автономно", "автономно", "без запроса продолжения", "делай все процессы без запроса", "автономный режим"]):

        st["autonomous"] = True

        st["stage_workflow"] = None

        is_direct_work = any(w in msg_l for w in ["сделай", "сгенерируй", "сцена", "видео", "куб", "треллис", "анимац", "рендер"])

        if not is_direct_work:

            reply = (

                "🚀 **Включен полностью автономный режим.**\n\n"

                "Все последующие команды (расстановка 3D сцены, запуск TRELLIS.2, анимация камеры, запекание раскадровки и Layout-to-Render) "

                "будут запускаться автоматически без остановок на подтверждение."

            )

            return {"response_ru": reply, "studio_actions": [], "decision": {"action": None}, "model_used": "rules"}



    # 2. Details inspector

    if any(k in msg_l for k in ["детализация 3д", "детализация сцены", "детализация 3d", "инспектор деталей", "дитейлс", "открой детали", "показать детали", "детали меша", "детализация"]):

        acts = [

            {"type": "open_details", "target": "mesh3d"},

            {"type": "switch_tab", "tab_id": "details"}

        ]

        reply = (

            "🔍 **Открываю инспектор деталей 3D сцены (Details Inspector):**\n\n"

            "• Активирован Three.js turntable просмотр 3D геометрии с возможностью кругового вращения.\n"

            "• Открыты карты PBR текстур (Albedo, Normal, Roughness, UV-развертка).\n"

            "• Доступны кнопки быстрой подмены текстурных карт."

        )

        return {"response_ru": reply, "studio_actions": acts, "decision": {"action": "open_details", "params": {"target": "mesh3d"}}, "model_used": "rules"}

    # 2b. Direct 3D mesh request (e.g. "сгенерируй 3д меш", "сгенерируй 3d", "создай 3д модель", "3d mesh", "trellis")
    is_stage_appr = any(w in msg_l for w in ["этап 1 утвержден", "этап 2 утвержден", "этап 3 утвержден", "утверждаю этап", "утвержден, продолжай", "следующий этап", "утверждаю и продолжай", "этап утвержден", "утвердить и продолжить"])
    is_3d_mesh_req = not is_stage_appr and any(w in msg_l for w in ["сгенерируй 3д", "сгенерируй 3d", "создай 3д", "создай 3d", "3д меш", "3d меш", "3д модель", "3d модель", "3д-меш", "3d-меш", "3д-модел", "3d-модел"]) and not any(w in msg_l for w in ["видео", "video", "ролик", "клип", "два куба", "2 куба", "лейаут то рендер", "layout to render"])
    if is_3d_mesh_req:
        ref_img = ""
        ref_title = "выбранного референса"
        if attached:
            ref_img = attached[0].get("url") or attached[0].get("path") or ""
            ref_title = attached[0].get("title") or attached[0].get("name") or ref_title
        if not ref_img:
            m_at = re.search(r'@([^\s,\)\]]+)', user_message)
            if m_at:
                ref_img = m_at.group(1)
                ref_title = ref_img
        if not ref_img and studio_ctx.get("active_image"):
            ai = studio_ctx["active_image"]
            ref_img = ai.get("url") or ai.get("path") or ai.get("id") or ""
            ref_title = ai.get("title") or ai.get("name") or ref_title
        if not ref_img:
            for e in reversed(log):
                r_list = e.get("refs") or e.get("references") or []
                if r_list:
                    r0 = r_list[0]
                    ref_img = (r0.get("url") or r0.get("path") or r0.get("name") or "") if isinstance(r0, dict) else str(r0)
                    if isinstance(r0, dict):
                        ref_title = r0.get("title") or r0.get("name") or ref_title
                    break

        actor_name = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.splitext(os.path.basename(ref_img))[0]) if ref_img else "Hero_Prop_3D"
        if not actor_name or actor_name == "_":
            actor_name = "Hero_Prop_3D"

        acts = [
            {"type": "switch_tab", "tab_id": "mesh_gen"},
            {"type": "mesh3d_job", "params": {
                "engine": "trellis2",
                "actor_name": actor_name,
                "image_source": ref_img,
                "target_object_id": "Cube_Proxy",
                "auto_replace": True
            }}
        ]
        reply = f"Принято! Запускаю генерацию 3D-меша через TRELLIS.2 по референсу «{ref_title}»."
        st["pending"] = None
        st["stage_workflow"] = None
        return {
            "response_ru": reply,
            "studio_actions": acts,
            "decision": {"action": "mesh3d", "params": {"engine": "trellis2", "image_source": ref_img, "actor_name": actor_name}, "confirm": False},
            "model_used": "rules"
        }



    # 3. Check if user explicitly asked to PRESERVE the existing 3D scene

    is_go = is_go_command(user_message)

    recent_context = " ".join([str(e.get("text", "")).lower() for e in log[-4:]])

    scene_preserved = any(phrase in msg_l or phrase in recent_context for phrase in [

        "ничего не меняй", "не меняй", "готовая сцена", "готовая 3д сцена", "существующую камеру",

        "существующую сцену", "сохраняя текущую", "main stage", "у тебя готовая"

    ]) or bool((studio_ctx.get("scene_3d") or {}).get("objects"))



    # 4a. Question or request regarding 97 frames / one-pass vs 33 frames

    is_97_frames_request = any(k in msg_l for k in [

        "97 кадр", "97 кадров", "по 33 кадра", "целиком 97", "однопроходн", "без разбивки на куски", "один проход"

    ])

    if is_97_frames_request:

        is_launch_cmd = any(k in msg_l for k in ["запусти", "сделай", "сгенерируй", "рендер", "поехали", "давай", "пуск", "старт", "выполни"])

        reply = (

            "⚡ **Однопроходный режим (One-Pass) на 97 кадров в LTX-2.5:**\n\n"

            "• **Как это работает:** В движке студии (`video_layers_service.py` и `ltx25_ic_lora_v2v.json`) при отсутствии параметра `chunk: 33` система автоматически активирует однопроходный режим `_one_pass`.\n"

            "• **Резервирование памяти:** Для 97 кадров автоматически выделяется резерв **5.0 ГБ VRAM** (`reserve_vram_gb: 5.0`), что предотвращает фрагментацию памяти и замедление 22B модели.\n"

            "• **Преимущества:** Генерация 97 кадров занимает ~3.3 минуты (на RTX 3090/4090) и выполняется **абсолютно монолитно**, без швов, артефактов на стыках чанков и без дёрганья первого кадра.\n"

        )

        if is_launch_cmd:
            prompt_en = "Cinematic photorealistic video render with fluid natural motion, dynamic lighting, 4k"
            acts = [

                {"type": "switch_dimension", "dimension": "2D"},

                {"type": "trigger_generation", "mode": "video", "workflow_type": "ltx_video", "title": "LTX 97 Frames One-Pass", "prompt": prompt_en,

                 "custom_workflow": "LTX-2.5/LTX-2.5_ICLoRA_Layout_To_Render_Two_Stage_Distilled.json", "generation_mode": "layout_to_render", "source_video": "baked_layout_sequence", "video_id": "baked_layout_sequence"}

            ]

            reply += "\n🚀 **Запускаю однопроходную генерацию на 97 кадров без разбивки на куски!**"

            return {

                "response_ru": reply,

                "studio_actions": acts,

                "decision": {"action": "generate_video", "params": {"mode": "layout_to_render", "prompt": prompt_en}, "confirm": False},

                "model_used": "rules",

                "ltx_prompt": prompt_en,

                "is_video": True

            }

        else:

            reply += "\n💡 Вы можете сразу дать команду «Запускай 97 кадров» или нажать «Запустить генерацию», и я отрендеpredю клип монолитно."

            return {

                "response_ru": reply,

                "studio_actions": [],

                "decision": {"action": None},

                "model_used": "rules"

            }



    # 4. Pose capture from 3D skeleton + Video Generation pipeline

    is_pose_capture_request = any(k in msg_l for k in [
        "захват позы", "сними позу", "снять позу", "захват анимации", "позу со скелета",
        "позу с скелета", "анимации с скелета", "анимацию со скелета", "извлеки позу",
        "скелетон с анимацией", "скелет с анимацией", "анимированная камера плюс скелетон"
    ])

    is_video_followup = any(k in msg_l for k in ["генерацию видео", "сделай видео", "сгенерируй видео", "создай видео", "танцует", "пляж", "видео", "создать видео", "клип"])



    last_user_req = ""

    last_substantive_req = ""

    for entry in reversed(log[-10:]):

        if entry.get("role") == "user":

            t_entry = str(entry.get("text", "")).strip().lower()

            if not last_user_req:

                last_user_req = t_entry

            if not is_go_command(t_entry) and not last_substantive_req:

                last_substantive_req = t_entry

                break

    if not last_substantive_req:

        last_substantive_req = last_user_req



    is_resume_pose_video = False

    if is_pose_capture_request and is_video_followup:
        target_text = user_message
        target_lower = target_text.lower()
        prompt_en = "Cinematic video render of character motion in scene, dynamic camera motion, photorealistic, 4k"

        acts = [
            {"type": "render_3d_layout", "frames": 49},
            {"type": "project_skeleton_to_pose"},
            {"type": "switch_dimension", "dimension": "2D"},
            {"type": "create_storyboard_card", "title": "Захват позы и видео", "story_note": target_text, "mode": "video", "flux_prompt": "", "ltx_prompt": prompt_en},
            {"type": "trigger_generation", "mode": "video", "workflow_type": "ltx_video", "title": "LTX: Захват позы и видео", "prompt": prompt_en,
             "custom_workflow": "LTX-2.5/LTX-2.5_ICLoRA_Layout_To_Render_Two_Stage_Distilled.json", "pose_layer": "Pose from 3D", "generation_mode": "layout_to_render", "source_video": "baked_layout_sequence", "video_id": "baked_layout_sequence"}
        ]
        reply = (
            "🎬 **Запускаю захват позы и генерацию видео:**\n\n"
            "1. **3D Лейаут и раскадровка:** Запекаю траекторию камеры Three.js и геометрию сцены в согласованную раскадровку (49 кадров).\n"
            "2. **Захват позы:** Извлекаю скелетную анимацию `Skeleton` через виртуальную камеру сцены («клинплейт») в согласованный слой OpenPose на таймлинии 2D.\n"
            "3. **Layout-to-Render:** Переношу позу и раскадровку в двухстадийный пайплайн `LTX-2.5 IC-LoRA 22B`.\n"
            "4. **Генерация:** Запускаю рендеринг видео на основе захваченной позы и геометрии сцены.\n\n"
            "🚀 Все действия запущены в производство!"
        )
        st["pending"] = None
        return {

            "response_ru": reply,

            "studio_actions": acts,

            "decision": {"action": "generate_video", "params": {"mode": "layout_to_render", "prompt": prompt_en}, "confirm": False},

            "model_used": "rules",

            "ltx_prompt": prompt_en,

            "is_video": True

        }



    # 5. Direct 3D scene + Trellis + Two cubes blockout (only when explicitly requested with cubes/trellis, never for skeleton/dance video)
    is_pose_or_skeleton = any(k in msg_l for k in ["скелет", "скелетон", "танцу", "пляж", "клинплейт", "песочн", "захват позы", "модель человека"])
    is_cubes_trellis = ("два куба" in msg_l or "2 куба" in msg_l) and ("треллис" in msg_l or "замени ими кубы" in msg_l or "куб" in msg_l)
    has_camera_or_layout = any(k in msg_l for k in ["камер", "крупного плана", "общий план", "лейаут то рендер", "layout to render", "создай видео"])
    is_resume_cubes = is_go and not scene_preserved and len(log) > 0 and any("два куба" in str(e.get("text", "")).lower() or "2 куба" in str(e.get("text", "")).lower() for e in log[-2:])

    if (not scene_preserved and not is_pose_or_skeleton and is_cubes_trellis) or is_resume_cubes:
        cubes = [

            {"name": "Cube_Girl", "position": [0.0, 0.0, 0.5], "scale": [0.9, 0.9, 1.8], "color": "#e05588"},

            {"name": "Cube_Table", "position": [1.6, 0.0, 0.45], "scale": [1.4, 0.9, 0.8], "color": "#8b5a2b"}

        ]

        cam_opts = {"arcDeg": 180, "radius": 5.2, "lift": 1.1, "frames": 49}

        acts = [

            {"type": "switch_dimension", "dimension": "3D"},

            {"type": "setup_3d_layout", "cubes": cubes, "camera_preset": "orbit_half", "camera_opts": cam_opts}

        ]



        ref1 = attached[0] if len(attached) > 0 else {}

        ref2 = attached[1] if len(attached) > 1 else {}

        ref1_url = ref1.get("url") or ref1.get("path") or ""

        ref2_url = ref2.get("url") or ref2.get("path") or ""



        acts.append({

            "type": "mesh3d_job",

            "actor_name": "Girl_Model",

            "target_object_id": "Cube_Girl",

            "auto_replace": True,

            "params": {

                "engine": "trellis2",

                "actor_name": "Girl_Model",

                "image_source": ref1_url,

                "target_object_id": "Cube_Girl"

            }

        })

        if ref2_url:

            acts.append({

                "type": "mesh3d_job",

                "actor_name": "Table_Model",

                "target_object_id": "Cube_Table",

                "auto_replace": True,

                "params": {

                    "engine": "trellis2",

                    "actor_name": "Table_Model",

                    "image_source": ref2_url,

                    "target_object_id": "Cube_Table"

                }

            })



        if has_camera_or_layout or "лейаут" in msg_l or "layout" in msg_l or "видео" in msg_l or is_resume_cubes:

            acts.append({

                "type": "run_layout_to_render",

                "workflow": "LTX-2.5/LTX-2.5_ICLoRA_Layout_To_Render_Two_Stage_Distilled.json",

                "prompt": "Cinematic photorealistic shot of a girl standing next to a table, camera smooth dolly back from close-up to wide shot, natural lighting 70s style, 4k",

                "frames": 49

            })



        reply = (

            "🎬 **Запускаю полный цикл 3D постановки и Layout-to-Render:**\n\n"

            "1. **3D Сцена (Three.js):** Создаю плоскость пола, расставляю прокси-кубы персонажа (`Cube_Girl`) и стола (`Cube_Table`).\n"

            "2. **Анимация камеры:** Настроен динамический отъезд от крупного плана девушки на общий план со столом (49 кадров).\n"

            "3. **TRELLIS.2:** Запускаю генерацию PBR 3D мешей по вашим референсам с автоматической заменой кубов в 3D пространстве.\n"

            "4. **Layout-to-Render:** Раскадровка передаётся в двухстадийный пайплайн `LTX-2.5 IC-LoRA 22B` для финального рендеринга видео."

        )

        return {

            "response_ru": reply,

            "studio_actions": acts,

            "decision": {"action": "setup_3d_layout", "params": {"cubes": cubes}, "confirm": False},

            "model_used": "rules"

        }



    return None





def plan_turn(user_message: str, project_dir: str, studio_ctx: Dict[str, Any], agent_config: Dict[str, Any],

              ui_history: Optional[List[Dict[str, Any]]] = None, llm_fallback=None) -> Dict[str, Any]:

    agent_config = agent_config or {}

    mem = AgentMemory(project_dir)

    st = mem.state()

    if ui_history and len(ui_history) > 0:
        log = []
        for h in ui_history:
            txt = str(h.get("text") or h.get("content") or "").strip()
            if txt:
                role = "user" if h.get("role") == "user" else "assistant"
                log.append({"role": role, "text": txt, "action": h.get("action"), "params": h.get("params")})
    else:
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

    if is_go_command(user_message):
        last_turn = log[-1] if log else {}
        last_txt = str(last_turn.get("text", "")).lower()
        prev_user_req = ""
        prev_refs = []
        for e in reversed(log):
            if e.get("role") == "user" and not is_go_command(str(e.get("text", ""))):
                prev_user_req = str(e.get("text", "")).lower()
                prev_refs = e.get("refs") or e.get("references") or []
                break

        is_prev_3d = any(w in last_txt for w in ["trellis", "3d-меш", "3д-меш", "3д модель", "3d модель", "генерацию 3d", "генерацию 3д", "3d-модел"]) or \
                     any(w in prev_user_req for w in ["3д меш", "3d меш", "сгенерируй 3д", "сгенерируй 3d", "треллис", "trellis", "3д модель", "3d модель", "создай 3д"])
        is_prev_video = any(w in prev_user_req for w in ["видео", "ролик", "клип", "video", "анимируй"])

        if is_prev_3d and not is_prev_video:
            ref_img = ""
            if studio_ctx.get("attached"):
                ref_img = studio_ctx["attached"][0].get("url") or studio_ctx["attached"][0].get("path") or ""
            elif prev_refs:
                r0 = prev_refs[0]
                ref_img = (r0.get("url") or r0.get("path") or r0.get("name") or "") if isinstance(r0, dict) else str(r0)
            if not ref_img:
                m_at = re.search(r'@([^\s,\)\]]+)', prev_user_req)
                if m_at:
                    ref_img = m_at.group(1)

            acts = [
                {"type": "switch_tab", "tab_id": "mesh_gen"},
                {"type": "mesh3d_job", "params": {
                    "engine": "trellis2",
                    "actor_name": "Generated_3D_Asset",
                    "image_source": ref_img,
                    "target_object_id": "Cube_Proxy",
                    "auto_replace": True
                }}
            ]
            reply = "Принято! Запускаю генерацию 3D-меша через TRELLIS.2 по вашему референсу."
            mem.append("user", user_message)
            mem.append("assistant", reply, action="mesh3d", params={"engine": "trellis2", "image_source": ref_img})
            st["pending"] = None
            st["stage_workflow"] = None
            mem.save_state(st)
            return {
                "response_ru": reply,
                "studio_actions": acts,
                "decision": {"action": "mesh3d", "params": {"engine": "trellis2", "image_source": ref_img}, "confirm": False},
                "model_used": "rules"
            }



    msg_l = (user_message or "").lower()

    is_visualize = any(w in msg_l for w in ["визуализир", "показывай действия", "показывая окна", "открывая окна", "покажи в окне", "открой окно", "с визуализацией", "покажи процесс"])

    is_stage_confirm = any(w in msg_l for w in ["подтверждени", "по этапам", "по шагам", "сделай кусок", "если утвердили", "поэтапн", "утверждением этапов", "просыпался", "продолжения от пользователя", "кусок показал результат", "по шагам с паузой"])

    is_stage_approved = any(w in msg_l for w in ["этап 1 утвержден", "этап 2 утвержден", "этап 3 утвержден", "утверждаю этап", "утвержден, продолжай", "следующий этап", "утверждаю и продолжай", "этап утвержден", "утвердить и продолжить"])



    if not (is_stage_confirm and not st.get("autonomous")):

        synth = synthesize_intent_actions(user_message, studio_ctx, st, log)

        if synth:

            mem.append("user", user_message)

            mem.append("assistant", synth["response_ru"])

            mem.save_state(st)

            return synth



    perf_transfer_triggers = [

        "сними анимацию", "анимацию поз", "анимацию людей", "трекинг камер", "проецируем на камеру анимацию скелета",

        "нововй сцене новый персонаж", "новой сцене новый персонаж", "новый персонаж но камера та же",

        "движение человека то же", "рескиннинг", "перенос движения", "перенос перформанса", "reskin"

    ]

    is_perf_transfer = any(t in msg_l for t in perf_transfer_triggers)



    has_complex_intent = any(w in msg_l for w in [
        "сложно?", "сможем?", "как лучше", "посмотрим как", "разработать пайплайн",
        "разработаем пайплайн", "попробуй это сделать", "что думаешь", "как думаешь", "как считаешь",
        "пайплайн", "на самом деле"
    ]) or ("?" in msg_l and any(q in msg_l for q in ["как", "сможем", "сложно", "лучше", "вариант", "почему"]))
    if is_perf_transfer and is_stage_confirm and not has_complex_intent:

        vid_id = (studio_ctx.get("active_video") or {}).get("id") or "vid_active"

        acts = [

            {"type": "video_job", "kind": "pose", "video_id": vid_id, "params": {}},

            {"type": "video_job", "kind": "track_camera", "video_id": vid_id, "params": {}}

        ]

        stage_wf = {

            "active": True,

            "stage_index": 1,

            "total_stages": 4,

            "stage_name": "Снятие анимации поз (OpenPose) и 3D трекинг камеры (GLOMAP)",

            "next_stage_name": "Сборка нового 3D окружения в Three.js",

            "summary": "Запущено покадровое извлечение движений человека (слой поз OpenPose) и 3D трекинг камеры оператора через GLOMAP.",

            "awaiting_approval": True,

            "next_instruction": "Этап 1 утвержден. Переходи к этапу 2: Сборка нового 3D окружения в Three.js. Визуализируй действия в окнах студии.",

            "rework_instruction": "Переделай этап 1: скорректируй параметры трекинга."

        }

        reply = (

            "🎬 **Запускаю пайплайн переноса перформанса и рескиннинга персонажа!**\n\n"

            "• **Этап 1:** Запускаю покадровое извлечение поз человека (`OpenPose`) и 3D трекинг камеры через `GLOMAP`.\n"

            "• По завершении трекинга нажмите **[ ✓ Утвердить и продолжить ]**, чтобы я проснулся и перешел к сборке новой 3D сцены!"

        )

        st["stage_workflow"] = stage_wf

        mem.append("user", user_message)

        mem.append("assistant", reply)

        mem.save_state(st)

        return {"response_ru": reply, "studio_actions": acts, "stage_workflow": stage_wf, "decision": {"action": "video_process", "params": {"kind": "pose"}, "confirm": False}}



    elif is_perf_transfer and not has_complex_intent:

        vid_id = (studio_ctx.get("active_video") or {}).get("id") or "vid_active"

        acts = [

            {"type": "video_job", "kind": "pose", "video_id": vid_id, "params": {}},

            {"type": "video_job", "kind": "track_camera", "video_id": vid_id, "params": {}},

            {"type": "switch_dimension", "dimension": "3D"},

            {"type": "setup_3d_layout", "cubes": [

                {"name": "New_Scene_Environment_1", "position": [-2.5, 0.0, 1.0], "scale": [2.0, 2.0, 2.0], "color": "#3388ff"},

                {"name": "New_Scene_Environment_2", "position": [2.5, 0.0, 1.0], "scale": [2.0, 2.0, 2.0], "color": "#ffaa22"}

            ]},

            {"type": "add_character_skeleton", "name": "UE_Mannequin", "position": [0.0, 0.0, 0.0]},

            {"type": "project_skeleton_to_pose"},

            {"type": "create_storyboard_card", "title": "Рескиннинг персонажа", "story_note": user_message, "mode": "video", "flux_prompt": "Cinematic new character in 3D scene", "ltx_prompt": "Matchmove performance render with tracked camera"},

            {"type": "trigger_generation", "mode": "video", "workflow_type": "ltx_video", "title": "LTX Рескиннинг", "prompt": "Cinematic render of character in new scene with tracked camera", "custom_workflow": "LTX-2.5/LTX-2.5_ICLoRA_Layout_To_Render_Two_Stage_Distilled.json"}

        ]

        reply = (

            "🎬 **Выполняю полный пайплайн переноса перформанса и рескиннинга:**\n\n"

            "1. Снимаю позы человека (`OpenPose`) и рассчитываю 3D трекинг камеры (`GLOMAP`).\n"

            "2. Перехожу в 3D и собираю новое окружение с геометрией.\n"

            "3. Помещаю скелет манекена и проецирую его анимацию через виртуальную кинокамеру в согласованный слой поз.\n"

            "4. Запускаю генерацию нового персонажа по референсу в LTX-2.5 Layout-to-Render!"

        )

        mem.append("user", user_message)

        mem.append("assistant", reply)

        mem.save_state(st)

        return {"response_ru": reply, "studio_actions": acts, "decision": {"action": "generate_video", "params": {"mode": "layout_to_render"}, "confirm": False}}



    curr_stage_wf = st.get("stage_workflow")
    # Verify stage_workflow is still relevant to the recent dialogue
    last_assistant_text = ""
    for entry in reversed(log):
        if entry.get("role") == "assistant":
            last_assistant_text = str(entry.get("text", "")).lower()
            break
    is_stage_context = any(w in last_assistant_text for w in ["этап 1", "этап 2", "этап 3", "утвердить и продолжить", "блокаут", "прокси-куб", "проснулся"])
    is_explicit_stage_approval = is_stage_approved or (
        curr_stage_wf and curr_stage_wf.get("awaiting_approval") and is_stage_context and any(w in msg_l for w in ["утвердить и продолжить", "этап утвержден", "утверждаю этап", "следующий этап", "утверждаю и продолжай"])
    )
    if curr_stage_wf and not is_explicit_stage_approval:
        curr_stage_wf = None
        st["stage_workflow"] = None

    if is_explicit_stage_approval:

        prev_idx = curr_stage_wf.get("stage_index", 1) if curr_stage_wf else (1 if "этап 1" in msg_l else 2)

        if prev_idx == 1 or "этап 1" in msg_l:

            acts = [

                {"type": "switch_tab", "tab_id": "mesh_gen"},

                {"type": "mesh3d_job", "params": {"engine": "trellis2", "actor_name": "Generated_3D_Asset", "target_object_id": "Cube_Actor_1", "auto_replace": True}}

            ]

            stage_wf = {

                "active": True,

                "stage_index": 2,

                "total_stages": 3,

                "stage_name": "Генерация 3D модели (TRELLIS.2) и замена куба",

                "next_stage_name": "Запуск Layout-to-Render (LTX-2.5 IC-LoRA)",

                "summary": "Открыта вкладка генерации 3D («mesh_gen»). Запущен процесс TRELLIS.2 для генерации PBR 3D меша и замены куба в сцене.",

                "awaiting_approval": True,

                "next_instruction": "Этап 2 утвержден. Переходи к этапу 3: Запуск Layout-to-Render в LTX-2.5 IC-LoRA. Визуализируй действия в окнах студии.",

                "rework_instruction": "Переделай этап 2: повтори генерацию 3D меша с новыми параметрами."

            }

            reply = (

                "✨ **Этап 1 утвержден! Я проснулся и перехожу к этапу 2:**\n\n"

                "• Открываю вкладку генерации 3D (`mesh_gen`), чтобы вы видели параметры и ход запекания геометрии.\n"

                "• Запускаю генерацию текстурированного PBR меша через TRELLIS.2.\n"

                "• По завершении готовый GLB заменит прокси-куб в Three.js.\n\n"

                "Проверьте геометрию и нажмите **[ ✓ Утвердить и продолжить ]**, чтобы я проснулся для запуска финального Layout-to-Render."

            )

            st["stage_workflow"] = stage_wf

            mem.append("user", user_message)

            mem.append("assistant", reply)

            mem.save_state(st)

            return {"response_ru": reply, "studio_actions": acts, "stage_workflow": stage_wf, "decision": {"action": "mesh3d", "params": {"engine": "trellis2"}, "confirm": False}}

        elif prev_idx >= 2 or "этап 2" in msg_l:

            selected_wf = "LTX-2.5/LTX-2.5_ICLoRA_Layout_To_Render_Two_Stage_Distilled.json"

            acts = [

                {"type": "switch_dimension", "dimension": "2D"},

                {"type": "run_layout_to_render", "workflow": selected_wf, "prompt": "Cinematic photorealistic layout render with dynamic camera motion", "frames": 49}

            ]

            stage_wf = {

                "active": False,

                "stage_index": 3,

                "total_stages": 3,

                "stage_name": "Layout-to-Render диффузия (LTX-2.5 IC-LoRA)",

                "next_stage_name": "Все этапы завершены",

                "summary": "3D раскадровка передана в двухстадийный пайплайн LTX-2.5 IC-LoRA 22B на таймлинию 2D. Итоговое видео генерируется.",

                "awaiting_approval": False

            }

            reply = (

                "✨ **Этап 2 утвержден! Я проснулся и перехожу к финальному этапу 3:**\n\n"

                "• Переключаю окно на таймлинию 2D.\n"

                "• Запекаю 3D раскадровку сцены и траекторию камеры в видеоплейт.\n"

                "• Запускаю двухстадийную диффузию в LTX-2.5 IC-LoRA 22B на GPU.\n\n"

                "🚀 Все этапы успешно пройдены и запущены в производство!"

            )

            st["stage_workflow"] = stage_wf

            mem.append("user", user_message)

            mem.append("assistant", reply)

            mem.save_state(st)

            return {"response_ru": reply, "studio_actions": acts, "stage_workflow": stage_wf, "decision": {"action": "generate_video", "params": {"mode": "layout_to_render"}, "confirm": False}}

    elif is_stage_confirm and not has_complex_intent and any(w in msg_l for w in ["расставь 3д", "расставь сцену", "сгенерируй меш", "прокси-куб", "блокаут"]):
        acts = [
            {"type": "switch_dimension", "dimension": "3D"},
            {"type": "setup_3d_layout", "cubes": [
                {"name": "Cube_Actor_1", "position": [0.0, 0.0, 0.5], "scale": [1.2, 1.2, 1.2], "color": "#3388ff"}
            ], "camera_preset": "orbit_half", "camera_opts": {"arcDeg": 180, "radius": 5.2, "lift": 1.1, "frames": 49}}
        ]
        stage_wf = {
            "active": True,
            "stage_index": 1,
            "total_stages": 3,
            "stage_name": "Расстановка 3D блокаута и камеры",
            "next_stage_name": "Генерация 3D модели (TRELLIS.2) и замена прокси-куба",
            "summary": "Открыт 3D вьюпорт Three.js. Прокси-куб установлен в пространстве, настроен круговой облет камеры 180°. Ожидаю вашего подтверждения.",
            "awaiting_approval": True,
            "next_instruction": "Этап 1 утвержден. Переходи к этапу 2: Генерация 3D мешей через TRELLIS.2 и замена кубов. Визуализируй действия в окнах студии.",
            "rework_instruction": "Переделай этап 1: измени расстановку 3D сцены."
        }
        reply = (
            "🎬 **Запускаю пошаговое выполнение с подтверждением этапов и визуализацией!**\n\n"
            "• **Этап 1:** Переключаю окно на 3D вьюпорт, расставляю прокси-кубы и настраиваю круговой облет камеры 180°.\n"
            "• Посмотрите на 3D сцену на холсте. Если блокаут вас устраивает — нажмите **[ ✓ Утвердить и продолжить ]**, "
            "я мгновенно проснусь и перейду к генерации 3D меша в TRELLIS.2!"
        )
        st["stage_workflow"] = stage_wf
        mem.append("user", user_message)
        mem.append("assistant", reply)
        mem.save_state(st)
        return {"response_ru": reply, "studio_actions": acts, "stage_workflow": stage_wf, "decision": {"action": "setup_3d_layout", "params": {}, "confirm": False}}

    if curr_stage_wf and curr_stage_wf.get("awaiting_approval"):
        curr_stage_wf["awaiting_approval"] = False
        st["stage_workflow"] = curr_stage_wf

    provider = str(agent_config.get("provider") or "ollama")

    local = provider in LOCAL_PROVIDERS or provider in LEGACY_LOCAL_PROVIDERS

    if local:

        micro = None

        # 1) plain one-line commands ("сделай глубину") are recognised by rules, without any model:

        #    the 0.8B dispatcher misread real requests and launched wrong jobs, so it is gone.

        cmd = rule_route(user_message, studio_ctx)

        if cmd:

            act, prm = "video_process", {"kind": cmd}

            reply = micro_phrase(act, prm) + "…"

            acts, extra = to_studio_actions(act, prm, studio_ctx)

            st["pending"] = None

            mem.append("user", user_message)

            mem.append("assistant", reply, **({"action": act, "params": prm} if acts else {}))

            mem.save_state(st)

            return {"response_ru": reply, "studio_actions": acts, "decision": {"action": act, "params": prm, "confirm": False},

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
    raw_ctx = int(agent_config.get("context_tokens") or 80000)
    # Context window: default to 80,000 tokens for Qwen and large models per user requirement
    if re.search(r"qwen3\.8|qwen38|qwen", model.lower()):
        num_ctx = max(raw_ctx, 80000)
    else:
        num_ctx = max(raw_ctx, 32768)

    maybe_summarize(mem, st, log, model, agent_config)

    system = build_system_prompt(studio_ctx, st, user_message)

    messages = [{"role": "system", "content": system}] + dialogue_messages(log, int(st.get("summarized_upto", 0)))

    messages.append({"role": "user", "content": user_message})



    t0 = time.time()

    tools_schema = get_agent_tools()

    tool_calls = []

    if local:

        text, tool_calls = ollama_chat(model, messages, agent_config, num_ctx=num_ctx, tools=tools_schema)

    elif llm_fallback:

        text = llm_fallback(system, messages)

    else:

        text = ""

    if not text and not tool_calls:
        if provider == "mock" or agent_config.get("mock"):
            text = "Mock LLM Director response."
        else:
            raise RuntimeError("LLM returned nothing")



    prose, decision = parse_decision(text) if text else ("", {})



    if tool_calls:

        tc = tool_calls[0]

        fn_name = tc.get("function", {}).get("name", "")

        fn_args = tc.get("function", {}).get("arguments", {})

        if isinstance(fn_args, str):

            try: fn_args = json.loads(fn_args)

            except Exception: fn_args = {}

        if fn_name == "ask_studio_agent":

            try:

                import keshiki_mcp as km

                kb_ans = km.t_ask_studio_agent(fn_args)

                messages.append({"role": "assistant", "content": text or "Consulting studio knowledge base...", "tool_calls": [tc]})

                messages.append({"role": "tool", "content": json.dumps(kb_ans, ensure_ascii=False)})

                text_2, tool_calls_2 = ollama_chat(model, messages, agent_config, num_ctx=num_ctx, tools=tools_schema)

                if text_2: prose = text_2

                if tool_calls_2:

                    tc = tool_calls_2[0]

                    fn_name = tc.get("function", {}).get("name", "")

                    fn_args = tc.get("function", {}).get("arguments", {})

                    if isinstance(fn_args, str):

                        try: fn_args = json.loads(fn_args)

                        except Exception: fn_args = {}

            except Exception as _kbe:

                pass



        if fn_name:

            action = fn_name

            params = fn_args

            decision = {"action": action, "params": params, "confirm": False}

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



    if not action:

        synth = synthesize_intent_actions(text, studio_ctx, st, log)

        if synth and synth.get("studio_actions"):

            action = synth.get("decision", {}).get("action")

            params = synth.get("decision", {}).get("params", {})

            decision = synth.get("decision", {})

        else:

            ltx_m = re.search(r"(?:\[LTX VIDEO PROMPT\]|\*\*LTX:\*\*)\s*([^\r\n]+)", text, re.I)
            user_wants_3d_mesh = any(w in (user_message or "").lower() for w in ["3д меш", "3d меш", "меш", "3д модель", "3d модель", "trellis", "hunyuan"])
            user_wants_video = any(w in (user_message or "").lower() for w in ["видео", "ролик", "клип", "video", "анимируй", "лейаут то рендер", "layout to render"])

            if ltx_m and not user_wants_3d_mesh:
                ltx_p = ltx_m.group(1).strip()
                action = "generate_video"
                params = {"mode": "layout_to_render", "prompt": ltx_p}
                decision = {"action": action, "params": params, "confirm": False}
            elif user_wants_video and any(k in text.lower() for k in ["запускаю рендеринг видео", "запускаю генерацию видео", "mode: layout_to_render"]):
                action = "generate_video"
                params = {"mode": "layout_to_render", "prompt": "Cinematic 3D render of character and scene"}
                decision = {"action": action, "params": params, "confirm": False}



    user_imp = has_imperative_intent(user_message) or is_go_command(user_message)

    # heavy actions wait for a "go" unless the user explicitly commanded it ("сделай", "запусти", "давай", etc.)

    # or the model explicitly marked confirm: false

    if st.get("autonomous"):

        confirm = False

    elif "confirm" in decision:

        confirm = bool(decision["confirm"]) and not user_imp

    else:

        confirm = is_heavy(action, params) and not user_imp if action else False



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

        if is_visualize and acts:

            if action in ("setup_3d_layout", "replace_proxy_cube", "add_skeleton", "project_pose"):

                if not any(a.get("type") == "switch_dimension" for a in acts):

                    acts.insert(0, {"type": "switch_dimension", "dimension": "3D"})

            elif action == "mesh3d":

                if not any(a.get("type") == "switch_tab" for a in acts):

                    acts.insert(0, {"type": "switch_tab", "tab_id": "mesh_gen"})

            elif action in ("generate_video", "video_process"):

                if not any(a.get("type") == "switch_dimension" for a in acts):

                    acts.insert(0, {"type": "switch_dimension", "dimension": "2D"})

            elif action in ("generate_image", "edit_image"):

                if not any(a.get("type") == "switch_dimension" for a in acts):

                    acts.insert(0, {"type": "switch_dimension", "dimension": "1D"})

    mem.append("user", user_message)

    mem.append("assistant", prose, **({"action": action, "params": params} if action and not confirm else {}))

    mem.save_state(st)

    return {"response_ru": prose or "…", "studio_actions": acts, "decision": {"action": action, "params": params, "confirm": confirm},

            "model_used": model, "seconds": round(time.time() - t0, 1), **extra}

