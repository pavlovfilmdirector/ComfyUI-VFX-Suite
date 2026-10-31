# -*- coding: utf-8 -*-
"""
User Pipelines Service (My Pipelines / Мои Пайплайны)
=====================================================
Multi-stage macro pipeline orchestration for KESHIKI VFX AI Studio:
Chains multiple ComfyUI workflows and studio processes sequentially.
The output of Step N (image, video, audio, mask, mesh) automatically
wires into the inputs of Step N+1, executing the entire pipeline in one run.
"""

import copy
import json
import os
import re
import shutil
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    from .process_runner import ProcessError, run_process, load_template, inject_params, _is_api_prompt, get_run_status, cancel_run, free_memory
    from .project_manager import get_project_dir
except ImportError:  # pragma: no cover
    from process_runner import ProcessError, run_process, load_template, inject_params, _is_api_prompt, get_run_status, cancel_run, free_memory
    from project_manager import get_project_dir

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINES_DIR = os.path.join(BASE_DIR, "user_pipelines")
os.makedirs(PIPELINES_DIR, exist_ok=True)

_PIPELINE_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

# Standard Built-in Workflow Templates available as Macro Stages
BUILTIN_STAGES = [
    {
        "id": "flux_t2i",
        "name": "FLUX.3 Text-to-Image",
        "title_ru": "FLUX.3 Текст в Картинку",
        "description": "Генерация фотореалистичного кадра из промпта (FLUX.3 Klein)",
        "category": "image",
        "icon": "🎨",
        "workflow": "api/storyboard/flux3_klein_t2i.json",
        "inputs": [
            {"id": "prompt", "label": "Prompt", "label_ru": "Промпт", "kind": "text", "default": "Cinematic heroic portrait, natural light, 8k"},
        ],
        "outputs": [
            {"id": "image", "label": "Generated Image", "label_ru": "Картинка", "kind": "image"}
        ]
    },
    {
        "id": "ltx25_i2v",
        "name": "LTX-2.5 Image-to-Video",
        "title_ru": "LTX-2.5 Картинка в Видео (I2V)",
        "description": "Оживление первого кадра в видеоролик с движением камеры",
        "category": "video",
        "icon": "🎬",
        "workflow": "api/video_gen/ltx25_i2v.json",
        "inputs": [
            {"id": "start_image", "label": "Start Image", "label_ru": "Начальный кадр", "kind": "image"},
            {"id": "prompt", "label": "Motion Prompt", "label_ru": "Промпт движения", "kind": "text", "default": "Dynamic cinematic camera motion, realistic motion"},
            {"id": "length", "label": "Frames", "label_ru": "Кадров", "kind": "number", "default": 49}
        ],
        "outputs": [
            {"id": "video", "label": "Generated Video", "label_ru": "Видеоролик", "kind": "video"}
        ]
    },
    {
        "id": "ltx25_layout_to_render",
        "name": "LTX-2.5 Layout-to-Render",
        "title_ru": "LTX-2.5 3D-Лейаут в Финал",
        "description": "Рендеринг движения 3D-анимации в фотореалистичное видео",
        "category": "video",
        "icon": "🎥",
        "workflow": "api/video_fx/ltx25_ic_lora_v2v.json",
        "inputs": [
            {"id": "source_video", "label": "Layout Video", "label_ru": "Видео 3D-анимации", "kind": "video"},
            {"id": "ref_image", "label": "First Frame Ref", "label_ru": "Референс кадра (стиль)", "kind": "image"},
            {"id": "prompt", "label": "Render Prompt", "label_ru": "Промпт рендера", "kind": "text", "default": "Photorealistic cinematic 4k render, detailed volumetric lighting"}
        ],
        "outputs": [
            {"id": "video", "label": "Rendered Video", "label_ru": "Отрендеренное видео", "kind": "video"}
        ]
    },
    {
        "id": "ltx23_dubit",
        "name": "DubIt Speech Lip-Sync",
        "title_ru": "DubIt Липсинк Речи (LTX-2.3)",
        "description": "Синхронизация губ актера в готовом видео под аудио дорожку",
        "category": "voice",
        "icon": "👄",
        "workflow": "voice/ltx23_dubit.json",
        "inputs": [
            {"id": "source_video", "label": "Input Video", "label_ru": "Видео для липсинка", "kind": "video"},
            {"id": "audio", "label": "Speech Audio", "label_ru": "Аудиодорожка речи", "kind": "audio"},
            {"id": "prompt", "label": "Prompt", "label_ru": "Промпт", "kind": "text", "default": "talking naturally, fluid facial expression"}
        ],
        "outputs": [
            {"id": "video", "label": "Dubbed Video", "label_ru": "Озвученное видео", "kind": "video"}
        ]
    },
    {
        "id": "ltx25_a2v",
        "name": "Lip-Sync Audio-to-Video",
        "title_ru": "Липсинк Audio-to-Video (LTX-2.5)",
        "description": "Оживление говорящего лица из одной фотографии и аудиофайла",
        "category": "voice",
        "icon": "🎤",
        "workflow": "voice/ltx25_a2v.json",
        "inputs": [
            {"id": "start_image", "label": "Portrait Image", "label_ru": "Фото персонажа", "kind": "image"},
            {"id": "audio", "label": "Speech Audio", "label_ru": "Аудиодорожка речи", "kind": "audio"},
            {"id": "duration", "label": "Duration (sec)", "label_ru": "Длительность (сек)", "kind": "number", "default": 4.0}
        ],
        "outputs": [
            {"id": "video", "label": "Lip-Sync Video", "label_ru": "Говорящее видео", "kind": "video"}
        ]
    },
    {
        "id": "trellis_3d",
        "name": "TRELLIS.2 3D Model",
        "title_ru": "TRELLIS.2 3D Модель",
        "description": "Создание PBR 3D меша (.glb) из одного изображения",
        "category": "3d",
        "icon": "🧊",
        "workflow": "api/mesh_gen/trellis2_pixal3d.json",
        "inputs": [
            {"id": "image", "label": "Reference Image", "label_ru": "Изображение объекта", "kind": "image"}
        ],
        "outputs": [
            {"id": "mesh", "label": "3D Mesh (GLB)", "label_ru": "3D Меш (.glb)", "kind": "3d"}
        ]
    },
    {
        "id": "rembg_matte",
        "name": "Rembg / Alpha Matte",
        "title_ru": "Удаление фона / Маска",
        "description": "Выделение чистого объекта и альфа-маски из изображения",
        "category": "image",
        "icon": "✂️",
        "workflow": "api/deconstruct/sam3_text_mask_image.json",
        "inputs": [
            {"id": "image", "label": "Input Image", "label_ru": "Исходная картинка", "kind": "image"}
        ],
        "outputs": [
            {"id": "image", "label": "Cutout Foreground", "label_ru": "Вырезанный объект", "kind": "image"},
            {"id": "mask", "label": "Alpha Matte", "label_ru": "Альфа-маска", "kind": "mask"}
        ]
    }
]

# Image edit + 2D video-layer job stages (the "3D layout → robot → CorridorKey actor" demo pipeline).
# Stages with "video_job" run video_layers_service.start_job(kind, project, video_id, params) instead of a
# ComfyUI prompt; their "layer" outputs are layer ids that later stages can take with from_stage.
BUILTIN_STAGES += [
    {
        "id": "flux_edit",
        "name": "FLUX.3 Klein Edit",
        "title_ru": "FLUX.3 Klein Редактирование кадра",
        "description": "Перерисовка картинки по промпту (например, первый кадр из 3D-лейаута + позы)",
        "category": "image",
        "icon": "🖌️",
        "workflow": "api/storyboard/flux3_klein_edit.json",
        "inputs": [
            {"id": "image", "label": "Source Image", "label_ru": "Исходная картинка", "kind": "image"},
            {"id": "prompt", "label": "Edit Prompt", "label_ru": "Промпт", "kind": "text", "default": ""},
            {"id": "seed", "label": "Seed", "label_ru": "Сид", "kind": "number", "default": 428825},
            {"id": "steps", "label": "Steps", "label_ru": "Шаги", "kind": "number", "default": 16},
            {"id": "megapixels", "label": "Megapixels", "label_ru": "Мегапиксели", "kind": "number", "default": 1.0}
        ],
        "outputs": [
            {"id": "image_url", "label": "Edited Image", "label_ru": "Картинка", "kind": "image"}
        ]
    },
    {
        "id": "vl_pose_cleanup",
        "name": "3D Pose Cleanup (remove root bone)",
        "title_ru": "Чистка позы из 3D (убрать root-кость)",
        "description": "Убирает из рендера скелета длинные кости (root «бёдра → начало координат»), из-за которых появляется второй персонаж",
        "category": "video_layers",
        "icon": "🦴",
        "video_job": "pose_cleanup",
        "inputs": [
            {"id": "video_id", "label": "Video ID", "label_ru": "ID видео", "kind": "text"},
            {"id": "source_layer", "label": "Skeleton render layer", "label_ru": "Слой рендера скелета (id или video/id)", "kind": "text"},
            {"id": "max_bone_px", "label": "Max bone length (px)", "label_ru": "Макс. длина кости (px)", "kind": "number", "default": 110},
            {"id": "name", "label": "Layer name", "label_ru": "Имя слоя", "kind": "text", "default": "Pose from 3D (clean)"}
        ],
        "outputs": [{"id": "layer", "label": "Pose layer", "label_ru": "Слой позы", "kind": "layer"}]
    },
    {
        "id": "vl_layout_cleanup",
        "name": "3D Layout Cleanup (no actor / skeleton)",
        "title_ru": "Чистка 3D-лейаута (без актёра и скелета)",
        "description": "Убирает актёра и скелет из layout-прохода и делает гайд первого кадра (лейаут + поза)",
        "category": "video_layers",
        "icon": "🧱",
        "video_job": "layout_cleanup",
        "inputs": [
            {"id": "video_id", "label": "Video ID", "label_ru": "ID видео", "kind": "text"},
            {"id": "layout_source", "label": "Layout source", "label_ru": "Источник лейаута (layout_render или слой)", "kind": "text", "default": "layout_render"},
            {"id": "skeleton_source", "label": "Skeleton render layer", "label_ru": "Слой рендера скелета", "kind": "text"},
            {"id": "matte_layer", "label": "Actor matte layer", "label_ru": "Матт актёра (CorridorKey matte)", "kind": "text"},
            {"id": "pose_layer", "label": "Clean pose layer", "label_ru": "Чистый слой позы", "kind": "layer"},
            {"id": "name", "label": "Layer name", "label_ru": "Имя слоя", "kind": "text", "default": "3D Layout (clean)"}
        ],
        "outputs": [
            {"id": "layer", "label": "Layout layer", "label_ru": "Слой лейаута", "kind": "layer"},
            {"id": "image", "label": "First-frame guide", "label_ru": "Гайд первого кадра", "kind": "image"}
        ]
    },
    {
        "id": "vl_ltx_ic_lora",
        "name": "LTX-2.5 IC-LoRA on layers (dual)",
        "title_ru": "LTX-2.5 IC-LoRA по слоям (две LoRA)",
        "description": "Генерация видео по слоям-контролам: layout-to-render + union-control (поза), с первым кадром",
        "category": "video_layers",
        "icon": "🎞️",
        "video_job": "ltx_ic_lora",
        "inputs": [
            {"id": "video_id", "label": "Video ID", "label_ru": "ID видео", "kind": "text"},
            {"id": "source_layer", "label": "Control layer 1", "label_ru": "Слой-контрол 1", "kind": "layer"},
            {"id": "lora", "label": "IC-LoRA 1", "label_ru": "IC-LoRA 1", "kind": "text", "default": "layout-to-render"},
            {"id": "source_layer2", "label": "Control layer 2", "label_ru": "Слой-контрол 2", "kind": "layer"},
            {"id": "lora2", "label": "IC-LoRA 2", "label_ru": "IC-LoRA 2", "kind": "text", "default": "union-control"},
            {"id": "ref_image", "label": "First frame", "label_ru": "Первый кадр", "kind": "image"},
            {"id": "prompt", "label": "Prompt", "label_ru": "Промпт", "kind": "text", "default": ""},
            {"id": "negative", "label": "Negative", "label_ru": "Негатив", "kind": "text", "default": ""},
            {"id": "seed", "label": "Seed", "label_ru": "Сид", "kind": "number", "default": 44},
            {"id": "start", "label": "Start frame", "label_ru": "Первый кадр (№)", "kind": "number", "default": 0},
            {"id": "end", "label": "End frame", "label_ru": "Последний кадр (№)", "kind": "number", "default": 96},
            {"id": "name", "label": "Layer name", "label_ru": "Имя слоя", "kind": "text", "default": "LTX IC-LoRA"}
        ],
        "outputs": [{"id": "layer", "label": "Generated layer", "label_ru": "Слой генерации", "kind": "layer"}]
    },
    {
        "id": "vl_composite",
        "name": "Composite on layers",
        "title_ru": "Композит слоёв",
        "description": "Актёр (fg) поверх фона (bg); alpha_source=fg берёт альфу из RGBA CorridorKey foreground",
        "category": "video_layers",
        "icon": "🧩",
        "video_job": "composite",
        "inputs": [
            {"id": "video_id", "label": "Video ID", "label_ru": "ID видео", "kind": "text"},
            {"id": "fg", "label": "Foreground layer", "label_ru": "Передний план (слой)", "kind": "text"},
            {"id": "bg", "label": "Background layer", "label_ru": "Фон (слой)", "kind": "layer"},
            {"id": "mask", "label": "Mask layer", "label_ru": "Маска (слой)", "kind": "text"},
            {"id": "alpha_source", "label": "Alpha source (mask / fg)", "label_ru": "Источник альфы (mask / fg)", "kind": "text", "default": "mask"},
            {"id": "despill", "label": "Despill (true / false)", "label_ru": "Деспилл (true / false)", "kind": "text", "default": "false"},
            {"id": "erode", "label": "Erode", "label_ru": "Эрозия", "kind": "number", "default": 2},
            {"id": "feather", "label": "Feather", "label_ru": "Растушёвка", "kind": "number", "default": 4},
            {"id": "harmonize", "label": "Harmonize", "label_ru": "Гармонизация", "kind": "number", "default": 0},
            {"id": "light_wrap", "label": "Light wrap", "label_ru": "Light wrap", "kind": "number", "default": 0},
            {"id": "name", "label": "Layer name", "label_ru": "Имя слоя", "kind": "text", "default": "Composite"}
        ],
        "outputs": [{"id": "layer", "label": "Composite layer", "label_ru": "Слой композита", "kind": "layer"}]
    },
    {
        "id": "vl_layer_to_video",
        "name": "Layer → MP4",
        "title_ru": "Слой → MP4",
        "description": "Экспорт слоя в видео (можно задать размер, например 960×540)",
        "category": "video_layers",
        "icon": "📼",
        "video_job": "layer_to_video",
        "inputs": [
            {"id": "video_id", "label": "Video ID", "label_ru": "ID видео", "kind": "text"},
            {"id": "layer_id", "label": "Layer", "label_ru": "Слой", "kind": "layer"},
            {"id": "width", "label": "Width (0 = plate)", "label_ru": "Ширина (0 = как плейт)", "kind": "number", "default": 0},
            {"id": "height", "label": "Height (0 = plate)", "label_ru": "Высота (0 = как плейт)", "kind": "number", "default": 0}
        ],
        "outputs": [{"id": "video", "label": "Video", "label_ru": "Видео", "kind": "video"}]
    },
]
VIDEO_JOB_STAGES = {s["id"]: s["video_job"] for s in BUILTIN_STAGES if s.get("video_job")}

# Default Presets offered out of the box
DEFAULT_PIPELINES = [
    {
        "id": "preset_flux_to_ltx",
        "name": "Scene Creation: FLUX.3 → LTX-2.5 Video",
        "name_ru": "Создание сцены: FLUX.3 Текст → LTX-2.5 Видео",
        "description": "Генерирует фотореалистичный кадр по промпту и превращает его в динамичный кинематографичный видеоролик.",
        "stages": [
            {
                "id": "stage_1",
                "title": "FLUX.3 Text-to-Image",
                "stage_type": "flux_t2i",
                "workflow": "api/storyboard/flux3_klein_t2i.json",
                "inputs": {
                    "prompt": "Cinematic heroic portrait of a wanderer in glowing sunset, volumetric light, 8k"
                }
            },
            {
                "id": "stage_2",
                "title": "LTX-2.5 Image-to-Video",
                "stage_type": "ltx25_i2v",
                "workflow": "api/video_gen/ltx25_i2v.json",
                "inputs": {
                    "start_image": {"from_stage": "stage_1", "output_key": "image"},
                    "prompt": "Dynamic camera push in, atmospheric wind and subtle movement",
                    "length": 49
                }
            }
        ]
    },
    {
        "id": "preset_portrait_to_speech",
        "name": "Talking Actor: FLUX.3 → Lip-Sync Speech",
        "name_ru": "Говорящий персонаж: FLUX.3 → Lip-Sync Речь",
        "description": "Создает персонажа и сразу оживляет его мимику и речь под аудиофайл.",
        "stages": [
            {
                "id": "stage_1",
                "title": "FLUX.3 Portrait",
                "stage_type": "flux_t2i",
                "workflow": "api/storyboard/flux3_klein_t2i.json",
                "inputs": {
                    "prompt": "Close-up portrait of a charismatic actor looking into camera, cinematic lighting, ultra detailed"
                }
            },
            {
                "id": "stage_2",
                "title": "Lip-Sync Audio-to-Video",
                "stage_type": "ltx25_a2v",
                "workflow": "voice/ltx25_a2v.json",
                "inputs": {
                    "start_image": {"from_stage": "stage_1", "output_key": "image"},
                    "audio": ""
                }
            }
        ]
    },
    {
        "id": "preset_layout_to_render_dubit",
        "name": "Complete Movie Shot: 3D Layout → LTX-2.5 → DubIt",
        "name_ru": "Полный кадр: 3D Лейаут → LTX Рендер → DubIt Дубляж",
        "description": "Превращает черновой 3D-лейаут в кинокадр и накладывает точный дубляж речи.",
        "stages": [
            {
                "id": "stage_1",
                "title": "LTX-2.5 Layout-to-Render",
                "stage_type": "ltx25_layout_to_render",
                "workflow": "api/video_fx/ltx25_ic_lora_v2v.json",
                "inputs": {
                    "source_video": "",
                    "ref_image": "",
                    "prompt": "Photorealistic cinematic movie shot, dramatic sunset lighting"
                }
            },
            {
                "id": "stage_2",
                "title": "DubIt Speech Lip-Sync",
                "stage_type": "ltx23_dubit",
                "workflow": "voice/ltx23_dubit.json",
                "inputs": {
                    "source_video": {"from_stage": "stage_1", "output_key": "video"},
                    "audio": "",
                    "prompt": "talking expressively and naturally"
                }
            }
        ]
    }
]


def list_builtin_stages() -> List[Dict[str, Any]]:
    return copy.deepcopy(BUILTIN_STAGES)


def _ensure_default_presets():
    for p in DEFAULT_PIPELINES:
        p_dir = os.path.join(PIPELINES_DIR, p["id"])
        p_file = os.path.join(p_dir, "pipeline.json")
        if not os.path.exists(p_file):
            os.makedirs(p_dir, exist_ok=True)
            with open(p_file, "w", encoding="utf-8") as f:
                json.dump(p, f, ensure_ascii=False, indent=2)


def list_pipelines() -> List[Dict[str, Any]]:
    """List all saved and preset user pipelines."""
    _ensure_default_presets()
    out = []
    if not os.path.exists(PIPELINES_DIR):
        return out
    for entry in os.listdir(PIPELINES_DIR):
        p_dir = os.path.join(PIPELINES_DIR, entry)
        if not os.path.isdir(p_dir):
            continue
        cfg_file = os.path.join(p_dir, "pipeline.json")
        if os.path.exists(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if isinstance(cfg, dict):
                        out.append(cfg)
            except Exception:
                pass
    out.sort(key=lambda x: str(x.get("name", "")).lower())
    return out


def get_pipeline(pipeline_id: str) -> Optional[Dict[str, Any]]:
    _ensure_default_presets()
    p_file = os.path.join(PIPELINES_DIR, pipeline_id, "pipeline.json")
    if os.path.exists(p_file):
        try:
            with open(p_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_pipeline(pipeline_def: Dict[str, Any]) -> Dict[str, Any]:
    pid = str(pipeline_def.get("id") or "").strip()
    if not pid:
        pid = f"pipe_{int(time.time())}"
        pipeline_def["id"] = pid
    p_dir = os.path.join(PIPELINES_DIR, pid)
    os.makedirs(p_dir, exist_ok=True)
    p_file = os.path.join(p_dir, "pipeline.json")
    with open(p_file, "w", encoding="utf-8") as f:
        json.dump(pipeline_def, f, ensure_ascii=False, indent=2)
    return {"status": "ok", "id": pid, "pipeline": pipeline_def}


def delete_pipeline(pipeline_id: str) -> Dict[str, Any]:
    p_dir = os.path.join(PIPELINES_DIR, pipeline_id)
    if os.path.exists(p_dir):
        shutil.rmtree(p_dir, ignore_errors=True)
    return {"status": "ok", "deleted": pipeline_id}


def run_pipeline(project_id: str, pipeline_def: Dict[str, Any], initial_values: Optional[Dict[str, Any]] = None) -> str:
    """Executes all stages of the pipeline sequentially, passing outputs into downstream inputs."""
    job_id = f"pjob_{uuid.uuid4().hex[:10]}"
    stages = pipeline_def.get("stages", [])
    if not stages:
        raise ProcessError("Pipeline has no stages defined")

    with _LOCK:
        _PIPELINE_JOBS[job_id] = {
            "id": job_id,
            "project_id": project_id,
            "pipeline_id": pipeline_def.get("id"),
            "pipeline_name": pipeline_def.get("name") or "Macro Pipeline",
            "status": "running",
            "progress": 0,
            "current_stage_idx": 0,
            "total_stages": len(stages),
            "stage_name": stages[0].get("title") or "Stage 1",
            "logs": [],
            "stage_results": {},
            "final_outputs": [],
            "error": None,
            "start_time": time.time(),
            "cancelled": False
        }

    t = threading.Thread(target=_execute_pipeline_worker, args=(job_id, project_id, pipeline_def, initial_values or {}), daemon=True)
    t.start()
    return job_id


def get_pipeline_job(job_id: str) -> Dict[str, Any]:
    with _LOCK:
        return copy.deepcopy(_PIPELINE_JOBS.get(job_id, {"status": "not_found"}))


def cancel_pipeline_job(job_id: str) -> Dict[str, Any]:
    with _LOCK:
        if job_id in _PIPELINE_JOBS:
            _PIPELINE_JOBS[job_id]["cancelled"] = True
            _PIPELINE_JOBS[job_id]["status"] = "cancelled"
            try:
                cancel_run(run_tag=job_id)
            except Exception:
                pass
            return {"status": "ok", "message": "Pipeline cancelled"}
    return {"status": "not_found"}


def _resolve_asset_path(raw_path: str, project_id: str) -> str:
    if not raw_path:
        return ""
    raw = str(raw_path).strip()
    if os.path.isabs(raw) and os.path.exists(raw):
        return raw

    # 1. 3D Layout Render fallback
    if raw.lower() in ("клинплейт", "baked_layout_sequence", "3d layout render", "layout", "3d", "раскадровка"):
        l_mp4 = os.path.join(BASE_DIR, "output_cache", "layout_renders", "layout_render.mp4")
        if os.path.exists(l_mp4):
            return l_mp4

    pdir = get_project_dir(project_id) if project_id else ""
    if raw.startswith("/vfx/project_file/"):
        parts = raw.split("/", 4)
        if len(parts) >= 5 and pdir:
            candidate = os.path.join(pdir, parts[4].replace("/", os.sep))
            if os.path.exists(candidate):
                return candidate
    if pdir:
        candidate2 = os.path.join(pdir, raw.replace("/", os.sep))
        if os.path.exists(candidate2):
            return candidate2

        # 2. Search project files by title or filename
        if os.path.exists(pdir):
            for root, _, files in os.walk(pdir):
                for fn in files:
                    if fn.lower() == raw.lower() or os.path.splitext(fn)[0].lower() == raw.lower():
                        return os.path.join(root, fn)
                    if raw.lower() in fn.lower():
                        return os.path.join(root, fn)

    # 3. Search across all projects under projects/ directory
    suite_projects = os.path.join(BASE_DIR, "projects")
    if os.path.exists(suite_projects):
        for root, _, files in os.walk(suite_projects):
            for fn in files:
                if fn.lower() == raw.lower() or os.path.splitext(fn)[0].lower() == raw.lower():
                    return os.path.join(root, fn)
                if raw.lower() in fn.lower():
                    return os.path.join(root, fn)

    return raw


def _run_video_job_stage(job_id: str, project_id: str, kind: str, inputs: Dict[str, Any], title: str, log) -> Dict[str, Any]:
    """Runs one 2D video-layer job and waits for it. Outputs: layer (id), image (path), video (path) + *_url."""
    try:
        from . import video_layers_service as vls
    except ImportError:  # pragma: no cover
        import video_layers_service as vls
    params = dict(inputs)
    vid = str(params.pop("video_id", "") or "").strip()
    if not vid:
        raise ProcessError(f"Stage '{title}': video_id is not set")
    for k, v in list(params.items()):   # numbers typed in the UI arrive as strings
        if isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
            params[k] = float(v) if "." in v else int(v)
    if kind in ("ltx_ic_lora", "ltx_alpha"):
        free_memory(wait_s=1.0)
    j = vls.start_job(kind, project_id, vid, params)
    vjob = j.get("id")
    log(f"  → 2D job '{kind}' started on {vid} ({vjob})")
    last = ""
    while True:
        with _LOCK:
            if _PIPELINE_JOBS.get(job_id, {}).get("cancelled"):
                vls.cancel_job(vjob)
                raise ProcessError("Pipeline cancelled by user")
        st = vls.get_job(vjob)
        msg = str(st.get("message") or "")
        if msg and msg != last and st.get("status") == "running":
            last = msg
            with _LOCK:
                _PIPELINE_JOBS[job_id]["stage_name"] = f"{title}: {msg}"
        if st.get("status") == "done":
            break
        if st.get("status") in ("error", "cancelled", "not_found"):
            raise ProcessError(f"Stage '{title}' failed: {st.get('message') or st.get('status')}")
        time.sleep(1.5)

    res = st.get("result") or {}
    out: Dict[str, Any] = {"video_id": vid}
    layer = res.get("layer")
    if isinstance(layer, dict) and layer.get("id"):
        out["layer"] = layer["id"]
        log(f"  ✓ Layer: {layer['id']} ({layer.get('name', '')})")
    if res.get("image"):
        out["image"] = res["image"]
    video = res.get("video")
    if isinstance(video, dict) and video.get("url"):
        out["video_url"] = video["url"]
        try:
            out["video"] = vls.resolve_media_path(video["url"])
        except Exception:
            out["video"] = video["url"]
        log(f"  ✓ Video: {video['url']}")
    if len(out) == 1:
        raise ProcessError(f"Stage '{title}' completed but produced nothing")
    return out


def _execute_pipeline_worker(job_id: str, project_id: str, pipeline_def: Dict[str, Any], initial_values: Dict[str, Any]):
    import folder_paths

    stages = pipeline_def.get("stages", [])
    total = len(stages)
    stage_artifacts: Dict[str, Dict[str, Any]] = {}
    pdir = get_project_dir(project_id)
    out_dir = os.path.join(pdir, "pipelines", job_id)
    os.makedirs(out_dir, exist_ok=True)

    def log(msg: str):
        with _LOCK:
            if job_id in _PIPELINE_JOBS and isinstance(_PIPELINE_JOBS[job_id].get("logs"), list):
                _PIPELINE_JOBS[job_id]["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    try:
        log(f"Starting pipeline '{pipeline_def.get('name')}' ({total} stages)...")

        for idx, stage in enumerate(stages):
            with _LOCK:
                if _PIPELINE_JOBS[job_id].get("cancelled"):
                    log("Pipeline execution aborted by user.")
                    return
                _PIPELINE_JOBS[job_id]["current_stage_idx"] = idx
                _PIPELINE_JOBS[job_id]["stage_name"] = stage.get("title") or f"Stage {idx+1}"
                _PIPELINE_JOBS[job_id]["progress"] = int((idx / total) * 100)

            st_id = stage.get("id") or f"stage_{idx+1}"
            st_title = stage.get("title") or f"Stage {idx+1}"
            log(f"▶ [{idx+1}/{total}] Running {st_title}...")

            # 1. Resolve inputs for this stage
            resolved_inputs: Dict[str, Any] = {}
            for inp_key, inp_cfg in (stage.get("inputs") or {}).items():
                if isinstance(inp_cfg, dict) and "from_stage" in inp_cfg:
                    src_st = inp_cfg["from_stage"]
                    src_key = inp_cfg.get("output_key", "image")
                    prev_val = stage_artifacts.get(src_st, {}).get(src_key)
                    if prev_val:
                        resolved_inputs[inp_key] = prev_val
                        log(f"  ↳ Input '{inp_key}' received from {src_st}.{src_key}: {os.path.basename(str(prev_val))}")
                    else:
                        log(f"  ⚠ Warning: Input '{inp_key}' from {src_st}.{src_key} was empty")
                elif isinstance(inp_cfg, dict) and "value" in inp_cfg:
                    resolved_inputs[inp_key] = inp_cfg["value"]
                elif inp_cfg not in (None, ""):
                    resolved_inputs[inp_key] = inp_cfg

            # User overrides from modal
            for k, v in (initial_values.get(st_id) or {}).items():
                if v not in (None, ""):
                    resolved_inputs[k] = v

            # ${name} -> pipeline_def["variables"][name] (e.g. one video_id for every layer stage)
            pvars = pipeline_def.get("variables") or {}
            for k, v in list(resolved_inputs.items()):
                if isinstance(v, str) and "${" in v:
                    resolved_inputs[k] = re.sub(r"\$\{(\w+)\}", lambda m: str(pvars.get(m.group(1), m.group(0))), v)

            # 2D video-layer job stage (no ComfyUI prompt of its own; the job may queue ComfyUI itself)
            _st_type0 = stage.get("stage_type") or "custom"
            vjob_kind = VIDEO_JOB_STAGES.get(_st_type0) or (resolved_inputs.pop("kind", None) if _st_type0 == "video_job" else None)
            if vjob_kind:
                stage_out = _run_video_job_stage(job_id, project_id, vjob_kind, resolved_inputs, st_title, log)
                stage_artifacts[st_id] = stage_out
                with _LOCK:
                    _PIPELINE_JOBS[job_id]["stage_results"][st_id] = stage_out
                log(f"✓ [{idx+1}/{total}] {st_title} completed successfully.")
                continue

            st_wf = stage.get("workflow") or ""
            st_type = stage.get("stage_type") or "custom"

            # 2. Build or load prompt
            prompt_data = None
            if st_wf.endswith(".json"):
                candidates = [
                    os.path.join(BASE_DIR, "workflows", st_wf),
                    os.path.join(BASE_DIR, "workflows", "api", st_wf),
                    os.path.join(BASE_DIR, "workflows", "api", os.path.basename(st_wf)),
                ]
                for wf_path in candidates:
                    if os.path.exists(wf_path):
                        try:
                            with open(wf_path, "r", encoding="utf-8") as f:
                                loaded = json.load(f)
                            if isinstance(loaded, dict) and "api_prompt" in loaded and _is_api_prompt(loaded["api_prompt"]):
                                prompt_data = loaded["api_prompt"]
                                break
                            elif isinstance(loaded, dict) and "prompt" in loaded and _is_api_prompt(loaded["prompt"]):
                                prompt_data = loaded["prompt"]
                                break
                            elif _is_api_prompt(loaded):
                                prompt_data = loaded
                                break
                        except Exception:
                            pass

            if not prompt_data or not _is_api_prompt(prompt_data):
                # Built-in stage fallback loaders (ensure strict API format)
                if st_type in ("ltx25_layout_to_render", "layout_to_render") or "layout" in st_wf.lower() or "ic_lora" in st_wf.lower():
                    prompt_data = load_template("video_fx", "ltx25_ic_lora_v2v")
                elif st_type == "flux_t2i" or "flux3_klein_t2i" in st_wf:
                    prompt_data = load_template("storyboard", "flux3_klein_t2i")
                elif st_type == "ltx25_i2v" or "ltx25_i2v" in st_wf:
                    prompt_data = load_template("video_gen", "ltx25_i2v")
                elif st_type == "ltx23_dubit" or "ltx23_dubit" in st_wf:
                    prompt_data = load_template("voice", "ltx23_dubit")
                elif st_type == "ltx25_a2v" or "ltx25_a2v" in st_wf:
                    prompt_data = load_template("voice", "ltx25_a2v")
                elif st_type == "trellis_3d" or "trellis2_pixal3d" in st_wf:
                    prompt_data = load_template("mesh_gen", "trellis2_pixal3d")
                elif st_type == "rembg_matte":
                    prompt_data = load_template("deconstruct", "sam3_text_mask_image")
                else:
                    raise ProcessError(f"Cannot find API workflow template for stage '{st_title}' ({st_wf})")

            # 3. Inject inputs into ComfyUI prompt nodes
            injected_prompt = copy.deepcopy(prompt_data)
            input_dir = folder_paths.get_input_directory()
            os.makedirs(input_dir, exist_ok=True)

            # Image resolution & copy
            clean_img = ""
            img_val = resolved_inputs.get("start_image") or resolved_inputs.get("image") or resolved_inputs.get("ref_image")
            if img_val:
                src_img = _resolve_asset_path(img_val, project_id)
                if src_img and os.path.exists(src_img):
                    ext = os.path.splitext(src_img)[1] or ".png"
                    clean_img = f"keshiki_pipe_{st_id}_img_{job_id[:6]}{ext}"
                    try:
                        shutil.copyfile(src_img, os.path.join(input_dir, clean_img))
                    except Exception:
                        clean_img = src_img
                else:
                    clean_img = str(img_val)

            # Video resolution & copy
            clean_vid = ""
            vid_val = resolved_inputs.get("source_video") or resolved_inputs.get("video")
            if vid_val:
                src_vid = _resolve_asset_path(vid_val, project_id)
                if src_vid and os.path.exists(src_vid):
                    ext = os.path.splitext(src_vid)[1] or ".mp4"
                    clean_vid = f"keshiki_pipe_{st_id}_vid_{job_id[:6]}{ext}"
                    try:
                        shutil.copyfile(src_vid, os.path.join(input_dir, clean_vid))
                    except Exception:
                        clean_vid = src_vid
                else:
                    clean_vid = str(vid_val)

            # Audio resolution & copy
            clean_aud = ""
            aud_val = resolved_inputs.get("audio") or resolved_inputs.get("audio_file") or resolved_inputs.get("speech")
            if aud_val:
                src_aud = _resolve_asset_path(aud_val, project_id)
                if src_aud and os.path.exists(src_aud):
                    ext = os.path.splitext(src_aud)[1] or ".wav"
                    clean_aud = f"keshiki_pipe_{st_id}_aud_{job_id[:6]}{ext}"
                    try:
                        shutil.copyfile(src_aud, os.path.join(input_dir, clean_aud))
                    except Exception:
                        clean_aud = src_aud
                else:
                    clean_aud = str(aud_val)

            p_val = str(resolved_inputs.get("prompt", "")).strip()

            for nid, node in injected_prompt.items():
                if not isinstance(node, dict):
                    continue
                ctype = node.get("class_type", "")
                inputs = node.setdefault("inputs", {})
                meta_title = ((node.get("_meta") or {}).get("title") or "").lower()

                # Text prompt injection
                if p_val:
                    if ctype in ("CLIPTextEncode", "FluxTextEncode", "LTXVPredict") or "prompt" in meta_title:
                        if "text" in inputs and not meta_title.startswith("negative"):
                            inputs["text"] = p_val
                        elif "prompt" in inputs:
                            inputs["prompt"] = p_val
                        elif "value" in inputs:
                            inputs["value"] = p_val
                    elif ctype == "PrimitiveStringMultiline" and "prompt" in meta_title and not meta_title.startswith("negative"):
                        if "value" in inputs:
                            inputs["value"] = p_val
                        elif "string" in inputs:
                            inputs["string"] = p_val

                # Numeric sampler / scale settings (seed, steps, megapixels) when the stage sets them
                for nk in ("seed", "steps", "megapixels"):
                    if nk in inputs and resolved_inputs.get(nk) not in (None, "") and not isinstance(inputs[nk], list):
                        try:
                            inputs[nk] = float(resolved_inputs[nk]) if nk == "megapixels" else int(float(resolved_inputs[nk]))
                        except (TypeError, ValueError):
                            pass

                # Image injection
                if clean_img and (ctype == "LoadImage" or "load image" in meta_title or "start image" in meta_title):
                    inputs["image"] = clean_img

                # Video injection
                if clean_vid and (ctype in ("VHS_LoadVideo", "LoadVideo") or "video" in meta_title or "source video" in meta_title):
                    if "file" in inputs or ctype == "LoadVideo":
                        inputs["file"] = clean_vid
                    if "video" in inputs or ctype != "LoadVideo":
                        inputs["video"] = clean_vid

                # Audio injection
                if clean_aud and (ctype in ("LoadAudio", "VHS_LoadAudio") or "audio" in meta_title or "speech" in meta_title):
                    if "audio" in inputs:
                        inputs["audio"] = clean_aud
                    if "file" in inputs:
                        inputs["file"] = clean_aud

            # Auto-heal model checkpoint / VAE / LoRA names against installed ComfyUI models
            try:
                try:
                    from .workflow_inspector import heal_and_validate_prompt
                except ImportError:
                    from workflow_inspector import heal_and_validate_prompt
                injected_prompt, _repairs = heal_and_validate_prompt(injected_prompt)
            except Exception:
                pass

            # 4. Queue run in ComfyUI
            free_memory(wait_s=1.0)
            q = run_process("pipeline_stage", prompt=injected_prompt, client_id="keshiki_pipelines", run_tag=job_id)
            prompt_id = q["prompt_id"]
            log(f"  → Queued in ComfyUI (prompt_id: {prompt_id[:8]}...)")

            # 5. Poll until stage completion
            t0 = time.time()
            st = None
            while True:
                if _PIPELINE_JOBS[job_id].get("cancelled"):
                    raise ProcessError("Pipeline cancelled by user")
                st = get_run_status(prompt_id)
                status = st.get("status")
                if status == "done":
                    break
                if status == "error":
                    msgs = [e.get("exception_message", "").strip() for e in (st.get("errors") or []) if isinstance(e, dict)]
                    err_text = "; ".join(m for m in msgs if m) or "ComfyUI execution error"
                    raise ProcessError(f"Stage '{st_title}' failed: {err_text}")
                if status == "cancelled":
                    raise ProcessError(f"Stage '{st_title}' was cancelled")

                # Update stage percent
                elapsed = int(time.time() - t0)
                sub_progress = int((idx / total) * 100) + int((1.0 / total) * 50)
                with _LOCK:
                    _PIPELINE_JOBS[job_id]["progress"] = min(99, sub_progress)
                time.sleep(1.5)

            # 6. Extract stage outputs
            stage_out: Dict[str, Any] = {}
            stamp = time.strftime("%Y%m%d_%H%M%S")
            for o in st.get("outputs", []):
                base = {"temp": folder_paths.get_temp_directory(), "input": folder_paths.get_input_directory()}.get(
                    o.get("type", "output"), folder_paths.get_output_directory())
                src = os.path.join(base, o.get("subfolder", ""), o["filename"])
                if not os.path.isfile(src):
                    continue
                name = f"{st_id}_{stamp}_{o['filename']}"
                dst = os.path.join(out_dir, name)
                shutil.copy2(src, dst)
                ext = os.path.splitext(name)[1].lower()
                kind = "video" if ext in (".mp4", ".webm", ".mov", ".gif") else "audio" if ext in (".wav", ".mp3", ".flac", ".ogg") else "3d" if ext in (".glb", ".gltf", ".obj") else "image"
                asset_url = f"/vfx/project_file/{project_id}/pipelines/{job_id}/{name}"
                stage_out[kind] = dst
                stage_out[f"{kind}_url"] = asset_url
                log(f"  ✓ Generated {kind}: {name}")

            if not stage_out:
                raise ProcessError(f"Stage '{st_title}' completed but produced no output files")

            stage_artifacts[st_id] = stage_out
            with _LOCK:
                _PIPELINE_JOBS[job_id]["stage_results"][st_id] = stage_out

            log(f"✓ [{idx+1}/{total}] {st_title} completed successfully in {int(time.time() - t0)}s.")

        # Finished all stages!
        with _LOCK:
            _PIPELINE_JOBS[job_id]["progress"] = 100
            _PIPELINE_JOBS[job_id]["status"] = "done"
            last_stage_id = stages[-1].get("id") or f"stage_{total}"
            _PIPELINE_JOBS[job_id]["final_outputs"] = stage_artifacts.get(last_stage_id, {})
        log(f"🎉 Pipeline '{pipeline_def.get('name')}' fully completed all {total} stages!")

    except Exception as ex:
        log(f"❌ Pipeline failed: {ex}")
        with _LOCK:
            _PIPELINE_JOBS[job_id]["status"] = "error"
            _PIPELINE_JOBS[job_id]["error"] = str(ex)
