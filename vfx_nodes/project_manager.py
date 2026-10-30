"""
VFX Studio Project & Global Library Manager
===========================================
Manages:
1. Structured Project Directories:
   projects/<project_id>/
     ├── project.json (Metadata, Storyboard graph, Layer stack, Node graph, Chat history)
     ├── storyboard/  (Flux generated frames & reference images)
     ├── characters/  (Project-specific character overrides)
     ├── audio_tts/   (Dialogue WAVs, translated tracks)
     ├── source_video/(Generated LTX-2.5 base shots or ingested plates)
     ├── passes/      (Extracted Tracking, SAM2, CorridorKey, LTX Alpha LoRA, Depth, Canny, Pose)
     ├── vfx_renders/ (3D Layout renders, inpainted shots, lip-sync renders)
     ├── music/       (ACE-Step and MiniMax generated soundtracks)
     └── exports/     (Final composites, .chan camera trajectories, EXR/MP4 outputs)

2. Global Cross-Project Libraries:
   global_library/
     ├── characters/  (Trained Character LoRAs, dataset previews, trigger words)
     └── voices/      (Saved TTS voice profiles, cloned voice samples, dialogue presets)
"""

import os
import json
import time
import shutil
import re
import base64
import logging
from typing import Optional, List, Dict, Any

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

LOGGER = logging.getLogger("keshiki.project_manager")


def _log_suppressed(err: Exception) -> None:
    """Non-fatal errors used to be swallowed silently; keep going but leave a trace in the log."""
    tb = err.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    line = tb.tb_lineno if tb is not None else "?"
    LOGGER.warning("[ProjectManager] non-fatal error (line %s): %r", line, err)


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_ROOT = os.path.join(BASE_DIR, "projects")
PROJECTS_REGISTRY_FILE = os.path.join(PROJECTS_ROOT, "projects_registry.json")
GLOBAL_LIB_ROOT = os.path.join(BASE_DIR, "global_library")
GLOBAL_CHARS_DIR = os.path.join(GLOBAL_LIB_ROOT, "characters")
GLOBAL_LOCS_DIR = os.path.join(GLOBAL_LIB_ROOT, "locations")
GLOBAL_VOICES_DIR = os.path.join(GLOBAL_LIB_ROOT, "voices")

PROJECT_SUBDIRS = [
    "storyboard",
    "characters",
    "locations",
    "audio_tts",
    "source_video",
    "passes",
    "splats",
    "vfx_renders",
    "music",
    "exports",
    "autosaves"
]

os.makedirs(PROJECTS_ROOT, exist_ok=True)
os.makedirs(GLOBAL_CHARS_DIR, exist_ok=True)
os.makedirs(GLOBAL_LOCS_DIR, exist_ok=True)
os.makedirs(GLOBAL_VOICES_DIR, exist_ok=True)


def _sanitize_id(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_\-\u0400-\u04FF]", "_", name.strip())
    return clean or f"project_{int(time.time())}"


def load_projects_registry() -> dict:
    """Loads map of registered projects: { project_id: { id, name, path, mode, updated_at } }."""
    if os.path.exists(PROJECTS_REGISTRY_FILE):
        try:
            with open(PROJECTS_REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as _e:
            _log_suppressed(_e)
    return {}


def save_projects_registry(registry: dict):
    """Persists projects registry to projects/projects_registry.json."""
    try:
        os.makedirs(os.path.dirname(PROJECTS_REGISTRY_FILE), exist_ok=True)
        with open(PROJECTS_REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[VFX ProjectManager] Failed saving registry: {e}")


def register_project(pid: str, p_dir: str, name: str = None, mode: str = "production"):
    """Registers or updates a project's disk location in the global registry."""
    clean_pid = _sanitize_id(pid)
    reg = load_projects_registry()
    reg[clean_pid] = {
        "id": clean_pid,
        "name": name or clean_pid,
        "path": os.path.abspath(p_dir),
        "mode": mode,
        "updated_at": time.time()
    }
    save_projects_registry(reg)


def get_project_dir(project_id: str) -> str:
    """
    Resolves the absolute root directory of a project.
    Prioritizes projects_registry.json, then PROJECTS_ROOT, then direct path if valid.
    """
    if not project_id:
        return os.path.join(PROJECTS_ROOT, "sandbox_playground")

    clean_pid = _sanitize_id(project_id)
    reg = load_projects_registry()
    if clean_pid in reg:
        reg_path = reg[clean_pid].get("path")
        if reg_path and os.path.exists(reg_path):
            return os.path.abspath(reg_path)

    default_dir = os.path.join(PROJECTS_ROOT, clean_pid)
    if os.path.exists(default_dir):
        return default_dir

    if os.path.isabs(project_id) and os.path.isdir(project_id):
        return os.path.abspath(project_id)

    return default_dir


def ensure_default_global_libraries():
    """Seeds default global voices and starter characters if empty."""
    voices_manifest = os.path.join(GLOBAL_VOICES_DIR, "voices_index.json")
    if not os.path.exists(voices_manifest):
        default_voices = [
            {
                "id": "dmitry_ru",
                "name": "Дмитрий (Кино-баритон RU)",
                "engine": "edge_tts",
                "voice_code": "dmitry",
                "lang": "ru",
                "tags": ["мужской", "драма", "диктор"]
            },
            {
                "id": "svetlana_ru",
                "name": "Светлана (Мягкий женский RU)",
                "engine": "edge_tts",
                "voice_code": "svetlana",
                "lang": "ru",
                "tags": ["женский", "лирика", "диалог"]
            },
            {
                "id": "christopher_en",
                "name": "Christopher (Hollywood Trailer EN)",
                "engine": "edge_tts",
                "voice_code": "christopher",
                "lang": "en",
                "tags": ["male", "cinematic", "deep"]
            },
            {
                "id": "jenny_en",
                "name": "Jenny (Natural Dialogue EN)",
                "engine": "edge_tts",
                "voice_code": "jenny",
                "lang": "en",
                "tags": ["female", "clear", "protagonist"]
            },
            {
                "id": "guy_en",
                "name": "Guy (Action Hero EN)",
                "engine": "edge_tts",
                "voice_code": "guy",
                "lang": "en",
                "tags": ["male", "action", "dynamic"]
            }
        ]
        with open(voices_manifest, "w", encoding="utf-8") as f:
            json.dump(default_voices, f, indent=2, ensure_ascii=False)

    chars_manifest = os.path.join(GLOBAL_CHARS_DIR, "characters_index.json")
    if not os.path.exists(chars_manifest):
        default_chars = [
            {
                "id": "char_cyber_stalker",
                "name": "Кael (Cyberpunk Stalker)",
                "trigger_word": "kael_stalker_vfx",
                "lora_flux": "flux_kael_stalker_v1.safetensors",
                "lora_ltx": "ltx25_kael_stalker_v1.safetensors",
                "status": "ready",
                "preview_url": "",
                "description": "Главный герой в потертом неоновом плаще, шрам на левой брови"
            },
            {
                "id": "char_sci_fi_pilot",
                "name": "Ева (Пилот Космофлота)",
                "trigger_word": "eva_pilot_vfx",
                "lora_flux": "flux_eva_pilot_v1.safetensors",
                "lora_ltx": "ltx25_eva_pilot_v1.safetensors",
                "status": "ready",
                "preview_url": "",
                "description": "Девушка-пилот в серебристом скафандре, короткая стрижка"
            }
        ]
        with open(chars_manifest, "w", encoding="utf-8") as f:
            json.dump(default_chars, f, indent=2, ensure_ascii=False)

    locs_manifest = os.path.join(GLOBAL_LOCS_DIR, "locations_index.json")
    if not os.path.exists(locs_manifest):
        default_locs = [
            {
                "id": "loc_neon_city",
                "name": "Neon Megacity Rooftop",
                "trigger_word": "loc_neon_city_vfx",
                "lora_flux": "flux_loc_neon_city_v1.safetensors",
                "lora_ltx": "ltx25_loc_neon_city_v1.safetensors",
                "status": "ready",
                "preview_url": "",
                "description": "Rain-slicked cyberpunk high-rise rooftop with towering neon holograms"
            },
            {
                "id": "loc_desert_citadel",
                "name": "Desert Oasis Citadel",
                "trigger_word": "loc_desert_citadel_vfx",
                "lora_flux": "flux_loc_desert_citadel_v1.safetensors",
                "lora_ltx": "ltx25_loc_desert_citadel_v1.safetensors",
                "status": "ready",
                "preview_url": "",
                "description": "Ancient sandstone fortress amidst vast golden dunes, sunset lighting"
            }
        ]
        with open(locs_manifest, "w", encoding="utf-8") as f:
            json.dump(default_locs, f, indent=2, ensure_ascii=False)


ensure_default_global_libraries()


def list_projects() -> list:
    """Returns a list of all existing projects (both default and custom disk paths) sorted by last modified time."""
    projects_map = {}

    # 1. Registered projects from projects_registry.json
    reg = load_projects_registry()
    for pid, info in list(reg.items()):
        p_path = info.get("path")
        if p_path and os.path.exists(p_path):
            meta_file = os.path.join(p_path, "project.json")
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    projects_map[pid] = {
                        "id": meta.get("id", pid),
                        "name": meta.get("name", info.get("name", pid)),
                        "mode": meta.get("mode", info.get("mode", "production")),
                        "updated_at": meta.get("updated_at", os.path.getmtime(meta_file)),
                        "path": os.path.abspath(p_path),
                        "shots_count": len(meta.get("storyboard", {}).get("cards", [])),
                        "is_custom_path": os.path.abspath(p_path) != os.path.abspath(os.path.join(PROJECTS_ROOT, pid))
                    }
                    continue
                except Exception as _e:
                    _log_suppressed(_e)
            projects_map[pid] = {
                "id": pid,
                "name": info.get("name", pid),
                "mode": info.get("mode", "production"),
                "updated_at": info.get("updated_at", os.path.getmtime(p_path)),
                "path": os.path.abspath(p_path),
                "shots_count": 0,
                "is_custom_path": os.path.abspath(p_path) != os.path.abspath(os.path.join(PROJECTS_ROOT, pid))
            }

    # 2. Local PROJECTS_ROOT projects (fallback / discovery)
    if os.path.exists(PROJECTS_ROOT):
        for item in os.listdir(PROJECTS_ROOT):
            p_dir = os.path.join(PROJECTS_ROOT, item)
            if os.path.isdir(p_dir) and item != "__pycache__":
                clean_pid = _sanitize_id(item)
                if clean_pid not in projects_map:
                    meta_file = os.path.join(p_dir, "project.json")
                    if os.path.exists(meta_file):
                        try:
                            with open(meta_file, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                            projects_map[clean_pid] = {
                                "id": meta.get("id", clean_pid),
                                "name": meta.get("name", clean_pid),
                                "mode": meta.get("mode", "production"),
                                "updated_at": meta.get("updated_at", os.path.getmtime(meta_file)),
                                "path": os.path.abspath(p_dir),
                                "shots_count": len(meta.get("storyboard", {}).get("cards", [])),
                                "is_custom_path": False
                            }
                        except Exception as _e:
                            _log_suppressed(_e)
                    else:
                        projects_map[clean_pid] = {
                            "id": clean_pid,
                            "name": item,
                            "mode": "production",
                            "updated_at": os.path.getmtime(p_dir),
                            "path": os.path.abspath(p_dir),
                            "shots_count": 0,
                            "is_custom_path": False
                        }

    projects = list(projects_map.values())
    projects.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return projects


def create_or_open_project(name: str, mode: str = "production", project_id: str = None, custom_path: str = None) -> dict:
    """
    Creates a new project with full folder hierarchy or opens an existing one.
    mode can be 'production' or 'sandbox' (для баловства).
    custom_path allows the user to specify any folder on any disk (e.g. D:\\Projects\\MyFilm).
    """
    if mode == "sandbox" and not name:
        name = "Sandbox (Quick Start)"
        project_id = "sandbox_playground"

    pid = project_id or _sanitize_id(name)

    if custom_path and mode != "sandbox":
        c_clean = os.path.abspath(str(custom_path).strip().rstrip("\\/"))
        base_name = os.path.basename(c_clean).lower()
        if base_name in (pid.lower(), _sanitize_id(name).lower()) or (os.path.exists(c_clean) and os.path.exists(os.path.join(c_clean, "project.json"))):
            p_dir = c_clean
        else:
            p_dir = os.path.join(c_clean, pid)
    else:
        p_dir = get_project_dir(pid)

    os.makedirs(p_dir, exist_ok=True)

    for sub in PROJECT_SUBDIRS:
        os.makedirs(os.path.join(p_dir, sub), exist_ok=True)

    meta_file = os.path.join(p_dir, "project.json")
    if os.path.exists(meta_file):
        data = None
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                while content.endswith("}}"):
                    content = content[:-1]
                data = json.loads(content)
        except Exception as _e:
            _log_suppressed(_e)
            import glob
            for a in sorted(glob.glob(os.path.join(p_dir, "autosaves", "autosave_*.json")), key=os.path.getmtime, reverse=True):
                try:
                    with open(a, "r", encoding="utf-8") as af:
                        data = json.load(af)
                        break
                except Exception:
                    pass
        if data:
            data["updated_at"] = time.time()
            data["path"] = p_dir
            data["folders"] = {sub: os.path.join(p_dir, sub) for sub in PROJECT_SUBDIRS}
            if not data.get("chatHistory"):
                ch = get_project_chat_history(pid)
                if ch:
                    data["chatHistory"] = ch
            tmp_meta = meta_file + ".tmp"
            with open(tmp_meta, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_meta, meta_file)
            register_project(pid, p_dir, name=data.get("name", name), mode=data.get("mode", mode))
            return data

    # Initialize default structured project state
    initial_state = {
        "id": pid,
        "name": name or pid,
        "mode": mode,
        "created_at": time.time(),
        "updated_at": time.time(),
        "path": p_dir,
        "folders": {sub: os.path.join(p_dir, sub) for sub in PROJECT_SUBDIRS},
        "agent_config": {
            "provider": "ollama",
            "model_name": "",
            "api_url": "http://127.0.0.1:11434/api/chat"
        },
        "storyboard": {
            "cards": [
                {
                    "id": "shot_01",
                    "title": "Shot 1: Opening Scene",
                    "x": 120,
                    "y": 140,
                    "story_note": "Protagonist standing on rain-slicked rooftop edge looking at giant city hologram.",
                    "flux_prompt": "Cinematic wide shot, cyberpunk protagonist standing on rain-slicked rooftop edge looking at giant city hologram, moody teal and orange lighting, 35mm anamorphic lens",
                    "image_url": "",
                    "character_id": "char_cyber_stalker",
                    "duration_sec": 4.0
                },
                {
                    "id": "shot_02",
                    "title": "Shot 2: Close-up & Dialogue",
                    "x": 520,
                    "y": 140,
                    "story_note": "Camera pushes in smoothly on protagonist face as key dialogue line is spoken.",
                    "flux_prompt": "Close-up portrait of cyberpunk protagonist in rain, neon reflections on wet skin, intense expression, shallow depth of field, 85mm prime lens",
                    "image_url": "",
                    "character_id": "char_cyber_stalker",
                    "duration_sec": 3.5
                }
            ],
            "links": [
                {"from": "shot_01", "to": "shot_02"}
            ]
        },
        "deconstruction": {
            "tracking_glomap": True,
            "mask_sam2_rough": True,
            "mask_corridor_key": True,
            "mask_ltx_alpha_lora": True,
            "pass_depth": True,
            "pass_canny": True,
            "pass_pose": True,
            "extracted_passes": []
        },
        "compositing": {
            "view_mode": "layers",  # 'layers' (Layer Stack) or 'nodes' (Node Graph)
            "layers": [
                {"id": "lyr_fx", "name": "Atmosphere / Foreground (LTX Alpha)", "type": "fx", "blend": "Screen", "opacity": 0.85, "visible": True},
                {"id": "lyr_actor", "name": "Protagonist (CorridorKey + Lipsync)", "type": "fg", "blend": "Normal", "opacity": 1.0, "visible": True},
                {"id": "lyr_mid", "name": "3D Scene Context (Layout Render)", "type": "3d", "blend": "Normal", "opacity": 1.0, "visible": True},
                {"id": "lyr_bg", "name": "Cinematic Background (LTX-2.5 Environment)", "type": "bg", "blend": "Normal", "opacity": 1.0, "visible": True}
            ],
            "nodes": [
                {"id": "nd_plate", "title": "Actor Plate (CorridorKey)", "type": "input", "x": 80, "y": 80, "outs": ["nd_relight"]},
                {"id": "nd_bg", "title": "LTX-2.5 Background", "type": "input", "x": 80, "y": 240, "outs": ["nd_zmerge"]},
                {"id": "nd_alpha", "title": "LTX Alpha LoRA Matte", "type": "mask", "x": 80, "y": 400, "outs": ["nd_zmerge"]},
                {"id": "nd_relight", "title": "AI Relight & Reinhard", "type": "process", "x": 360, "y": 120, "outs": ["nd_zmerge"]},
                {"id": "nd_zmerge", "title": "Z-Depth & LightWrap Merge", "type": "merge", "x": 640, "y": 220, "outs": ["nd_out"]},
                {"id": "nd_out", "title": "Final Master Output", "type": "output", "x": 920, "y": 220, "outs": []}
            ]
        },
        "music": {
            "engine": "ace_step",
            "prompt": "Dark atmospheric cyberpunk synthwave, slow pulsing bass, rain ambience, Hans Zimmer style",
            "duration_sec": 15.0,
            "tracks": []
        },
        "mediaSplats": []
    }

    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(initial_state, f, indent=2, ensure_ascii=False)

    register_project(pid, p_dir, name=name or pid, mode=mode)
    return initial_state


def save_project_state(project_id: str, state_updates: dict) -> dict:
    pid = _sanitize_id(project_id)
    p_dir = get_project_dir(pid)
    meta_file = os.path.join(p_dir, "project.json")
    current = None
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                while content.endswith("}}"):
                    content = content[:-1]
                current = json.loads(content)
        except Exception:
            import glob
            for a in sorted(glob.glob(os.path.join(p_dir, "autosaves", "autosave_*.json")), key=os.path.getmtime, reverse=True):
                try:
                    with open(a, "r", encoding="utf-8") as af:
                        current = json.load(af)
                        break
                except Exception:
                    pass
    if not current:
        current = create_or_open_project(project_id, project_id=project_id)

    for k, v in state_updates.items():
        current[k] = v
    if "chatHistory" in state_updates:
        save_project_chat_history(pid, state_updates["chatHistory"])
    current["updated_at"] = time.time()
    current["path"] = p_dir
    current["folders"] = {sub: os.path.join(p_dir, sub) for sub in PROJECT_SUBDIRS}

    tmp_meta = meta_file + ".tmp"
    with open(tmp_meta, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    os.replace(tmp_meta, meta_file)

    register_project(pid, p_dir, name=current.get("name", pid), mode=current.get("mode", "production"))
    return current


def get_project_chat_history(project_id: str) -> list:
    """Load persistent chat history for the project from chat_history.json or project.json."""
    pid = _sanitize_id(project_id)
    p_dir = get_project_dir(pid)
    chat_file = os.path.join(p_dir, "chat_history.json")
    if os.path.exists(chat_file):
        try:
            with open(chat_file, "r", encoding="utf-8") as f:
                hist = json.load(f)
                if isinstance(hist, list):
                    return hist
        except Exception as _e:
            _log_suppressed(_e)
    meta_file = os.path.join(p_dir, "project.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                hist = meta.get("chatHistory") or meta.get("chat_history")
                if isinstance(hist, list):
                    return hist
        except Exception as _e:
            _log_suppressed(_e)
    return []


def save_project_chat_history(project_id: str, history: list) -> bool:
    """Save persistent chat history for the project to chat_history.json and sync to project.json."""
    pid = _sanitize_id(project_id)
    p_dir = get_project_dir(pid)
    os.makedirs(p_dir, exist_ok=True)
    trimmed = history[-200:] if isinstance(history, list) else []
    chat_file = os.path.join(p_dir, "chat_history.json")
    try:
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, indent=2, ensure_ascii=False)
    except Exception as _e:
        _log_suppressed(_e)
        return False
    meta_file = os.path.join(p_dir, "project.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["chatHistory"] = trimmed
            meta["updated_at"] = time.time()
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
        except Exception as _e:
            _log_suppressed(_e)
    return True


def clear_project_chat_history(project_id: str) -> bool:
    """Clear chat history file and remove chatHistory from project metadata."""
    pid = _sanitize_id(project_id)
    p_dir = get_project_dir(pid)
    chat_file = os.path.join(p_dir, "chat_history.json")
    if os.path.exists(chat_file):
        try:
            os.remove(chat_file)
        except Exception as _e:
            _log_suppressed(_e)
    meta_file = os.path.join(p_dir, "project.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if "chatHistory" in meta or "chat_history" in meta:
                meta.pop("chatHistory", None)
                meta.pop("chat_history", None)
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2, ensure_ascii=False)
        except Exception as _e:
            _log_suppressed(_e)
    return True


def save_project_autosave(project_id: str, state: dict, max_autosaves: int = 10) -> dict:
    pid = _sanitize_id(project_id)
    p_dir = get_project_dir(pid)
    autosaves_dir = os.path.join(p_dir, "autosaves")
    os.makedirs(autosaves_dir, exist_ok=True)

    timestamp = int(time.time())
    filename = f"autosave_{timestamp}.json"
    filepath = os.path.join(autosaves_dir, filename)

    snapshot = dict(state) if isinstance(state, dict) else {}
    snapshot["autosave_timestamp"] = timestamp
    snapshot["autosave_date_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    # Rotate autosaves: keep only the newest max_autosaves
    existing = []
    for item in os.listdir(autosaves_dir):
        if item.startswith("autosave_") and item.endswith(".json"):
            fp = os.path.join(autosaves_dir, item)
            try:
                mtime = os.path.getmtime(fp)
                existing.append((mtime, fp))
            except Exception as _e:
                _log_suppressed(_e)
    existing.sort(key=lambda x: x[0])

    while len(existing) > max(1, int(max_autosaves)):
        oldest = existing.pop(0)
        try:
            os.remove(oldest[1])
        except Exception as _e:
            _log_suppressed(_e)

    return {
        "ok": True,
        "filename": filename,
        "timestamp": timestamp,
        "date_str": snapshot["autosave_date_str"],
        "count": len(existing)
    }


def list_project_autosaves(project_id: str) -> list:
    pid = _sanitize_id(project_id)
    p_dir = get_project_dir(pid)
    autosaves_dir = os.path.join(p_dir, "autosaves")
    if not os.path.exists(autosaves_dir):
        return []

    saves = []
    for item in os.listdir(autosaves_dir):
        if item.startswith("autosave_") and item.endswith(".json"):
            fp = os.path.join(autosaves_dir, item)
            try:
                mtime = os.path.getmtime(fp)
                size = os.path.getsize(fp)
                date_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
                saves.append({
                    "filename": item,
                    "timestamp": mtime,
                    "date_str": date_str,
                    "size": size
                })
            except Exception as _e:
                _log_suppressed(_e)
    saves.sort(key=lambda x: x["timestamp"], reverse=True)
    return saves


def restore_project_autosave(project_id: str, filename: str) -> dict:
    pid = _sanitize_id(project_id)
    p_dir = get_project_dir(pid)
    autosaves_dir = os.path.join(p_dir, "autosaves")
    filepath = os.path.normpath(os.path.join(autosaves_dir, filename))
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Autosave {filename} not found")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta_file = os.path.join(p_dir, "project.json")
    data["updated_at"] = time.time()
    data["path"] = p_dir
    data["folders"] = {sub: os.path.join(p_dir, sub) for sub in PROJECT_SUBDIRS}
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return data


def save_project_as(source_project_id: str, new_name: str, state: dict = None, custom_path: str = None) -> dict:
    new_pid = _sanitize_id(new_name)
    src_pid = _sanitize_id(source_project_id)
    src_dir = get_project_dir(src_pid)

    if custom_path:
        c_clean = os.path.abspath(str(custom_path).strip().rstrip("\\/"))
        if os.path.basename(c_clean).lower() == new_pid.lower():
            dst_dir = c_clean
        else:
            dst_dir = os.path.join(c_clean, new_pid)
    else:
        src_parent = os.path.dirname(src_dir)
        if os.path.exists(src_parent) and os.path.abspath(src_parent) != os.path.abspath(PROJECTS_ROOT):
            dst_dir = os.path.join(src_parent, new_pid)
        else:
            dst_dir = os.path.join(PROJECTS_ROOT, new_pid)

    os.makedirs(dst_dir, exist_ok=True)
    for sub in PROJECT_SUBDIRS:
        os.makedirs(os.path.join(dst_dir, sub), exist_ok=True)

    # Copy media assets from source project if available
    if os.path.exists(src_dir) and src_dir != dst_dir:
        for sub in ("storyboard", "source_video", "characters", "locations", "audio_tts"):
            s_sub = os.path.join(src_dir, sub)
            d_sub = os.path.join(dst_dir, sub)
            if os.path.exists(s_sub):
                for f in os.listdir(s_sub):
                    sf = os.path.join(s_sub, f)
                    df = os.path.join(d_sub, f)
                    if os.path.isfile(sf) and not os.path.exists(df):
                        try:
                            shutil.copy2(sf, df)
                        except Exception as _e:
                            _log_suppressed(_e)

    # Build project.json
    meta_file = os.path.join(dst_dir, "project.json")
    if state and isinstance(state, dict):
        new_data = dict(state)
    elif os.path.exists(os.path.join(src_dir, "project.json")):
        with open(os.path.join(src_dir, "project.json"), "r", encoding="utf-8") as f:
            new_data = json.load(f)
    else:
        new_data = create_or_open_project(new_name, project_id=new_pid, custom_path=custom_path)

    new_data["id"] = new_pid
    new_data["name"] = new_name
    new_data["created_at"] = time.time()
    new_data["updated_at"] = time.time()
    new_data["path"] = dst_dir
    new_data["folders"] = {sub: os.path.join(dst_dir, sub) for sub in PROJECT_SUBDIRS}

    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False)

    register_project(new_pid, dst_dir, name=new_name, mode=new_data.get("mode", "production"))
    return new_data


def get_global_characters() -> list:
    chars_manifest = os.path.join(GLOBAL_CHARS_DIR, "characters_index.json")
    if os.path.exists(chars_manifest):
        try:
            with open(chars_manifest, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as _e:
            _log_suppressed(_e)
    return []


def add_global_character(char_data: dict) -> list:
    chars = get_global_characters()
    cid = char_data.get("id") or f"char_{int(time.time())}"
    char_dir = os.path.join(GLOBAL_CHARS_DIR, cid)
    os.makedirs(char_dir, exist_ok=True)
    os.makedirs(os.path.join(char_dir, "dataset_raw"), exist_ok=True)
    os.makedirs(os.path.join(char_dir, "dataset_formatted_512"), exist_ok=True)
    os.makedirs(os.path.join(char_dir, "weights"), exist_ok=True)

    new_entry = {
        "id": cid,
        "name": char_data.get("name", "New Character"),
        "trigger_word": char_data.get("trigger_word", f"{cid}_vfx"),
        "lora_flux": f"flux_{cid}.safetensors",
        "lora_ltx": f"ltx25_{cid}.safetensors",
        "status": char_data.get("status", "ready"),
        "preview_url": char_data.get("preview_url", ""),
        "description": char_data.get("description", ""),
        "folder": char_dir
    }
    chars.insert(0, new_entry)
    chars_manifest = os.path.join(GLOBAL_CHARS_DIR, "characters_index.json")
    with open(chars_manifest, "w", encoding="utf-8") as f:
        json.dump(chars, f, indent=2, ensure_ascii=False)
    return chars


def get_global_voices() -> list:
    voices_manifest = os.path.join(GLOBAL_VOICES_DIR, "voices_index.json")
    if os.path.exists(voices_manifest):
        try:
            with open(voices_manifest, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as _e:
            _log_suppressed(_e)
    return []


def add_global_voice(voice_data: dict) -> list:
    voices = get_global_voices()
    vid = voice_data.get("id") or f"voice_{int(time.time())}"
    new_v = {
        "id": vid,
        "name": voice_data.get("name", "Custom Voice"),
        "engine": voice_data.get("engine", "edge_tts"),
        "voice_code": voice_data.get("voice_code", "dmitry"),
        "lang": voice_data.get("lang", "ru"),
        "tags": voice_data.get("tags", ["custom"])
    }
    voices.insert(0, new_v)
    voices_manifest = os.path.join(GLOBAL_VOICES_DIR, "voices_index.json")
    with open(voices_manifest, "w", encoding="utf-8") as f:
        json.dump(voices, f, indent=2, ensure_ascii=False)
    return voices


def convert_character_dataset_batch(char_id: str, files_list: list, target_size: int = 1024, trigger_word: str = "") -> dict:
    """
    Standardizes a batch of raw character photos/concepts:
    - Ingests multiple formats (PNG, JPG, WEBP, etc.)
    - Center-square crops or standardizes aspect ratios
    - Resizes with high-quality Lanczos/Area interpolation to target_size x target_size (e.g. 1024x1024)
    - Normalizes RGBA to solid RGB
    - Auto-generates initial descriptive caption txt files
    - Returns standardized items with preview data URLs for the character board canvas
    """
    cid = char_id or "char_default"
    char_dir = os.path.join(GLOBAL_CHARS_DIR, cid)
    os.makedirs(char_dir, exist_ok=True)
    raw_dir = os.path.join(char_dir, "dataset_raw")
    fmt_dir = os.path.join(char_dir, "dataset_formatted_512")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(fmt_dir, exist_ok=True)

    chars = get_global_characters()
    char_meta = next((c for c in chars if c.get("id") == cid), None)
    trigger = trigger_word or (char_meta.get("trigger_word") if char_meta else f"{cid}_vfx") or "sks_character"
    char_name = (char_meta.get("name") if char_meta else cid) or "Character"

    converted_items = []
    ts = int(time.time())

    for idx, f_item in enumerate(files_list):
        orig_name = f_item.get("filename") or f"upload_{idx}.jpg"
        raw_bytes = f_item.get("bytes")
        if not raw_bytes:
            continue

        clean_base = re.sub(r"[^a-zA-Z0-9_\-.]", "_", orig_name)
        raw_path = os.path.join(raw_dir, f"{ts}_{idx:03d}_{clean_base}")
        try:
            with open(raw_path, "wb") as f:
                f.write(raw_bytes)
        except Exception as _e:
            _log_suppressed(_e)

        orig_w, orig_h = 0, 0
        preview_data_url = ""
        std_filename = f"ds_{ts}_{idx:03d}.jpg"
        std_path = os.path.join(fmt_dir, std_filename)
        std_txt_path = os.path.join(fmt_dir, f"ds_{ts}_{idx:03d}.txt")

        caption = f"{trigger}, portrait of {char_name}, cinematic composition, 85mm lens, sharp focus, natural texture"

        if cv2 is not None and np is not None:
            try:
                np_arr = np.frombuffer(raw_bytes, np.uint8)
                img = cv2.imdecode(np_arr, cv2.IMREAD_UNCHANGED)
                if img is not None:
                    orig_h, orig_w = img.shape[:2]

                    if img.ndim == 3 and img.shape[2] == 4:
                        alpha = img[:, :, 3].astype(np.float32) / 255.0
                        bgr = img[:, :, :3].astype(np.float32)
                        bg = np.ones_like(bgr, dtype=np.float32) * 235.0
                        img = (bgr * alpha[:, :, None] + bg * (1.0 - alpha[:, :, None])).astype(np.uint8)
                    elif img.ndim == 2:
                        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

                    h, w = img.shape[:2]
                    dim = min(h, w)
                    y0 = (h - dim) // 2
                    x0 = (w - dim) // 2
                    cropped = img[y0:y0+dim, x0:x0+dim]

                    interp = cv2.INTER_AREA if dim >= target_size else cv2.INTER_LANCZOS4
                    standardized = cv2.resize(cropped, (target_size, target_size), interpolation=interp)

                    cv2.imwrite(std_path, standardized, [int(cv2.IMWRITE_JPEG_QUALITY), 95])

                    preview_thumb = cv2.resize(standardized, (280, 280), interpolation=cv2.INTER_AREA)
                    _, enc_buf = cv2.imencode(".jpg", preview_thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
                    b64_str = base64.b64encode(enc_buf.tobytes()).decode("ascii")
                    preview_data_url = f"data:image/jpeg;base64,{b64_str}"
            except Exception as _e:
                _log_suppressed(_e)

        try:
            with open(std_txt_path, "w", encoding="utf-8") as f:
                f.write(caption)
        except Exception as _e:
            _log_suppressed(_e)

        converted_items.append({
            "id": f"cnode_ds_{ts}_{idx}",
            "filename": std_filename,
            "url": f"/vfx/character/{cid}/dataset/file/{std_filename}",
            "preview_url": preview_data_url or f"/vfx/character/{cid}/dataset/file/{std_filename}",
            "original_size": f"{orig_w}x{orig_h}" if orig_w and orig_h else "Auto",
            "converted_size": f"{target_size}x{target_size}",
            "approved": True,
            "caption": caption,
            "prompt": caption,
            "title": f"Dataset #{idx + 1} ({target_size}x{target_size})"
        })

    return {
        "status": "success",
        "char_id": cid,
        "trigger_word": trigger,
        "converted_count": len(converted_items),
        "items": converted_items
    }


def start_character_lora_training(char_id: str, trigger_word: str = "", approved_items: list = None, base_model: str = "flux", steps: int = 1200) -> dict:
    """
    Prepares dataset package with verified captions and registers a training job for the character.
    """
    cid = char_id or "char_default"
    char_dir = os.path.join(GLOBAL_CHARS_DIR, cid)
    os.makedirs(char_dir, exist_ok=True)
    fmt_dir = os.path.join(char_dir, "dataset_formatted_512")
    os.makedirs(fmt_dir, exist_ok=True)

    approved = approved_items or []
    for item in approved:
        fn = item.get("filename")
        cap = item.get("caption") or item.get("prompt")
        if fn and cap:
            base_txt = os.path.splitext(fn)[0] + ".txt"
            txt_path = os.path.join(fmt_dir, base_txt)
            try:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(cap.strip())
            except Exception as _e:
                _log_suppressed(_e)

    manifest = {
        "char_id": cid,
        "trigger_word": trigger_word,
        "base_model": base_model,
        "steps": steps,
        "approved_count": len(approved),
        "timestamp": time.time(),
        "status": "training",
        "output_lora": f"flux_{trigger_word}_v1.safetensors",
        "items": [
            {
                "id": it.get("id"),
                "filename": it.get("filename"),
                "caption": it.get("caption") or it.get("prompt")
            }
            for it in approved
        ]
    }
    with open(os.path.join(char_dir, "training_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    chars = get_global_characters()
    for c in chars:
        if c.get("id") == cid:
            c["status"] = "training"
            c["dataset_count"] = len(approved)
            c["last_trained"] = time.time()
            break
    chars_manifest = os.path.join(GLOBAL_CHARS_DIR, "characters_index.json")
    with open(chars_manifest, "w", encoding="utf-8") as f:
        json.dump(chars, f, indent=2, ensure_ascii=False)

    return {
        "status": "training_started",
        "char_id": cid,
        "trigger_word": trigger_word,
        "approved_count": len(approved),
        "output_lora": f"flux_{trigger_word}_v1.safetensors",
        "message": f"LoRA training started for {cid} with {len(approved)} approved dataset samples."
    }


def get_global_locations() -> list:
    locs_manifest = os.path.join(GLOBAL_LOCS_DIR, "locations_index.json")
    if os.path.exists(locs_manifest):
        try:
            with open(locs_manifest, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as _e:
            _log_suppressed(_e)
    return []


def add_global_location(loc_data: dict) -> list:
    locs = get_global_locations()
    lid = loc_data.get("id") or f"loc_{int(time.time())}"
    loc_dir = os.path.join(GLOBAL_LOCS_DIR, lid)
    os.makedirs(loc_dir, exist_ok=True)
    os.makedirs(os.path.join(loc_dir, "dataset_raw"), exist_ok=True)
    os.makedirs(os.path.join(loc_dir, "dataset_formatted_512"), exist_ok=True)
    os.makedirs(os.path.join(loc_dir, "weights"), exist_ok=True)

    new_entry = {
        "id": lid,
        "name": loc_data.get("name", "New Location"),
        "trigger_word": loc_data.get("trigger_word", f"{lid}_vfx"),
        "lora_flux": f"flux_{lid}.safetensors",
        "lora_ltx": f"ltx25_{lid}.safetensors",
        "status": loc_data.get("status", "ready"),
        "preview_url": loc_data.get("preview_url", ""),
        "description": loc_data.get("description", ""),
        "folder": loc_dir
    }
    locs.insert(0, new_entry)
    locs_manifest = os.path.join(GLOBAL_LOCS_DIR, "locations_index.json")
    with open(locs_manifest, "w", encoding="utf-8") as f:
        json.dump(locs, f, indent=2, ensure_ascii=False)
    return locs


def convert_location_dataset_batch(loc_id: str, files_list: list, target_size: int = 1024, trigger_word: str = "") -> dict:
    lid = loc_id or "loc_default"
    loc_dir = os.path.join(GLOBAL_LOCS_DIR, lid)
    os.makedirs(loc_dir, exist_ok=True)
    raw_dir = os.path.join(loc_dir, "dataset_raw")
    fmt_dir = os.path.join(loc_dir, "dataset_formatted_512")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(fmt_dir, exist_ok=True)

    locs = get_global_locations()
    loc_meta = next((l for l in locs if l.get("id") == lid), None)
    trigger = trigger_word or (loc_meta.get("trigger_word") if loc_meta else f"{lid}_vfx") or "loc_world_vfx"
    loc_name = (loc_meta.get("name") if loc_meta else lid) or "Location"

    converted_items = []
    ts = int(time.time())

    for idx, f_item in enumerate(files_list):
        orig_name = f_item.get("filename") or f"upload_{idx}.jpg"
        raw_bytes = f_item.get("bytes")
        if not raw_bytes:
            continue

        clean_base = re.sub(r"[^a-zA-Z0-9_\-.]", "_", orig_name)
        raw_path = os.path.join(raw_dir, f"{ts}_{idx:03d}_{clean_base}")
        try:
            with open(raw_path, "wb") as f:
                f.write(raw_bytes)
        except Exception as _e:
            _log_suppressed(_e)

        orig_w, orig_h = 0, 0
        preview_data_url = ""
        std_filename = f"loc_ds_{ts}_{idx:03d}.jpg"
        std_path = os.path.join(fmt_dir, std_filename)
        std_txt_path = os.path.join(fmt_dir, f"loc_ds_{ts}_{idx:03d}.txt")

        caption = f"{trigger}, cinematic matte painting of {loc_name}, wide panoramic vista, atmospheric depth, realistic environmental lighting"

        if cv2 is not None and np is not None:
            try:
                np_arr = np.frombuffer(raw_bytes, np.uint8)
                img = cv2.imdecode(np_arr, cv2.IMREAD_UNCHANGED)
                if img is not None:
                    orig_h, orig_w = img.shape[:2]

                    if img.ndim == 3 and img.shape[2] == 4:
                        alpha = img[:, :, 3].astype(np.float32) / 255.0
                        bgr = img[:, :, :3].astype(np.float32)
                        bg = np.ones_like(bgr, dtype=np.float32) * 235.0
                        img = (bgr * alpha[:, :, None] + bg * (1.0 - alpha[:, :, None])).astype(np.uint8)
                    elif img.ndim == 2:
                        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

                    h, w = img.shape[:2]
                    dim = min(h, w)
                    y0 = (h - dim) // 2
                    x0 = (w - dim) // 2
                    cropped = img[y0:y0+dim, x0:x0+dim]

                    interp = cv2.INTER_AREA if dim >= target_size else cv2.INTER_LANCZOS4
                    standardized = cv2.resize(cropped, (target_size, target_size), interpolation=interp)

                    cv2.imwrite(std_path, standardized, [int(cv2.IMWRITE_JPEG_QUALITY), 95])

                    preview_thumb = cv2.resize(standardized, (280, 280), interpolation=cv2.INTER_AREA)
                    _, enc_buf = cv2.imencode(".jpg", preview_thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
                    b64_str = base64.b64encode(enc_buf.tobytes()).decode("ascii")
                    preview_data_url = f"data:image/jpeg;base64,{b64_str}"
            except Exception as _e:
                _log_suppressed(_e)

        try:
            with open(std_txt_path, "w", encoding="utf-8") as f:
                f.write(caption)
        except Exception as _e:
            _log_suppressed(_e)

        converted_items.append({
            "id": f"lnode_ds_{ts}_{idx}",
            "filename": std_filename,
            "url": f"/vfx/location/{lid}/dataset/file/{std_filename}",
            "preview_url": preview_data_url or f"/vfx/location/{lid}/dataset/file/{std_filename}",
            "original_size": f"{orig_w}x{orig_h}" if orig_w and orig_h else "Auto",
            "converted_size": f"{target_size}x{target_size}",
            "approved": True,
            "caption": caption,
            "prompt": caption,
            "title": f"Location #{idx + 1} ({target_size}x{target_size})"
        })

    return {
        "status": "success",
        "loc_id": lid,
        "trigger_word": trigger,
        "converted_count": len(converted_items),
        "items": converted_items
    }


def start_location_lora_training(loc_id: str, trigger_word: str = "", approved_items: list = None, base_model: str = "flux", steps: int = 1200) -> dict:
    lid = loc_id or "loc_default"
    loc_dir = os.path.join(GLOBAL_LOCS_DIR, lid)
    os.makedirs(loc_dir, exist_ok=True)
    fmt_dir = os.path.join(loc_dir, "dataset_formatted_512")
    os.makedirs(fmt_dir, exist_ok=True)

    approved = approved_items or []
    for item in approved:
        fn = item.get("filename")
        cap = item.get("caption") or item.get("prompt")
        if fn and cap:
            base_txt = os.path.splitext(fn)[0] + ".txt"
            txt_path = os.path.join(fmt_dir, base_txt)
            try:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(cap.strip())
            except Exception as _e:
                _log_suppressed(_e)

    manifest = {
        "loc_id": lid,
        "trigger_word": trigger_word,
        "base_model": base_model,
        "steps": steps,
        "approved_count": len(approved),
        "timestamp": time.time(),
        "status": "training",
        "output_lora": f"flux_{trigger_word}_v1.safetensors",
        "items": [
            {
                "id": it.get("id"),
                "filename": it.get("filename"),
                "caption": it.get("caption") or it.get("prompt")
            }
            for it in approved
        ]
    }
    with open(os.path.join(loc_dir, "training_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    locs = get_global_locations()
    for l in locs:
        if l.get("id") == lid:
            l["status"] = "training"
            l["dataset_count"] = len(approved)
            l["last_trained"] = time.time()
            break
    locs_manifest = os.path.join(GLOBAL_LOCS_DIR, "locations_index.json")
    with open(locs_manifest, "w", encoding="utf-8") as f:
        json.dump(locs, f, indent=2, ensure_ascii=False)

    return {
        "status": "training_started",
        "loc_id": lid,
        "trigger_word": trigger_word,
        "approved_count": len(approved),
        "output_lora": f"flux_{trigger_word}_v1.safetensors",
        "message": f"Location LoRA training started for {lid} with {len(approved)} approved dataset samples."
    }


def delete_character_lora(char_id: str) -> dict:
    chars = get_global_characters()
    for c in chars:
        if c.get("id") == char_id:
            c["status"] = "untrained"
            c["lora_flux"] = ""
            c["lora_ltx"] = ""
            c["last_trained"] = None
            break
    chars_manifest = os.path.join(GLOBAL_CHARS_DIR, "characters_index.json")
    with open(chars_manifest, "w", encoding="utf-8") as f:
        json.dump(chars, f, indent=2, ensure_ascii=False)
    w_dir = os.path.join(GLOBAL_CHARS_DIR, char_id, "weights")
    if os.path.exists(w_dir):
        for f in os.listdir(w_dir):
            if f.endswith(".safetensors") or f.endswith(".bin"):
                try:
                    os.remove(os.path.join(w_dir, f))
                except Exception as _e:
                    _log_suppressed(_e)
    return {"status": "deleted", "char_id": char_id, "characters": chars}


def delete_location_lora(loc_id: str) -> dict:
    locs = get_global_locations()
    for l in locs:
        if l.get("id") == loc_id:
            l["status"] = "untrained"
            l["lora_flux"] = ""
            l["lora_ltx"] = ""
            l["last_trained"] = None
            break
    locs_manifest = os.path.join(GLOBAL_LOCS_DIR, "locations_index.json")
    with open(locs_manifest, "w", encoding="utf-8") as f:
        json.dump(locs, f, indent=2, ensure_ascii=False)
    w_dir = os.path.join(GLOBAL_LOCS_DIR, loc_id, "weights")
    if os.path.exists(w_dir):
        for f in os.listdir(w_dir):
            if f.endswith(".safetensors") or f.endswith(".bin"):
                try:
                    os.remove(os.path.join(w_dir, f))
                except Exception as _e:
                    _log_suppressed(_e)
    return {"status": "deleted", "loc_id": loc_id, "locations": locs}


def _get_comfyui_output_dir() -> Optional[str]:
    try:
        import folder_paths
        if hasattr(folder_paths, "get_output_directory"):
            return folder_paths.get_output_directory()
    except Exception:
        pass
    for cand in (
        r"B:\StabilityMatrix2\ComfyUI\output",
        r"B:\ComfyUI\output",
        r"C:\ComfyUI\output"
    ):
        if os.path.isdir(cand):
            return cand
    return None


def delete_project_asset(project_id: str, asset_id: str = None, url: str = None, filename: str = None, sub: str = None) -> dict:
    """
    Permanently deletes an asset from a project:
    1. Removes the physical file from the project directory on disk (storyboard, source_video, audio, etc.).
    2. Removes any associated layers directory (projects/<project_id>/layers/<asset_id>).
    3. Cleans up references in project.json (mediaImages1D, mediaVideos2D, mediaAudio, mediaScenes3D, mediaTexts, mediaSplats).
    """
    pid = _sanitize_id(project_id)
    p_dir = get_project_dir(pid)
    removed_files = []

    # 1. Determine file paths to delete on disk
    subpath_candidates = []
    url_fn = None
    url_sub = None
    if url and "?" in url:
        try:
            from urllib.parse import parse_qs, urlparse
            pq = parse_qs(urlparse(url).query)
            if "filename" in pq and pq["filename"]:
                url_fn = pq["filename"][0]
            if "subfolder" in pq and pq["subfolder"]:
                url_sub = pq["subfolder"][0]
        except Exception:
            pass

    target_fn = url_fn or (os.path.basename(filename) if filename and filename != "view" else None)
    target_sub = sub or url_sub or ""

    if target_sub and target_fn:
        subpath_candidates.append(os.path.join(target_sub, target_fn))
    if url:
        pattern = f"/vfx/project_file/{pid}/"
        if pattern in url:
            rel = url.split(pattern, 1)[1].split("?")[0]
            subpath_candidates.append(rel)
    if target_fn:
        for s in ("storyboard", "source_video", "audio", "characters", "locations", "splats", "vfx_renders", "exports"):
            subpath_candidates.append(os.path.join(s, target_fn))

    if url and not target_fn:
        url_fn_raw = os.path.basename(url.split("?")[0])
        if url_fn_raw and "." in url_fn_raw and url_fn_raw != "view":
            for s in ("storyboard", "source_video", "audio", "characters", "locations", "splats", "vfx_renders", "exports"):
                subpath_candidates.append(os.path.join(s, url_fn_raw))

    seen = set()
    for sp in subpath_candidates:
        if sp in seen:
            continue
        seen.add(sp)
        target = os.path.normpath(os.path.join(p_dir, sp))
        if os.path.commonpath([p_dir, target]) == p_dir and os.path.isfile(target):
            try:
                os.remove(target)
                removed_files.append(sp)
            except Exception as _e:
                _log_suppressed(_e)

    # 1b. Check and delete from ComfyUI output directory if asset is a generated output
    if target_fn:
        try:
            out_base = _get_comfyui_output_dir()
            if out_base and os.path.isdir(out_base):
                for s_cand in (target_sub, "keshiki", ""):
                    c_target = os.path.normpath(os.path.join(out_base, s_cand or "", target_fn))
                    if os.path.commonpath([out_base, c_target]) == out_base and os.path.isfile(c_target):
                        try:
                            os.remove(c_target)
                            removed_files.append(f"comfyui_output/{s_cand}/{target_fn}" if s_cand else f"comfyui_output/{target_fn}")
                        except Exception as _e:
                            _log_suppressed(_e)
        except Exception as _e:
            _log_suppressed(_e)

    # 2. Check layers directory
    if asset_id:
        layers_dir = os.path.normpath(os.path.join(p_dir, "layers", str(asset_id)))
        if os.path.commonpath([p_dir, layers_dir]) == p_dir and os.path.isdir(layers_dir):
            try:
                shutil.rmtree(layers_dir, ignore_errors=True)
                removed_files.append(f"layers/{asset_id}")
            except Exception as _e:
                _log_suppressed(_e)

    # 3. Clean up project.json
    meta_file = os.path.join(p_dir, "project.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                while content.endswith("}}"):
                    content = content[:-1]
                data = json.loads(content)

            def _match(item):
                if not isinstance(item, dict):
                    return False
                if asset_id and str(item.get("id")) == str(asset_id):
                    return True
                if url:
                    item_u = item.get("url") or item.get("videoUrl") or item.get("audioUrl") or item.get("glbUrl") or item.get("ply_url")
                    if item_u:
                        if item_u == url:
                            return True
                        if "/view?" in item_u and "/view?" in url:
                            try:
                                from urllib.parse import parse_qs, urlparse
                                q1 = parse_qs(urlparse(item_u).query).get("filename", [""])[0]
                                q2 = parse_qs(urlparse(url).query).get("filename", [""])[0]
                                if q1 and q2 and q1 == q2:
                                    return True
                            except Exception:
                                pass
                        elif "?" not in item_u and "?" not in url and item_u.split("?")[0] == url.split("?")[0]:
                            return True
                if target_fn:
                    item_fn = item.get("filename")
                    if not item_fn:
                        raw_u = str(item.get("url") or item.get("videoUrl") or item.get("audioUrl") or "")
                        if "filename=" in raw_u:
                            try:
                                from urllib.parse import parse_qs, urlparse
                                item_fn = parse_qs(urlparse(raw_u).query).get("filename", [""])[0]
                            except Exception:
                                pass
                        if not item_fn:
                            b = os.path.basename(raw_u.split("?")[0])
                            if b != "view":
                                item_fn = b
                    if item_fn and item_fn == target_fn:
                        return True
                return False

            changed = False
            for key in ("mediaImages1D", "mediaVideos2D", "mediaAudio", "mediaScenes3D", "mediaTexts", "mediaSplats"):
                if key in data and isinstance(data[key], list):
                    orig_len = len(data[key])
                    data[key] = [x for x in data[key] if not _match(x)]
                    if len(data[key]) != orig_len:
                        changed = True

            if changed:
                data["updated_at"] = time.time()
                tmp_meta = meta_file + ".tmp"
                with open(tmp_meta, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_meta, meta_file)
        except Exception as _e:
            _log_suppressed(_e)

    return {
        "success": True,
        "deleted": True,
        "project_id": pid,
        "asset_id": asset_id,
        "removed_files": removed_files
    }


