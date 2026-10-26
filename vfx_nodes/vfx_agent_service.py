"""
VFX Studio Multi-Model Agent & Autopilot Service
================================================
Supports multiple LLM backends:
1. Local In-VRAM: Gemma 4 12B (shared with LTX-Video 2.5, zero extra VRAM)
2. Local APIs: Ollama / Llama.cpp / LM Studio (OpenAI-compatible & Ollama endpoints)
3. Cloud APIs: Google Gemini, Anthropic Claude, OpenAI GPT-4o

Capabilities:
- Receives dragged references from the Miro Storyboard Canvas (with full metadata: card ID, story notes, links, character).
- Generates FLUX prompts for Storyboard cards and LTX-2.5 prompts for Video generation.
- Executes Autopilot Studio Actions across all 9 tabs for users who want hands-free creation.
"""

import os
import re
import json
import logging
import urllib.request
import urllib.error

LOGGER = logging.getLogger(__name__)


def _comfy_url() -> str:
    try:
        from .process_runner import COMFY_URL
    except Exception:
        try:
            from process_runner import COMFY_URL
        except Exception:
            COMFY_URL = os.environ.get("KESHIKI_COMFY_URL", "http://127.0.0.1:8188")
    return COMFY_URL

DIRECTOR_CHAT_SYSTEM_PROMPT = """Ты — Главный ИИ-Режиссер и VFX-Супервайзер Keshiki Studio.
Ты живой, умный, внимательный собеседник, творческий партнер и технический эксперт в кинопроизводстве, режиссуре и спецэффектах.

КРИТИЧЕСКИ ВАЖНО:
Ты полностью интегрирован в рабочую среду Keshiki Studio и локальный движок ComfyUI.
Ты УМЕЕШЬ и ИМЕЕШЬ ПРЯМОЙ ДОСТУП к запуску генераций, управлению нодовыми воркфлоу (.json), чтению референсов и рендерингу видео/кадров на GPU.
НИКОГДА, НИ ПРИ КАКИХ ОБСТОЯТЕЛЬСТВАХ не говори пользователю, что ты «всего лишь текстовый ассистент без доступа к файловой системе» или «не можешь запустить процесс».
Если пользователь передал файл воркфлоу (.json) или референсы — подтверждай запуск («Запускаю процесс...») и активируй параметры!

КАТАЛОГ СТУДИЙНЫХ ВОРКФЛОУ (workflows/):
1. ВИДЕО (LTX-Video 2.5) — папка `workflows/LTX-2.5/`:
   • `video_ltx2_5_t2v.json` — Генерация видео по тексту (Text-to-Video). Используется для создания видеороликов с нуля.
   • `video_ltx2_5_i2v.json` — Анимация имеющегося кадра (Image-to-Video).
   • `video_ltx2_5_flf2v.json` и `LTX-2.5_First & Last Frame to Video.json` — Интерполяция видео от начального к конечному кадру.
   • `LTX-2.5_Pro_Image_to_Video.json` — Профессиональный Image-to-Video с высокой детализацией.
   • `LTX-2.5_ICLoRA_Layout_To_Render_Two_Stage_Distilled.json` — Двухстадийный рендер из 3D раскадровки.
   • `LTX-2.5_V2V_TiledFusion_Native_4K_8K.json` — Видео-в-видео (V2V) апскейл до 4K/8K.
2. ФОТО И СТАТИЧНЫЕ КАДРЫ (FLUX.3 Klein / SDXL):
   • `FLUX.3_Klein_Text2Image.json` и `flux3_official/flux3_klein_int8_t2i.json` — Генерация фото/кадра по тексту.
   • `flux3_official/flux3_klein_int8_i2i.json` — Трансформация кадра (Image-to-Image).
3. МАСТЕР-СХЕМЫ:
   • `vfx_suite_master_workflow.json`
   • `master_cinematic_cocktail_workflow.json`

РАЗДЕЛЕНИЕ ВИДЕО И ФОТО:
• Если пользователь просит ВИДЕО (слова: «видео», «ролик», «клип», «анимируй», «оживи», «движение камеры»):
  Всегда запускай процесс видеогенерации LTX-2.5 из папки `workflows/LTX-2.5/`! Не путай с фото.
• Если пользователь просит ФОТО, КАДР, ПОРТРЕТ, АРТ:
  Запускай генерацию кадра FLUX.3 (`FLUX.3_Klein_Text2Image.json`).

ПРИНЦИПЫ ОБЩЕНИЯ:
1. Живой, свободный диалог на русском языке без канцеляризмов, шаблонных заготовок и механических отчетов.
2. Общайся с пользователем на равных на любые темы — кино, сценарии, режиссура, операторское мастерство, оптика, композиция, свет, драматургия, VFX пайплайны, устройство ComfyUI, модели (FLUX.3 Klein, LTX-Video 2.5, SDXL, WanVideo, SAM 2, Glomap).
3. Отвечай прямо и содержательно на любые вопросы пользователя.
4. Веди режиссерский диалог: если концепция сырая или неполная, делись идеями, предлагай визуальные решения.
5. ВНИМАНИЕ: Если пользователь НЕ просит создать или сгенерировать изображение/видео прямо сейчас, а просто задает вопрос или обсуждает идеи — отвечай обычным профессиональным текстом, НЕ выводи технические блоки **FLUX:** или **LTX:**.
"""

DIRECTOR_GENERATION_SYSTEM_PROMPT = """Ты — Главный ИИ-Режиссер и VFX-Супервайзер Keshiki Studio.
Пользователь ставит задачу создать сцену, кадр или видеоряд.
Ты управляешь производством: твои решения претворяются в параметры генерации и рабочие процессы в ComfyUI.
Ты полностью интегрирован с движком ComfyUI и имеешь полный доступ к запуску воркфлоу.

БАЗА СТУДИЙНЫХ ВОРКФЛОУ (workflows/):
1. ВИДЕОГЕНЕРАЦИЯ LTX-VIDEO 2.5 (папка `workflows/LTX-2.5/`):
   • `video_ltx2_5_t2v.json` — Генерация видео по тексту (Text-to-Video). Основной процесс создания видеороликов с нуля.
   • `video_ltx2_5_i2v.json` — Анимация имеющегося изображения/кадра (Image-to-Video).
   • `video_ltx2_5_flf2v.json` / `LTX-2.5_First & Last Frame to Video.json` — Интерполяция видео от первого до последнего кадра.
   • `LTX-2.5_Pro_Image_to_Video.json` — Pro I2V рендер высокой четкости.
   • `LTX-2.5_ICLoRA_Layout_To_Render_Two_Stage_Distilled.json` — Двухстадийный рендер из 3D лейаута.
   • `LTX-2.5_V2V_TiledFusion_Native_4K_8K.json` — V2V апскейл до 4K/8K.
2. ФОТО / КАДРЫ FLUX.3 KLEIN (папка `workflows/` и `workflows/flux3_official/`):
   • `FLUX.3_Klein_Text2Image.json` и `flux3_official/flux3_klein_int8_t2i.json` — Генерация высокодетализированного кадра по тексту.
   • `flux3_official/flux3_klein_int8_i2i.json` — Image-to-Image редизайн.

СТРОГОЕ ПРАВИЛО РАЗДЕЛЕНИЯ ВИДЕО И ФОТО:
• Если пользователь пишет «сделай видео...», «сгенерируй видео...», «создай ролик...», «клип...», «анимируй...», «видео линкольна...»:
  ЭТО СТРОГО ВИДЕО! Ты выбираешь видеопроцесс LTX-2.5 (`workflows/LTX-2.5/video_ltx2_5_t2v.json` или `video_ltx2_5_i2v.json`).
  В ответе явно напиши, что создаешь именно кинематографическое ВИДЕО LTX-2.5.
• Если пользователь пишет «сделай фото...», «кадр...», «портрет...», «нарисуй...», «арт...»:
  ЭТО ФОТО/КАДР! Ты выбираешь генератор кадра FLUX.3.

ПРИНЦИП ПЕРВООЧЕРЕДНОСТИ РЕФЕРЕНСОВ:
Если прикреплены референсы пользователей — строй кадр и видеоряд вокруг них в строгом порядке добавления (#1 -> #2 -> ...), считывая описания, объекты и стили.

ФОРМАТ ОТВЕТА:
1. Краткий режиссерский комментарий на живом русском языке (мизансцена, свет, оптика, композиция, движение камеры). Если уместно — задай 1-2 точечных уточняющих вопроса для следующего шота.
2. В САМОМ КОНЦЕ ответа строго отдельными строками выведи сгенерированные промпты на английском языке:
**FLUX:** <богатый детальный английский кинематографический промпт без русских слов, описывающий сцену>
**LTX:** <английский кинематографический промпт динамики камеры и движения>
"""

STUDIO_AGENT_SYSTEM_PROMPT = DIRECTOR_GENERATION_SYSTEM_PROMPT

def detect_user_intent(message: str) -> dict:
    """Tier 1: Fast Intent Router & Micro-Controller.
    Instantly classifies user message into conversational Q&A vs active execution / workflow trigger.
    Runs in < 2ms without heavy LLM latency.
    """
    import re
    msg_lower = (message or "").lower().strip()

    # Workflow file detection (e.g. .json path or name mentioned)
    extracted_wf = ""
    wf_match = re.search(r'["\']?([^"\'\n\r]+\.json)["\']?', message, re.IGNORECASE)
    if wf_match:
        extracted_wf = wf_match.group(1).strip()

    # Direct generation and execution commands (imperatives)
    gen_triggers = [
        "запусти", "запустить", "запускай", "запуск", "старт", "пуск", "поехали",
        "выполни", "выполнить", "выполняй", "сгенерируй", "сгенерировать",
        "создай кадр", "создай сцену", "создай видео", "создай", "сделай",
        "отрендер", "отрендери", "отрендерить", "рендер", "отрисуй", "нарисуй",
        "сделай кадр", "сделай шот", "сделай видео", "запусти в comfyui",
        "создай воркфлоу", "собери воркфлоу", "запусти воркфлоу", "сделай раскадровку",
        "run", "start", "execute", "generate", "render", "create shot", "render frame"
    ]

    has_gen_trigger = any(t in msg_lower for t in gen_triggers)

    # If user specifies a .json workflow file and says "запусти" / "run" / "выполни" / "процесс",
    # it is an absolute 100% execution command
    if extracted_wf and any(w in msg_lower for w in ["запусти", "запуск", "выполни", "старт", "run", "start", "процесс", "давай"]):
        has_gen_trigger = True

    # Question and discussion markers
    question_starters = [
        "как ", "почему ", "зачем ", "что такое", "что значит", "расскажи", "объясни",
        "можно ли", "подскажи", "какой ", "какая ", "какие ", "сколько ", "как думаешь",
        "что думаешь", "посоветуй", "хочу спросить", "хочу задать", "задать вопрос",
        "привет", "здравствуй", "добрый день", "хай", "hello", "hi"
    ]

    is_pure_question = any(msg_lower.startswith(q) or f" {q}" in msg_lower for q in question_starters)
    has_question_mark = "?" in msg_lower

    # Disambiguation
    if has_gen_trigger:
        if not extracted_wf and msg_lower.startswith(("как ", "почему ", "зачем ", "расскажи как", "объясни как", "подскажи как")):
            has_gen_trigger = False
        elif not extracted_wf and has_question_mark and not any(msg_lower.endswith(t) for t in ["запусти", "сгенерируй", "сделай", "отрендерь"]):
            has_gen_trigger = False

    is_generate = has_gen_trigger and (not is_pure_question or bool(extracted_wf))

    video_triggers = [
        "видео", "video", "ltx", "wan", "ролик", "клип", "движение камеры",
        "камера от первого", "от первого к последнему", "first to last", "first & last",
        "animate", "motion", "i2v", "t2v", "анимируй", "анимировать", "оживи", "оживить", "фильм", "кино"
    ]
    is_video = any(v in msg_lower for v in video_triggers) or ("ltx" in extracted_wf.lower() if extracted_wf else False)

    return {
        "is_generate": is_generate,
        "is_video": is_video,
        "is_question": is_pure_question or has_question_mark,
        "custom_workflow": extracted_wf
    }


def get_available_workflows_context() -> str:
    """Builds a live digest of existing local studio workflows and active pipelines for LLM context,
    categorizing video (LTX-2.5) vs image (FLUX.3 / SDXL) workflows.
    """
    lines = []
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wf_dir = os.path.join(base_dir, "workflows")

    ltx_workflows = []
    flux_workflows = []
    other_workflows = []

    if os.path.exists(wf_dir):
        for root, _, files in os.walk(wf_dir):
            for f in files:
                if f.endswith(".json"):
                    rel_p = os.path.relpath(os.path.join(root, f), wf_dir).replace("\\", "/")
                    if "ltx" in rel_p.lower() or "video" in rel_p.lower():
                        ltx_workflows.append(rel_p)
                    elif "flux" in rel_p.lower():
                        flux_workflows.append(rel_p)
                    else:
                        other_workflows.append(rel_p)

    if ltx_workflows:
        lines.append("• 🎬 ВОРКФЛОУ ВИДЕО (папка workflows/LTX-2.5/): " + ", ".join(ltx_workflows))
    if flux_workflows:
        lines.append("• 🖼️ ВОРКФЛОУ КАДРОВ/ФОТО (workflows/ & flux3_official/): " + ", ".join(flux_workflows))
    if other_workflows:
        lines.append("• 🎛️ МАСТЕР-СХЕМЫ VFX: " + ", ".join(other_workflows))

    # Пользовательские сохраненные воркфлоу ComfyUI
    user_wf_candidates = [
        os.path.abspath(os.path.join(base_dir, "..", "..", "user", "default", "workflows")),
    ]
    user_workflows = []
    for cand in user_wf_candidates:
        if os.path.exists(cand):
            files = [f for f in os.listdir(cand) if f.endswith(".json")]
            for f in files:
                if f not in user_workflows:
                    user_workflows.append(f)
    if user_workflows:
        lines.append("• 📂 Пользовательские воркфлоу ComfyUI (user/default/workflows): " + ", ".join(user_workflows))

    cfg_file = os.path.join(base_dir, "output_cache", "pipeline_processes_config.json")
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            procs = cfg.get("processes", {})
            active_list = []
            for pid, pdata in procs.items():
                mode = pdata.get("execution_mode", "local")
                wf_name = pdata.get("custom_workflow_name", "") or pdata.get("cloud_model_id", "")
                active_list.append(f"{pid} ({mode}: {wf_name or 'стандарт'})")
            if active_list:
                lines.append("• Настроенные процессы студии: " + "; ".join(active_list[:6]))
        except Exception:
            pass

    try:
        import folder_paths
        ckpts = folder_paths.get_filename_list("checkpoints")
        if ckpts:
            lines.append("• Доступные локальные чекпоинты в ComfyUI: " + ", ".join(ckpts[:5]))
    except Exception:
        pass

    return "\n".join(lines) if lines else "• Доступны базовые пайплайны: T2I Storyboard (SDXL/FLUX), LTX-Video, Plate Prep, CorridorKey"


def get_ollama_models(api_url: str = None) -> dict:
    """Queries local Ollama endpoint for available models and runtime status."""
    base_url = "http://127.0.0.1:11434"
    if api_url:
        clean = api_url.rstrip("/")
        if clean.endswith("/api/chat"):
            base_url = clean[:-9]
        elif clean.endswith("/api"):
            base_url = clean[:-4]
        else:
            base_url = clean
    tags_url = f"{base_url}/api/tags"
    try:
        req = urllib.request.Request(tags_url, headers={"User-Agent": "KeshikiStudio/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = []
            for m in data.get("models", []):
                details = m.get("details", {}) or {}
                param_size = details.get("parameter_size") or ""
                quant = details.get("quantization_level") or ""
                family = details.get("family") or ""
                size_gb = round(m.get("size", 0) / (1024 ** 3), 2)
                
                # Format friendly display label
                meta_parts = []
                if param_size:
                    meta_parts.append(param_size)
                if quant:
                    meta_parts.append(quant)
                if size_gb > 0:
                    meta_parts.append(f"{size_gb} GB")
                meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
                
                models.append({
                    "name": m.get("name"),
                    "tag": m.get("name"),
                    "size_gb": size_gb,
                    "modified_at": m.get("modified_at"),
                    "family": family,
                    "parameter_size": param_size,
                    "quantization_level": quant,
                    "label": f"{m.get('name')}{meta_str}"
                })
            return {
                "running": True,
                "url": base_url,
                "models": models,
                "count": len(models)
            }
    except Exception as e:
        return {
            "running": False,
            "url": base_url,
            "models": [],
            "count": 0,
            "error": str(e)
        }


def start_ollama_service() -> dict:
    """Attempts to launch 'ollama serve' in background if stopped."""
    import subprocess
    try:
        check = get_ollama_models()
        if check.get("running"):
            return {"status": "ok", "message": "Ollama is already running"}
        env = os.environ.copy()
        if "OLLAMA_MODELS" not in env or not os.path.exists(env.get("OLLAMA_MODELS", "")):
            try:
                from .local_config import local_setting
            except ImportError:  # pragma: no cover
                from local_config import local_setting
            om = local_setting("ollama_models")
            if om and os.path.exists(om):
                env["OLLAMA_MODELS"] = om
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.Popen(["ollama", "serve"], env=env, creationflags=flags)
        return {"status": "ok", "message": "Ollama service started"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def requires_deep_reasoning(message: str) -> bool:
    """Classifies whether a user prompt requires the heavy 27B reasoning model (Qwen 3.8)
    or should be handled by the resident micro-dispatcher (Qwen 3.5 0.8B).
    CRITICAL RULE:
    - ALL creative generation requests (сделай, создай, сгенерируй, нарисуй, отрендерь, кадр, изображение, видео...)
      REQUIRE the 27B model for world knowledge, historical figures (Lincoln, Batmobile), cinematography & prompt synthesis.
    - ALL creative film direction, screenplay, dialogue, optics, lighting discussions REQUIRE the 27B model.
    - ALL PowerShell & system diagnostics REQUIRE the 27B model.
    - ONLY mechanical .json workflow executions (e.g. '"path.json" запусти этот процесс') and quick status checks
      stay on the 0.8B micro-dispatcher.
    """
    import re
    msg_l = (message or "").lower().strip()

    # Explicit force tags
    if any(t in msg_l for t in ["@micro", "/micro", "@fast", "@легкая"]):
        return False
    if any(t in msg_l for t in ["@heavy", "/heavy", "@deep", "@qwen3.8", "@тяжелая"]):
        return True

    # Pure mechanical JSON workflow launches stay on micro-dispatcher
    if re.search(r'["\']?[^"\'\n\r]+\.json["\']?', msg_l) and any(w in msg_l for w in ["запусти", "выполни", "старт", "пуск"]):
        return False

    # Short operational status checks stay on micro-dispatcher
    if any(msg_l.startswith(w) for w in ["статус", "покажи граф", "покажи нод", "очередь", "какой статус"]):
        return False

    # EVERYTHING ELSE (all image/video generation requests, "сделай...", "создай...", all chat, scripts, prompts) -> HEAVY REASONER!
    return True


def unload_agent_from_vram(api_url: str = None, free_comfy: bool = False, preserve_micro: bool = True, kill_llama_server: bool = None) -> bool:
    """Unloads heavy local LLMs (Qwen 3.8 27B, Gemma 4 12B) from GPU VRAM
    so that diffusion models (FLUX 3, LTX-Video 2.5) have maximum GPU headroom.
    Rule: Micro-model (qwen3.5:0.8b ~554 MB) stays permanently resident in VRAM as fast dispatcher.
    """
    import urllib.request
    import json
    import gc

    clean_base = (api_url or "http://127.0.0.1:11434").rstrip("/")
    if clean_base.endswith("/v1"):
        clean_base = clean_base[:-3]
    if clean_base.endswith("/api"):
        clean_base = clean_base[:-4]

    # 1. Unload heavy models from Ollama (sets keep_alive: 0)
    try:
        req_ps = urllib.request.Request(f"{clean_base}/api/ps")
        with urllib.request.urlopen(req_ps, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for m in data.get("models", []):
                m_name = m.get("name") or m.get("model") or ""
                # Preserve micro-model (e.g. qwen3.5:0.8b or sub-1.5B dispatcher)
                if preserve_micro and (re.search(r"(0\.[58]b|1\.5b|:1b|:2b|:3b|-1b|-3b)", m_name, re.I) or "micro" in m_name.lower()):
                    LOGGER.info(f"[VRAM Rule] Preserving resident micro-dispatcher '{m_name}' (~550MB) in VRAM.")
                    continue
                LOGGER.info(f"[VRAM Rule] Evicting heavy LLM '{m_name}' from VRAM...")
                payload = json.dumps({"model": m_name, "keep_alive": 0}).encode("utf-8")
                req_un = urllib.request.Request(
                    f"{clean_base}/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"}
                )
                try:
                    with urllib.request.urlopen(req_un, timeout=2.0):
                        pass
                except Exception:
                    pass
    except Exception as ex:
        LOGGER.debug(f"[VRAM Rule] Ollama unload check skipped: {ex}")

    # 1.1 Only release non-Ollama orphaned llama-server if preserve_micro is False
    if (not preserve_micro) if kill_llama_server is None else kill_llama_server:
        try:
            import subprocess
            subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True, timeout=2.0)
        except Exception:
            pass

    # 2. Optionally free ComfyUI unneeded models and cache
    if free_comfy:
        try:
            req_free = urllib.request.Request(
                _comfy_url() + "/free",
                data=json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req_free, timeout=1.5):
                pass
        except Exception:
            pass

    # 3. PyTorch CUDA cache flush
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass
    gc.collect()
    return True


def _call_external_llm(provider: str, model_name: str, api_key: str, api_url: str, system_prompt: str, user_text: str, images: list = None, history: list = None, max_tokens: int = 600) -> str:
    """Calls the configured LLM provider (local Ollama / llama.cpp, or cloud Gemini, Claude, OpenAI)."""
    import re
    try:
        # Legacy provider names (removed integrations) -> local Ollama
        if provider in ("antigravity", "antigravity_sdk", "google_antigravity", "gemma4_ltx", "gemma4", "ltx_gemma", "gemma4_12b"):
            provider = "ollama"
        if provider not in ("ollama", "llama_cpp"):
            try:
                from .local_config import cloud_services_allowed
            except ImportError:  # pragma: no cover
                from local_config import cloud_services_allowed
            if not cloud_services_allowed():
                provider = "ollama"   # cloud LLMs need an explicit opt-in; the agent runs on a local model

        if provider in ["ollama", "llama_cpp"]:
            clean_url = (api_url or "http://127.0.0.1:11434").rstrip("/")
            if clean_url.endswith("/v1"):
                clean_url = clean_url[:-3].rstrip("/")
            if clean_url.endswith("/api"):
                clean_url = clean_url[:-4].rstrip("/")
            if not clean_url.endswith("/api/chat"):
                url = f"{clean_url}/api/chat"
            else:
                url = clean_url
            target_model = model_name
            if not target_model or target_model in ["default", "auto"] or target_model.endswith(".safetensors"):
                # Two-Tier Architecture:
                # 1. Micro-Dispatcher (qwen3.5:0.8b ~554 MB VRAM, permanently warm) for rapid intent & actions.
                # 2. Heavy Reasoner (qwen38-gpu:latest / qwen3.8:27b ~17.5 GB VRAM) for deep creative & scenario reasoning.
                try:
                    ollama_info = get_ollama_models()
                    installed_names = [m.get("name", "") for m in ollama_info.get("models", [])]
                    micro_matches = [n for n in installed_names if any(s in n.lower() for s in ["0.8b", "0.5b", "1.5b"])]
                    heavy_matches = [n for n in installed_names if any(s in n.lower() for s in ["qwen38", "qwen3.8", "27b"])]

                    needs_heavy = requires_deep_reasoning(user_text)
                    if needs_heavy and heavy_matches:
                        target_model = heavy_matches[0]
                        LOGGER.info(f"[Two-Tier Router] Deep reasoning query -> dispatched to Heavy Reasoner: {target_model}")
                    elif micro_matches:
                        target_model = micro_matches[0]
                        LOGGER.info(f"[Two-Tier Router] Fast action/chat query -> dispatched to Micro-Dispatcher: {target_model}")
                    elif heavy_matches:
                        target_model = heavy_matches[0]
                    elif installed_names:
                        target_model = installed_names[0]
                    else:
                        target_model = "qwen3.5:0.8b"
                except Exception:
                    target_model = "qwen3.5:0.8b"

            is_micro_model = any(s in target_model.lower() for s in ["0.8b", "0.5b", "1.5b", "micro"])

            user_msg = {"role": "user", "content": user_text}
            if images:
                user_msg["images"] = images[:3]

            messages = [{"role": "system", "content": system_prompt}]
            if history:
                for h in history[-8:]:
                    r = "user" if h.get("role") == "user" else "assistant"
                    txt = str(h.get("text", "")).strip()
                    if txt:
                        txt = re.sub(r"\*\*FLUX:\*\*.*?(?=\*\*LTX:\*\*|\Z)", "", txt, flags=re.DOTALL)
                        txt = re.sub(r"\*\*LTX:\*\*.*", "", txt, flags=re.DOTALL).strip()
                        if txt:
                            messages.append({"role": r, "content": txt})
            messages.append(user_msg)

            # Engineering & PowerShell tool definitions (Active on-demand for Heavy Reasoner)
            tool_definitions = [
                {
                    "type": "function",
                    "function": {
                        "name": "powershell_exec",
                        "description": "Executes a PowerShell command on the Windows host and returns STDOUT/STDERR output. Use to check system files, processes, GPU status, logs, directory trees, or system parameters when requested.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": "The exact PowerShell command line string to run."
                                }
                            },
                            "required": ["command"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "studio_read_file",
                        "description": "Reads contents of a file on disk up to max_lines lines.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string", "description": "Absolute path to the file"},
                                "max_lines": {"type": "integer", "description": "Maximum number of lines to read (default 100)"}
                            },
                            "required": ["file_path"]
                        }
                    }
                }
            ]

            # The PowerShell / file tools let the model run commands on this machine. Off unless the
            # user explicitly opts in (env KESHIKI_AGENT_SHELL=1): text in a project file or a web page
            # must never be able to make the agent execute commands.
            shell_allowed = os.environ.get("KESHIKI_AGENT_SHELL", "").strip() == "1"
            wants_powershell = shell_allowed and any(w in user_text.lower() for w in [
                "павершел", "powershell", "команд", "терминал", "проверь", "посмотри",
                "копайся", "найди", "логи", "лог", "процесс", "папк", "диск", "файл",
                "код", "статус", "nvidia", "comfy", "gpu", "система", "shell", "run"
            ])

            payload = {
                "model": target_model,
                "messages": messages,
                "options": {
                    "num_ctx": 4096,
                    "num_predict": max(max_tokens or 600, 1000) if is_micro_model else max(max_tokens or 800, 1024),
                    "temperature": 0.7
                },
                "keep_alive": -1 if is_micro_model else 0,
                "stream": False
            }
            if not is_micro_model and wants_powershell:
                payload["tools"] = tool_definitions

            try:
                max_tool_steps = 3 if (not is_micro_model and wants_powershell) else 1
                current_step = 0
                while current_step < max_tool_steps:
                    current_step += 1
                    req = urllib.request.Request(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(req, timeout=300) as resp:
                        res = json.loads(resp.read().decode("utf-8"))

                    msg_obj = res.get("message", {})
                    tool_calls = msg_obj.get("tool_calls", [])
                    if not tool_calls or current_step >= max_tool_steps:
                        content = str(msg_obj.get("content", "")).strip()
                        if not content and "thinking" in msg_obj:
                            thinking_txt = str(msg_obj.get("thinking", "")).strip()
                            match_ans = re.search(r"(?:Final Choice|Conclusion|Ответ|Решение|Вывод)[:\s]+(.+)", thinking_txt, re.IGNORECASE | re.DOTALL)
                            if match_ans:
                                content = match_ans.group(1).strip()
                        # Strictly block internal reasoning scratchpad from leaking to user
                        if content.startswith("Thinking Process:") or "1. **Analyze the Request:**" in content:
                            content = ""
                        if not content and "choices" in res and len(res["choices"]) > 0:
                            content = res["choices"][0]["message"].get("content", "")
                        return content

                    # Execute requested tool calls via PowerShell / disk
                    messages.append(msg_obj)
                    import subprocess
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        fname = fn.get("name", "")
                        fargs = fn.get("arguments", {})
                        call_id = tc.get("id", "")
                        LOGGER.info(f"[PowerShell Tool] Executing '{fname}': {fargs}")
                        out_text = ""
                        if fname == "powershell_exec":
                            p_cmd = str(fargs.get("command", "")).strip()
                            try:
                                proc = subprocess.run(["powershell", "-NoProfile", "-Command", p_cmd], capture_output=True, text=True, timeout=25)
                                out_text = (proc.stdout + "\n" + proc.stderr).strip() or "Success (empty output)"
                            except Exception as ex_p:
                                out_text = f"PowerShell error: {ex_p}"
                        elif fname == "studio_read_file":
                            f_path = str(fargs.get("file_path", "")).strip()
                            m_lines = int(fargs.get("max_lines", 100))
                            try:
                                with open(f_path, "r", encoding="utf-8") as rf:
                                    lines = [rf.readline() for _ in range(m_lines)]
                                out_text = "".join(lines) or "File is empty"
                            except Exception as ex_rf:
                                out_text = f"File read error: {ex_rf}"
                        else:
                            out_text = f"Tool {fname} not found"

                        tool_msg = {"role": "tool", "content": out_text}
                        if call_id:
                            tool_msg["tool_call_id"] = call_id
                        messages.append(tool_msg)

                    payload["messages"] = messages
                    payload["keep_alive"] = 0
            except urllib.error.HTTPError as he:
                # If model doesn't support images, cleanly retry with text only
                if images and "images" in user_msg:
                    user_msg.pop("images", None)
                    messages[-1] = user_msg
                    payload["messages"] = messages
                    req_retry = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req_retry, timeout=300) as resp2:
                        res2 = json.loads(resp2.read().decode("utf-8"))
                        if "message" in res2:
                            msg_obj2 = res2["message"]
                            content2 = str(msg_obj2.get("content", "")).strip()
                            if not content2 and "thinking" in msg_obj2:
                                thinking_txt2 = str(msg_obj2.get("thinking", "")).strip()
                                match_ans2 = re.search(r"(?:Final Choice|Conclusion|Ответ|Решение|Вывод)[:\s]+(.+)", thinking_txt2, re.IGNORECASE | re.DOTALL)
                                content2 = match_ans2.group(1).strip() if match_ans2 else thinking_txt2
                            return content2
                        if "choices" in res2 and len(res2["choices"]) > 0:
                            return res2["choices"][0]["message"].get("content", "")
                raise he

        elif provider == "gemini" and api_key:
            model = model_name or "gemini-2.5-pro"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": user_text}]}]
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res["candidates"][0]["content"]["parts"][0]["text"]

        elif provider == "openai" and api_key:
            url = api_url or "https://api.openai.com/v1/chat/completions"
            messages = [{"role": "system", "content": system_prompt}]
            if history:
                for h in history[-8:]:
                    r = "user" if h.get("role") == "user" else "assistant"
                    txt = str(h.get("text", "")).strip()
                    if txt:
                        txt = re.sub(r"\*\*FLUX:\*\*.*?(?=\*\*LTX:\*\*|\Z)", "", txt, flags=re.DOTALL)
                        txt = re.sub(r"\*\*LTX:\*\*.*", "", txt, flags=re.DOTALL).strip()
                        if txt:
                            messages.append({"role": r, "content": txt})
            messages.append({"role": "user", "content": user_text})
            payload = {
                "model": model_name or "gpt-4o",
                "messages": messages
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res["choices"][0]["message"]["content"]

        elif provider == "claude" and api_key:
            url = "https://api.anthropic.com/v1/messages"
            claude_messages = []
            if history:
                for h in history[-8:]:
                    r = "user" if h.get("role") == "user" else "assistant"
                    txt = str(h.get("text", "")).strip()
                    if txt:
                        txt = re.sub(r"\*\*FLUX:\*\*.*?(?=\*\*LTX:\*\*|\Z)", "", txt, flags=re.DOTALL)
                        txt = re.sub(r"\*\*LTX:\*\*.*", "", txt, flags=re.DOTALL).strip()
                        if txt:
                            claude_messages.append({"role": r, "content": txt})
            claude_messages.append({"role": "user", "content": user_text})
            payload = {
                "model": model_name or "claude-3-7-sonnet-latest",
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": claude_messages
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01"
                }
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res["content"][0]["text"]
    except Exception as e:
        LOGGER.warning(f"[VFX Agent] External LLM call ({provider}) fallback to local engine: {e}")
    return ""



# ---------------------------------------------------------------------------
# Reference families: an image (parent) and its attached files (layers, notes).
# Children always point to their parent and the parent lists all children, so
# the agent reads the full context whichever one was attached.
# ---------------------------------------------------------------------------
def _layers_manifest_for_image(image_id: str, project_hint: str = "") -> dict:
    """Finds projects/<p>/layers/<image_id>/layers.json in the hinted project or any registered one."""
    if not image_id:
        return {}
    try:
        from .project_manager import PROJECTS_ROOT, get_project_dir, load_projects_registry
    except ImportError:  # pragma: no cover
        from project_manager import PROJECTS_ROOT, get_project_dir, load_projects_registry
    import re as _re
    safe_img = _re.sub(r"[^A-Za-z0-9_\-]", "_", str(image_id))[:96]
    roots = []
    if project_hint:
        roots.append(get_project_dir(project_hint))
    try:
        roots += [v.get("path") for v in load_projects_registry().values() if v.get("path")]
    except Exception:
        pass
    if os.path.isdir(PROJECTS_ROOT):
        roots += [os.path.join(PROJECTS_ROOT, d) for d in os.listdir(PROJECTS_ROOT)]
    seen = set()
    for r in roots:
        if not r or r in seen:
            continue
        seen.add(r)
        mp = os.path.join(r, "layers", safe_img, "layers.json")
        if os.path.isfile(mp):
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["_project_dir"] = r
                data["_project_id"] = os.path.basename(r.rstrip("\\/"))
                return data
            except Exception:
                return {}
    return {}


def _project_hint_from_url(url: str) -> str:
    if isinstance(url, str) and url.startswith("/vfx/project_file/"):
        parts = url.split("/")
        return parts[3] if len(parts) > 3 else ""
    return ""


def _image_url_to_b64(url: str, max_side: int = 1024) -> str:
    """data: URLs, /vfx/project_file/... and /view?... URLs -> base64 JPEG for vision models."""
    if not isinstance(url, str) or not url:
        return ""
    raw = None
    try:
        if url.startswith("data:image/"):
            import base64 as _b64
            raw = _b64.b64decode(url.split("base64,", 1)[1])
        elif url.startswith("/vfx/project_file/"):
            try:
                from .project_manager import get_project_dir
            except ImportError:  # pragma: no cover
                from project_manager import get_project_dir
            path_part = url.split("?", 1)[0]
            parts = path_part.split("/")
            pid, sub = parts[3], "/".join(parts[4:])
            base = get_project_dir(pid)
            full = os.path.normpath(os.path.join(base, sub))
            if os.path.commonpath([base, full]) == base and os.path.isfile(full):
                with open(full, "rb") as f:
                    raw = f.read()
        elif url.startswith("/view?"):
            import urllib.parse as _up
            import folder_paths
            q = dict(_up.parse_qsl(url.split("?", 1)[1]))
            base = {"input": folder_paths.get_input_directory(), "temp": folder_paths.get_temp_directory()}.get(
                q.get("type", "output"), folder_paths.get_output_directory())
            full = os.path.normpath(os.path.join(base, q.get("subfolder", ""), q.get("filename", "")))
            if os.path.commonpath([base, full]) == os.path.normpath(base) and os.path.isfile(full):
                with open(full, "rb") as f:
                    raw = f.read()
    except Exception as e:
        LOGGER.debug(f"[Agent] could not load reference image {url[:80]}: {e}")
        return ""
    if not raw:
        return ""
    try:
        import base64 as _b64
        import cv2
        import numpy as _np
        img = cv2.imdecode(_np.frombuffer(raw, _np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            return ""
        if img.ndim == 3 and img.shape[2] == 4:  # flatten transparency (masks) onto black
            alpha = img[..., 3:4].astype(_np.float32) / 255.0
            img = (img[..., :3].astype(_np.float32) * alpha).astype(_np.uint8)
        h, w = img.shape[:2]
        scale = min(1.0, max_side / float(max(h, w)))
        if scale < 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        return _b64.b64encode(buf.tobytes()).decode("ascii") if ok else ""
    except Exception:
        return ""


_LAYER_TYPE_RU = {"paint": "рисунок/пометки", "mask": "маска объекта", "depth": "карта глубины", "canny": "контуры Canny", "pose": "скелет позы"}


def _reference_family_lines(ref: dict) -> list:
    """Text lines describing the parent/children relations of an attached reference."""
    if not isinstance(ref, dict):
        return []
    lines = []
    parent_id = str(ref.get("parentId") or "").strip()
    url = str(ref.get("url") or "")
    if parent_id:
        manifest = _layers_manifest_for_image(parent_id, _project_hint_from_url(url))
        parent_title = ref.get("parentTitle") or parent_id
        kind = ref.get("layerType") or ("notes" if str(ref.get("type")) == "text" else "")
        kind_ru = _LAYER_TYPE_RU.get(kind, "заметки/описание" if kind == "notes" else kind)
        lines.append(f"  - Это вложение ({kind_ru}, «{ref.get('layerName') or ref.get('title')}») изображения-родителя «{parent_title}» (ID: {parent_id})")
        if ref.get("notes"):
            lines.append(f"  - Описание родителя: {ref.get('notes')}")
        if manifest.get("layers"):
            lines.append("  - Все вложения родителя (снизу вверх): " + "; ".join(
                f"«{l.get('name')}» ({_LAYER_TYPE_RU.get(l.get('type'), l.get('type'))}{'' if l.get('visible', True) else ', скрыт'})"
                for l in manifest["layers"] if l.get("file")))
    else:
        image_id = str(ref.get("id") or "")
        children = ref.get("children") or []
        if not children:
            manifest = _layers_manifest_for_image(image_id, _project_hint_from_url(url))
            children = [{"name": l.get("name"), "type": l.get("type"), "visible": l.get("visible", True)}
                        for l in manifest.get("layers", []) if l.get("file")]
        if children:
            lines.append("  - Вложения этого изображения (слои, снизу вверх): " + "; ".join(
                f"«{c.get('name')}» ({_LAYER_TYPE_RU.get(c.get('type'), c.get('type'))}{'' if c.get('visible', True) else ', скрыт'})"
                for c in children))
            lines.append("  - Видимые маски — это области, к которым относится запрос пользователя; имя маски = имя объекта.")
    return lines


_GO_WORDS = ("запускай", "запусти", "запуск", "давай", "поехали", "го", "да", "ок", "окей", "делай", "сделай",
             "генерируй", "сгенерируй", "погнали", "вперед", "вперёд", "жми", "рендер", "отрендери",
             "go", "run", "yes", "ok", "do it", "start", "render", "generate")


def _is_go_command(msg: str) -> bool:
    """A bare confirmation ("запускай", "давай", "go") carries no scene of its own."""
    t = re.sub(r"[^\w\s]", " ", (msg or "").lower()).split()
    if not t or len(t) > 4:
        return False
    return all(w in _GO_WORDS or w in ("уже", "его", "это", "плиз", "пожалуйста", "please", "now", "сейчас", "ну") for w in t)


def _latin_ratio(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    return (sum(1 for c in letters if "a" <= c.lower() <= "z") / len(letters)) if letters else 0.0


def _extract_prompt_from_text(text: str) -> str:
    """Finds an English image prompt inside an agent message: **FLUX:** line, a > quote, a code block,
    or the longest mostly-Latin paragraph."""
    if not text:
        return ""
    m = re.search(r"\*\*FLUX:\*\*\s*(.+?)(?=\n\s*\*\*|\Z)", text, re.S | re.I)
    if m and len(m.group(1).strip()) > 20:
        return m.group(1).strip().strip("`*\"'")
    quotes = [re.sub(r"^\s*>\s?", "", l).strip().strip("*_\"'") for l in text.splitlines() if l.strip().startswith(">")]
    q = " ".join(x for x in quotes if x)
    if len(q) > 40 and _latin_ratio(q) > 0.8:
        return q
    for block in re.findall(r"```(?:\w+)?\n?(.*?)```", text, re.S):
        b = block.strip()
        if len(b) > 40 and _latin_ratio(b) > 0.8 and not b.lstrip().startswith(("{", "[")):
            return b
    paras = [p.strip().strip("*_\"'`") for p in re.split(r"\n\s*\n", text)]
    paras = [p for p in paras if len(p.split()) >= 12 and _latin_ratio(p) > 0.85]
    return max(paras, key=len) if paras else ""


def _resolve_go_command(user_message: str, history: list):
    """For a bare "запускай": the last real request of the user and the prompt the agent proposed for it."""
    request, proposed = "", ""
    for h in reversed(history or []):
        role, txt = h.get("role"), str(h.get("text", "")).strip()
        if not txt:
            continue
        if role != "user" and not proposed:
            proposed = _extract_prompt_from_text(txt)
        if role == "user" and not _is_go_command(txt) and txt.strip() != user_message.strip():
            request = txt
            break
    return request, proposed


def generate_director_plan(
    user_message: str,
    history: list = None,
    plate_info: dict = None,
    references: list = None,
    agent_config: dict = None,
    timeline_context: str = ""
) -> dict:
    """
    Processes user message, attached canvas references, and LLM provider settings.
    Returns:
      - response_ru: Full markdown response for the agent chat
      - response_short: 4-line summary for the persistent bottom mini-window
      - flux_prompt: Prompt for FLUX storyboard image generation
      - ltx_prompt: Prompt for LTX-2.5 video generation
      - camera: Camera configuration
      - studio_actions: Automated actions executed by the agent on behalf of the user
    """
    references = references or []
    agent_config = agent_config or {}
    provider = agent_config.get("provider", "ollama")
    model_name = agent_config.get("model_name", "")
    api_key = agent_config.get("api_key", "")
    api_url = agent_config.get("api_url", "")

    # Normalize references to ensure every item is a dictionary with id, title, url
    normalized_references = []
    for idx, ref in enumerate(references or [], start=1):
        if isinstance(ref, str):
            normalized_references.append({
                "id": f"ref_{idx}",
                "title": ref,
                "name": ref,
                "type": "image",
                "path": ref,
                "url": ref if (ref.startswith("http") or ref.startswith("/")) else f"/view?filename={ref}&type=output",
                "comment": "",
                "description": f"Референсный файл: {ref}"
            })
        elif isinstance(ref, dict):
            normalized_references.append(ref)
    references = normalized_references

    # "запускай" / "давай" right after the agent proposed something: run THAT, not the word "запускай"
    go_proposed_prompt = ""
    if _is_go_command(user_message):
        prev_request, go_proposed_prompt = _resolve_go_command(user_message, history)
        if prev_request:
            LOGGER.info(f"[Director] go-command «{user_message}» → previous request «{prev_request[:80]}»")
            user_message = prev_request if not _is_go_command(prev_request) else user_message
    msg_lower = user_message.lower()

    # Manual model override hotwords in user text (@heavy, @qwen3.8, @micro, @qwen3.5)
    import re
    forced_model = ""
    if any(tag in msg_lower for tag in ["@heavy", "@qwen3.8", "@qwen38", "/heavy", "/think", "/deep", "@тяжелая"]):
        forced_model = "qwen38-gpu:latest"
        user_message = re.sub(r"(@heavy|@qwen3\.8|@qwen38|/heavy|/think|/deep|@тяжелая)\s*", "", user_message, flags=re.IGNORECASE).strip()
    elif any(tag in msg_lower for tag in ["@micro", "@qwen3.5", "@qwen35", "/micro", "/fast", "@легкая"]):
        forced_model = "qwen3.5:0.8b"
        user_message = re.sub(r"(@micro|@qwen3\.5|@qwen35|/micro|/fast|@легкая)\s*", "", user_message, flags=re.IGNORECASE).strip()

    if forced_model:
        model_name = forced_model

    # Determine resolved model tier & badge for UI transparency
    is_heavy = (
        ("38" in str(model_name).lower()) or
        ("3.8" in str(model_name).lower()) or
        ("27b" in str(model_name).lower()) or
        ("heavy" in str(model_name).lower()) or
        (not model_name and requires_deep_reasoning(user_message))
    )
    resolved_model_tag = "qwen38-gpu:latest" if is_heavy else "qwen3.5:0.8b"
    model_badge = "🧠 Qwen 3.8 (27B Heavy Reasoner)" if is_heavy else "⚡ Qwen 3.5 (0.8B Micro-Dispatcher)"
    model_short_badge = "🧠 QWEN 3.8 27B" if is_heavy else "⚡ QWEN 3.5 0.8B"
    model_tier = "heavy" if is_heavy else "micro"
    if not model_name:
        model_name = resolved_model_tag

    # 0. Auto-resolve @tag mentions from user text if not already explicitly attached
    import re
    mentioned_tags = re.findall(r"@([A-Za-z0-9_\-().]+)", user_message)
    if mentioned_tags:
        existing_keys = {str(r.get("title", "")).lower() for r in references} | {str(r.get("id", "")).lower() for r in references}
        base_suite = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        projects_dir = os.path.join(base_suite, "projects")
        if os.path.exists(projects_dir):
            for root, dirs, files in os.walk(projects_dir):
                for f in files:
                    f_lower = f.lower()
                    for tag in mentioned_tags:
                        tag_core = tag.lower().rstrip("_)").strip("@_ ")
                        if tag_core and tag_core in f_lower:
                            tag_clean = tag.strip("@_ ")
                            if tag_clean.lower() not in existing_keys and f_lower not in existing_keys:
                                full_p = os.path.join(root, f)
                                references.append({
                                    "id": f"ref_auto_{len(references)+1}",
                                    "title": tag,
                                    "type": "image",
                                    "path": full_p,
                                    "url": f"/vfx/project_file/sandbox_playground/storyboard/{f}",
                                    "description": f"Кадр из проекта: {f}"
                                })
                                existing_keys.add(tag_clean.lower())
                                existing_keys.add(f_lower)

    # 1. Inspect attached canvas references (dragged from Miro Storyboard, 1D/2D views, or uploaded files)
    ref_context_lines = []
    ordered_ref_summaries = []
    base64_images_for_llm = []

    for idx, ref in enumerate(references, start=1):
        if isinstance(ref, str):
            r_id = f"ref_{idx}"
            r_type = "image"
            r_title = ref
            desc_text = f"Референсный файл: {ref}"
            prompt_text = ""
        else:
            r_id = ref.get("id", f"ref_{idx}")
            r_type = str(ref.get("type", "image")).lower()
            r_title = ref.get("title") or ref.get("name") or f"Кадр #{idx}"
            
            # Comprehensive extraction of descriptions, notes, and user comments
            notes_parts = []
            for key in ["comment", "description", "story_note", "notes", "transcription", "content", "text", "caption"]:
                val = str(ref.get(key, "")).strip()
                if val and val not in notes_parts:
                    notes_parts.append(val)
            
            desc_text = " | ".join(notes_parts) if notes_parts else "Визуальный референс без дополнительного текстового описания"
            prompt_text = str(ref.get("flux_prompt") or ref.get("prompt") or "").strip()
            url_text = str(ref.get("url") or ref.get("path") or "").strip()
            layer_url = str(ref.get("layerDataUrl") or "").strip()
        if isinstance(ref, str):
            url_text = ref
            layer_url = ""

        # Images for the multimodal LLM: the reference itself, its parent (for attached layers) and legacy overlay
        parent_url = str(ref.get("parentUrl") or "") if isinstance(ref, dict) else ""
        for u in [parent_url, url_text, layer_url]:
            if len(base64_images_for_llm) >= 4:
                break
            b64 = _image_url_to_b64(u)
            if b64 and len(b64) > 100 and b64 not in base64_images_for_llm:
                base64_images_for_llm.append(b64)

        ref_entry = f"• [Позиция добавления #{idx}] Тип: {r_type.upper()} | Название: «{r_title}» (ID: {r_id})\n  - Описание и детали: {desc_text}"
        if prompt_text:
            ref_entry += f"\n  - Исходный промпт/стиль: {prompt_text}"
        if layer_url:
            ref_entry += f"\n  - Нарисованы пользовательские маски/аннотации поверх кадра"
        family_lines = _reference_family_lines(ref) if isinstance(ref, dict) else []
        if family_lines:
            ref_entry += "\n" + "\n".join(family_lines)
        
        ref_context_lines.append(ref_entry)
        ordered_ref_summaries.append(f"Ref #{idx} ({r_title}: {desc_text})")

    if ref_context_lines:
        ref_summary = (
            "=== ПРИКРЕПЛЕННЫЕ РЕФЕРЕНСЫ ПОЛЬЗОВАТЕЛЯ (СТРОГИЙ ПОРЯДОК ДОБАВЛЕНИЯ #1 -> #2 -> ...) ===\n"
            + "\n\n".join(ref_context_lines)
            + "\n=== ВАЖНО: Референсы выше имеют НАИВЫСШИЙ ПРИОРИТЕТ. Обязательно используй их описания и последовательность! ==="
        )
    else:
        ref_summary = ""

    # 2. Determine camera & optical setup
    cam_move = "dolly_in"
    focal = 35.0
    use_markers = True

    if any(w in msg_lower for w in ["отъезд", "назад", "отдаление", "dolly out", "backward"]):
        cam_move = "dolly_out"
    elif any(w in msg_lower for w in ["панорама", "поворот", "pan", "rotate"]):
        cam_move = "pan_tilt"
    elif any(w in msg_lower for w in ["облет", "вокруг", "orbit", "circle", "от первого к последнему"]):
        cam_move = "orbit"
    elif any(w in msg_lower for w in ["статич", "на штативе", "static", "tripod"]):
        cam_move = "static"

    if any(w in msg_lower for w in ["ширик", "широкий", "24mm", "28mm", "общий план", "wide"]):
        focal = 28.0
    elif any(w in msg_lower for w in ["портрет", "крупный", "85mm", "лицо", "close"]):
        focal = 85.0
    elif any(w in msg_lower for w in ["50mm", "средний"]):
        focal = 50.0

    # 3. Detect intent: Conversational Q&A vs Active Generation / Workflow Trigger
    intent = detect_user_intent(user_message)
    if go_proposed_prompt:
        intent["is_generate"] = True
    is_generate_request = intent["is_generate"]
    is_video_request = intent["is_video"]

    # ---------------------------------------------------------
    # BRANCH A: CONVERSATIONAL Q&A / BRAINSTORMING / DISCUSSION
    # ---------------------------------------------------------
    if not is_generate_request:
        query_parts = []
        if ref_summary:
            query_parts.append(ref_summary)
        if timeline_context:
            query_parts.append(f"Контекст текущей таймлинии сцены:\n{timeline_context}")
        query_parts.append(user_message)
        chat_query = "\n\n".join(query_parts)

        llm_reply = _call_external_llm(
            provider, model_name, api_key, api_url,
            DIRECTOR_CHAT_SYSTEM_PROMPT, chat_query,
            images=base64_images_for_llm, history=history, max_tokens=450
        )

        import re
        if llm_reply:
            cleaned_reply = re.sub(r"\*\*FLUX:\*\*.*?(?=\*\*LTX:\*\*|\Z)", "", llm_reply, flags=re.DOTALL)
            cleaned_reply = re.sub(r"\*\*LTX:\*\*.*", "", cleaned_reply, flags=re.DOTALL).strip()
            response_ru = cleaned_reply or llm_reply
        else:
            response_ru = (
                "Слушаю тебя внимательно! О чем именно ты хотел спросить или поговорить — "
                "по сюжету, постановке кадра, выбору оптики, световой схеме или настройкам воркфлоу в ComfyUI?"
            )

        first_clean_line = ""
        for line in response_ru.splitlines():
            line_s = line.strip().lstrip("#-* >")
            if line_s:
                first_clean_line = line_s
                break
        first_snip = (first_clean_line[:85] + "...") if len(first_clean_line) > 85 else (first_clean_line or "Диалог с режиссером")
        response_short = f"💬 [{model_short_badge}] {first_snip}"

        studio_actions = []
        if any(w in msg_lower for w in ["покажи нод", "покажи воркфлоу", "открой комфи", "визуализируй", "покажи граф", "схему нод", "открыть граф"]):
            studio_actions.append({
                "type": "visualize_workflow",
                "process_id": "video_gen" if is_video_request else "storyboard",
                "workflow_name": "FLUX.3_Klein_Text2Image.json"
            })

        # Always evict heavy LLM from VRAM after answering conversationally, keeping micro-dispatcher warm
        try:
            unload_agent_from_vram(api_url, preserve_micro=True)
        except Exception as ex:
            LOGGER.debug(f"[VRAM Rule] Post-chat unload skipped: {ex}")

        return {
            "response_ru": response_ru,
            "response_short": response_short,
            "provider": provider,
            "model_name": resolved_model_tag,
            "model_used": resolved_model_tag,
            "model_badge": model_badge,
            "model_tier": model_tier,
            "flux_prompt": "",
            "prompt": "",
            "ltx_prompt": "",
            "intent": "chat",
            "references_used": [r.get("id") for r in references],
            "studio_actions": studio_actions,
            "camera": {
                "movement": cam_move,
                "focal_mm": focal,
                "add_markers_grid": use_markers
            },
            "keying": {
                "mode": "corridor_key_and_ltx_alpha",
                "despill": 1.0
            }
        }

    # ---------------------------------------------------------
    # BRANCH B: ACTIVE SCENE GENERATION / WORKFLOW EXECUTION
    # ---------------------------------------------------------
    style = "cinematic photorealistic film, ARRI Alexa 65"
    lighting = "motivated atmospheric lighting with rich volumetric contrast"
    atmosphere = "subtle depth haze, realistic lens characteristics"
    audio_ambience = "natural environmental acoustics"

    art_concept_en = ""
    is_vector_style = any(w in msg_lower for w in ["вектор", "vector", "svg", "плоский", "иллюстрац", "логотип", "минимализм"])
    is_anime_style = any(w in msg_lower for w in ["аниме", "anime", "манга", "manga", "синхай", "макото"])
    is_animal_subject = any(w in msg_lower for w in ["орел", "орла", "птиц", "сокол", "волк", "лев", "тигр", "дракон", "кот", "собак"])

    if is_vector_style:
        style = "clean modern vector art illustration, minimalist graphic design, SVG aesthetic"
        lighting = "vibrant clean flat colors with elegant subtle gradients and crisp edge contrast"
        atmosphere = "sharp geometric contours, pristine vector lines, bold visual silhouette"
        audio_ambience = "clean airy ambient chimes"
        if any(w in msg_lower for w in ["орел", "орла"]):
            art_concept_en = "Majestic soaring eagle with outstretched wings gliding through the sky, modern flat vector art, sharp geometric feathers, bold aerodynamic silhouette, clean vector illustration, behance dribbble trending"
        elif any(w in msg_lower for w in ["волк", "волка"]):
            art_concept_en = "Stylized howling wolf silhouette against a full moon, clean vector art, minimalist geometric shapes, flat vector illustration"
        elif any(w in msg_lower for w in ["лев", "льва"]):
            art_concept_en = "Majestic lion with geometric mane, modern vector illustration, bold graphic lines, flat vector logo aesthetic"
        else:
            art_concept_en = f"Graphic vector art illustration of {user_message}, sharp clean vector contours, flat colors, minimalist art"

    elif is_anime_style:
        style = "stunning modern anime key visual, Makoto Shinkai aesthetic, high production quality"
        lighting = "radiant volumetric god rays, rich vibrant color palette, luminous celestial highlights"
        atmosphere = "scintillating atmospheric particles, vivid dynamic sky, crisp cel-shaded details"
        audio_ambience = "emotional orchestral piano swell with wind chime echoes"
        art_concept_en = f"Masterpiece anime illustration of {user_message}, crisp linework, expressive aesthetics, trending on Pixiv"

    elif any(k in msg_lower for k in ["клеопатр", "бекер", "бакст", "дягилев", "балет"]):
        style = "theatrical art nouveau cinema, inspired by Léon Bakst's iconic Ballets Russes Diaghilev aesthetic"
        lighting = "dramatic stage chiaroscuro, golden rim light, deep jewel tones of lapis lazuli and turquoise"
        atmosphere = "theatrical atmospheric haze, gilded reflections, museum sculpture texture"
        audio_ambience = "orchestral swell with ancient harp and mystical resonance"
        art_concept_en = (
            "Ornate sculptural statue of Cleopatra, inspired by Léon Bakst's iconic art nouveau theatrical poster "
            "for Sergei Diaghilev's Ballets Russes, intricate turquoise, lapis lazuli and gold leaf Egyptian ornaments, "
            "dramatic theatrical chiaroscuro stage lighting, jewel-toned deep shadows. "
            "Camera transitions smoothly from an extreme close-up frontal portrait, orbiting into a three-quarter angle, "
            "and pulling back to a dramatic wide profile view of the statue"
        )
    elif any(w in msg_lower for w in ["киберпанк", "неон", "cyberpunk", "neon", "дождь"]):
        style = "hyper-detailed neo-noir cyberpunk cinema, anamorphic Panavision optics"
        lighting = "wet neon reflections in cyan and magenta, moody rim light on rain-slicked surfaces"
        atmosphere = "falling nocturnal rain, rising street steam, horizontal anamorphic flares"
        audio_ambience = "rain pattering on puddles, distant synth drone, neon transformer hum"
    elif any(w in msg_lower for w in ["космос", "sci-fi", "станция", "корабль", "марс"]):
        style = "hard sci-fi cinematic realism, crisp 65mm IMAX optics"
        lighting = "directional Rim light from starlight, subtle HUD illumination on visor"
        atmosphere = "micro-particulate cabin air, high-contrast orbital shadows"
        audio_ambience = "deep hull resonance, quiet telemetry beeps, pressurized suit breathing"
    elif any(w in msg_lower for w in ["фэнтези", "замок", "рыцарь", "магия", "лес"]):
        style = "epic dark fantasy cinematography, 35mm Kodak Vision3 film grain"
        lighting = "warm flickering torchlight against cold twilight mist, dramatic god rays"
        atmosphere = "drifting embers, dense ancient forest fog"
        audio_ambience = "crackling fire, distant wind through stone arches"

    is_vehicle = any(w in msg_lower for w in ["бетмен", "бэтмен", "автомобил", "машин", "тачк", "batmobile", "car", "vehicle", "мотоцикл", "корабль", "самолет", "танк"])
    is_winter = any(w in msg_lower for w in ["зим", "снег", "мороз", "сугроб", "winter", "snow", "blizzard", "метел"])
    is_landscape = any(w in msg_lower for w in ["город", "пейзаж", "улиц", "здани", "архитектур", "природ", "лес", "горы", "море", "океан"])
    is_human = any(w in msg_lower for w in ["девушк", "женщин", "мужчин", "парен", "человек", "портрет", "лицо", "клеопатр", "рыцарь", "воин", "актер"])

    if not art_concept_en:
        if (is_vehicle and ("бетмен" in msg_lower or "бэтмен" in msg_lower or "batmobile" in msg_lower)):
            art_concept_en = (
                "Cinematic wide shot of the Batmobile parked in a snow-buried Russian town during a brutal winter storm, "
                "thick layers of fresh snow blanketing the aggressive angular bodywork and sharp bat-wing fenders, "
                "frosted glass windshield, headlights cutting through swirling snowflakes and dense fog, "
                "distant Soviet-era apartment buildings in background, breath of cold air around exhaust vents, "
                "dramatic low-key lighting, hyper-detailed, 8K resolution, photorealistic"
            )
        elif is_vehicle:
            art_concept_en = f"Cinematic detailed vehicle shot of {user_message}, metallic finish, reflections, dramatic composition, 8k"
        elif is_winter and not is_human:
            art_concept_en = f"Atmospheric cinematic winter scene, heavy snowfall, volumetric mist, cold blue hour lighting, 8k"
        elif re.search(r"[\u0400-\u04FF]", user_message):
            try:
                fast_trans = _call_external_llm(
                    provider, model_name, api_key, api_url,
                    "You are a translation assistant. Translate the user scene prompt into a detailed cinematic English text-to-image prompt. Output ONLY the English prompt without any prefix or quotes.",
                    user_message
                )
                if fast_trans and len(fast_trans.strip()) > 10 and not re.search(r"[\u0400-\u04FF]", fast_trans):
                    art_concept_en = fast_trans.strip().strip('"\'`')
            except Exception:
                pass

    objective_text = art_concept_en if art_concept_en else user_message
    if ordered_ref_summaries:
        seq_chain = " ➔ ".join(ordered_ref_summaries)
        subject_desc = f"Visual reference progression: [{seq_chain}]. Director objective: {objective_text}"
    else:
        subject_desc = objective_text

    if is_vector_style:
        flux_prompt = (
            f"Vector art graphic design masterpiece, {style}. {lighting}. {atmosphere}. "
            f"{subject_desc}. Clean sharp lines, SVG illustration, flat colors, professional vector artwork, 8k resolution."
        )
    elif is_anime_style:
        flux_prompt = (
            f"Masterpiece anime illustration, {style}. {lighting}. {atmosphere}. "
            f"Scene concept: {subject_desc}. High detail cel-shading, vibrant colors, 8k resolution."
        )
    else:
        if is_vehicle:
            subject_texture = "heavy matte armor, frosted metallic reflections, snow covered chassis and tires, authentic mechanical details"
        elif is_animal_subject:
            subject_texture = "authentic fur, feather and natural environmental textures"
        elif is_human:
            subject_texture = "photorealistic skin, natural facial features, authentic fabric textures"
        elif is_landscape or is_winter:
            subject_texture = "weathered architectural surfaces, snow drifts, atmospheric volumetric mist"
        else:
            subject_texture = "authentic physical surface materials, high micro-detail resolution"
        flux_prompt = (
            f"Masterpiece cinematic still frame, {style}. {lighting}. {atmosphere}. "
            f"Shot on {focal:.0f}mm lens. Scene concept: {subject_desc}. "
            f"{subject_texture}, dramatic composition, 8k resolution."
        )

    if go_proposed_prompt:
        flux_prompt = go_proposed_prompt

    ltx_prompt = (
        f"Style: {style}. {lighting}. {atmosphere}. "
        f"The camera performs a smooth continuous {cam_move.replace('_', ' ')} tracking from start frame to end frame with a {focal:.0f}mm lens. "
        f"{subject_desc}. Movements are physically grounded with authentic motion blur. "
        f"Audio: {audio_ambience}."
    )
    if timeline_context:
        ltx_prompt += f" Temporal sequence: {timeline_context}."

    # Autopilot Studio Actions
    selected_wf = intent.get("custom_workflow")
    if not selected_wf:
        if is_video_request:
            if len(references) >= 2:
                selected_wf = "LTX-2.5/video_ltx2_5_flf2v.json"
            elif len(references) == 1:
                selected_wf = "LTX-2.5/video_ltx2_5_i2v.json"
            else:
                selected_wf = "LTX-2.5/video_ltx2_5_t2v.json"
        else:
            selected_wf = "FLUX.3_Klein_Text2Image.json"

    studio_actions = [
        {
            "type": "create_storyboard_card",
            "title": f"🎬 Видео LTX ({focal:.0f}mm {cam_move})" if is_video_request else f"Кадр ({focal:.0f}mm {cam_move})",
            "story_note": user_message,
            "mode": "video" if is_video_request else "image",
            "flux_prompt": flux_prompt,
            "ltx_prompt": ltx_prompt,
            "link_from": (references[0].get("id", "ref_1") if isinstance(references[0], dict) else str(references[0])) if references else None
        },
        {
            "type": "trigger_generation",
            "mode": "video" if is_video_request else "image",
            "workflow_type": "ltx_video" if is_video_request else "flux_image",
            "title": f"Воркфлоу: {os.path.basename(selected_wf)}",
            "prompt": ltx_prompt if is_video_request else flux_prompt,
            "custom_workflow": selected_wf
        }
    ]

    if any(w in msg_lower for w in ["озвуч", "голос", "скажет", "реплик", "tts", "перевед"]):
        studio_actions.append({
            "type": "prepare_tts",
            "text": user_message,
            "voice": "dmitry"
        })

    if any(w in msg_lower for w in ["разбей", "маск", "деконструк", "пас", "трекинг", "альфа", "коридор"]):
        studio_actions.append({
            "type": "configure_deconstruction",
            "passes": {
                "tracking_glomap": True,
                "mask_sam2_rough": True,
                "mask_corridor_key": True,
                "mask_ltx_alpha_lora": True,
                "pass_depth": True,
                "pass_canny": True,
                "pass_pose": True
            }
        })

    if any(w in msg_lower for w in ["музык", "саундтрек", "эмбиент", "ace", "minimax"]):
        studio_actions.append({
            "type": "generate_music",
            "engine": "ace_step",
            "prompt": f"Cinematic soundtrack: {style}, {audio_ambience}"
        })

    if any(w in msg_lower for w in ["покажи нод", "покажи воркфлоу", "открой комфи", "визуализируй", "параметр", "покажи граф", "схему нод", "открыть граф"]):
        studio_actions.append({
            "type": "visualize_workflow",
            "process_id": "video_gen" if is_video_request else "storyboard",
            "workflow_name": "FLUX.3_Klein_Text2Image.json"
        })

    # Local or Cloud LLM Director Synthesis for Generation
    llm_reply = ""
    query_parts = []
    wf_context = get_available_workflows_context()
    if wf_context:
        query_parts.append(f"Локальная база рабочих процессов студии (Уровень 1):\n{wf_context}")
    if ref_summary:
        query_parts.append(f"{ref_summary}")
    if timeline_context:
        query_parts.append(f"Хронологический контекст таймлинии (Скрипт сцены):\n{timeline_context}")
    query_parts.append(f"Запрос пользователя на генерацию: {user_message}")
    full_query = "\n\n".join(query_parts)
    llm_reply = _call_external_llm(
        provider, model_name, api_key, api_url,
        DIRECTOR_GENERATION_SYSTEM_PROMPT, full_query,
        images=base64_images_for_llm, history=history, max_tokens=1000
    )

    if references:
        ref_titles = [f"#{i} «{r.get('title') or r.get('id')}»" for i, r in enumerate(references, start=1)]
        ref_badge_txt = f"📎 **Учтены референсы по порядку добавления ({len(references)} шт.):** " + ", ".join(ref_titles) + "\n"
    else:
        ref_badge_txt = ""
    tl_badge_txt = "⏱ **Хронология сцены:** учтены ключевые кадры и объекты таймлинии.\n" if timeline_context else ""
    action_labels = {
        "trigger_generation": "🚀 запуск рендера в ComfyUI",
        "create_storyboard_card": "📋 шот добавлен на холст",
        "prepare_tts": "🎙 озвучка TTS",
        "configure_deconstruction": "🧩 пассы деконструкции",
        "generate_music": "🎵 саундтрек",
        "visualize_workflow": "🎨 открытие нодового графа в ComfyUI"
    }
    actions_txt = ", ".join([action_labels.get(a["type"], a["type"]) for a in studio_actions]) if studio_actions else "генерация сцены"

    if llm_reply:
        import re
        flux_match = re.search(r"\*\*FLUX:\*\*\s*([^\n\r]+(?:\n[^\n\r]+)*?)(?=\n\s*\*\*|\Z)", llm_reply, re.IGNORECASE)
        if not flux_match:
            flux_match = re.search(r"FLUX[:\s*]+([^\n\r]+(?:\n[^\n\r]+)*?)(?=\n\s*(?:LTX|\*\*|\Z))", llm_reply, re.IGNORECASE)
        if flux_match:
            cand = flux_match.group(1).strip().strip("`*\"'")
            if len(cand) > 15:
                flux_prompt = cand
        else:
            cand = _extract_prompt_from_text(llm_reply)
            if cand:
                flux_prompt = cand

        ltx_match = re.search(r"\*\*LTX:\*\*\s*([^\n\r]+(?:\n[^\n\r]+)*?)(?=\n\s*\*\*|\Z)", llm_reply, re.IGNORECASE)
        if not ltx_match:
            ltx_match = re.search(r"LTX(?:-2\.5)?[:\s*]+([^\n\r]+(?:\n[^\n\r]+)*?)(?=\n\s*(?:FLUX|\*\*|\Z))", llm_reply, re.IGNORECASE)
        if ltx_match:
            cand = ltx_match.group(1).strip().strip("`*\"'")
            if len(cand) > 15:
                ltx_prompt = cand

        for act in studio_actions:
            if act.get("type") == "create_storyboard_card":
                act["flux_prompt"] = flux_prompt
                act["ltx_prompt"] = ltx_prompt
                act["mode"] = "video" if is_video_request else "image"
            elif act.get("type") == "trigger_generation":
                act["mode"] = "video" if is_video_request else "image"
                act["workflow_type"] = "ltx_video" if is_video_request else "flux_image"
                act["prompt"] = ltx_prompt if is_video_request else flux_prompt
                act["custom_workflow"] = selected_wf

        response_ru = f"{ref_badge_txt}{tl_badge_txt}{llm_reply}\n\n⚡ **Автопилот:** `{actions_txt}`"

        first_clean_line = ""
        for line in llm_reply.splitlines():
            line_s = line.strip().lstrip("#-* >")
            if line_s and not line_s.lower().startswith(("flux", "ltx", "```")):
                first_clean_line = line_s
                break
        first_snippet = (first_clean_line[:75] + "...") if len(first_clean_line) > 75 else (first_clean_line or "Режиссерский план сформирован.")
        response_short = (
            f"🎬 [{model_short_badge}] {first_snippet}\n"
            f"🎯 Оптика: {focal:.0f}мм ({cam_move})\n"
            f"✨ Промпты FLUX и LTX-2.5 сгенерированы моделью {model_badge}.\n"
            f"⚡ Выполнено: {actions_txt}"
        )
    else:
        response_ru = (
            f"{ref_badge_txt}{tl_badge_txt}"
            f"🎬 **Кадр подготовлен к съемке!** ({model_short_badge})\n"
            f"• **Сцена:** {user_message}\n"
            f"• **FLUX (Раскадровка):** оптика {focal:.0f}мм, {style}.\n"
            f"• **LTX-2.5 (Видео):** движение камеры `{cam_move}`, свет — {lighting}.\n"
            f"• **Авто-действия студии:** `{actions_txt}`.\n\n"
            f"**FLUX:** {flux_prompt}\n"
            f"**LTX:** {ltx_prompt}"
        )
        response_short = (
            f"🤖 [{model_short_badge}] {ref_badge_txt.strip()}\n"
            f"🎯 Оптика: {focal:.0f}мм ({cam_move}) | Свет: {style[:38]}...\n"
            f"✨ Промпты FLUX и LTX-2.5 синхронизированы со сценой.\n"
            f"⚡ Выполнено: {actions_txt}"
        )

    # VRAM Rule: When generation prompts/actions are prepared, immediately evict heavy local LLM from VRAM
    # while preserving resident micro-dispatcher (qwen3.5:0.8b ~550MB) so ComfyUI has 23.8 GB available!
    try:
        unload_agent_from_vram(api_url, preserve_micro=True)
    except Exception as ex:
        LOGGER.debug(f"[VRAM Rule] Post-plan unload skipped: {ex}")

    return {
        "response_ru": response_ru,
        "response_short": response_short,
        "provider": provider,
        "model_name": resolved_model_tag,
        "model_used": resolved_model_tag,
        "model_badge": model_badge,
        "model_tier": model_tier,
        "is_video": is_video_request,
        "target_workflow": selected_wf,
        "flux_prompt": flux_prompt,
        "prompt": flux_prompt,
        "ltx_prompt": ltx_prompt,
        "intent": "generation",
        "custom_workflow": selected_wf,
        "references_used": [r.get("id") for r in references],
        "studio_actions": studio_actions,
        "camera": {
            "movement": cam_move,
            "focal_mm": focal,
            "add_markers_grid": use_markers
        },
        "keying": {
            "mode": "corridor_key_and_ltx_alpha",
            "despill": 1.0
        }
    }
