r"""
Pipeline Processes, Model Catalog, In-Place Custom Model Folders, Cloud API & Custom Workflow Manager
=====================================================================================================
Provides per-process configuration for KESHIKI VFX AI Studio:
1. Per-process Model Catalog with official direct download URLs (HuggingFace / GitHub) and status check.
2. In-place Custom Model Directory (`custom_model_dir`) mounting without copying or moving files.
   Automatically registers paths with ComfyUI's `folder_paths` when available.
3. Background model downloader with live progress tracking (`_DOWNLOAD_JOBS`).
4. Per-process Paid Cloud API configuration (`execution_mode`: 'local' | 'cloud' | 'custom_workflow',
   `cloud_provider`, `cloud_api_url`, `cloud_api_key`, `cloud_model_id`).
5. Per-process Custom ComfyUI Workflow binding (`custom_workflow_name`, `custom_workflow_json`).
"""

import os
import sys
import time
import json
import uuid
import threading
import urllib.request
from typing import Dict, Any, List
try:
    from .local_config import local_setting as _ls, cloud_services_allowed as _cloud_ok
except ImportError:  # pragma: no cover
    from local_config import local_setting as _ls, cloud_services_allowed as _cloud_ok

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "output_cache")
CONFIG_PATH = os.path.join(CACHE_DIR, "pipeline_processes_config.json")
CUSTOM_WORKFLOWS_DIR = os.path.join(CACHE_DIR, "custom_workflows")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(CUSTOM_WORKFLOWS_DIR, exist_ok=True)

# Detect ComfyUI root directory (StabilityMatrix, custom root or folder_paths)
COMFY_ROOT = None
try:
    import folder_paths
    if hasattr(folder_paths, "models_dir") and os.path.exists(folder_paths.models_dir):
        COMFY_ROOT = os.path.dirname(folder_paths.models_dir)
except Exception:
    pass

if not COMFY_ROOT or not os.path.exists(os.path.join(COMFY_ROOT, "models")):
    candidates = [
        os.path.abspath(os.path.join(BASE_DIR, "..", "..")),
        BASE_DIR
    ]
    for cand in candidates:
        if os.path.exists(os.path.join(cand, "models")):
            COMFY_ROOT = cand
            break
    if not COMFY_ROOT:
        COMFY_ROOT = BASE_DIR

MODEL_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".sft")

_DOWNLOAD_JOBS: Dict[str, Dict[str, Any]] = {}
_DOWNLOAD_LOCK = threading.Lock()

# Curated catalog of all studio pipeline processes, their required/optional models, download links, and cloud presets
PROCESS_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "agent",
        "icon": "bot",
        "title_ru": "1. ИИ-Режиссер и Сценарист (AI Director LLM)",
        "title_en": "1. AI Director & Screenwriter (LLM / VLM)",
        "desc_ru": "Генерация режиссерских тритментов, раскадровок, промптов FLUX/LTX и управление студией.",
        "desc_en": "Director treatments, shot breakdowns, FLUX/LTX prompt engineering, and studio automation.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "LLM"),
        "comfy_folder_Category": "LLM",
        "cloud_providers": [
            {"id": "gemini", "name": "Google Gemini API (gemini-2.5-pro / flash)", "default_url": "https://generativelanguage.googleapis.com/v1beta", "default_model": "gemini-2.5-pro"},
            {"id": "openai", "name": "OpenAI API (GPT-4o / o3-mini)", "default_url": "https://api.openai.com/v1/chat/completions", "default_model": "gpt-4o"},
            {"id": "claude", "name": "Anthropic Claude API (Claude 3.7 Sonnet)", "default_url": "https://api.anthropic.com/v1/messages", "default_model": "claude-3-7-sonnet-latest"},
            {"id": "openrouter", "name": "OpenRouter (Любые облачные LLM)", "default_url": "https://openrouter.ai/api/v1/chat/completions", "default_model": "google/gemini-2.5-pro"}
        ],
        "models": [
            {
                "id": "gemma3_12b_it",
                "name": "Gemma 3 / 4 12B Instruct (Q4_K_M / Safetensors)",
                "filename": "gemma-3-12b-it-Q4_K_M.gguf",
                "alt_Matches": ["gemma", "12b"],
                "size_gb": 7.3,
                "required": True,
                "url": "https://huggingface.co/unsloth/gemma-3-12b-it-GGUF/resolve/main/gemma-3-12b-it-Q4_K_M.gguf",
                "page_url": "https://huggingface.co/unsloth/gemma-3-12b-it-GGUF",
                "desc_ru": "Локальная языковая модель режиссера с поддержкой визуального контекста."
            },
            {
                "id": "qwen25_vl_7b",
                "name": "Qwen 2.5-VL 7B Instruct (Анализ видео и кадров)",
                "filename": "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
                "alt_Matches": ["qwen2.5-vl", "qwen"],
                "size_gb": 4.8,
                "required": False,
                "url": "https://huggingface.co/bartowski/Qwen_Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen_Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
                "page_url": "https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct",
                "desc_ru": "Мультимодальная VLM-модель для разбора референсов и раскадровки."
            }
        ]
    },
    {
        "id": "storyboard",
        "icon": "layout-grid",
        "title_ru": "2. Раскадровка и Концепты (1D / FLUX.3 & FLUX.1)",
        "title_en": "2. Storyboard & Concept Art (1D / FLUX.3 & FLUX.1)",
        "desc_ru": "Генерация ключевых кадров раскадровки и концепт-артов через нативный FLUX.3 Klein (int8-convrot) или FLUX.1.",
        "desc_en": "Storyboard keyframe generation, concept art, and 1D image synthesis via native FLUX.3 Klein or FLUX.1.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "diffusion_models"),
        "comfy_folder_Category": "diffusion_models",
        "cloud_providers": [
            {"id": "fal_flux", "name": "Fal.ai — FLUX.1 Pro / Dev", "default_url": "https://queue.fal.run/fal-ai/flux-pro/v1.1", "default_model": "fal-ai/flux-pro/v1.1"},
            {"id": "replicate_flux", "name": "Replicate — black-forest-labs/flux-dev", "default_url": "https://api.replicate.com/v1/models/black-forest-labs/flux-dev/predictions", "default_model": "black-forest-labs/flux-dev"},
            {"id": "runpod", "name": "RunPod Serverless ComfyUI Endpoint", "default_url": "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/run", "default_model": "flux1-dev-fp8"}
        ],
        "models": [
            {
                "id": "flux3_klein_int8",
                "name": "FLUX.3 Klein Int8 Convrot (Нативная модель кадров FLUX.3)",
                "filename": "flux3-image-klein-int8-convrot.safetensors",
                "alt_Matches": ["flux3", "klein", "flux3-image", "flux3_klein"],
                "size_gb": 8.5,
                "required": True,
                "url": "https://huggingface.co/Comfy-Org/flux3-image-klein/resolve/main/flux3-image-klein-int8-convrot.safetensors",
                "page_url": "https://huggingface.co/Comfy-Org/flux3-image-klein",
                "desc_ru": "Сверхбыстрый квантованный DiT FLUX.3 Klein для генерации кадров за 16 секунд."
            },
            {
                "id": "qwen3vl_4b_text_encoder",
                "name": "Qwen3-VL 4B Text Encoder (Текстовый энкодер FLUX.3)",
                "filename": "qwen3vl_4b_int8_convrot.safetensors",
                "alt_Matches": ["qwen3vl", "qwen3_4b", "flux3_text", "qwen3vl_4b"],
                "size_gb": 4.1,
                "required": True,
                "url": "https://huggingface.co/Comfy-Org/flux3-text-encoders/resolve/main/qwen3vl_4b_int8_convrot.safetensors",
                "page_url": "https://huggingface.co/Comfy-Org/flux3-text-encoders",
                "desc_ru": "Мультимодальный текстовый энкодер Qwen3-VL 4B для понимания кинематографических сцен."
            },
            {
                "id": "flux2_vae",
                "name": "FLUX.2 / FLUX.3 VAE (128-ch Latent VAE)",
                "filename": "flux2-vae.safetensors",
                "alt_Matches": ["flux2-vae", "flux2_vae"],
                "size_gb": 0.35,
                "required": True,
                "url": "https://huggingface.co/Comfy-Org/flux2-vae/resolve/main/flux2-vae.safetensors",
                "page_url": "https://huggingface.co/Comfy-Org/flux2-vae",
                "desc_ru": "128-канальный VAE декодер высокого разрешения для FLUX.3."
            },
            {
                "id": "flux1_dev_fp8",
                "name": "FLUX.1-Dev FP8 (Альтернативная модель FLUX.1)",
                "filename": "flux1-dev-fp8.safetensors",
                "alt_Matches": ["flux1-dev", "flux1_dev"],
                "size_gb": 11.9,
                "required": False,
                "url": "https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors",
                "page_url": "https://huggingface.co/Comfy-Org/flux1-dev",
                "desc_ru": "Оптимизированная FP8 версия FLUX.1-dev для 12–24 ГБ VRAM."
            },
            {
                "id": "t5xxl_fp8",
                "name": "T5-XXL FP8 Text Encoder (Текстовый энкодер FLUX.1)",
                "filename": "t5xxl_fp8_e4m3fn.safetensors",
                "alt_Matches": ["t5xxl"],
                "size_gb": 4.9,
                "required": False,
                "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors",
                "page_url": "https://huggingface.co/comfyanonymous/flux_text_encoders",
                "desc_ru": "Текстовый энкодер T5 для понимания сложных сцен и промптов FLUX.1."
            },
            {
                "id": "clip_l",
                "name": "CLIP-L Text Encoder",
                "filename": "clip_l.safetensors",
                "alt_Matches": ["clip_l"],
                "size_gb": 0.24,
                "required": False,
                "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors",
                "page_url": "https://huggingface.co/comfyanonymous/flux_text_encoders",
                "desc_ru": "Второй текстовый энкодер для FLUX.1."
            },
            {
                "id": "flux_ae",
                "name": "FLUX.1 VAE (ae.safetensors)",
                "filename": "ae.safetensors",
                "alt_Matches": ["ae.safetensors", "flux_vae"],
                "size_gb": 0.33,
                "required": False,
                "url": "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors",
                "page_url": "https://huggingface.co/black-forest-labs/FLUX.1-schnell",
                "desc_ru": "Декодер латентного пространства FLUX.1."
            }
        ]
    },
    {
        "id": "character",
        "icon": "user",
        "title_ru": "3. Персонажи и Обучение Character LoRA",
        "title_en": "3. Characters & Identity LoRA Training",
        "desc_ru": "Удержание внешности актеров (PuLID / IP-Adapter) и обучение персональных LoRA в 1 клик.",
        "desc_en": "Character identity preservation (PuLID) and 1-click Character LoRA training.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "loras"),
        "comfy_folder_Category": "loras",
        "cloud_providers": [
            {"id": "fal_lora_train", "name": "Fal.ai — Fast FLUX LoRA Trainer", "default_url": "https://queue.fal.run/fal-ai/flux-lora-fast-training", "default_model": "fal-ai/flux-lora-fast-training"},
            {"id": "replicate_lora", "name": "Replicate — ostris/flux-dev-lora-trainer", "default_url": "https://api.replicate.com/v1/models/ostris/flux-dev-lora-trainer/predictions", "default_model": "ostris/flux-dev-lora-trainer"}
        ],
        "models": [
            {
                "id": "pulid_flux",
                "name": "PuLID-FLUX v0.9.1 (Удержание лица без обучения)",
                "filename": "pulid_flux_v0.9.1.safetensors",
                "alt_Matches": ["pulid"],
                "size_gb": 1.1,
                "required": True,
                "url": "https://huggingface.co/guozinan/PuLID/resolve/main/pulid_flux_v0.9.1.safetensors",
                "page_url": "https://huggingface.co/guozinan/PuLID",
                "desc_ru": "Мгновенный перенос лица персонажа по 1 фото во FLUX."
            },
            {
                "id": "eva_clip",
                "name": "EVA02-CLIP-L-14-336 (Визуальный энкодер лиц для PuLID)",
                "filename": "EVA02_CLIP_L_336_psz14_s6B.pt",
                "alt_Matches": ["eva02", "insightface"],
                "size_gb": 0.85,
                "required": False,
                "url": "https://huggingface.co/QuanSun/EVA-CLIP/resolve/main/EVA02_CLIP_L_336_psz14_s6B.pt",
                "page_url": "https://huggingface.co/QuanSun/EVA-CLIP",
                "desc_ru": "Энкодер черт лица для работы PuLID."
            }
        ]
    },
    {
        "id": "location",
        "icon": "map-pin",
        "title_ru": "4. Локации, Окружение и Location LoRA",
        "title_en": "4. Locations, Environments & World LoRA",
        "desc_ru": "Генерация архитектуры, фонов (Matte Painting), панорам и обучение LoRA локаций.",
        "desc_en": "Environment matte paintings, architectural control, and Location LoRA training.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "loras"),
        "comfy_folder_Category": "loras",
        "cloud_providers": [
            {"id": "fal_flux_fill", "name": "Fal.ai — FLUX.1 Fill / Depth Pro", "default_url": "https://queue.fal.run/fal-ai/flux-pro/v1/fill", "default_model": "fal-ai/flux-pro/v1/fill"},
            {"id": "replicate_env", "name": "Replicate — FLUX Depth / Canny Pro", "default_url": "https://api.replicate.com/v1/predictions", "default_model": "black-forest-labs/flux-depth-pro"}
        ],
        "models": [
            {
                "id": "flux_depth_lora",
                "name": "FLUX.1-Depth-dev-LoRA (Генерация локаций по 3D-глубине)",
                "filename": "flux1-depth-dev-lora.safetensors",
                "alt_Matches": ["flux1-depth", "depth-dev-lora"],
                "size_gb": 1.24,
                "required": True,
                "url": "https://huggingface.co/black-forest-labs/FLUX.1-Depth-dev-lora/resolve/main/flux1-depth-dev-lora.safetensors",
                "page_url": "https://huggingface.co/black-forest-labs/FLUX.1-Depth-dev-lora",
                "desc_ru": "Удерживает 3D-геометрию и перспективу при генерации локаций."
            },
            {
                "id": "flux_canny_lora",
                "name": "FLUX.1-Canny-dev-LoRA (Генерация архитектуры по контурам)",
                "filename": "flux1-canny-dev-lora.safetensors",
                "alt_Matches": ["flux1-canny", "canny-dev-lora"],
                "size_gb": 1.24,
                "required": False,
                "url": "https://huggingface.co/black-forest-labs/FLUX.1-Canny-dev-lora/resolve/main/flux1-canny-dev-lora.safetensors",
                "page_url": "https://huggingface.co/black-forest-labs/FLUX.1-Canny-dev-lora",
                "desc_ru": "Точное следование линиям зданий и чертежам сцены."
            }
        ]
    },
    {
        "id": "tts_stage1",
        "icon": "mic",
        "title_ru": "5. Озвучка, Голос и Липсинг (TTS & Lip-Sync)",
        "title_en": "5. Voice Synthesis, Cloning & Lip-Sync",
        "desc_ru": "Синтез речи, клонирование тембра актеров, синхронный перевод и анимация губ.",
        "desc_en": "Speech synthesis, voice cloning, translation, and facial lip-sync animation.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "tts"),
        "comfy_folder_Category": "tts",
        "cloud_providers": [
            {"id": "elevenlabs", "name": "ElevenLabs API (Multilingual v2 / Voice Clone)", "default_url": "https://api.elevenlabs.io/v1/text-to-speech", "default_model": "eleven_multilingual_v2"},
            {"id": "fal_latentsync", "name": "Fal.ai — LatentSync / MuseTalk Lip-Sync", "default_url": "https://queue.fal.run/fal-ai/latentsync", "default_model": "fal-ai/latentsync"},
            {"id": "openai_tts", "name": "OpenAI Audio Speech API (gpt-4o-mini-tts)", "default_url": "https://api.openai.com/v1/audio/speech", "default_model": "gpt-4o-mini-tts"}
        ],
        "models": [
            {
                "id": "xtts_v2",
                "name": "Coqui XTTS-v2 (Мультиязычное клонирование голоса)",
                "filename": "model.pth",
                "alt_Matches": ["xtts", "cosyvoice", "qwen3_tts"],
                "size_gb": 1.87,
                "required": True,
                "url": "https://huggingface.co/coqui/XTTS-v2/resolve/main/model.pth",
                "page_url": "https://huggingface.co/coqui/XTTS-v2",
                "desc_ru": "Локальное клонирование любого голоса по 5-секундному сэмплу (RU/EN)."
            },
            {
                "id": "latentsync_unet",
                "name": "ByteDance LatentSync v1.5 (Кинематографичный Липсинг)",
                "filename": "latentsync_unet.pt",
                "alt_Matches": ["latentsync", "musetalk"],
                "size_gb": 2.4,
                "required": False,
                "url": "https://huggingface.co/ByteDance/LatentSync-1.5/resolve/main/latentsync_unet.pt",
                "page_url": "https://huggingface.co/ByteDance/LatentSync-1.5",
                "desc_ru": "Точечная перерисовка артикуляции губ под аудиодорожку."
            }
        ]
    },
    {
        "id": "video_gen",
        "icon": "video",
        "title_ru": "6. Первичное Видео и Анимация (LTX-Video 2.5 / Wan 2.1)",
        "title_en": "6. Primary Video & Animation (LTX-2.5 / IC-LoRA / Wan 2.1)",
        "desc_ru": "Генерация видео-шотов из раскадровки, управление 3D-камерой через IC-LoRA и рендер.",
        "desc_en": "Primary video generation from storyboard shots, 3D camera control via IC-LoRA.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "checkpoints", "LTX-2.3"),
        "comfy_folder_Category": "checkpoints",
        "cloud_providers": [
            {"id": "fal_ltx", "name": "Fal.ai — LTX-Video / Wan 2.1 Pro Cloud", "default_url": "https://queue.fal.run/fal-ai/ltx-video-v095", "default_model": "fal-ai/ltx-video-v095"},
            {"id": "kling", "name": "Kling AI Video API (v1.6 / Master)", "default_url": "https://api.klingai.com/v1/videos/image2video", "default_model": "kling-v1-6-pro"},
            {"id": "runway", "name": "Runway Gen-3 Alpha Turbo API", "default_url": "https://api.dev.runwayml.com/v1/image_to_video", "default_model": "gen3a_turbo"},
            {"id": "replicate_wan", "name": "Replicate — Wan 2.1 14B 720p", "default_url": "https://api.replicate.com/v1/models/wavespeedai/wan-2.1-i2v-720p/predictions", "default_model": "wavespeedai/wan-2.1-i2v-720p"}
        ],
        "models": [
            {
                "id": "ltx_23_22b",
                "name": "LTX-Video 2.3 / 2.5 (Базовый видео-чекпоинт)",
                "filename": "ltx-video-2b-v0.9.5.safetensors",
                "alt_Matches": ["ltx", "ltx-2", "ltxv"],
                "size_gb": 9.4,
                "required": True,
                "url": "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.5.safetensors",
                "page_url": "https://huggingface.co/Lightricks/LTX-Video",
                "desc_ru": "Высокоскоростной генератор кинематографичного видео со звуком и 3D-контролем."
            },
            {
                "id": "ltx_iclora_union",
                "name": "LTX IC-LoRA Union Control (Глубина + Поза + Контур)",
                "filename": "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors",
                "alt_Matches": ["ic-lora", "iclora", "union-control"],
                "size_gb": 0.65,
                "required": True,
                "url": "https://huggingface.co/Lightricks/LTX-Video-ICLoRA-depth-13b-0.9.7/resolve/main/ltxv-13b-0.9.7-ic-lora-depth.safetensors",
                "page_url": "https://huggingface.co/Lightricks/LTX-Video",
                "desc_ru": "Управляющая LoRA для жесткой привязки к 3D-пассам глубины и скелета."
            },
            {
                "id": "ltx_iclora_camctrl",
                "name": "LTX IC-LoRA 3D Camera Markers (Контроль 3D-траектории камеры)",
                "filename": "IC-LORA_camctrl_00090.safetensors",
                "alt_Matches": ["camctrl", "markers"],
                "size_gb": 0.42,
                "required": False,
                "url": "https://huggingface.co/Lightricks/LTX-Video/tree/main",
                "page_url": "https://huggingface.co/Lightricks/LTX-Video",
                "desc_ru": "Удерживает движение камеры строго по запеченным 3D-маркерам сцены."
            },
            {
                "id": "wan21_i2v_14b",
                "name": "Wan 2.1 I2V 14B 720p FP8 (Тяжелый фотореалистичный рендер)",
                "filename": "wan2.1_i2v_720p_14B_fp8_e4m3fn.safetensors",
                "alt_Matches": ["wan2.1", "wan_2.1"],
                "size_gb": 14.3,
                "required": False,
                "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_i2v_720p_14B_fp8_e4m3fn.safetensors",
                "page_url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged",
                "desc_ru": "Опциональная 14B видео-модель для сложной физики и мимики."
            }
        ]
    },
    {
        "id": "mesh_gen",
        "icon": "boxes",
        "title_ru": "7. Генерация 3D-Меша (Hunyuan3D-Omni & TRELLIS.2-4B)",
        "title_en": "7. 3D Mesh Generation (Hunyuan3D-Omni & TRELLIS.2-4B)",
        "desc_ru": "Создание 3D-объектов (.GLB) по фото или промпту с подгонкой под габариты 3D-куба.",
        "desc_en": "Image/Prompt-to-3D .GLB generation with native Cube BBox conditioning and PBR textures.",
        "default_model_dir": (os.path.join(_ls("hunyuan3d_root"), "weights") if _ls("hunyuan3d_root") else os.path.join(COMFY_ROOT, "models", "3d")),
        "comfy_folder_Category": "checkpoints",
        "cloud_providers": [
            {"id": "tripo3d", "name": "Tripo3D Official Cloud API (v2.5 Textured GLB)", "default_url": "https://api.tripo3d.ai/v2/openapi/task", "default_model": "v2.5-20250123"},
            {"id": "meshy", "name": "Meshy.ai Cloud API (Image/Text to 3D v4)", "default_url": "https://api.meshy.ai/openapi/v1/image-to-3d", "default_model": "meshy-4"},
            {"id": "fal_trellis", "name": "Fal.ai — TRELLIS / Hunyuan3D Cloud", "default_url": "https://queue.fal.run/fal-ai/trellis", "default_model": "fal-ai/trellis"},
            {"id": "rodin", "name": "Hyper3D Rodin Gen-1.5 Cloud API", "default_url": "https://hyperhuman.deemos.com/api/v2/rodin", "default_model": "rodin-v1.5"}
        ],
        "models": [
            {
                "id": "hunyuan3d_omni_dit",
                "name": "Tencent Hunyuan3D-Omni / 2.0 DiT (Генерация формы с BBox-контролем)",
                "filename": "model.safetensors",
                "alt_Matches": ["hunyuan3d", "model.safetensors", "hunyuan3d-dit"],
                "size_gb": 4.6,
                "required": True,
                "url": "https://huggingface.co/tencent/Hunyuan3D-2/resolve/main/hunyuan3d-dit-v2-0/model.safetensors",
                "page_url": "https://huggingface.co/tencent/Hunyuan3D-2",
                "desc_ru": "Генерирует точную 3D-геометрию строго в пропорциях выбранного в сцене прокси-куба."
            },
            {
                "id": "hunyuan3d_paint",
                "name": "Tencent Hunyuan3D-Paint v2.0 (Генерация PBR текстур)",
                "filename": "hunyuan3d_paint_v2.safetensors",
                "alt_Matches": ["hunyuan3d-paint", "paint"],
                "size_gb": 5.2,
                "required": False,
                "url": "https://huggingface.co/tencent/Hunyuan3D-2/tree/main/hunyuan3d-paint-v2-0",
                "page_url": "https://huggingface.co/tencent/Hunyuan3D-2/tree/main/hunyuan3d-paint-v2-0",
                "desc_ru": "Запекает 2K/4K текстурный атлас на сгенерированный 3D-меш."
            },
            {
                "id": "trellis2_4b",
                "name": "Microsoft TRELLIS.2-4B (Высокодетализированный O-Voxel + PBR GLB)",
                "filename": "ss_flow_img_dit_L_16l8_fp16.safetensors",
                "alt_Matches": ["trellis", "pipeline.json", "ss_flow"],
                "size_gb": 8.4,
                "required": True,
                "url": "https://huggingface.co/microsoft/TRELLIS-image-large/resolve/main/ckpts/ss_flow_img_dit_L_16l8_fp16.safetensors",
                "page_url": "https://huggingface.co/microsoft/TRELLIS-image-large",
                "desc_ru": "Высокоточная генерация сложной 3D-топологии с готовыми PBR-материалами."
            }
        ]
    },
    {
        "id": "splat_gen",
        "icon": "sparkles",
        "title_ru": "8. Гауссовы сплаты 3DGS (Apple SHARP)",
        "title_en": "8. 3D Gaussian Splats (Apple SHARP)",
        "desc_ru": "3DGS-сцена из одного изображения за секунду. Лёгкое превью во вьюпорте, финальный рендер gsplat по камере шота.",
        "desc_en": "3DGS scene from a single image in about a second. Light preview in the viewport, final gsplat render along the shot camera.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "sharp"),
        "comfy_folder_Category": "sharp",
        "cloud_providers": [],
        "models": [
            {
                "id": "sharp_2572gikvuh",
                "name": "Apple SHARP checkpoint",
                "filename": "sharp_2572gikvuh.pt",
                "alt_Matches": ["sharp_2572gikvuh"],
                "size_gb": 2.8,
                "required": True,
                "url": "https://ml-site.cdn-apple.com/models/sharp/sharp_2572gikvuh.pt",
                "page_url": "https://github.com/apple/ml-sharp",
                "desc_ru": "Веса SHARP. Лицензия Apple: только для исследований.",
                "desc_en": "SHARP weights. Apple license: research use only."
            }
        ]
    },
    {
        "id": "deconstruct",
        "icon": "layers",
        "title_ru": "8. Деконструкция и 3D-Трекинг (GLOMAP / SAM 2.1 / CorridorKey)",
        "title_en": "8. Pass Deconstruction & 3D Camera Tracking",
        "desc_ru": "Извлечение 3D-камеры, масок объектов SAM 2.1, чистового кеинга волосков CorridorKey и Z-Depth.",
        "desc_en": "3D camera solve (GLOMAP), SAM 2.1 masks, CorridorKey hair matting, DepthAnythingV2, and DWPose.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "sam2"),
        "comfy_folder_Category": "sam2",
        "cloud_providers": [
            {"id": "fal_sam2", "name": "Fal.ai — SAM 2.1 Video Segmentation & Depth", "default_url": "https://queue.fal.run/fal-ai/sam2/video", "default_model": "fal-ai/sam2/video"},
            {"id": "replicate_deconstruct", "name": "Replicate — Video Matting & DepthAnythingV2", "default_url": "https://api.replicate.com/v1/predictions", "default_model": "chenxwh/depth-anything-v2"}
        ],
        "models": [
            {
                "id": "sam21_large",
                "name": "SAM 2.1 Hiera Large (Видео-сегментация объектов)",
                "filename": "sam2.1_hiera_large.pt",
                "alt_Matches": ["sam2", "sam2.1"],
                "size_gb": 0.89,
                "required": True,
                "url": "https://huggingface.co/facebook/sam2.1-hiera-large/resolve/main/sam2.1_hiera_large.pt",
                "page_url": "https://huggingface.co/facebook/sam2.1-hiera-large",
                "desc_ru": "Выделение любых объектов и персонажей по клику на протяжении всего видео."
            },
            {
                "id": "depth_anything_v2_vitl",
                "name": "DepthAnything V2 Large (16-битная карта глубины Z-Depth)",
                "filename": "depth_anything_v2_vitl.pth",
                "alt_Matches": ["depth_anything_v2", "depthanything"],
                "size_gb": 1.34,
                "required": True,
                "url": "https://huggingface.co/depth-anything/Depth-Anything-V2-Large/resolve/main/depth_anything_v2_vitl.pth",
                "page_url": "https://huggingface.co/depth-anything/Depth-Anything-V2-Large",
                "desc_ru": "Генерация точной карты глубины для 3D-блокинга и композитинга."
            },
            {
                "id": "corridor_key_v1",
                "name": "CorridorKey Neural Matting (Чистовой кеинг волос и полупрозрачности)",
                "filename": "CorridorKey_v1.0.pth",
                "alt_Matches": ["corridorkey", "vitmatte", "modnet"],
                "size_gb": 0.38,
                "required": False,
                "url": "https://huggingface.co/hustvl/ViTMatte-small-composition-1k/resolve/main/pytorch_model.bin",
                "page_url": "https://huggingface.co/hustvl/ViTMatte-small-composition-1k",
                "desc_ru": "Извлечение тонких прядей волос, дыма и расфокуса движения (Motion Blur)."
            },
            {
                "id": "dwpose_ll",
                "name": "DWPose WholeBody 384 (Скелет, кисти рук и мимика)",
                "filename": "dw-ll_ucoco_384.onnx",
                "alt_Matches": ["dw-ll", "dwpose"],
                "size_gb": 0.13,
                "required": False,
                "url": "https://huggingface.co/yzd-v/DWPose/resolve/main/dw-ll_ucoco_384.onnx",
                "page_url": "https://huggingface.co/yzd-v/DWPose",
                "desc_ru": "Детектор позы и кистей рук для переноса движения на 3D/AI персонажей."
            }
        ]
    },
    {
        "id": "composite",
        "icon": "git-merge",
        "title_ru": "9. Финальное Сведение, Релайтинг и Апскейл (Composite & Relight)",
        "title_en": "9. Final Compositing, AI Relighting & 4K Upscale",
        "desc_ru": "Гармонизация освещения слоев (IC-Light), Z-Depth туман и 4K апскейл финального мастера.",
        "desc_en": "Neural relighting (IC-Light), Z-Depth compositing, and 4K UltraSharp mastering.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "upscale_models"),
        "comfy_folder_Category": "upscale_models",
        "cloud_providers": [
            {"id": "fal_iclight", "name": "Fal.ai — IC-Light v2 & Clarity 4K Upscaler", "default_url": "https://queue.fal.run/fal-ai/iclight-v2", "default_model": "fal-ai/iclight-v2"},
            {"id": "replicate_upscale", "name": "Replicate — SeedVR2 / Clarity Upscaler", "default_url": "https://api.replicate.com/v1/predictions", "default_model": "philz1337x/clarity-upscaler"}
        ],
        "models": [
            {
                "id": "iclight_fc",
                "name": "IC-Light Foreground Conditioned (Гармонизация света и рефлексов)",
                "filename": "iclight_sd15_fc.safetensors",
                "alt_Matches": ["iclight", "ic-light"],
                "size_gb": 1.72,
                "required": True,
                "url": "https://huggingface.co/lllyasviel/ic-light/resolve/main/iclight_sd15_fc.safetensors",
                "page_url": "https://huggingface.co/lllyasviel/ic-light",
                "desc_ru": "Переосвещает вырезанного актера или 3D-объект под свет новой локации."
            },
            {
                "id": "ultrasharp_4x",
                "name": "4x-UltraSharp (Кинематографичный 4K Апскейл)",
                "filename": "4x-UltraSharp.pth",
                "alt_Matches": ["ultrasharp", "4x-", "esrgan"],
                "size_gb": 0.067,
                "required": True,
                "url": "https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth",
                "page_url": "https://huggingface.co/Kim2091/UltraSharp",
                "desc_ru": "Повышение четкости и детализации финального рендера до 4K."
            }
        ]
    },
    {
        "id": "music",
        "icon": "music",
        "title_ru": "10. Генерация музыки (ACE-Step 1.5)",
        "title_en": "10. Music Score & Stem Generation",
        "desc_ru": "Синтез оригинального саундтрека и звуковых фактур под хронометраж монтажа.",
        "desc_en": "Original soundtrack and ambient stem generation synced to timeline duration.",
        "default_model_dir": os.path.join(COMFY_ROOT, "models", "audio"),
        "comfy_folder_Category": "audio",
        "cloud_providers": [
            {"id": "minimax_music", "name": "MiniMax Music API (Cloud Soundtrack)", "default_url": "https://api.minimax.chat/v1/music_generation", "default_model": "music-01"},
            {"id": "fal_acestep", "name": "Fal.ai — ACE-Step Music Generator", "default_url": "https://queue.fal.run/fal-ai/ace-step", "default_model": "fal-ai/ace-step"},
            {"id": "elevenlabs_sfx", "name": "ElevenLabs Sound Effects & Ambient API", "default_url": "https://api.elevenlabs.io/v1/sound-generation", "default_model": "eleven_text_to_sound_v2"}
        ],
        "models": [
            {
                "id": "acestep15_turbo",
                "name": "ACE-Step 1.5 turbo (2B DiT, 8 steps)",
                "filename": "acestep_v1.5_turbo.safetensors",
                "alt_Matches": ["acestep_v1.5", "ace_step_1.5", "acestep15"],
                "size_gb": 4.8,
                "required": True,
                "url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/diffusion_models/acestep_v1.5_turbo.safetensors",
                "page_url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files",
                "desc_ru": "Генерация песен и саундтреков по стилю, тексту, BPM и тональности (MIT)."
            },
            {
                "id": "acestep15_lm06",
                "name": "ACE-Step 1.5 Qwen 0.6B text encoder",
                "filename": "qwen_0.6b_ace15.safetensors",
                "alt_Matches": ["qwen_0.6b_ace15"],
                "size_gb": 1.2,
                "required": True,
                "url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_0.6b_ace15.safetensors",
                "page_url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files",
                "desc_ru": "Текстовый энкодер ACE-Step 1.5."
            },
            {
                "id": "acestep15_lm4b",
                "name": "ACE-Step 1.5 Qwen 4B LM (song structure, audio codes)",
                "filename": "qwen_4b_ace15.safetensors",
                "alt_Matches": ["qwen_4b_ace15"],
                "size_gb": 8.4,
                "required": True,
                "url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_4b_ace15.safetensors",
                "page_url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files",
                "desc_ru": "LM ACE-Step 1.5: строит композицию и аудио-коды."
            },
            {
                "id": "acestep15_vae",
                "name": "ACE-Step 1.5 VAE",
                "filename": "ace_1.5_vae.safetensors",
                "alt_Matches": ["ace_1.5_vae"],
                "size_gb": 0.34,
                "required": True,
                "url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/vae/ace_1.5_vae.safetensors",
                "page_url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files",
                "desc_ru": "Аудио-VAE ACE-Step 1.5."
            }
        ]
    }
]


# ---------------------------------------------------------------------------
# Per-process extras: Comfy partner nodes (cloud via Comfy API key) and
# external Python environments for tools that cannot share ComfyUI's env.
# ---------------------------------------------------------------------------
PROCESS_EXTRAS: Dict[str, Dict[str, Any]] = {
    "agent": {"partner_nodes": ["GeminiNode", "OpenAIChatNode", "ClaudeNode"]},
    "storyboard": {"partner_nodes": ["Flux3ImageNode", "Flux2ImageNode", "FluxProUltraImageNode"]},
    "character": {
        "partner_nodes": [],
        "external_env": {
            "title_en": "LoRA trainers run outside ComfyUI",
            "title_ru": "Обучение LoRA запускается вне ComfyUI",
            "desc_en": "LTX-2 trainer runs in WSL2 (Ubuntu + uv); FLUX LoRA runs in ai-toolkit's own venv. Training needs a high-VRAM GPU: 24 GB works but is very slow.",
            "desc_ru": "LTX-2 тренер работает в WSL2 (Ubuntu + uv), FLUX LoRA — в собственном venv ai-toolkit. Нужна видеокарта с большим объёмом памяти: на 24 ГБ работает, но очень медленно.",
            "fields": ["external_python", "external_root"],
        },
    },
    "location": {"partner_nodes": [], "external_env_ref": "character"},
    "tts_stage1": {"partner_nodes": ["ElevenLabsTextToSpeech", "ElevenLabsInstantVoiceClone", "ElevenLabsSpeechToSpeech"]},
    "video_gen": {"partner_nodes": ["LtxApi25ImageToVideo", "LtxApi25TextToVideo", "LtxApi25AudioToVideo"]},
    "mesh_gen": {
        "partner_nodes": ["TencentImageToModelNode", "TripoImageToModelNodeV2", "Rodin3D_Gen25_Image", "MeshyImageToModelNode"],
        "external_env": {
            "title_en": "Runs in a separate Python environment",
            "title_ru": "Работает в отдельном окружении Python",
            "desc_en": "Hunyuan3D-Omni and TRELLIS.2 need their own CUDA extensions and cannot be installed into ComfyUI's env. Install them separately and point to their python.exe and repo folder. Or use the Comfy partner nodes (Tencent Hunyuan3D / Tripo / Rodin / Meshy) in cloud mode.",
            "desc_ru": "Hunyuan3D-Omni и TRELLIS.2 требуют своих CUDA-расширений и не ставятся в окружение ComfyUI. Установите их отдельно и укажите путь к python.exe и папке репозитория. Либо используйте облачный режим через партнёрские ноды Comfy (Tencent Hunyuan3D / Tripo / Rodin / Meshy).",
            "fields": ["external_python", "external_root"],
        },
    },
    "splat_gen": {
        "partner_nodes": [],
        "external_env": {
            "title_en": "Runs in a separate Python environment",
            "title_ru": "Работает в отдельном окружении Python",
            "desc_en": "Apple SHARP (ml-sharp) needs Python 3.13 + gsplat. Install it separately (conda create -n sharp python=3.13; pip install -r requirements.txt) and point to its python.exe. Final renders use gsplat (CUDA). Model license: research use only.",
            "desc_ru": "Apple SHARP (ml-sharp) требует Python 3.13 и gsplat. Установите отдельно (conda create -n sharp python=3.13; pip install -r requirements.txt) и укажите путь к python.exe. Финальный рендер — gsplat (CUDA). Лицензия модели: только для исследований.",
            "fields": ["external_python", "external_root"],
        },
    },
    "deconstruct": {"partner_nodes": []},
    "composite": {"partner_nodes": ["FluxProFillNode", "FluxVideoUpscaleNode"]},
    "music": {"partner_nodes": []},
}


def load_processes_config() -> Dict[str, Any]:
    """Loads saved user configuration per process from disk."""
    saved = {"global_models_root": "", "comfy_api_key": "", "processes": {}}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    saved.update(data)
        except Exception as e:
            print(f"[PipelineConfig] Failed reading config: {e}")

    # Ensure every process in PROCESS_CATALOG has an entry
    procs = saved.setdefault("processes", {})
    for p in PROCESS_CATALOG:
        pid = p["id"]
        if pid not in procs or not isinstance(procs[pid], dict):
            procs[pid] = {}
        entry = procs[pid]
        entry.setdefault("enabled", True)            # Unchecked = process excluded: no downloads, greyed out in UI, skipped by pipeline
        entry.setdefault("execution_mode", "local")  # 'local' | 'partner' | 'cloud' | 'custom_workflow'
        if entry["execution_mode"] in ("cloud", "partner") and not _cloud_ok():
            entry["execution_mode"] = "local"          # cloud modes need an explicit opt-in (open-weights default)
        entry.setdefault("external_python", "")      # python.exe of a separate env (TRELLIS / Hunyuan / SHARP / trainers)
        entry.setdefault("external_root", "")        # repo folder of that external tool
        entry.setdefault("custom_model_dir", "")     # User-specified existing model folder on disk (no copy/move)
        entry.setdefault("selected_local_file", "")  # Optional specific file chosen from custom_model_dir
        default_prov = p["cloud_providers"][0] if p.get("cloud_providers") else {}
        entry.setdefault("cloud_provider", default_prov.get("id", "custom"))
        entry.setdefault("cloud_api_url", default_prov.get("default_url", ""))
        entry.setdefault("cloud_api_key", "")
        entry.setdefault("cloud_model_id", default_prov.get("default_model", ""))
        entry.setdefault("custom_workflow_name", "")
        entry.setdefault("custom_workflow_file", "")

    return saved


def save_processes_config(new_data: Dict[str, Any]) -> Dict[str, Any]:
    """Saves updated configuration and registers any custom model directories in-place with ComfyUI."""
    current = load_processes_config()
    if "global_models_root" in new_data:
        current["global_models_root"] = str(new_data["global_models_root"] or "").strip()
    if "comfy_api_key" in new_data:
        current["comfy_api_key"] = str(new_data["comfy_api_key"] or "").strip()

    if "processes" in new_data and isinstance(new_data["processes"], dict):
        for pid, pconf in new_data["processes"].items():
            if not isinstance(pconf, dict):
                continue
            target = current["processes"].setdefault(pid, {})
            for k in (
                "enabled", "external_python", "external_root",
                "execution_mode", "custom_model_dir", "selected_local_file",
                "cloud_provider", "cloud_api_url", "cloud_api_key", "cloud_model_id",
                "custom_workflow_name", "custom_workflow_file"
            ):
                if k in pconf:
                    target[k] = pconf[k]

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[PipelineConfig] Failed writing config: {e}")

    _register_custom_folders_in_comfy(current)
    return current


def _register_custom_folders_in_comfy(cfg: Dict[str, Any]):
    """
    Mounts user-specified model folders directly into ComfyUI's `folder_paths`
    so ComfyUI uses already-downloaded weights in-place without copying or moving them.
    """
    try:
        import folder_paths
        has_fp = True
    except Exception:
        has_fp = False

    global_root = str(cfg.get("global_models_root", "")).strip()
    if has_fp and global_root and os.path.isdir(global_root):
        for cat in ("checkpoints", "unet", "loras", "vae", "clip", "upscale_models", "controlnet"):
            sub = os.path.join(global_root, cat)
            if os.path.isdir(sub):
                try:
                    folder_paths.add_model_folder_path(cat, sub)
                except Exception:
                    pass

    procs = cfg.get("processes", {})
    for p in PROCESS_CATALOG:
        pid = p["id"]
        cat = p.get("comfy_folder_Category", "checkpoints")
        cdir = str(procs.get(pid, {}).get("custom_model_dir", "")).strip()
        if cdir and os.path.isdir(cdir):
            if has_fp:
                try:
                    folder_paths.add_model_folder_path(cat, cdir)
                except Exception:
                    pass


def scan_directory_for_models(folder_path: str, max_files: int = 80) -> Dict[str, Any]:
    """
    Scans a directory (and up to 2 levels of subdirectories) for model files
    without copying or moving anything.
    """
    folder_path = (folder_path or "").strip()
    if not folder_path:
        return {"exists": False, "path": "", "files": [], "total_size_gb": 0.0}
    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        return {"exists": False, "path": folder_path, "files": [], "total_size_gb": 0.0}

    found_files = []
    total_bytes = 0
    base_depth = folder_path.rstrip("\\/").count(os.sep)

    try:
        for root, dirs, files in os.walk(folder_path):
            depth = root.rstrip("\\/").count(os.sep) - base_depth
            if depth > 2:
                dirs[:] = []
                continue
            for fn in files:
                lower = fn.lower()
                if lower.endswith(MODEL_EXTENSIONS) or lower == "pipeline.json":
                    full_p = os.path.join(root, fn)
                    try:
                        sz = os.path.getsize(full_p)
                    except Exception:
                        sz = 0
                    total_bytes += sz
                    rel_p = os.path.relpath(full_p, folder_path)
                    found_files.append({
                        "filename": fn,
                        "rel_path": rel_p,
                        "full_path": full_p,
                        "size_gb": round(sz / (1024 ** 3), 2),
                        "size_mb": round(sz / (1024 ** 2), 1)
                    })
                    if len(found_files) >= max_files:
                        break
            if len(found_files) >= max_files:
                break
    except Exception as e:
        return {"exists": True, "path": folder_path, "files": [], "error": str(e), "total_size_gb": 0.0}

    return {
        "exists": True,
        "path": folder_path,
        "files": found_files,
        "count": len(found_files),
        "total_size_gb": round(total_bytes / (1024 ** 3), 2)
    }


def get_full_processes_state() -> Dict[str, Any]:
    """
    Combines `PROCESS_CATALOG` with saved user settings, scans configured local directories,
    and checks whether each model is installed in either the default folder or the user's custom folder.
    """
    cfg = load_processes_config()
    _register_custom_folders_in_comfy(cfg)

    global_root = str(cfg.get("global_models_root", "")).strip()
    enriched_processes = []

    for p in PROCESS_CATALOG:
        pid = p["id"]
        user_p = cfg.get("processes", {}).get(pid, {})
        custom_dir = str(user_p.get("custom_model_dir", "")).strip()
        default_dir = p["default_model_dir"]

        # Scan custom_dir if set, plus default_dir
        custom_scan = scan_directory_for_models(custom_dir) if custom_dir else {"exists": False, "path": "", "files": []}
        default_scan = scan_directory_for_models(default_dir)
        global_scan = scan_directory_for_models(global_root) if global_root else {"exists": False, "path": "", "files": []}

        # Extra known roots for 3D generators and FLUX.3 / LTX-Video
        extra_files = []
        if pid == "mesh_gen":
            for extra_root in [os.path.join(r, "weights") for r in (_ls("hunyuan3d_root"), _ls("trellis_root")) if r]:
                if os.path.isdir(extra_root):
                    extra_files.extend(scan_directory_for_models(extra_root).get("files", []))
        elif pid == "storyboard":
            for extra_root in (
                os.path.join(COMFY_ROOT, "models", "diffusion_models"),
                os.path.join(COMFY_ROOT, "models", "text_encoders"),
                os.path.join(COMFY_ROOT, "models", "vae"),
                os.path.join(COMFY_ROOT, "models", "clip")
            ):
                if os.path.isdir(extra_root):
                    extra_files.extend(scan_directory_for_models(extra_root).get("files", []))
        elif pid == "video_gen":
            for extra_root in (
                os.path.join(COMFY_ROOT, "models", "checkpoints"),
                os.path.join(COMFY_ROOT, "models", "diffusion_models"),
                os.path.join(COMFY_ROOT, "models", "loras"),
                os.path.join(COMFY_ROOT, "models", "text_encoders"),
                os.path.join(COMFY_ROOT, "models", "vae")
            ):
                if os.path.isdir(extra_root):
                    extra_files.extend(scan_directory_for_models(extra_root).get("files", []))

        ext_root = str(user_p.get("external_root", "")).strip()
        if ext_root and os.path.isdir(ext_root):
            extra_files.extend(scan_directory_for_models(ext_root).get("files", []))

        all_scanned_files = (
            custom_scan.get("files", []) +
            default_scan.get("files", []) +
            global_scan.get("files", []) +
            extra_files
        )

        models_status = []
        for m in p.get("models", []):
            target_fn = m["filename"].lower()
            alt_matches = [a.lower() for a in m.get("alt_Matches", [])]
            matched_file = None

            for fobj in all_scanned_files:
                fn_low = fobj["filename"].lower()
                full_low = fobj["full_path"].lower()
                if fn_low == target_fn or any(alt in fn_low or alt in full_low for alt in alt_matches):
                    matched_file = fobj
                    break

            models_status.append({
                **m,
                "installed": matched_file is not None,
                "found_path": matched_file["full_path"] if matched_file else "",
                "found_size_gb": matched_file["size_gb"] if matched_file else 0.0
            })

        enriched_processes.append({
            "id": pid,
            "icon": p["icon"],
            "title_ru": p["title_ru"],
            "title_en": p["title_en"],
            "desc_ru": p["desc_ru"],
            "desc_en": p["desc_en"],
            "default_model_dir": default_dir,
            "cloud_providers": p.get("cloud_providers", []) if _cloud_ok() else [],
            "partner_nodes": _available_partner_nodes(PROCESS_EXTRAS.get(pid, {}).get("partner_nodes", [])) if _cloud_ok() else [],
            "external_env": _external_env_for(pid),
            "config": user_p,
            "custom_folder_scan": custom_scan,
            "default_folder_scan": default_scan,
            "models": models_status
        })

    with _DOWNLOAD_LOCK:
        downloads_snapshot = list(_DOWNLOAD_JOBS.values())

    api_key = str(cfg.get("comfy_api_key", "") or os.environ.get("COMFY_API_KEY", "")).strip()
    return {
        "global_models_root": global_root,
        "comfy_api_key_set": bool(api_key),
        "comfy_api_key_masked": (api_key[:4] + "…" + api_key[-4:]) if len(api_key) > 10 else ("set" if api_key else ""),
        "comfy_root": COMFY_ROOT,
        "processes": enriched_processes,
        "downloads": downloads_snapshot
    }


def _external_env_for(pid: str) -> Dict[str, Any]:
    ex = PROCESS_EXTRAS.get(pid, {})
    if "external_env_ref" in ex:
        ex = PROCESS_EXTRAS.get(ex["external_env_ref"], {})
    return ex.get("external_env") or {}


def _available_partner_nodes(node_ids: List[str]) -> List[Dict[str, Any]]:
    """Returns partner node ids with an 'available' flag (whether this ComfyUI build registers them)."""
    registered = set()
    try:
        import nodes as comfy_nodes  # ComfyUI's global node registry
        registered = set(getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}).keys())
    except Exception:
        pass
    return [{"id": n, "available": (n in registered) if registered else None} for n in node_ids]


def is_process_enabled(pid: str) -> bool:
    return bool(load_processes_config().get("processes", {}).get(pid, {}).get("enabled", True))


def get_comfy_api_key() -> str:
    cfg = load_processes_config()
    return str(cfg.get("comfy_api_key", "") or os.environ.get("COMFY_API_KEY", "")).strip()


def save_custom_workflow_for_process(process_id: str, workflow_name: str, workflow_json: Any) -> Dict[str, Any]:
    """Saves a ComfyUI workflow JSON file and binds it to the specified studio process."""
    safe_pid = "".join(c if c.isalnum() or c in "_-" else "_" for c in process_id) or "general"
    wf_filename = f"workflow_{safe_pid}.json"
    wf_path = os.path.join(CUSTOM_WORKFLOWS_DIR, wf_filename)

    if isinstance(workflow_json, str):
        try:
            parsed = json.loads(workflow_json)
        except Exception as e:
            raise ValueError(f"Invalid JSON workflow: {e}")
    else:
        parsed = workflow_json

    with open(wf_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)

    cfg = load_processes_config()
    p_entry = cfg.setdefault("processes", {}).setdefault(process_id, {})
    p_entry["custom_workflow_name"] = workflow_name or wf_filename
    p_entry["custom_workflow_file"] = wf_path
    p_entry["execution_mode"] = "custom_workflow"
    save_processes_config(cfg)

    return {
        "status": "ok",
        "process_id": process_id,
        "workflow_name": p_entry["custom_workflow_name"],
        "workflow_file": wf_path,
        "execution_mode": "custom_workflow"
    }


def start_model_download(process_id: str, model_id: str, url: str, filename: str, target_dir: str = "") -> Dict[str, Any]:
    """
    Starts a background download of a model weight file into the user's configured
    `custom_model_dir` (if specified) or the process's `default_model_dir`.
    """
    cfg = load_processes_config()
    p_cat = next((p for p in PROCESS_CATALOG if p["id"] == process_id), None)
    user_p = cfg.get("processes", {}).get(process_id, {})

    dest_dir = (target_dir or user_p.get("custom_model_dir") or (p_cat["default_model_dir"] if p_cat else CACHE_DIR)).strip()
    os.makedirs(dest_dir, exist_ok=True)

    safe_fn = os.path.basename(filename or "model.safetensors")
    dest_path = os.path.join(dest_dir, safe_fn)
    dl_id = f"dl_{process_id}_{model_id}"

    job = {
        "download_id": dl_id,
        "process_id": process_id,
        "model_id": model_id,
        "filename": safe_fn,
        "url": url,
        "dest_dir": dest_dir,
        "dest_path": dest_path,
        "status": "downloading",
        "progress": 0,
        "downloaded_mb": 0.0,
        "total_mb": 0.0,
        "message": f"Подключение к {url[:50]}...",
        "started_at": time.time()
    }

    with _DOWNLOAD_LOCK:
        _DOWNLOAD_JOBS[dl_id] = job

    def _dl_worker():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Keshiki-VFX-Studio)"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                content_type = resp.headers.get("Content-Type", "")
                # If the URL is a HuggingFace tree page instead of a direct binary resolve URL, note it cleanly
                if "text/html" in content_type and "/tree/" in url:
                    with _DOWNLOAD_LOCK:
                        job["status"] = "browser_required"
                        job["message"] = "Открыта страница репозитория — скачайте веса по прямой ссылке."
                    return

                total_len = int(resp.headers.get("Content-Length", 0) or 0)
                with _DOWNLOAD_LOCK:
                    job["total_mb"] = round(total_len / (1024 ** 2), 1) if total_len else 0.0

                part_path = dest_path + ".part"
                downloaded = 0
                chunk_size = 1024 * 512  # 512 KB chunks
                with open(part_path, "wb") as out_f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        downloaded += len(chunk)
                        with _DOWNLOAD_LOCK:
                            job["downloaded_mb"] = round(downloaded / (1024 ** 2), 1)
                            if total_len > 0:
                                job["progress"] = min(99, int((downloaded / total_len) * 100))
                                job["message"] = f"{job['downloaded_mb']} / {job['total_mb']} MB ({job['progress']}%)"
                            else:
                                job["message"] = f"Скачано {job['downloaded_mb']} MB..."

                if os.path.exists(dest_path):
                    os.remove(dest_path)
                os.replace(part_path, dest_path)

                with _DOWNLOAD_LOCK:
                    job["status"] = "completed"
                    job["progress"] = 100
                    job["message"] = f"Сохранено в {dest_path}"
        except Exception as e:
            with _DOWNLOAD_LOCK:
                job["status"] = "failed"
                job["message"] = f"Ошибка: {e}"

    t = threading.Thread(target=_dl_worker, daemon=True)
    t.start()
    return job


def get_download_jobs() -> List[Dict[str, Any]]:
    with _DOWNLOAD_LOCK:
        return list(_DOWNLOAD_JOBS.values())
