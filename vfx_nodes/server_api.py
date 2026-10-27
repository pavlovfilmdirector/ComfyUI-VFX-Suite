"""
ComfyUI Server Endpoints for Keshiki (景色) VFX AI Studio
======================================================
Architectural Overview:
- Runs inside ComfyUI's native `aiohttp` event loop (`PromptServer.instance`).
- All CPU/IO-intensive OpenCV, SAM2, CorridorKey, and LLM/TTS calls are offloaded via
  `asyncio.to_thread(...)` so 4K frame streaming and WebGL viewport requests never block.

Endpoint Groups:
1. `/vfx/studio/*`               : Standalone Keshiki Google Stitch Web Studio.
2. `/vfx/scene_data`             : Solved 4x4 camera matrices, FOV, and 3D LiDAR pointcloud (GLOMAP).
3. `/vfx/save_layout_batch`      : Multi-pass WebGL baking (Depth / Normal / Mask) + `layout_manifest.json`.
4. `/vfx/mesh3d/*`               : Async 3D asset generation bridge (`Hunyuan3D-Omni` BBox & `TRELLIS.2-4B`).
5. `/vfx/plate_prep/{node_id}/*` : Non-destructive 2D pipeline (Raw -> Undistort -> SAM2 -> CorridorKey -> Paint).
6. `/vfx/pipeline/*`             : Master Cinematic Cocktail orchestrator (LTX-2.3 22B + IC-LoRA + TTS).
"""

import os
import re
import sys
import json
import shutil
import base64
import asyncio
import logging
import cv2
import numpy as np
from aiohttp import web

try:
    from .process_runner import COMFY_URL as _COMFY_URL
except Exception:  # pragma: no cover - direct execution
    try:
        from process_runner import COMFY_URL as _COMFY_URL
    except Exception:
        _COMFY_URL = os.environ.get("KESHIKI_COMFY_URL", "http://127.0.0.1:8188")

LOGGER = logging.getLogger("ComfyUI-VFX-Suite")

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output_cache")
ACTOR_CUTOUT_DIR = os.path.join(CACHE_DIR, "actor_cutout")
LAYOUT_RENDER_DIR = os.path.join(CACHE_DIR, "layout_renders")
os.makedirs(LAYOUT_RENDER_DIR, exist_ok=True)

# Suppress harmless Windows asyncio ProactorEventLoop ConnectionResetError (WinError 10054) when browser closes sockets
if sys.platform == "win32":
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        if not getattr(_ProactorBasePipeTransport, "_vfx_patched", False):
            _orig_conn_lost = _ProactorBasePipeTransport._call_connection_lost

            def _silent_call_connection_lost(self, exc):
                try:
                    _orig_conn_lost(self, exc)
                except ConnectionResetError:
                    pass

            _ProactorBasePipeTransport._call_connection_lost = _silent_call_connection_lost
            _ProactorBasePipeTransport._vfx_patched = True
    except Exception:
        pass




def add_cors_headers(response):
    # No wildcard CORS: the Studio is served from the same origin as the API, and "*" would let
    # any website the user visits drive the local server.
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def register_server_routes():
    try:
        from server import PromptServer
        if not hasattr(PromptServer, "instance") or PromptServer.instance is None:
            return

        # --- UNIVERSAL HARDWARE VRAM ARBITRATION HOOK ON COMFYUI CORE ---
        try:
            import comfy.model_management
            if not getattr(comfy.model_management, "_vfx_evict_hooked", False):
                _orig_load_models_gpu = comfy.model_management.load_models_gpu

                def _vfx_safe_load_models_gpu(*args, **kwargs):
                    try:
                        import subprocess
                        # Terminate any active llama-server process instantly before allocating GPU for diffusion
                        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True, timeout=2.0)
                    except Exception:
                        pass
                    try:
                        import gc
                        import torch
                        gc.collect()
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except Exception:
                        pass
                    return _orig_load_models_gpu(*args, **kwargs)

                comfy.model_management.load_models_gpu = _vfx_safe_load_models_gpu
                comfy.model_management._vfx_evict_hooked = True
                print("[ComfyUI-VFX-Suite] Universal Hardware VRAM Hook installed on comfy.model_management.load_models_gpu")
        except Exception as _hook_err:
            print(f"[ComfyUI-VFX-Suite] Note: Failed to install model_management hook: {_hook_err}")

        routes = web.RouteTableDef()

        try:
            try:
                from .plate_prep_service import (
                    get_info, save_info, load_input_source, apply_undistortion,
                    apply_auto_mask, apply_thickness, apply_manual_paint,
                    save_uploaded_video, save_uploaded_sequence, auto_calibrate_distortion,
                    clear_frame_mask, reset_all_masks, interpolate_keyframes,
                    generate_tinted_mask_overlay_png, get_dirs,
                    save_uploaded_mask_video, save_uploaded_mask_sequence, save_uploaded_mask_single,
                    apply_corridor_key
                )
            except (ImportError, ValueError):
                from vfx_nodes.plate_prep_service import (
                    get_info, save_info, load_input_source, apply_undistortion,
                    apply_auto_mask, apply_thickness, apply_manual_paint,
                    save_uploaded_video, save_uploaded_sequence, auto_calibrate_distortion,
                    clear_frame_mask, reset_all_masks, interpolate_keyframes,
                    generate_tinted_mask_overlay_png, get_dirs,
                    save_uploaded_mask_video, save_uploaded_mask_sequence, save_uploaded_mask_single,
                    apply_corridor_key
                )
        except Exception as e:
            print(f"[ComfyUI-VFX-Suite] Note: Plate prep service optional import notice: {e}")

        try:
            from .vfx_agent_service import generate_director_plan, get_ollama_models, start_ollama_service
        except Exception:
            from vfx_nodes.vfx_agent_service import generate_director_plan, get_ollama_models, start_ollama_service

        try:
            from .audio_service import generate_speech, get_current_audio_info, AUDIO_DIR
        except Exception:
            from vfx_nodes.audio_service import generate_speech, get_current_audio_info, AUDIO_DIR

        try:
            from .project_manager import (
                list_projects, create_or_open_project, save_project_state,
                save_project_autosave, list_project_autosaves, restore_project_autosave, save_project_as,
                get_project_dir, PROJECTS_ROOT, _sanitize_id,
                get_global_characters, add_global_character,
                get_global_locations, add_global_location,
                get_global_voices, add_global_voice,
                convert_character_dataset_batch, start_character_lora_training, GLOBAL_CHARS_DIR,
                convert_location_dataset_batch, start_location_lora_training, GLOBAL_LOCS_DIR
            )
        except Exception:
            from vfx_nodes.project_manager import (
                list_projects, create_or_open_project, save_project_state,
                save_project_autosave, list_project_autosaves, restore_project_autosave, save_project_as,
                get_project_dir, PROJECTS_ROOT, _sanitize_id,
                get_global_characters, add_global_character,
                get_global_locations, add_global_location,
                get_global_voices, add_global_voice,
                convert_character_dataset_batch, start_character_lora_training, delete_character_lora, GLOBAL_CHARS_DIR,
                convert_location_dataset_batch, start_location_lora_training, delete_location_lora, GLOBAL_LOCS_DIR
            )

        # --- Project & Global Library Endpoints ---
        @routes.get("/vfx/projects/list")
        async def vfx_projects_list(request):
            return add_cors_headers(web.json_response({
                "projects": list_projects(),
                "default_root": PROJECTS_ROOT,
                "global_characters": get_global_characters(),
                "global_locations": get_global_locations(),
                "global_voices": get_global_voices()
            }))

        @routes.post("/vfx/projects/open")
        async def vfx_projects_open(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            name = str(data.get("name", "")).strip()
            mode = str(data.get("mode", "production")).strip()
            pid = data.get("project_id", None)
            custom_path = str(data.get("custom_path", "")).strip() or None
            proj = create_or_open_project(name=name, mode=mode, project_id=pid, custom_path=custom_path)
            return add_cors_headers(web.json_response(proj))

        @routes.post("/vfx/projects/save")
        async def vfx_projects_save(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            pid = str(data.get("project_id", "sandbox_playground")).strip()
            updates = data.get("updates", {})
            proj = save_project_state(pid, updates)
            return add_cors_headers(web.json_response(proj))

        @routes.post("/vfx/projects/save_as")
        async def vfx_projects_save_as(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            source_pid = str(data.get("source_project_id", "sandbox_playground")).strip()
            new_name = str(data.get("new_name", "Copy_Project")).strip()
            state = data.get("state", {})
            custom_path = str(data.get("custom_path", "")).strip() or None
            proj = save_project_as(source_pid, new_name, state, custom_path=custom_path)
            return add_cors_headers(web.json_response(proj))

        @routes.post("/vfx/projects/autosave")
        async def vfx_projects_autosave(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            pid = str(data.get("project_id", "sandbox_playground")).strip()
            state = data.get("state", {})
            max_saves = int(data.get("max_autosaves", 10))
            res = save_project_autosave(pid, state, max_saves)
            return add_cors_headers(web.json_response(res))

        @routes.get("/vfx/projects/{project_id}/autosaves")
        async def vfx_projects_list_autosaves(request):
            pid = str(request.match_info.get("project_id", "sandbox_playground")).strip()
            saves = list_project_autosaves(pid)
            return add_cors_headers(web.json_response({"autosaves": saves}))

        @routes.post("/vfx/projects/{project_id}/restore_autosave")
        async def vfx_projects_restore_autosave(request):
            pid = str(request.match_info.get("project_id", "sandbox_playground")).strip()
            try:
                data = await request.json()
            except Exception:
                data = {}
            filename = data.get("filename")
            try:
                proj = restore_project_autosave(pid, filename)
                return add_cors_headers(web.json_response(proj))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/projects/browse_folder")
        async def vfx_projects_browse_folder(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            initial_dir = str(data.get("initial_dir", "")).strip() or PROJECTS_ROOT
            if not os.path.exists(initial_dir):
                initial_dir = PROJECTS_ROOT

            def _pick():
                try:
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()
                    root.attributes('-topmost', True)
                    selected = filedialog.askdirectory(
                        initialdir=initial_dir,
                        title="Select Project Directory / Выберите папку для проекта"
                    )
                    root.destroy()
                    return selected or ""
                except Exception as e:
                    print(f"[VFX Studio] Folder picker error: {e}")
                    return ""

            loop = asyncio.get_event_loop()
            selected_path = await loop.run_in_executor(None, _pick)
            return add_cors_headers(web.json_response({
                "path": selected_path,
                "default_root": PROJECTS_ROOT
            }))

        @routes.get("/vfx/project_file/{project_id}/{subpath:.+}")
        async def serve_project_file(request):
            project_id = _sanitize_id(request.match_info.get("project_id", ""))
            subpath = request.match_info.get("subpath", "")
            p_dir = get_project_dir(project_id)
            target = os.path.normpath(os.path.join(p_dir, subpath))
            if os.path.commonpath([p_dir, target]) == p_dir and os.path.isfile(target):
                return add_cors_headers(web.FileResponse(target))
            return add_cors_headers(web.Response(status=404, text=f"File not found: {subpath}"))

        @routes.post("/vfx/projects/{project_id}/upload_media")
        async def vfx_project_upload_media(request):
            project_id = _sanitize_id(request.match_info.get("project_id", "sandbox_playground"))
            p_dir = get_project_dir(project_id)
            os.makedirs(p_dir, exist_ok=True)
            uploaded_files = []
            try:
                reader = await request.multipart()
                while True:
                    part = await reader.next()
                    if part is None:
                        break
                    raw_fn = part.filename or f"media_{int(time.time()*1000)}.jpg"
                    name_root, ext = os.path.splitext(raw_fn)
                    clean_name = f"{_sanitize_id(name_root)}{ext or '.jpg'}"
                    sub = "characters" if ext.lower() in (".fbx", ".glb", ".gltf", ".bvh") else (
                        "storyboard" if (ext.lower() in (".png", ".jpg", ".jpeg", ".webp") or part.headers.get("Content-Type", "").startswith("image/")) else "source_video")
                    target_dir = os.path.join(p_dir, sub)
                    os.makedirs(target_dir, exist_ok=True)
                    target_path = os.path.join(target_dir, clean_name)
                    with open(target_path, "wb") as f:
                        while True:
                            chunk = await part.read_chunk()
                            if not chunk:
                                break
                            f.write(chunk)
                    uploaded_files.append({
                        "filename": clean_name,
                        "url": f"/vfx/project_file/{project_id}/{sub}/{clean_name}",
                        "sub": sub
                    })
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=500))
            return add_cors_headers(web.json_response({"uploaded": uploaded_files}))

        @routes.get("/vfx/library/characters")
        async def vfx_lib_chars_get(request):
            return add_cors_headers(web.json_response({"characters": get_global_characters()}))

        @routes.post("/vfx/library/characters")
        async def vfx_lib_chars_add(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            chars = add_global_character(data)
            return add_cors_headers(web.json_response({"characters": chars}))

        # --- Character Dataset & LoRA Training Endpoints ---
        @routes.post("/vfx/character/{char_id}/dataset/convert_batch")
        async def vfx_char_dataset_convert(request):
            char_id = request.match_info.get("char_id", "char_default").strip()
            target_size = 1024
            trigger_word = ""
            files_list = []

            content_type = request.content_type or ""
            if "multipart" in content_type:
                try:
                    reader = await request.multipart()
                    while True:
                        part = await reader.next()
                        if part is None:
                            break
                        if part.name == "target_size":
                            try:
                                target_size = int((await part.text()).strip())
                            except Exception:
                                pass
                        elif part.name == "trigger_word":
                            trigger_word = (await part.text()).strip()
                        elif part.filename:
                            data = await part.read()
                            ext = os.path.splitext(part.filename)[1].lower()
                            if ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.tga']:
                                files_list.append({"filename": part.filename, "bytes": data})
                except Exception as e:
                    return add_cors_headers(web.json_response({"error": f"Multipart parse error: {e}"}, status=400))
            else:
                try:
                    data = await request.json()
                    target_size = int(data.get("target_size", 1024))
                    trigger_word = str(data.get("trigger_word", "")).strip()
                    for item in data.get("files", []):
                        fn = item.get("filename", "image.jpg")
                        b64 = item.get("data") or item.get("base64") or ""
                        if b64:
                            if "," in b64:
                                b64 = b64.split(",", 1)[1]
                            raw_b = base64.b64decode(b64)
                            files_list.append({"filename": fn, "bytes": raw_b})
                except Exception as e:
                    return add_cors_headers(web.json_response({"error": f"JSON parse error: {e}"}, status=400))

            if not files_list:
                return add_cors_headers(web.json_response({"error": "No valid image files provided for dataset conversion"}, status=400))

            res = await asyncio.to_thread(convert_character_dataset_batch, char_id, files_list, target_size=target_size, trigger_word=trigger_word)
            return add_cors_headers(web.json_response(res))

        @routes.get("/vfx/character/{char_id}/dataset/file/{filename}")
        async def vfx_char_dataset_file(request):
            char_id = request.match_info.get("char_id", "char_default").strip()
            filename = request.match_info.get("filename", "").strip()
            clean_fn = re.sub(r"[^a-zA-Z0-9_\-.]", "_", filename)
            char_dir = os.path.join(GLOBAL_CHARS_DIR, char_id)
            fmt_path = os.path.join(char_dir, "dataset_formatted_512", clean_fn)
            raw_path = os.path.join(char_dir, "dataset_raw", clean_fn)
            target = fmt_path if os.path.exists(fmt_path) else raw_path
            if not os.path.exists(target):
                return add_cors_headers(web.Response(status=404, text="Dataset file not found"))

            ext = os.path.splitext(clean_fn)[1].lower()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            with open(target, "rb") as f:
                content = f.read()
            return add_cors_headers(web.Response(body=content, content_type=mime))

        @routes.post("/vfx/character/{char_id}/train_lora")
        async def vfx_char_train_lora(request):
            char_id = request.match_info.get("char_id", "char_default").strip()
            try:
                data = await request.json()
            except Exception:
                data = {}
            trigger_word = str(data.get("trigger_word", "")).strip()
            approved_items = data.get("approved_items", [])
            base_model = str(data.get("base_model", "flux")).strip()
            steps = int(data.get("steps", 1200))

            res = await asyncio.to_thread(start_character_lora_training, char_id, trigger_word=trigger_word, approved_items=approved_items, base_model=base_model, steps=steps)
            return add_cors_headers(web.json_response(res))

        @routes.post("/vfx/character/{char_id}/delete_lora")
        async def vfx_char_delete_lora(request):
            char_id = request.match_info.get("char_id", "char_default").strip()
            res = await asyncio.to_thread(delete_character_lora, char_id)
            return add_cors_headers(web.json_response(res))

        # --- Location Library & Dataset Endpoints ---
        @routes.get("/vfx/library/locations")
        async def vfx_lib_locs_get(request):
            return add_cors_headers(web.json_response({"locations": get_global_locations()}))

        @routes.post("/vfx/library/locations")
        async def vfx_lib_locs_add(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            locs = add_global_location(data)
            return add_cors_headers(web.json_response({"locations": locs}))

        @routes.post("/vfx/location/{loc_id}/dataset/convert_batch")
        async def vfx_loc_dataset_convert(request):
            loc_id = request.match_info.get("loc_id", "loc_default").strip()
            target_size = 1024
            trigger_word = ""
            files_list = []

            content_type = request.content_type or ""
            if "multipart" in content_type:
                try:
                    reader = await request.multipart()
                    while True:
                        part = await reader.next()
                        if part is None:
                            break
                        if part.name == "target_size":
                            try:
                                target_size = int((await part.text()).strip())
                            except Exception:
                                pass
                        elif part.name == "trigger_word":
                            trigger_word = (await part.text()).strip()
                        elif part.filename:
                            data = await part.read()
                            ext = os.path.splitext(part.filename)[1].lower()
                            if ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.tga']:
                                files_list.append({"filename": part.filename, "bytes": data})
                except Exception as e:
                    return add_cors_headers(web.json_response({"error": f"Multipart parse error: {e}"}, status=400))
            else:
                try:
                    data = await request.json()
                    target_size = int(data.get("target_size", 1024))
                    trigger_word = str(data.get("trigger_word", "")).strip()
                    for item in data.get("files", []):
                        fn = item.get("filename", "image.jpg")
                        b64 = item.get("data") or item.get("base64") or ""
                        if b64:
                            if "," in b64:
                                b64 = b64.split(",", 1)[1]
                            raw_b = base64.b64decode(b64)
                            files_list.append({"filename": fn, "bytes": raw_b})
                except Exception as e:
                    return add_cors_headers(web.json_response({"error": f"JSON parse error: {e}"}, status=400))

            if not files_list:
                return add_cors_headers(web.json_response({"error": "No valid image files provided for location dataset conversion"}, status=400))

            res = await asyncio.to_thread(convert_location_dataset_batch, loc_id, files_list, target_size=target_size, trigger_word=trigger_word)
            return add_cors_headers(web.json_response(res))

        @routes.get("/vfx/location/{loc_id}/dataset/file/{filename}")
        async def vfx_loc_dataset_file(request):
            loc_id = request.match_info.get("loc_id", "loc_default").strip()
            filename = request.match_info.get("filename", "").strip()
            clean_fn = re.sub(r"[^a-zA-Z0-9_\-.]", "_", filename)
            loc_dir = os.path.join(GLOBAL_LOCS_DIR, loc_id)
            fmt_path = os.path.join(loc_dir, "dataset_formatted_512", clean_fn)
            raw_path = os.path.join(loc_dir, "dataset_raw", clean_fn)
            target = fmt_path if os.path.exists(fmt_path) else raw_path
            if not os.path.exists(target):
                return add_cors_headers(web.Response(status=404, text="Location dataset file not found"))

            ext = os.path.splitext(clean_fn)[1].lower()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            with open(target, "rb") as f:
                content = f.read()
            return add_cors_headers(web.Response(body=content, content_type=mime))

        @routes.post("/vfx/location/{loc_id}/train_lora")
        async def vfx_loc_train_lora(request):
            loc_id = request.match_info.get("loc_id", "loc_default").strip()
            try:
                data = await request.json()
            except Exception:
                data = {}
            trigger_word = str(data.get("trigger_word", "")).strip()
            approved_items = data.get("approved_items", [])
            base_model = str(data.get("base_model", "flux")).strip()
            steps = int(data.get("steps", 1200))

            res = await asyncio.to_thread(start_location_lora_training, loc_id, trigger_word=trigger_word, approved_items=approved_items, base_model=base_model, steps=steps)
            return add_cors_headers(web.json_response(res))

        @routes.post("/vfx/location/{loc_id}/delete_lora")
        async def vfx_loc_delete_lora(request):
            loc_id = request.match_info.get("loc_id", "loc_default").strip()
            res = await asyncio.to_thread(delete_location_lora, loc_id)
            return add_cors_headers(web.json_response(res))

        @routes.get("/vfx/library/voices")
        async def vfx_lib_voices_get(request):
            return add_cors_headers(web.json_response({"voices": get_global_voices()}))

        @routes.post("/vfx/library/voices")
        async def vfx_lib_voices_add(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            voices = add_global_voice(data)
            return add_cors_headers(web.json_response({"voices": voices}))

        # --- Deconstruction Pass Extractor (with LTX Alpha LoRA & CorridorKey) ---
        @routes.post("/vfx/deconstruct/extract")
        async def vfx_deconstruct_extract(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            checks = data.get("checks", {})
            extracted = []
            if checks.get("tracking_glomap", True):
                extracted.append({"id": "pass_glomap", "name": "🎥 3D Камера & Облако точек (GLOMAP)", "type": "camera", "status": "Готово (35mm, Z=0)"})
            if checks.get("mask_sam2_rough", True):
                extracted.append({"id": "pass_sam2", "name": "🎭 Черновые маски объектов (SAM 2.1 Cryptomatte)", "type": "mask", "status": "Готово (ID слои)"})
            if checks.get("mask_corridor_key", True):
                extracted.append({"id": "pass_corridor", "name": "🟩 Чистовой кеинг & Деспилл (CorridorKey)", "type": "matte", "status": "Готово (Волоски + FG)"})
            if checks.get("mask_ltx_alpha_lora", True):
                extracted.append({"id": "pass_ltx_alpha", "name": "✨ Альфа-канал LTX (LTX Alpha LoRA)", "type": "alpha", "status": "Готово (Прозрачность)"})
            if checks.get("pass_depth", True):
                extracted.append({"id": "pass_depth", "name": "🌊 Карта глубины (3D Z-Depth / DepthAnything)", "type": "depth", "status": "Готово (16-bit Z)"})
            if checks.get("pass_canny", True):
                extracted.append({"id": "pass_canny", "name": "📐 Контур и Линии (Canny / Wireframe)", "type": "canny", "status": "Готово"})
            if checks.get("pass_pose", True):
                extracted.append({"id": "pass_pose", "name": "🕺 Скелет и Позы (DWPose / OpenPose)", "type": "pose", "status": "Готово"})
            return add_cors_headers(web.json_response({"status": "ok", "extracted_passes": extracted}))

        # --- Music: ACE-Step 1.5 through ComfyUI's native nodes (vfx_nodes/music_service.py) ---
        @routes.get("/vfx/music/status")
        async def vfx_music_status(request):
            from .music_service import status as _mst, list_tracks as _mlist
            pid = _sanitize_id(request.query.get("project_id", ""))
            try:
                res = _mst()
                res["tracks"] = _mlist(pid) if pid else []
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/music/generate")
        async def vfx_music_generate(request):
            from .music_service import start as _mstart
            try:
                data = await request.json()
                pid = _sanitize_id(str(data.get("project_id", "")))
                res = await asyncio.to_thread(_mstart, pid, data)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.get("/vfx/music/job/{job_id}")
        async def vfx_music_job(request):
            from .music_service import get_job as _mjob
            return add_cors_headers(web.json_response(_mjob(request.match_info["job_id"])))

        @routes.post("/vfx/music/job/{job_id}/cancel")
        async def vfx_music_cancel(request):
            from .music_service import cancel as _mcancel
            return add_cors_headers(web.json_response(_mcancel(request.match_info["job_id"])))

        # --- Voice board: cards (image / video) with lines of speech -> voice clone, lip-sync, mix ---
        @routes.get("/vfx/voiceboard/{project_id}")
        async def vfx_voiceboard_get(request):
            from .voice_board_service import load_board as _vb_load
            return add_cors_headers(web.json_response(_vb_load(_sanitize_id(request.match_info["project_id"]))))

        @routes.post("/vfx/voiceboard/{project_id}")
        async def vfx_voiceboard_save(request):
            from .voice_board_service import save_board as _vb_save
            try:
                data = await request.json()
                return add_cors_headers(web.json_response(_vb_save(_sanitize_id(request.match_info["project_id"]), data)))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/voiceboard/{project_id}/{action}")
        async def vfx_voiceboard_action(request):
            from . import voice_board_service as _vb
            pid = _sanitize_id(request.match_info["project_id"])
            action = request.match_info["action"]
            try:
                d = await request.json()
                if action == "voice":
                    res = _vb.start_voice(pid, str(d["card_id"]), str(d["cue_id"]))
                elif action == "lipsync":
                    res = _vb.start_lipsync(pid, str(d["card_id"]), str(d.get("prompt") or ""), d.get("seed"))
                elif action == "transcribe":
                    res = _vb.start_transcribe(pid, str(d["card_id"]), str(d["cue_id"]), str(d.get("which") or "ref"))
                elif action == "asr":
                    res = _vb.start_asr(pid, str(d["url"]), float(d.get("start") or 0), float(d.get("end") or 0),
                                        str(d.get("language") or "ru"), bool(d.get("whole")))
                elif action == "dubit":
                    res = _vb.start_dubit(pid, str(d["card_id"]), str(d.get("desc") or ""), float(d.get("start") or 0),
                                          float(d.get("max_s") or 5), d.get("seed"))
                elif action == "mix":
                    res = _vb.start_mix(pid, str(d["card_id"]), bool(d.get("keep_original", True)))
                else:
                    raise ValueError(f"Unknown action {action}")
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.get("/vfx/voiceboard_job/{job_id}")
        async def vfx_voiceboard_job(request):
            from .voice_board_service import get_job as _vb_job
            return add_cors_headers(web.json_response(_vb_job(request.match_info["job_id"])))

        @routes.post("/vfx/voiceboard_job/{job_id}/cancel")
        async def vfx_voiceboard_cancel(request):
            from .voice_board_service import cancel as _vb_cancel
            return add_cors_headers(web.json_response(_vb_cancel(request.match_info["job_id"])))

        # --- Voice-over: long text, paragraph cache (only edited paragraphs are re-voiced) ---
        @routes.get("/vfx/narration/{project_id}")
        async def vfx_narration_get(request):
            from .narration_service import status_parts as _n_st
            return add_cors_headers(web.json_response(_n_st(_sanitize_id(request.match_info["project_id"]))))

        @routes.post("/vfx/narration/{project_id}")
        async def vfx_narration_save(request):
            from .narration_service import save_doc as _n_save, status_parts as _n_st
            pid = _sanitize_id(request.match_info["project_id"])
            try:
                data = await request.json()
                keep = {k: data[k] for k in ("text", "ref_audio", "ref_title", "ref_start", "ref_end", "ref_text", "language", "pause", "seed", "speed") if k in data}
                _n_save(pid, keep)
                return add_cors_headers(web.json_response(_n_st(pid)))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/narration/{project_id}/{action}")
        async def vfx_narration_action(request):
            from . import narration_service as _n
            pid = _sanitize_id(request.match_info["project_id"])
            action = request.match_info["action"]
            try:
                d = await request.json()
                if action == "generate":
                    res = _n.start_generate(pid, [int(i) for i in (d.get("only") or [])])
                elif action == "prepare":
                    res = _n.start_prepare(pid, bool(d.get("add_stress", True)))
                elif action == "transcribe_ref":
                    res = _n.start_transcribe_ref(pid)
                else:
                    raise ValueError(f"Unknown action {action}")
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        # --- VFX Director Agent Endpoint (Non-blocking via asyncio.to_thread) ---
        def _studio_agent_turn(data, msg, history, references, agent_config, timeline_context):
            import sys as _sys
            core_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core_agent")
            if not os.path.isdir(core_dir):
                import folder_paths as _fp
                core_dir = os.path.join(_fp.base_path, "custom_nodes", "ComfyUI-VFX-Suite", "core_agent")
            if core_dir not in _sys.path:
                _sys.path.insert(0, core_dir)
            import importlib
            import studio_agent as _sa
            importlib.reload(_sa)
            from .project_manager import get_project_dir
            pid = _sanitize_id(str(data.get("project_id") or "sandbox_playground"))
            ctx = data.get("studio_context") or {}
            if references:
                ctx["attached"] = [{"title": r.get("title"), "type": r.get("mediaType") or r.get("type"), "id": r.get("id")} for r in references if isinstance(r, dict)]
            if timeline_context:
                ctx["timeline"] = timeline_context[:3000]
            res = _sa.plan_turn(msg, get_project_dir(pid), ctx, agent_config, ui_history=history)
            acts = res.get("studio_actions") or []
            if any(a.get("type") in ("trigger_generation", "video_job", "generate_music") for a in acts):
                try:
                    from .vfx_agent_service import unload_agent_from_vram
                    unload_agent_from_vram(agent_config.get("api_url"), preserve_micro=True)
                except Exception:
                    pass
            model = res.get("model_used") or ""
            heavy = bool(re.search(r"27b|32b|30b|70b|3\.8", model.lower())) if model else True
            reply = res.get("response_ru") or ""
            first = next((l.strip().lstrip("#-*> ") for l in reply.splitlines() if l.strip()), "")
            return {
                "planner": "v2",
                "response_ru": reply,
                "response_short": (first[:90] + "…") if len(first) > 90 else first,
                "studio_actions": acts,
                "flux_prompt": res.get("flux_prompt", ""),
                "prompt": res.get("flux_prompt", ""),
                "ltx_prompt": res.get("ltx_prompt", ""),
                "is_video": bool(res.get("is_video")),
                "intent": "generation" if acts else "chat",
                "decision": res.get("decision"),
                "model_used": model, "model_name": model,
                "model_badge": f"🧠 {model}" if heavy else f"⚡ {model}",
                "model_tier": "heavy" if heavy else "micro",
                "references_used": [r.get("id") for r in references if isinstance(r, dict)],
                "camera": {},
            }

        @routes.post("/vfx/agent/chat")
        async def vfx_agent_chat(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            msg = str(data.get("message", "")).strip()
            history = data.get("history", [])
            references = data.get("references", []) or []
            # Normalize: UI/agent may send plain strings (file names / URLs) instead of dicts
            _norm_refs = []
            for _i, _r in enumerate(references if isinstance(references, list) else [references], start=1):
                if isinstance(_r, dict):
                    _norm_refs.append(_r)
                elif isinstance(_r, str) and _r.strip():
                    _s = _r.strip()
                    _norm_refs.append({"id": f"ref_{_i}", "title": os.path.basename(_s), "path": _s, "url": _s})
            references = _norm_refs
            agent_config = data.get("agent_config", {})
            timeline_context = str(data.get("timeline_context", "")).strip()
            # v2: the studio agent reads the whole dialogue + project and decides (core_agent/studio_agent.py)
            if data.get("planner", "v2") == "v2":
                try:
                    plan2 = await asyncio.to_thread(_studio_agent_turn, data, msg, history, references, agent_config, timeline_context)
                    return add_cors_headers(web.json_response(plan2))
                except Exception as e:
                    print(f"[VFX-Suite] studio agent v2 failed, falling back to the legacy director: {e}")
            from .vfx_agent_service import generate_director_plan as gen_plan
            plan = await asyncio.to_thread(
                gen_plan,
                msg,
                history=history,
                references=references,
                agent_config=agent_config,
                timeline_context=timeline_context
            )
            return add_cors_headers(web.json_response(plan))

        @routes.get("/vfx/agent/ollama_models")
        async def vfx_agent_ollama_models(request):
            url = request.query.get("url", None)
            res = await asyncio.to_thread(get_ollama_models, url)
            return add_cors_headers(web.json_response(res))

        @routes.post("/vfx/agent/ollama_start")
        async def vfx_agent_ollama_start(request):
            res = await asyncio.to_thread(start_ollama_service)
            return add_cors_headers(web.json_response(res))

        @routes.post("/vfx/agent/unload_vram")
        async def vfx_agent_unload_vram(request):
            """Explicit endpoint to unload all active local LLMs from GPU VRAM before generation."""
            try:
                from .vfx_agent_service import unload_agent_from_vram
                res = await asyncio.to_thread(unload_agent_from_vram)
                return add_cors_headers(web.json_response({"status": "ok", "unloaded": res}))
            except Exception as e:
                return add_cors_headers(web.json_response({"status": "error", "error": str(e)}, status=500))

        # --- Autonomous ComfyUI Workflow Execution & Output Monitoring ---
        @routes.post("/vfx/agent/execute_workflow")
        async def vfx_agent_execute_workflow(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            prompt_text = str(data.get("prompt", "")).strip() or "Cinematic shot, highly detailed, 8k"
            neg_prompt = str(data.get("negative_prompt", "")).strip() or "blurry, low quality, deformed, artifacts, ugly, text, watermark, bad anatomy, static, jittery"
            
            # If prompt contains Cyrillic and wasn't translated into an English visual prompt, translate/expand it
            import re
            if re.search(r"[\u0400-\u04FF]", prompt_text):
                try:
                    from .vfx_agent_service import generate_director_plan as gen_plan_fallback
                    plan = await asyncio.to_thread(gen_plan_fallback, prompt_text, references=data.get("references", []))
                    if (plan.get("is_video") or data.get("mode") == "video") and plan.get("ltx_prompt"):
                        prompt_text = plan.get("ltx_prompt")
                    elif plan.get("flux_prompt"):
                        prompt_text = plan.get("flux_prompt")
                except Exception as ex:
                    LOGGER.warning(f"[execute_workflow] Auto-expand Cyrillic prompt fallback failed: {ex}")
            custom_wf = str(data.get("custom_workflow", "")).strip()
            mode = str(data.get("mode", "image")).strip().lower()
            workflow_type = str(data.get("workflow_type", "")).strip().lower()
            card_id = str(data.get("card_id", "")).strip()
            references = data.get("references", []) or []
            # Normalize: UI/agent may send plain strings (file names / URLs) instead of dicts
            _norm_refs = []
            for _i, _r in enumerate(references if isinstance(references, list) else [references], start=1):
                if isinstance(_r, dict):
                    _norm_refs.append(_r)
                elif isinstance(_r, str) and _r.strip():
                    _s = _r.strip()
                    _norm_refs.append({"id": f"ref_{_i}", "title": os.path.basename(_s), "path": _s, "url": _s})
            references = _norm_refs

            is_video = (
                mode == "video" or
                workflow_type == "ltx_video" or
                ("ltx" in custom_wf.lower()) or
                any(w in prompt_text.lower() for w in ["video", "ltx", "wan", "видео", "ролик", "клип", "движение камеры", "камера от первого", "от первого к последнему", "animate", "motion"])
            )
            if is_video and data.get("ltx_prompt"):
                prompt_text = str(data.get("ltx_prompt")).strip()
            # Never let a photo workflow name (e.g. FLUX.3_Klein_Text2Image.json) leak into a video job
            if is_video and custom_wf and "ltx" not in custom_wf.lower():
                custom_wf = "LTX-2.5/video_ltx2_5_t2v.json"
            LOGGER.info(f"[execute_workflow] Executing: mode={mode}, workflow_type={workflow_type}, is_video={is_video}, custom_wf={custom_wf}")

            import random
            import shutil
            import base64
            seed = random.randint(100000, 999999999)

            import folder_paths
            input_dir = folder_paths.get_input_directory()
            os.makedirs(input_dir, exist_ok=True)
            available_ckpts = folder_paths.get_filename_list("checkpoints") if hasattr(folder_paths, "get_filename_list") else []
            models_dir = folder_paths.models_dir

            flux3_diff_file = "flux3-image-klein-int8-convrot.safetensors"
            flux3_clip_file = "qwen3vl_4b_int8_convrot.safetensors"
            flux3_vae_file = "flux2-vae.safetensors"
            diff_models = folder_paths.get_filename_list("diffusion_models") if hasattr(folder_paths, "get_filename_list") else []
            flux3_ready = (flux3_diff_file in diff_models) or os.path.exists(os.path.join(models_dir, "diffusion_models", flux3_diff_file))
            is_flux = (
                workflow_type in ("flux", "flux3", "flux_image") or
                data.get("model_family") in ("flux", "flux3") or
                any(kw in prompt_text.lower() for kw in ("flux", "flux3", "флюкс", "klein"))
            )
            # still frames: FLUX.3 Klein whenever no SD/SDXL checkpoint is installed (LTX / SAM files in
            # models/checkpoints are not image checkpoints)
            sd_ckpts = [c for c in available_ckpts if not any(k in c.lower() for k in ("ltx", "sam", "vocoder"))]
            if flux3_ready and not sd_ckpts:
                is_flux = True

            # -------------------------------------------------------------
            # CASE A: LTX-VIDEO CAMERA CONTROL & INTERPOLATION PIPELINE
            # -------------------------------------------------------------
            if is_video:
                ltx_ckpt = "ltx-2.3-22b-dev-fp8.safetensors"
                for cand in ["ltx-2.3-22b-dev-fp8.safetensors", "ltx-2.3-22b-dev.safetensors", "ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors"]:
                    if cand in available_ckpts:
                        ltx_ckpt = cand
                        break
                else:
                    for cand in available_ckpts:
                        if "ltx" in cand.lower():
                            ltx_ckpt = cand
                            break

                width = int(data.get("width", 544))
                height = int(data.get("height", 960))
                length = int(data.get("length", 25))
                fps = float(data.get("fps", 24.0))

                # Identify start and end frame images
                start_img_file = None
                end_img_file = None

                def extract_and_save_frame(ref_item, prefix):
                    if not ref_item:
                        return None
                    url_or_path = ref_item.get("path") or ref_item.get("url") or ""
                    # 1. Base64
                    if "base64," in url_or_path:
                        try:
                            raw_b64 = url_or_path.split("base64,")[1]
                            ext = "png" if "data:image/png" in url_or_path else "jpg"
                            fn = f"keshiki_{prefix}_{card_id or 'frame'}.{ext}"
                            fp = os.path.join(input_dir, fn)
                            with open(fp, "wb") as bf:
                                bf.write(base64.b64decode(raw_b64))
                            return fn
                        except Exception:
                            pass
                    # 2. Local absolute path
                    if os.path.exists(url_or_path):
                        ext = os.path.splitext(url_or_path)[1] or ".jpg"
                        fn = f"keshiki_{prefix}_{card_id or 'frame'}{ext}"
                        fp = os.path.join(input_dir, fn)
                        shutil.copyfile(url_or_path, fp)
                        return fn
                    # 3. Relative project URL like /vfx/project_file/...
                    if "/vfx/project_file/" in url_or_path:
                        rel = url_or_path.split("/vfx/project_file/")[1]
                        suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        disk_p = os.path.join(suite_root, "projects", rel.replace("/", os.sep))
                        if os.path.exists(disk_p):
                            ext = os.path.splitext(disk_p)[1] or ".jpg"
                            fn = f"keshiki_{prefix}_{card_id or 'frame'}{ext}"
                            fp = os.path.join(input_dir, fn)
                            shutil.copyfile(disk_p, fp)
                            return fn
                    # 4. Search in projects directory by title or id
                    suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    proj_root = os.path.join(suite_root, "projects")
                    target_kw = (ref_item.get("title") or ref_item.get("id") or "").lower()
                    if target_kw and os.path.exists(proj_root):
                        for root_dir, _, f_list in os.walk(proj_root):
                            for fname in f_list:
                                if fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                                    if target_kw in fname.lower() or fname.lower() in target_kw:
                                        full_path = os.path.join(root_dir, fname)
                                        ext = os.path.splitext(full_path)[1]
                                        fn = f"keshiki_{prefix}_{card_id or 'frame'}{ext}"
                                        fp = os.path.join(input_dir, fn)
                                        shutil.copyfile(full_path, fp)
                                        return fn
                    return None

                # Find explicit start / end frames
                start_ref = None
                end_ref = None
                for r in references:
                    t_str = f"{r.get('title', '')} {r.get('id', '')}".lower()
                    if "start" in t_str and not start_ref:
                        start_ref = r
                    elif "end" in t_str and not end_ref:
                        end_ref = r

                if not start_ref and references:
                    start_ref = references[0]
                if not end_ref and len(references) > 1:
                    end_ref = references[-1]

                start_img_file = extract_and_save_frame(start_ref, "start") if start_ref else None
                end_img_file = extract_and_save_frame(end_ref, "end") if end_ref and end_ref != start_ref else None

                # For pure Text-to-Video (no references attached), start from scratch without stale image conditioning
                if not references and not any(kw in (custom_wf + prompt_text).lower() for kw in ["i2v", "image_to_video", "flf2v", "first & last"]):
                    start_img_file = None
                    end_img_file = None
                # (no hidden fallback frames: only the images the user actually gave are used)

                # Detect available LTX text encoder / clip
                ltx_clip_name = "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
                try:
                    text_encoders = folder_paths.get_filename_list("text_encoders")
                    for cand in [
                        "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
                        "gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
                        "umt5-xxl-enc-bf16.safetensors"
                    ]:
                        if cand in text_encoders:
                            ltx_clip_name = cand
                            break
                except Exception:
                    pass

                # Assemble API prompt graph
                workflow = {
                    "1": {
                        "class_type": "CheckpointLoaderSimple",
                        "inputs": {"ckpt_name": ltx_ckpt}
                    },
                    "14": {
                        "class_type": "CLIPLoader",
                        "inputs": {"clip_name": ltx_clip_name, "type": "ltxv"}
                    },
                    "2": {
                        "class_type": "CLIPTextEncode",
                        "inputs": {"text": prompt_text, "clip": ["14", 0]}
                    },
                    "3": {
                        "class_type": "CLIPTextEncode",
                        "inputs": {"text": neg_prompt, "clip": ["14", 0]}
                    },
                    "4": {
                        "class_type": "LTXVConditioning",
                        "inputs": {"positive": ["2", 0], "negative": ["3", 0], "frame_rate": fps}
                    },
                    "5": {
                        "class_type": "EmptyLTXVLatentVideo",
                        "inputs": {"width": width, "height": height, "length": length, "batch_size": 1}
                    }
                }

                current_pos = ["4", 0]
                current_neg = ["4", 1]
                current_latent = ["5", 0]

                if start_img_file:
                    workflow["6"] = {
                        "class_type": "LoadImage",
                        "inputs": {"image": start_img_file}
                    }
                    workflow["7"] = {
                        "class_type": "LTXAddVideoICLoRAGuide",
                        "inputs": {
                            "positive": current_pos,
                            "negative": current_neg,
                            "vae": ["1", 2],
                            "latent": current_latent,
                            "image": ["6", 0],
                            "frame_idx": 0,
                            "strength": 1.0,
                            "latent_downscale_factor": 1.0,
                            "crop": "disabled",
                            "use_tiled_encode": False,
                            "tile_size": 256,
                            "tile_overlap": 64
                        }
                    }
                    current_pos = ["7", 0]
                    current_neg = ["7", 1]
                    current_latent = ["7", 2]

                if end_img_file:
                    workflow["8"] = {
                        "class_type": "LoadImage",
                        "inputs": {"image": end_img_file}
                    }
                    workflow["9"] = {
                        "class_type": "LTXAddVideoICLoRAGuide",
                        "inputs": {
                            "positive": current_pos,
                            "negative": current_neg,
                            "vae": ["1", 2],
                            "latent": current_latent,
                            "image": ["8", 0],
                            "frame_idx": max(0, length - 1),
                            "strength": 1.0,
                            "latent_downscale_factor": 1.0,
                            "crop": "disabled",
                            "use_tiled_encode": False,
                            "tile_size": 256,
                            "tile_overlap": 64
                        }
                    }
                    current_pos = ["9", 0]
                    current_neg = ["9", 1]
                    current_latent = ["9", 2]

                workflow["10"] = {
                    "class_type": "KSampler",
                    "inputs": {
                        "model": ["1", 0],
                        "positive": current_pos,
                        "negative": current_neg,
                        "latent_image": current_latent,
                        "seed": seed,
                        "steps": 20,
                        "cfg": 3.0,
                        "sampler_name": "euler",
                        "scheduler": "normal",
                        "denoise": 1.0
                    }
                }
                workflow["11"] = {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["10", 0], "vae": ["1", 2]}
                }
                workflow["12"] = {
                    "class_type": "VHS_VideoCombine",
                    "inputs": {
                        "images": ["11", 0],
                        "frame_rate": int(fps),
                        "loop_count": 0,
                        "filename_prefix": f"Keshiki_LTX_{card_id or 'Video'}",
                        "format": "video/h264-mp4",
                        "pingpong": False,
                        "save_output": True
                    }
                }
                workflow["13"] = {
                    "class_type": "SaveImage",
                    "inputs": {"images": ["11", 0], "filename_prefix": f"Keshiki_LTX_Frames_{card_id or 'Video'}"}
                }

                # Construct rich UI workflow for ComfyUI Canvas
                ui_workflow = {
                    "last_node_id": 14,
                    "last_link_id": 26,
                    "nodes": [
                        {
                            "id": 1,
                            "type": "CheckpointLoaderSimple",
                            "pos": [40, 60],
                            "size": [315, 98],
                            "title": "LTX-Video Checkpoint",
                            "properties": {"Node name for S&R": "CheckpointLoaderSimple"},
                            "widgets_values": [ltx_ckpt]
                        },
                        {
                            "id": 14,
                            "type": "CLIPLoader",
                            "pos": [40, 190],
                            "size": [315, 98],
                            "title": "LTX Text Encoder (Gemma 4/LTX)",
                            "properties": {"Node name for S&R": "CLIPLoader"},
                            "widgets_values": [ltx_clip_name, "ltxv"]
                        },
                        {
                            "id": 2,
                            "type": "CLIPTextEncode",
                            "pos": [400, 100],
                            "size": [440, 200],
                            "title": "Positive Prompt (ИИ-Режиссер / LTX)",
                            "properties": {"Node name for S&R": "CLIPTextEncode"},
                            "widgets_values": [prompt_text]
                        },
                        {
                            "id": 3,
                            "type": "CLIPTextEncode",
                            "pos": [400, 340],
                            "size": [440, 160],
                            "title": "Negative Prompt",
                            "properties": {"Node name for S&R": "CLIPTextEncode"},
                            "widgets_values": [neg_prompt]
                        },
                        {
                            "id": 4,
                            "type": "LTXVConditioning",
                            "pos": [880, 100],
                            "size": [220, 80],
                            "title": "LTX Video Conditioning (24 fps)",
                            "properties": {"Node name for S&R": "LTXVConditioning"},
                            "widgets_values": [fps]
                        },
                        {
                            "id": 5,
                            "type": "EmptyLTXVLatentVideo",
                            "pos": [400, 540],
                            "size": [315, 120],
                            "title": "Empty LTX Video Latent (9:16 Vertical)",
                            "properties": {"Node name for S&R": "EmptyLTXVLatentVideo"},
                            "widgets_values": [width, height, length, 1]
                        },
                        {
                            "id": 6,
                            "type": "LoadImage",
                            "pos": [40, 320],
                            "size": [315, 240],
                            "title": "Start Frame (Кадр #1 — Фас)",
                            "properties": {"Node name for S&R": "LoadImage"},
                            "widgets_values": [start_img_file or "keshiki_start_frame.jpg", "image"]
                        },
                        {
                            "id": 7,
                            "type": "LTXAddVideoICLoRAGuide",
                            "pos": [880, 220],
                            "size": [320, 260],
                            "title": "LTX Guide: Frame 0 (Старт)",
                            "properties": {"Node name for S&R": "LTXAddVideoICLoRAGuide"},
                            "widgets_values": [0, 1.0, 1.0, "disabled", False, 256, 64]
                        },
                        {
                            "id": 8,
                            "type": "LoadImage",
                            "pos": [40, 600],
                            "size": [315, 240],
                            "title": "End Frame (Финал — Профиль)",
                            "properties": {"Node name for S&R": "LoadImage"},
                            "widgets_values": [end_img_file or "keshiki_end_frame.jpg", "image"]
                        },
                        {
                            "id": 9,
                            "type": "LTXAddVideoICLoRAGuide",
                            "pos": [1240, 220],
                            "size": [320, 260],
                            "title": f"LTX Guide: Frame {max(0, length - 1)} (Финиш)",
                            "properties": {"Node name for S&R": "LTXAddVideoICLoRAGuide"},
                            "widgets_values": [max(0, length - 1), 1.0, 1.0, "disabled", False, 256, 64]
                        },
                        {
                            "id": 10,
                            "type": "KSampler",
                            "pos": [1600, 200],
                            "size": [315, 262],
                            "title": "LTX Video KSampler (Euler)",
                            "properties": {"Node name for S&R": "KSampler"},
                            "widgets_values": [seed, "randomize", 20, 3.0, "euler", "normal", 1.0]
                        },
                        {
                            "id": 11,
                            "type": "VAEDecode",
                            "pos": [1960, 200],
                            "size": [210, 46],
                            "title": "VAE Decode Video Latent",
                            "properties": {"Node name for S&R": "VAEDecode"},
                            "widgets_values": []
                        },
                        {
                            "id": 12,
                            "type": "VHS_VideoCombine",
                            "pos": [2220, 140],
                            "size": [315, 280],
                            "title": "Export MP4 Video (H.264)",
                            "properties": {"Node name for S&R": "VHS_VideoCombine"},
                            "widgets_values": [int(fps), 0, f"Keshiki_LTX_{card_id or 'Video'}", "video/h264-mp4", False, True]
                        },
                        {
                            "id": 13,
                            "type": "SaveImage",
                            "pos": [2220, 460],
                            "size": [315, 220],
                            "title": "Save Frame Sequence",
                            "properties": {"Node name for S&R": "SaveImage"},
                            "widgets_values": [f"Keshiki_LTX_Frames_{card_id or 'Video'}"]
                        }
                    ],
                    "links": [
                        [1, 1, 0, 10, 0, "MODEL"],
                        [2, 14, 0, 2, 0, "CLIP"],
                        [3, 14, 0, 3, 0, "CLIP"],
                        [4, 2, 0, 4, 0, "CONDITIONING"],
                        [5, 3, 0, 4, 1, "CONDITIONING"],
                        [6, 4, 0, 7, 0, "CONDITIONING"],
                        [7, 4, 1, 7, 1, "CONDITIONING"],
                        [8, 1, 2, 7, 2, "VAE"],
                        [9, 5, 0, 7, 3, "LATENT"],
                        [10, 6, 0, 7, 4, "IMAGE"],
                        [11, 7, 0, 9, 0, "CONDITIONING"],
                        [12, 7, 1, 9, 1, "CONDITIONING"],
                        [13, 1, 2, 9, 2, "VAE"],
                        [14, 7, 2, 9, 3, "LATENT"],
                        [15, 8, 0, 9, 4, "IMAGE"],
                        [16, 9, 0, 10, 1, "CONDITIONING"],
                        [17, 9, 1, 10, 2, "CONDITIONING"],
                        [18, 9, 2, 10, 3, "LATENT"],
                        [19, 10, 0, 11, 0, "LATENT"],
                        [20, 1, 2, 11, 1, "VAE"],
                        [21, 11, 0, 12, 0, "IMAGE"],
                        [22, 11, 0, 13, 0, "IMAGE"]
                    ],
                    "groups": [],
                    "config": {},
                    "extra": {},
                    "version": 0.4
                }
                ckpt_name = ltx_ckpt

            # -------------------------------------------------------------
            # CASE B: FLUX.3 KLEIN HIGH-QUALITY IMAGE GENERATION
            # -------------------------------------------------------------
            elif is_flux and flux3_ready:
                ckpt_name = "FLUX.3 Klein (int8-convrot + Qwen3-VL)"
                width = int(data.get("width", 1024))
                height = int(data.get("height", 576))
                f_prompt = data.get("flux_prompt") or prompt_text

                workflow = {
                    "1": {
                        "class_type": "UNETLoader",
                        "inputs": {"unet_name": flux3_diff_file, "weight_dtype": "default"}
                    },
                    "2": {
                        "class_type": "CLIPLoader",
                        "inputs": {"clip_name": flux3_clip_file, "type": "flux3"}
                    },
                    "3": {
                        "class_type": "VAELoader",
                        "inputs": {"vae_name": flux3_vae_file}
                    },
                    "4": {
                        "class_type": "CLIPTextEncode",
                        "inputs": {"text": f_prompt, "clip": ["2", 0]}
                    },
                    "5": {
                        "class_type": "FluxGuidance",
                        "inputs": {"guidance": 3.5, "conditioning": ["4", 0]}
                    },
                    "6": {
                        "class_type": "EmptyFlux2LatentImage",
                        "inputs": {"width": width, "height": height, "batch_size": 1}
                    },
                    "7": {
                        "class_type": "KSampler",
                        "inputs": {
                            "model": ["1", 0],
                            "positive": ["5", 0],
                            "negative": ["4", 0],
                            "latent_image": ["6", 0],
                            "seed": seed,
                            "steps": 25,
                            "cfg": 1.0,
                            "sampler_name": "euler",
                            "scheduler": "simple",
                            "denoise": 1.0
                        }
                    },
                    "8": {
                        "class_type": "VAEDecode",
                        "inputs": {"samples": ["7", 0], "vae": ["3", 0]}
                    },
                    "9": {
                        "class_type": "SaveImage",
                        "inputs": {"images": ["8", 0], "filename_prefix": f"Keshiki_{card_id or 'Shot'}"}
                    }
                }

                ui_workflow = {
                    "last_node_id": 9,
                    "last_link_id": 9,
                    "nodes": [
                        {
                            "id": 1,
                            "type": "UNETLoader",
                            "pos": [40, 100],
                            "size": [315, 82],
                            "flags": {},
                            "order": 0,
                            "mode": 0,
                            "outputs": [
                                {"name": "MODEL", "type": "MODEL", "links": [1], "slot_index": 0}
                            ],
                            "title": "FLUX.3 Klein DiT (int8-convrot)",
                            "properties": {"Node name for S&R": "UNETLoader"},
                            "widgets_values": [flux3_diff_file, "default"]
                        },
                        {
                            "id": 2,
                            "type": "CLIPLoader",
                            "pos": [40, 240],
                            "size": [315, 106],
                            "flags": {},
                            "order": 1,
                            "mode": 0,
                            "outputs": [
                                {"name": "CLIP", "type": "CLIP", "links": [2], "slot_index": 0}
                            ],
                            "title": "Qwen3-VL 4B Text Encoder (flux3)",
                            "properties": {"Node name for S&R": "CLIPLoader"},
                            "widgets_values": [flux3_clip_file, "flux3"]
                        },
                        {
                            "id": 3,
                            "type": "VAELoader",
                            "pos": [40, 400],
                            "size": [315, 58],
                            "flags": {},
                            "order": 2,
                            "mode": 0,
                            "outputs": [
                                {"name": "VAE", "type": "VAE", "links": [8], "slot_index": 0}
                            ],
                            "title": "FLUX 2/3 VAE (128-ch Latents)",
                            "properties": {"Node name for S&R": "VAELoader"},
                            "widgets_values": [flux3_vae_file]
                        },
                        {
                            "id": 4,
                            "type": "CLIPTextEncode",
                            "pos": [400, 100],
                            "size": [420, 200],
                            "flags": {},
                            "order": 3,
                            "mode": 0,
                            "inputs": [
                                {"name": "clip", "type": "CLIP", "link": 2}
                            ],
                            "outputs": [
                                {"name": "CONDITIONING", "type": "CONDITIONING", "links": [3, 4], "slot_index": 0}
                            ],
                            "title": "Positive Prompt (ИИ-Режиссер / FLUX 3)",
                            "properties": {"Node name for S&R": "CLIPTextEncode"},
                            "widgets_values": [f_prompt]
                        },
                        {
                            "id": 5,
                            "type": "FluxGuidance",
                            "pos": [860, 100],
                            "size": [220, 60],
                            "flags": {},
                            "order": 4,
                            "mode": 0,
                            "inputs": [
                                {"name": "conditioning", "type": "CONDITIONING", "link": 3}
                            ],
                            "outputs": [
                                {"name": "CONDITIONING", "type": "CONDITIONING", "links": [5], "slot_index": 0}
                            ],
                            "title": "FLUX Guidance (3.5)",
                            "properties": {"Node name for S&R": "FluxGuidance"},
                            "widgets_values": [3.5]
                        },
                        {
                            "id": 6,
                            "type": "EmptyFlux2LatentImage",
                            "pos": [860, 220],
                            "size": [315, 106],
                            "flags": {},
                            "order": 5,
                            "mode": 0,
                            "outputs": [
                                {"name": "LATENT", "type": "LATENT", "links": [6], "slot_index": 0}
                            ],
                            "title": "Empty Flux 2/3 Latent (1024x576, 128ch)",
                            "properties": {"Node name for S&R": "EmptyFlux2LatentImage"},
                            "widgets_values": [width, height, 1]
                        },
                        {
                            "id": 7,
                            "type": "KSampler",
                            "pos": [1220, 150],
                            "size": [315, 262],
                            "flags": {},
                            "order": 6,
                            "mode": 0,
                            "inputs": [
                                {"name": "model", "type": "MODEL", "link": 1},
                                {"name": "positive", "type": "CONDITIONING", "link": 5},
                                {"name": "negative", "type": "CONDITIONING", "link": 4},
                                {"name": "latent_image", "type": "LATENT", "link": 6}
                            ],
                            "outputs": [
                                {"name": "LATENT", "type": "LATENT", "links": [7], "slot_index": 0}
                            ],
                            "title": "FLUX 3 KSampler (Euler Simple)",
                            "properties": {"Node name for S&R": "KSampler"},
                            "widgets_values": [seed, "randomize", 25, 1.0, "euler", "simple", 1.0]
                        },
                        {
                            "id": 8,
                            "type": "VAEDecode",
                            "pos": [1580, 200],
                            "size": [210, 46],
                            "flags": {},
                            "order": 7,
                            "mode": 0,
                            "inputs": [
                                {"name": "samples", "type": "LATENT", "link": 7},
                                {"name": "vae", "type": "VAE", "link": 8}
                            ],
                            "outputs": [
                                {"name": "IMAGE", "type": "IMAGE", "links": [9], "slot_index": 0}
                            ],
                            "title": "VAE Decode",
                            "properties": {"Node name for S&R": "VAEDecode"},
                            "widgets_values": []
                        },
                        {
                            "id": 9,
                            "type": "SaveImage",
                            "pos": [1830, 200],
                            "size": [315, 270],
                            "flags": {},
                            "order": 8,
                            "mode": 0,
                            "inputs": [
                                {"name": "images", "type": "IMAGE", "link": 9}
                            ],
                            "title": "Save FLUX 3 Frame",
                            "properties": {"Node name for S&R": "SaveImage"},
                            "widgets_values": [f"Keshiki_{card_id or 'Shot'}"]
                        }
                    ],
                    "links": [
                        [1, 1, 0, 7, 0, "MODEL"],
                        [2, 2, 0, 4, 0, "CLIP"],
                        [3, 4, 0, 5, 0, "CONDITIONING"],
                        [4, 4, 0, 7, 2, "CONDITIONING"],
                        [5, 5, 0, 7, 1, "CONDITIONING"],
                        [6, 6, 0, 7, 3, "LATENT"],
                        [7, 7, 0, 8, 0, "LATENT"],
                        [8, 3, 0, 8, 1, "VAE"],
                        [9, 8, 0, 9, 0, "IMAGE"]
                    ],
                    "groups": [],
                    "config": {},
                    "extra": {},
                    "version": 0.4
                }

            # -------------------------------------------------------------
            # CASE C: SDXL HIGH-QUALITY STILL STORYBOARD FRAME (FALLBACK)
            # -------------------------------------------------------------
            else:
                if not sd_ckpts:
                    return add_cors_headers(web.json_response({"status": "error", "error": "No image model for a still frame: install FLUX.3 Klein (install.bat --download-models image)"}, status=400))
                ckpt_name = sd_ckpts[0]
                width = int(data.get("width", 1024))
                height = int(data.get("height", 576))
                try:
                    if "juggernautXL_ragnarokBy.safetensors" in available_ckpts:
                        ckpt_name = "juggernautXL_ragnarokBy.safetensors"
                        width, height = 1024, 576
                    elif "epicrealism_naturalSinRC1VAE.safetensors" in available_ckpts:
                        ckpt_name = "epicrealism_naturalSinRC1VAE.safetensors"
                        width, height = 768, 432
                    else:
                        ckpt_name = sd_ckpts[0]
                except Exception:
                    pass

                workflow = {
                    "1": {
                        "class_type": "CheckpointLoaderSimple",
                        "inputs": {"ckpt_name": ckpt_name}
                    },
                    "2": {
                        "class_type": "CLIPTextEncode",
                        "inputs": {"text": prompt_text, "clip": ["1", 1]}
                    },
                    "3": {
                        "class_type": "CLIPTextEncode",
                        "inputs": {"text": neg_prompt, "clip": ["1", 1]}
                    },
                    "4": {
                        "class_type": "EmptyLatentImage",
                        "inputs": {"width": width, "height": height, "batch_size": 1}
                    },
                    "5": {
                        "class_type": "KSampler",
                        "inputs": {
                            "model": ["1", 0],
                            "positive": ["2", 0],
                            "negative": ["3", 0],
                            "latent_image": ["4", 0],
                            "seed": seed,
                            "steps": 25,
                            "cfg": 6.5,
                            "sampler_name": "dpmpp_2m",
                            "scheduler": "karras",
                            "denoise": 1.0
                        }
                    },
                    "6": {
                        "class_type": "VAEDecode",
                        "inputs": {"samples": ["5", 0], "vae": ["1", 2]}
                    },
                    "7": {
                        "class_type": "SaveImage",
                        "inputs": {"images": ["6", 0], "filename_prefix": f"Keshiki_{card_id or 'Shot'}"}
                    }
                }

                ui_workflow = {
                    "last_node_id": 7,
                    "last_link_id": 9,
                    "nodes": [
                        {
                            "id": 1,
                            "type": "CheckpointLoaderSimple",
                            "pos": [40, 220],
                            "size": [315, 98],
                            "flags": {},
                            "order": 0,
                            "mode": 0,
                            "outputs": [
                                {"name": "MODEL", "type": "MODEL", "links": [1], "slot_index": 0},
                                {"name": "CLIP", "type": "CLIP", "links": [2, 3], "slot_index": 1},
                                {"name": "VAE", "type": "VAE", "links": [8], "slot_index": 2}
                            ],
                            "properties": {"Node name for S&R": "CheckpointLoaderSimple"},
                            "widgets_values": [ckpt_name]
                        },
                        {
                            "id": 2,
                            "type": "CLIPTextEncode",
                            "pos": [400, 100],
                            "size": [420, 200],
                            "flags": {},
                            "order": 1,
                            "mode": 0,
                            "inputs": [
                                {"name": "clip", "type": "CLIP", "link": 2}
                            ],
                            "outputs": [
                                {"name": "CONDITIONING", "type": "CONDITIONING", "links": [4], "slot_index": 0}
                            ],
                            "title": "Positive Prompt (ИИ-Режиссер)",
                            "properties": {"Node name for S&R": "CLIPTextEncode"},
                            "widgets_values": [prompt_text]
                        },
                        {
                            "id": 3,
                            "type": "CLIPTextEncode",
                            "pos": [400, 340],
                            "size": [420, 180],
                            "flags": {},
                            "order": 2,
                            "mode": 0,
                            "inputs": [
                                {"name": "clip", "type": "CLIP", "link": 3}
                            ],
                            "outputs": [
                                {"name": "CONDITIONING", "type": "CONDITIONING", "links": [5], "slot_index": 0}
                            ],
                            "title": "Negative Prompt",
                            "properties": {"Node name for S&R": "CLIPTextEncode"},
                            "widgets_values": [neg_prompt]
                        },
                        {
                            "id": 4,
                            "type": "EmptyLatentImage",
                            "pos": [400, 560],
                            "size": [315, 106],
                            "flags": {},
                            "order": 3,
                            "mode": 0,
                            "outputs": [
                                {"name": "LATENT", "type": "LATENT", "links": [6], "slot_index": 0}
                            ],
                            "properties": {"Node name for S&R": "EmptyLatentImage"},
                            "widgets_values": [width, height, 1]
                        },
                        {
                            "id": 5,
                            "type": "KSampler",
                            "pos": [870, 200],
                            "size": [315, 262],
                            "flags": {},
                            "order": 4,
                            "mode": 0,
                            "inputs": [
                                {"name": "model", "type": "MODEL", "link": 1},
                                {"name": "positive", "type": "CONDITIONING", "link": 4},
                                {"name": "negative", "type": "CONDITIONING", "link": 5},
                                {"name": "latent_image", "type": "LATENT", "link": 6}
                            ],
                            "outputs": [
                                {"name": "LATENT", "type": "LATENT", "links": [7], "slot_index": 0}
                            ],
                            "properties": {"Node name for S&R": "KSampler"},
                            "widgets_values": [seed, "randomize", 25, 6.5, "dpmpp_2m", "karras", 1.0]
                        },
                        {
                            "id": 6,
                            "type": "VAEDecode",
                            "pos": [1230, 200],
                            "size": [210, 46],
                            "flags": {},
                            "order": 5,
                            "mode": 0,
                            "inputs": [
                                {"name": "samples", "type": "LATENT", "link": 7},
                                {"name": "vae", "type": "VAE", "link": 8}
                            ],
                            "outputs": [
                                {"name": "IMAGE", "type": "IMAGE", "links": [9], "slot_index": 0}
                            ],
                            "properties": {"Node name for S&R": "VAEDecode"},
                            "widgets_values": []
                        },
                        {
                            "id": 7,
                            "type": "SaveImage",
                            "pos": [1230, 300],
                            "size": [315, 270],
                            "flags": {},
                            "order": 6,
                            "mode": 0,
                            "inputs": [
                                {"name": "images", "type": "IMAGE", "link": 9}
                            ],
                            "properties": {"Node name for S&R": "SaveImage"},
                            "widgets_values": [f"Keshiki_{card_id or 'Shot'}"]
                        }
                    ],
                    "links": [
                        [1, 1, 0, 5, 0, "MODEL"],
                        [2, 1, 1, 2, 0, "CLIP"],
                        [3, 1, 1, 3, 0, "CLIP"],
                        [4, 2, 0, 5, 1, "CONDITIONING"],
                        [5, 3, 0, 5, 2, "CONDITIONING"],
                        [6, 4, 0, 5, 3, "LATENT"],
                        [7, 5, 0, 6, 0, "LATENT"],
                        [8, 1, 2, 6, 1, "VAE"],
                        [9, 6, 0, 7, 0, "IMAGE"]
                    ],
                    "groups": [],
                    "config": {},
                    "extra": {},
                    "version": 0.4
                }

            try:
                suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                cache_dir = os.path.join(suite_root, "output_cache")
                os.makedirs(cache_dir, exist_ok=True)
                with open(os.path.join(cache_dir, "latest_agent_workflow.json"), "w", encoding="utf-8") as wf_f:
                    json.dump(ui_workflow, wf_f, indent=2, ensure_ascii=False)
                with open(os.path.join(cache_dir, "latest_agent_prompt.json"), "w", encoding="utf-8") as p_f:
                    json.dump(workflow, p_f, indent=2, ensure_ascii=False)
            except Exception:
                pass

            try:
                # VRAM Management Rule: Evict heavy LLMs from GPU VRAM before queueing diffusion models,
                # keeping resident micro-dispatcher (qwen3.5:0.8b ~550MB) warm and responsive
                try:
                    from .vfx_agent_service import unload_agent_from_vram
                    await asyncio.to_thread(unload_agent_from_vram, preserve_micro=True)
                except Exception as evict_err:
                    LOGGER.debug(f"[VRAM Rule] Pre-queue evict note: {evict_err}")

                import aiohttp
                post_payload = {
                    "prompt": workflow,
                    "extra_data": {
                        "extra_pnginfo": {
                            "workflow": ui_workflow
                        }
                    }
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{_COMFY_URL}/prompt", json=post_payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        resp_json = await resp.json()
                        prompt_id = resp_json.get("prompt_id")

                return add_cors_headers(web.json_response({
                    "status": "queued",
                    "prompt_id": prompt_id,
                    "card_id": card_id,
                    "checkpoint": ckpt_name,
                    "custom_workflow": custom_wf,
                    "workflow_title": os.path.basename(custom_wf) if custom_wf else ("LTX-Video 2.5 Interpolation" if is_video else "FLUX.3 Klein"),
                    "width": width,
                    "height": height,
                    "ui_workflow": ui_workflow
                }))
            except Exception as e:
                return add_cors_headers(web.json_response({"status": "error", "error": str(e)}, status=500))

        @routes.get("/vfx/agent/workflow_status")
        async def vfx_agent_workflow_status(request):
            prompt_id = request.query.get("prompt_id", "")
            if not prompt_id:
                return add_cors_headers(web.json_response({"status": "error", "error": "Missing prompt_id"}, status=400))
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{_COMFY_URL}/history/{prompt_id}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        data = await resp.json()
                if prompt_id in data:
                    item = data[prompt_id]
                    outputs = item.get("outputs", {})
                    images = []
                    videos = []
                    video_url = None
                    for nid, nout in outputs.items():
                        if "gifs" in nout:
                            for vid in nout["gifs"]:
                                fn = vid.get("filename")
                                sub = vid.get("subfolder", "")
                                itype = vid.get("type", "output")
                                format_type = vid.get("format", "video/h264-mp4")
                                url = f"/view?filename={fn}&type={itype}&format={format_type}"
                                if sub:
                                    url += f"&subfolder={sub}"
                                videos.append({"url": url, "filename": fn, "node_id": nid})
                                if not video_url:
                                    video_url = url
                        if "videos" in nout:
                            for vid in nout["videos"]:
                                fn = vid.get("filename")
                                sub = vid.get("subfolder", "")
                                itype = vid.get("type", "output")
                                url = f"/view?filename={fn}&type={itype}"
                                if sub:
                                    url += f"&subfolder={sub}"
                                videos.append({"url": url, "filename": fn, "node_id": nid})
                                if not video_url:
                                    video_url = url
                        if "images" in nout:
                            for img in nout["images"]:
                                fn = img.get("filename")
                                sub = img.get("subfolder", "")
                                itype = img.get("type", "output")
                                url = f"/view?filename={fn}&type={itype}"
                                if sub:
                                    url += f"&subfolder={sub}"
                                images.append({
                                    "url": url,
                                    "filename": fn,
                                    "node_id": nid
                                })
                    return add_cors_headers(web.json_response({
                        "status": "completed",
                        "prompt_id": prompt_id,
                        "images": images,
                        "videos": videos,
                        "video_url": video_url or (videos[0]["url"] if videos else None)
                    }))
                else:
                    return add_cors_headers(web.json_response({"status": "running", "prompt_id": prompt_id}))
            except Exception as e:
                return add_cors_headers(web.json_response({"status": "error", "error": str(e)}, status=500))

        def _convert_ui_workflow_to_api_prompt(ui_wf):
            if not isinstance(ui_wf, dict):
                return {}
            nodes = ui_wf.get("nodes", [])
            links = ui_wf.get("links", [])
            link_map = {l[0]: (str(l[1]), l[2]) for l in links if len(l) >= 3}
            
            prompt = {}
            for node in nodes:
                nid = str(node.get("id"))
                ctype = node.get("type", "")
                wvals = node.get("widgets_values") or []
                inputs = {}
                for inp in node.get("inputs", []):
                    lk = inp.get("link")
                    nm = inp.get("name")
                    if lk is not None and lk in link_map:
                        inputs[nm] = [link_map[lk][0], int(link_map[lk][1])]
                        
                if ctype == "UNETLoader":
                    if len(wvals) > 0 and "unet_name" not in inputs: inputs["unet_name"] = str(wvals[0])
                    if len(wvals) > 1 and "weight_dtype" not in inputs: inputs["weight_dtype"] = str(wvals[1])
                elif ctype == "CLIPLoader":
                    if len(wvals) > 0 and "clip_name" not in inputs: inputs["clip_name"] = str(wvals[0])
                    if len(wvals) > 1 and "type" not in inputs: inputs["type"] = str(wvals[1])
                elif ctype == "VAELoader":
                    if len(wvals) > 0 and "vae_name" not in inputs: inputs["vae_name"] = str(wvals[0])
                elif ctype == "CheckpointLoaderSimple":
                    if len(wvals) > 0 and "ckpt_name" not in inputs: inputs["ckpt_name"] = str(wvals[0])
                elif ctype == "CLIPTextEncode":
                    if len(wvals) > 0 and "text" not in inputs: inputs["text"] = str(wvals[0])
                elif ctype == "FluxGuidance":
                    if len(wvals) > 0 and "guidance" not in inputs:
                        try: inputs["guidance"] = float(wvals[0])
                        except Exception: inputs["guidance"] = 3.5
                elif ctype in ("EmptyFlux2LatentImage", "EmptyLatentImage"):
                    if len(wvals) > 0 and "width" not in inputs:
                        try: inputs["width"] = int(wvals[0])
                        except Exception: inputs["width"] = 1024
                    if len(wvals) > 1 and "height" not in inputs:
                        try: inputs["height"] = int(wvals[1])
                        except Exception: inputs["height"] = 576
                    if len(wvals) > 2 and "batch_size" not in inputs:
                        try: inputs["batch_size"] = int(wvals[2])
                        except Exception: inputs["batch_size"] = 1
                elif ctype == "EmptyLTXVLatentVideo":
                    if len(wvals) > 0 and "width" not in inputs:
                        try: inputs["width"] = int(wvals[0])
                        except Exception: inputs["width"] = 768
                    if len(wvals) > 1 and "height" not in inputs:
                        try: inputs["height"] = int(wvals[1])
                        except Exception: inputs["height"] = 512
                    if len(wvals) > 2 and "length" not in inputs:
                        try: inputs["length"] = int(wvals[2])
                        except Exception: inputs["length"] = 97
                    if len(wvals) > 3 and "batch_size" not in inputs:
                        try: inputs["batch_size"] = int(wvals[3])
                        except Exception: inputs["batch_size"] = 1
                elif ctype == "LTXVConditioning":
                    if len(wvals) > 0 and "frame_rate" not in inputs:
                        try: inputs["frame_rate"] = float(wvals[0])
                        except Exception: inputs["frame_rate"] = 24.0
                elif ctype == "KSampler":
                    # widgets format: [seed, control_after_gen, steps, cfg, sampler_name, scheduler, denoise]
                    # or: [seed, steps, cfg, sampler_name, scheduler, denoise]
                    clean_vals = [v for v in wvals if not isinstance(v, str) or v not in ("randomize", "fixed", "increment", "decrement")]
                    if len(wvals) >= 7 and isinstance(wvals[1], str) and wvals[1] in ("randomize", "fixed", "increment", "decrement"):
                        try: inputs["seed"] = int(wvals[0])
                        except Exception: inputs["seed"] = 12345
                        try: inputs["steps"] = int(wvals[2])
                        except Exception: inputs["steps"] = 25
                        try: inputs["cfg"] = float(wvals[3])
                        except Exception: inputs["cfg"] = 1.0
                        inputs["sampler_name"] = str(wvals[4])
                        inputs["scheduler"] = str(wvals[5])
                        try: inputs["denoise"] = float(wvals[6])
                        except Exception: inputs["denoise"] = 1.0
                    elif len(clean_vals) >= 6:
                        try: inputs["seed"] = int(clean_vals[0])
                        except Exception: inputs["seed"] = 12345
                        try: inputs["steps"] = int(clean_vals[1])
                        except Exception: inputs["steps"] = 25
                        try: inputs["cfg"] = float(clean_vals[2])
                        except Exception: inputs["cfg"] = 1.0
                        inputs["sampler_name"] = str(clean_vals[3])
                        inputs["scheduler"] = str(clean_vals[4])
                        try: inputs["denoise"] = float(clean_vals[5])
                        except Exception: inputs["denoise"] = 1.0
                elif ctype == "SaveImage":
                    if len(wvals) > 0 and "filename_prefix" not in inputs: inputs["filename_prefix"] = str(wvals[0])
                elif ctype == "VHS_VideoCombine":
                    if len(wvals) > 0 and "frame_rate" not in inputs:
                        try: inputs["frame_rate"] = int(wvals[0])
                        except Exception: inputs["frame_rate"] = 24
                    if len(wvals) > 2 and "filename_prefix" not in inputs: inputs["filename_prefix"] = str(wvals[2])
                    inputs["format"] = "video/h264-mp4"
                    inputs["save_output"] = True
                    
                prompt[nid] = {"class_type": ctype, "inputs": inputs}
            return prompt

        @routes.get("/vfx/agent/latest_workflow")
        async def vfx_agent_latest_workflow(request):
            suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_file = os.path.join(suite_root, "output_cache", "latest_agent_workflow.json")
            prompt_file = os.path.join(suite_root, "output_cache", "latest_agent_prompt.json")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # Load or derive API prompt
                    api_prompt = {}
                    if os.path.exists(prompt_file):
                        try:
                            with open(prompt_file, "r", encoding="utf-8") as pf:
                                api_prompt = json.load(pf)
                        except Exception:
                            pass
                    if not api_prompt and isinstance(data, dict) and "nodes" in data:
                        api_prompt = _convert_ui_workflow_to_api_prompt(data)
                    data["api_prompt"] = api_prompt
                    return add_cors_headers(web.json_response(data))
                except Exception as e:
                    return add_cors_headers(web.json_response({"error": str(e)}, status=500))
            return add_cors_headers(web.json_response({"error": "No workflow generated yet"}, status=404))

        @routes.post("/vfx/agent/save_workflow")
        async def vfx_agent_save_workflow(request):
            import time
            try:
                body = await request.json()
            except Exception:
                return add_cors_headers(web.json_response({"status": "error", "error": "Invalid JSON body"}, status=400))
            
            ui_wf = body.get("workflow") if "workflow" in body else body
            api_prompt = body.get("prompt")
            
            if not isinstance(ui_wf, dict) or ("nodes" not in ui_wf and not api_prompt):
                return add_cors_headers(web.json_response({"status": "error", "error": "Missing valid workflow graph"}, status=400))
                
            suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_dir = os.path.join(suite_root, "output_cache")
            os.makedirs(cache_dir, exist_ok=True)
            cache_file = os.path.join(cache_dir, "latest_agent_workflow.json")
            prompt_file = os.path.join(cache_dir, "latest_agent_prompt.json")
            
            try:
                # If api_prompt not explicitly supplied, synthesize from UI workflow
                if not api_prompt:
                    api_prompt = _convert_ui_workflow_to_api_prompt(ui_wf)
                    
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(ui_wf, f, indent=2, ensure_ascii=False)
                with open(prompt_file, "w", encoding="utf-8") as f:
                    json.dump(api_prompt, f, indent=2, ensure_ascii=False)
                    
                return add_cors_headers(web.json_response({
                    "status": "saved",
                    "nodes_count": len(ui_wf.get("nodes", [])),
                    "cache_file": cache_file,
                    "updated_at": time.time()
                }))
            except Exception as e:
                return add_cors_headers(web.json_response({"status": "error", "error": str(e)}, status=500))

        @routes.post("/vfx/agent/execute_headless_workflow")
        async def vfx_agent_execute_headless_workflow(request):
            """Executes a workflow purely under the hood via ComfyUI /prompt API without switching tabs."""
            try:
                body = await request.json()
            except Exception:
                return add_cors_headers(web.json_response({"status": "error", "error": "Invalid JSON body"}, status=400))
                
            ui_wf = body.get("workflow") if "workflow" in body else body
            api_prompt = body.get("prompt")
            card_id = str(body.get("card_id", "previz_shot")).strip()
            
            if not isinstance(ui_wf, dict):
                return add_cors_headers(web.json_response({"status": "error", "error": "Invalid workflow format"}, status=400))
                
            suite_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_dir = os.path.join(suite_root, "output_cache")
            os.makedirs(cache_dir, exist_ok=True)
            cache_file = os.path.join(cache_dir, "latest_agent_workflow.json")
            prompt_file = os.path.join(cache_dir, "latest_agent_prompt.json")
            
            if not api_prompt:
                api_prompt = _convert_ui_workflow_to_api_prompt(ui_wf)
                
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(ui_wf, f, indent=2, ensure_ascii=False)
                with open(prompt_file, "w", encoding="utf-8") as f:
                    json.dump(api_prompt, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
                
            # Evict LLMs from VRAM before starting diffusion
            try:
                from .vfx_agent_service import unload_agent_from_vram
                await asyncio.to_thread(unload_agent_from_vram)
            except Exception as evict_err:
                LOGGER.debug(f"[VRAM Rule] Pre-headless evict note: {evict_err}")
                
            # Queue to ComfyUI Headless
            try:
                import aiohttp
                post_payload = {
                    "prompt": api_prompt,
                    "extra_data": {
                        "extra_pnginfo": {
                            "workflow": ui_wf
                        }
                    }
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{_COMFY_URL}/prompt", json=post_payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        resp_json = await resp.json()
                        prompt_id = resp_json.get("prompt_id")
                        
                # Determine title/checkpoint info for UI display
                ckpt_name = "FLUX 3 / LTX Workflow"
                for nid, nval in api_prompt.items():
                    ct = nval.get("class_type")
                    if ct == "UNETLoader":
                        ckpt_name = nval.get("inputs", {}).get("unet_name", "FLUX 3 Klein")
                        break
                    elif ct == "CheckpointLoaderSimple":
                        ckpt_name = nval.get("inputs", {}).get("ckpt_name", "LTX-Video Checkpoint")
                        break
                        
                return add_cors_headers(web.json_response({
                    "status": "queued",
                    "prompt_id": prompt_id,
                    "card_id": card_id,
                    "checkpoint": ckpt_name,
                    "ui_workflow": ui_wf
                }))
            except Exception as e:
                return add_cors_headers(web.json_response({"status": "error", "error": str(e)}, status=500))

        # --- Audio & TTS Endpoints (Non-blocking via asyncio.to_thread) ---
        @routes.post("/vfx/audio/tts")
        async def vfx_audio_tts(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            text = str(data.get("text", "")).strip()
            voice = str(data.get("voice", "dmitry")).strip()
            auto_translate = bool(data.get("auto_translate", False))
            fps = float(data.get("fps", 24.0))
            try:
                res = await asyncio.to_thread(
                    generate_speech,
                    text,
                    voice=voice,
                    auto_translate=auto_translate,
                    fps=fps
                )
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.get("/vfx/audio/current")
        async def vfx_audio_current(request):
            wav_path = os.path.join(AUDIO_DIR, "current_dialogue.wav")
            if os.path.exists(wav_path):
                return add_cors_headers(web.FileResponse(wav_path, headers={"Content-Type": "audio/wav"}))
            mp3_path = os.path.join(AUDIO_DIR, "current_dialogue.mp3")
            if os.path.exists(mp3_path):
                return add_cors_headers(web.FileResponse(mp3_path, headers={"Content-Type": "audio/mpeg"}))
            return add_cors_headers(web.Response(status=404, text="No audio generated yet."))

        @routes.get("/vfx/audio/info")
        async def vfx_audio_info(request):
            return add_cors_headers(web.json_response(get_current_audio_info()))

        # --- 3D Scene Data Endpoints ---
        @routes.get("/vfx/scene_data")
        async def get_scene_data(request):
            pid = request.query.get("project_id", "").strip()
            if pid:
                p_dir = get_project_dir(pid)
                p_scene = os.path.join(p_dir, "passes", "scene_data.json")
                if os.path.exists(p_scene):
                    try:
                        with open(p_scene, "r", encoding="utf-8") as f:
                            return add_cors_headers(web.json_response(json.load(f)))
                    except Exception:
                        pass
                p_meta = os.path.join(p_dir, "project.json")
                if os.path.exists(p_meta):
                    try:
                        with open(p_meta, "r", encoding="utf-8") as f:
                            pj = json.load(f)
                            if "scene3d" in pj and pj["scene3d"]:
                                return add_cors_headers(web.json_response(pj["scene3d"]))
                    except Exception:
                        pass
                return add_cors_headers(web.json_response({"points": [], "colors": [], "camera": None, "total_frames": 0}))

            scene_path = os.path.join(CACHE_DIR, "scene_data.json")
            if os.path.exists(scene_path):
                try:
                    with open(scene_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return add_cors_headers(web.json_response(data))
                except Exception as e:
                    return add_cors_headers(web.json_response({"error": str(e), "points": [], "matrices": []}))
            return add_cors_headers(web.json_response({"points": [], "matrices": [], "total_frames": 0}))

        # --- Standalone 3D Layout Studio Endpoints (Web Browser) ---
        STANDALONE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "standalone")

        @routes.get("/vfx/studio")
        @routes.get("/vfx/studio/")
        async def serve_studio_index(request):
            index_path = os.path.join(STANDALONE_DIR, "index.html")
            if os.path.exists(index_path):
                return add_cors_headers(web.FileResponse(index_path))
            return add_cors_headers(web.Response(status=404, text="VFX Studio index.html not found"))

        @routes.get("/vfx/studio/{filename:.+}")
        async def serve_studio_static(request):
            filename = request.match_info.get("filename", "")
            target = os.path.normpath(os.path.join(STANDALONE_DIR, filename))
            if os.path.commonpath([STANDALONE_DIR, target]) == STANDALONE_DIR and os.path.isfile(target):
                return add_cors_headers(web.FileResponse(target))
            return add_cors_headers(web.Response(status=404, text=f"File not found: {filename}"))

        @routes.get("/favicon.ico")
        async def serve_favicon_ico(request):
            fav_path = os.path.join(STANDALONE_DIR, "favicon.ico")
            if os.path.exists(fav_path):
                return add_cors_headers(web.FileResponse(fav_path))
            return add_cors_headers(web.Response(status=404))

        @routes.get("/vfx/layout_manifest")
        async def get_layout_manifest(request):
            pid = request.query.get("project_id", "").strip()
            if pid:
                p_dir = get_project_dir(pid)
                p_manifest = os.path.join(p_dir, "passes", "layout_manifest.json")
                if os.path.exists(p_manifest):
                    try:
                        with open(p_manifest, "r", encoding="utf-8") as f:
                            return add_cors_headers(web.json_response(json.load(f)))
                    except Exception:
                        pass
                p_meta = os.path.join(p_dir, "project.json")
                if os.path.exists(p_meta):
                    try:
                        with open(p_meta, "r", encoding="utf-8") as f:
                            pj = json.load(f)
                            if "manifest3d" in pj and isinstance(pj["manifest3d"], list):
                                return add_cors_headers(web.json_response(pj["manifest3d"]))
                    except Exception:
                        pass
                return add_cors_headers(web.json_response([]))

            manifest_path = os.path.join(CACHE_DIR, "layout_manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return add_cors_headers(web.json_response(data))
                except Exception as e:
                    return add_cors_headers(web.json_response({"error": str(e), "manifest": []}))
            return add_cors_headers(web.json_response([]))

        @routes.get("/vfx/export_chan")
        async def get_export_chan(request):
            scene_path = os.path.join(CACHE_DIR, "scene_data.json")
            if not os.path.exists(scene_path):
                return add_cors_headers(web.Response(status=404, text="No scene data available to export"))
            try:
                import math
                with open(scene_path, "r", encoding="utf-8") as f:
                    scene_data = json.load(f)
                cam = scene_data.get("camera", {})
                matrices = cam.get("matrices", [])
                if not matrices:
                    return add_cors_headers(web.Response(status=400, text="No camera matrices available"))
                focal = float(cam.get("focal_mm", 35.0))
                sensor_w = float(cam.get("sensor_width_mm", 36.0))
                fov_deg = 2.0 * math.degrees(math.atan((sensor_w / 2.0) / max(0.1, focal)))
                lines = []
                for i, mat in enumerate(matrices):
                    frame_num = 1 + i
                    tx = mat[0][3]
                    ty = mat[1][3]
                    tz = mat[2][3]
                    sy = math.sqrt(mat[0][0] * mat[0][0] + mat[1][0] * mat[1][0])
                    if sy >= 1e-6:
                        rx = math.degrees(math.atan2(mat[2][1], mat[2][2]))
                        ry = math.degrees(math.atan2(-mat[2][0], sy))
                        rz = math.degrees(math.atan2(mat[1][0], mat[0][0]))
                    else:
                        rx = math.degrees(math.atan2(-mat[1][2], mat[1][1]))
                        ry = math.degrees(math.atan2(-mat[2][0], sy))
                        rz = 0.0
                    lines.append(f"{frame_num}\t{tx:.6f}\t{ty:.6f}\t{tz:.6f}\t{rx:.6f}\t{ry:.6f}\t{rz:.6f}\t{fov_deg:.4f}")
                chan_content = "\n".join(lines) + "\n"
                resp = web.Response(body=chan_content.encode("utf-8"), content_type="text/plain")
                resp.headers["Content-Disposition"] = 'attachment; filename="vfx_camera_track.chan"'
                return add_cors_headers(resp)
            except Exception as e:
                return add_cors_headers(web.Response(status=500, text=f"Error exporting chan: {e}"))


        @routes.get("/vfx/actor_cutout/{frame}")
        async def get_actor_cutout(request):
            frame_str = request.match_info.get("frame", "0")
            try:
                frame_idx = int(frame_str)
            except ValueError:
                frame_idx = 0

            file_path = os.path.join(ACTOR_CUTOUT_DIR, f"frame_{frame_idx:04d}.png")
            if not os.path.exists(file_path):
                file_path = os.path.join(ACTOR_CUTOUT_DIR, "frame_0000.png")

            if os.path.exists(file_path):
                return add_cors_headers(web.FileResponse(file_path))
            return add_cors_headers(web.Response(status=404, text="Actor cutout frame not found"))

        # --- In-Node Layout 3D WebGL Render Endpoints ---
        @routes.post("/vfx/save_layout_batch")
        async def save_layout_batch(request):
            try:
                data = await request.json()
            except Exception as e:
                return add_cors_headers(web.json_response({"error": f"Invalid JSON payload: {e}"}, status=400))

            frames = data.get("frames", [])
            manifest = data.get("manifest", None)
            total_frames = data.get("total_frames", len(frames))

            # Save scene manifest if provided
            if manifest is not None:
                paths = [os.path.join(CACHE_DIR, "layout_manifest.json")]
                pid_m = str(data.get("project_id") or "").strip()
                if pid_m:  # per project: this is what /vfx/layout_manifest?project_id= reads back on reload
                    paths.insert(0, os.path.join(get_project_dir(_sanitize_id(pid_m)), "passes", "layout_manifest.json"))
                for manifest_path in paths:
                    try:
                        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
                        with open(manifest_path, "w", encoding="utf-8") as f:
                            json.dump(manifest, f, indent=2)
                    except Exception as e:
                        print(f"[ComfyUI-VFX-Suite] Error saving layout manifest: {e}")

            saved_count = 0
            for item in frames:
                # item can be {"frame": 0, "image": "data:image/png;base64,..."} or string
                if isinstance(item, dict):
                    f_idx = int(item.get("frame", saved_count))
                    img_data = item.get("image", "")
                else:
                    f_idx = saved_count
                    img_data = str(item)

                if "," in img_data:
                    img_data = img_data.split(",", 1)[1]

                try:
                    raw_bytes = base64.b64decode(img_data)
                    frame_path = os.path.join(LAYOUT_RENDER_DIR, f"frame_{f_idx:04d}.png")
                    with open(frame_path, "wb") as f_out:
                        f_out.write(raw_bytes)
                    saved_count += 1
                except Exception as e:
                    print(f"[ComfyUI-VFX-Suite] Failed writing layout render frame {f_idx}: {e}")

            return add_cors_headers(web.json_response({
                "status": "ok",
                "saved_count": saved_count,
                "total_frames": total_frames
            }))

        @routes.get("/vfx/layout_render/{frame}")
        async def get_layout_render_frame(request):
            frame_str = request.match_info.get("frame", "0")
            try:
                frame_idx = int(frame_str)
            except ValueError:
                frame_idx = 0

            file_path = os.path.join(LAYOUT_RENDER_DIR, f"frame_{frame_idx:04d}.png")
            if not os.path.exists(file_path):
                file_path = os.path.join(LAYOUT_RENDER_DIR, "frame_0000.png")

            if os.path.exists(file_path):
                return add_cors_headers(web.FileResponse(file_path))
            return add_cors_headers(web.Response(status=404, text=f"Layout render frame {frame_idx} not found"))

        @routes.get("/vfx/layout_render_info")
        async def get_layout_render_info(request):
            files = []
            if os.path.exists(LAYOUT_RENDER_DIR):
                files = sorted([f for f in os.listdir(LAYOUT_RENDER_DIR) if f.startswith("frame_") and f.endswith(".png")])
            return add_cors_headers(web.json_response({
                "has_renders": len(files) > 0,
                "count": len(files),
                "render_dir": LAYOUT_RENDER_DIR
            }))


        def extract_node_id(request, json_data=None):
            nid = request.match_info.get("node_id")
            if not nid:
                nid = request.query.get("node_id")
            if not nid and json_data and isinstance(json_data, dict):
                nid = json_data.get("node_id")
            return str(nid) if nid else "default"

        # --- Per-Node Plate Prep Endpoints (Offloaded to Worker Threads via asyncio.to_thread) ---
        @routes.get("/vfx/plate_prep/{node_id}/info")
        @routes.get("/vfx/plate_prep/info")
        async def plate_info(request):
            node_id = extract_node_id(request)
            return add_cors_headers(web.json_response(get_info(node_id)))

        @routes.post("/vfx/plate_prep/{node_id}/load")
        @routes.post("/vfx/plate_prep/load")
        async def plate_load(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                path = data.get("input_path", "").strip()
                res = await asyncio.to_thread(load_input_source, path, node_id=node_id)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/undistort")
        @routes.post("/vfx/plate_prep/undistort")
        async def plate_undistort(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                k1 = float(data.get("k1", 0.0))
                k2 = float(data.get("k2", 0.0))
                res = await asyncio.to_thread(apply_undistortion, k1, k2, node_id=node_id)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/auto_mask")
        @routes.post("/vfx/plate_prep/auto_mask")
        async def plate_auto_mask(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                classes = data.get("mask_classes", "person, car, truck, bus, motorcycle")
                detect_props = bool(data.get("detect_props", True))
                engine = data.get("engine", "sam2")
                res = await asyncio.to_thread(apply_auto_mask, classes, detect_props, engine=engine, node_id=node_id)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/corridor_key")
        @routes.post("/vfx/plate_prep/corridor_key")
        async def plate_corridor_key(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                despill = float(data.get("despill_strength", 1.0))
                refiner = float(data.get("refiner_strength", 1.0))
                auto_despeckle = str(data.get("auto_despeckle", "On"))
                despeckle_size = int(data.get("despeckle_size", 400))
                res = await asyncio.to_thread(
                    apply_corridor_key,
                    despill_strength=despill,
                    refiner_strength=refiner,
                    auto_despeckle=auto_despeckle,
                    despeckle_size=despeckle_size,
                    node_id=node_id
                )
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/thickness")
        @routes.post("/vfx/plate_prep/thickness")
        async def plate_thickness(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                thickness = int(data.get("thickness", 0))
                res = await asyncio.to_thread(apply_thickness, thickness, node_id=node_id)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/manual_paint")
        @routes.post("/vfx/plate_prep/manual_paint")
        async def plate_manual_paint(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                frame_idx = int(data.get("frame_idx", 0))
                mode = data.get("mode", "add")
                mask_b64 = data.get("mask_base64", "")
                if "," in mask_b64:
                    mask_b64 = mask_b64.split(",", 1)[1]
                mask_bytes = base64.b64decode(mask_b64)
                res = await asyncio.to_thread(apply_manual_paint, frame_idx, mode, mask_bytes, node_id=node_id)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/upload")
        @routes.post("/vfx/plate_prep/upload")
        async def plate_upload(request):
            node_id = extract_node_id(request)
            try:
                reader = await request.multipart()
                files_dict = {}
                video_bytes = None
                video_name = None

                while True:
                    part = await reader.next()
                    if part is None:
                        break
                    if part.name == "node_id":
                        val = (await part.text()).strip()
                        if val:
                            node_id = val
                    elif part.filename:
                        data = await part.read()
                        ext = os.path.splitext(part.filename)[1].lower()
                        if ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm']:
                            video_bytes = data
                            video_name = part.filename
                        elif ext in ['.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff']:
                            files_dict[part.filename] = data

                if video_bytes is not None:
                    res = await asyncio.to_thread(save_uploaded_video, video_name, video_bytes, node_id=node_id)
                    return add_cors_headers(web.json_response(res))
                elif files_dict:
                    res = await asyncio.to_thread(save_uploaded_sequence, files_dict, node_id=node_id)
                    return add_cors_headers(web.json_response(res))
                else:
                    return add_cors_headers(web.json_response({"error": "No valid video or sequence files found in upload"}, status=400))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/upload_mask")
        @routes.post("/vfx/plate_prep/upload_mask")
        async def plate_upload_mask(request):
            node_id = extract_node_id(request)
            try:
                reader = await request.multipart()
                files_dict = {}
                video_bytes = None
                video_name = None
                single_image_bytes = None
                single_image_name = None
                apply_all = False
                frame_idx = 0

                while True:
                    part = await reader.next()
                    if part is None:
                        break
                    if part.name == "node_id":
                        val = (await part.text()).strip()
                        if val:
                            node_id = val
                    elif part.name == "frame_idx":
                        try:
                            frame_idx = int((await part.text()).strip())
                        except ValueError:
                            frame_idx = 0
                    elif part.name == "apply_all":
                        val = (await part.text()).strip().lower()
                        apply_all = val in ["true", "1", "yes"]
                    elif part.filename:
                        data = await part.read()
                        ext = os.path.splitext(part.filename)[1].lower()
                        if ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm']:
                            video_bytes = data
                            video_name = part.filename
                        elif ext in ['.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff']:
                            files_dict[part.filename] = data
                            single_image_bytes = data
                            single_image_name = part.filename

                if video_bytes is not None:
                    res = await asyncio.to_thread(save_uploaded_mask_video, video_name, video_bytes, node_id=node_id)
                    return add_cors_headers(web.json_response(res))
                elif len(files_dict) > 1:
                    res = await asyncio.to_thread(save_uploaded_mask_sequence, files_dict, node_id=node_id)
                    return add_cors_headers(web.json_response(res))
                elif single_image_bytes is not None:
                    res = await asyncio.to_thread(save_uploaded_mask_single, single_image_name, single_image_bytes, frame_idx=frame_idx, apply_all=apply_all, node_id=node_id)
                    return add_cors_headers(web.json_response(res))
                else:
                    return add_cors_headers(web.json_response({"error": "No valid mask video or image files found in upload"}, status=400))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/auto_calibrate")
        @routes.post("/vfx/plate_prep/auto_calibrate")
        async def plate_auto_calibrate(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                res = await asyncio.to_thread(auto_calibrate_distortion, node_id=node_id)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/clear_frame")
        @routes.post("/vfx/plate_prep/clear_frame")
        async def plate_clear_frame(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                frame_idx = int(data.get("frame_idx", 0))
                res = await asyncio.to_thread(clear_frame_mask, frame_idx, node_id=node_id)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/reset_masks")
        @routes.post("/vfx/plate_prep/reset_masks")
        async def plate_reset_masks(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                res = await asyncio.to_thread(reset_all_masks, node_id=node_id)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/interpolate_keyframes")
        @routes.post("/vfx/plate_prep/interpolate_keyframes")
        async def plate_interpolate_keyframes(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                res = await asyncio.to_thread(interpolate_keyframes, node_id=node_id)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/plate_prep/{node_id}/set_invert")
        @routes.post("/vfx/plate_prep/set_invert")
        async def plate_set_invert(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            node_id = extract_node_id(request, data)
            try:
                invert_val = bool(data.get("invert", False))
                updated = save_info({"invert_mask": invert_val}, node_id=node_id)
                return add_cors_headers(web.json_response({"success": True, "invert_mask": updated.get("invert_mask", False)}))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        # True RGBA Ruby Mask Overlay endpoint
        @routes.get("/vfx/plate_prep/{node_id}/frame/mask_overlay/{frame_idx}")
        @routes.get("/vfx/plate_prep/frame/mask_overlay/{frame_idx}")
        async def plate_mask_overlay_frame(request):
            node_id = extract_node_id(request)
            frame_str = request.match_info.get("frame_idx", "0")
            try:
                fid = int(frame_str)
            except ValueError:
                fid = 0
            png_bytes = await asyncio.to_thread(generate_tinted_mask_overlay_png, fid, node_id=node_id)
            return add_cors_headers(web.Response(body=png_bytes, content_type="image/png"))

        # Standard frames (raw, undist, mask, cutout)
        @routes.get("/vfx/plate_prep/{node_id}/frame/{stage}/{frame_idx}")
        @routes.get("/vfx/plate_prep/frame/{stage}/{frame_idx}")
        async def plate_frame(request):
            node_id = extract_node_id(request)
            stage = request.match_info.get("stage", "raw")
            frame_str = request.match_info.get("frame_idx", "0")
            try:
                fid = int(frame_str)
            except ValueError:
                fid = 0

            d = get_dirs(node_id)
            stage_dirs = {
                "raw": (d["raw"], ".jpg"),
                "undist": (d["undist"], ".jpg"),
                "mask": (d["finalmask"], ".png"),
                "cutout": (d["cutout"], ".png")
            }

            if stage in stage_dirs:
                s_dir, ext = stage_dirs[stage]
                f_path = os.path.join(s_dir, f"frame_{fid:04d}{ext}")
                if not os.path.exists(f_path):
                    # Fallback to shared output_cache/plate/{stage} directory
                    fallback_dir = os.path.join(CACHE_DIR, "plate", "finalmask" if stage == "mask" else stage)
                    alt_path = os.path.join(fallback_dir, f"frame_{fid:04d}{ext}")
                    if os.path.exists(alt_path):
                        f_path = alt_path
                    else:
                        f_path = os.path.join(s_dir, f"frame_0000{ext}")
                        if not os.path.exists(f_path):
                            f_path = os.path.join(fallback_dir, f"frame_0000{ext}")

                if os.path.exists(f_path):
                    info = get_info(node_id)
                    invert = bool(info.get("invert_mask", False))
                    if invert and stage in ["mask", "cutout"]:
                        if stage == "mask":
                            m_gray = cv2.imread(f_path, cv2.IMREAD_GRAYSCALE)
                            if m_gray is not None:
                                inv_gray = 255 - m_gray
                                _, enc = cv2.imencode(".png", inv_gray)
                                return add_cors_headers(web.Response(body=enc.tobytes(), content_type="image/png"))
                        elif stage == "cutout":
                            c_rgba = cv2.imread(f_path, cv2.IMREAD_UNCHANGED)
                            if c_rgba is not None and c_rgba.ndim == 3 and c_rgba.shape[2] == 4:
                                inv_cutout = c_rgba.copy()
                                inv_cutout[:, :, 3] = 255 - inv_cutout[:, :, 3]
                                _, enc = cv2.imencode(".png", inv_cutout)
                                return add_cors_headers(web.Response(body=enc.tobytes(), content_type="image/png"))
                    return add_cors_headers(web.FileResponse(f_path))

            return add_cors_headers(web.Response(status=404, text=f"Frame {fid} for stage {stage} not found"))

        # --- Master Cocktail Pipeline Execution Endpoints ---
        COCKTAIL_STATUS_PATH = os.path.join(CACHE_DIR, "cocktail_status.json")

        @routes.post("/vfx/pipeline/execute_cocktail")
        async def vfx_execute_cocktail(request):
            import time
            try:
                data = await request.json()
            except Exception:
                data = {}

            director_prompt = data.get("director_prompt", "")
            negative_prompt = data.get("negative_prompt", "")
            audio_text = str(data.get("audio_text", "")).strip()
            audio_voice = str(data.get("audio_voice", "dmitry")).strip()
            translate = bool(data.get("translate", False))
            iclora_model = str(data.get("iclora_model", "union")).strip()
            pass_mode = str(data.get("pass_mode", "depth")).strip()

            status_record = {
                "timestamp": time.time(),
                "status": "starting",
                "steps": {
                    "audio": "pending",
                    "corridor_key": "pending",
                    "layout_bake": "pending",
                    "comfy_queue": "pending"
                },
                "details": {}
            }

            # 1. Synthesize Audio if requested
            if audio_text:
                status_record["steps"]["audio"] = "processing"
                try:
                    audio_res = generate_speech(
                        text=audio_text,
                        voice=audio_voice,
                        auto_translate=translate,
                        fps=24.0
                    )
                    status_record["steps"]["audio"] = "completed"
                    status_record["details"]["audio"] = audio_res
                except Exception as e:
                    status_record["steps"]["audio"] = f"warning: {e}"
            else:
                cur_audio = get_current_audio_info()
                if cur_audio.get("has_audio"):
                    status_record["steps"]["audio"] = "using_cached"
                    status_record["details"]["audio"] = cur_audio
                else:
                    status_record["steps"]["audio"] = "skipped"

            # 2. Check Plate Prep & CorridorKey
            prep_info = get_info("default")
            if prep_info.get("corridor_key_applied", False):
                status_record["steps"]["corridor_key"] = "ready (cached)"
            else:
                status_record["steps"]["corridor_key"] = "ready for node execution"

            # 3. Check 3D Layout baked frames
            layout_dir = os.path.join(CACHE_DIR, "layout_renders")
            baked_frames = []
            if os.path.exists(layout_dir):
                baked_frames = [f for f in os.listdir(layout_dir) if f.startswith("frame_") and f.endswith((".png", ".jpg"))]
            status_record["steps"]["layout_bake"] = f"ready ({len(baked_frames)} frames baked)"

            # 4. Prepare Master API Prompt
            api_prompt_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workflows", "master_cocktail_api_prompt.json")
            if not os.path.exists(api_prompt_file):
                from workflows.build_master_cocktail import build_workflow
                _, api_dict = build_workflow()
            else:
                with open(api_prompt_file, "r", encoding="utf-8") as pf:
                    api_dict = json.load(pf)

            if director_prompt and "31" in api_dict:
                api_dict["31"]["inputs"]["text"] = director_prompt
            if negative_prompt and "32" in api_dict:
                api_dict["32"]["inputs"]["text"] = negative_prompt

            if iclora_model == "camctrl_markers" and "51" in api_dict:
                api_dict["51"]["inputs"]["lora_name"] = r"LTX-2.3\IC-LORA_camctrl_00090.safetensors"
            elif iclora_model == "union" and "51" in api_dict:
                api_dict["51"]["inputs"]["lora_name"] = r"LTX-2.3\ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors"

            # 5. Queue into ComfyUI
            prompt_id = None
            try:
                from .vfx_agent_service import unload_agent_from_vram
                await asyncio.to_thread(unload_agent_from_vram)
            except Exception:
                pass

            try:
                import urllib.request
                req_data = json.dumps({"prompt": api_dict}).encode("utf-8")
                req = urllib.request.Request(f"{_COMFY_URL}/prompt", data=req_data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    prompt_id = resp_json.get("prompt_id")
                status_record["steps"]["comfy_queue"] = "queued"
                status_record["status"] = "running"
                status_record["prompt_id"] = prompt_id
            except Exception as e:
                # Direct queue attempt via PromptServer if HTTP loopback is busy/not available
                try:
                    server_inst = getattr(PromptServer, "instance", None)
                    if server_inst and hasattr(server_inst, "prompt_queue"):
                        import uuid
                        prompt_id = str(uuid.uuid4())
                        server_inst.prompt_queue.put((server_inst.number, prompt_id, api_dict, {}, []))
                        server_inst.number += 1
                        status_record["steps"]["comfy_queue"] = "queued_direct"
                        status_record["status"] = "running"
                        status_record["prompt_id"] = prompt_id
                    else:
                        status_record["steps"]["comfy_queue"] = f"queue note: {e}"
                        status_record["status"] = "ready_for_comfy"
                except Exception as ex2:
                    status_record["steps"]["comfy_queue"] = f"queue note: {e}"
                    status_record["status"] = "ready_for_comfy"

            try:
                with open(COCKTAIL_STATUS_PATH, "w", encoding="utf-8") as sf:
                    json.dump(status_record, sf, indent=2)
            except Exception:
                pass

            return add_cors_headers(web.json_response(status_record))

        @routes.get("/vfx/pipeline/status")
        async def vfx_pipeline_status(request):
            if os.path.exists(COCKTAIL_STATUS_PATH):
                try:
                    with open(COCKTAIL_STATUS_PATH, "r", encoding="utf-8") as sf:
                        st = json.load(sf)
                    return add_cors_headers(web.json_response(st))
                except Exception:
                    pass
            return add_cors_headers(web.json_response({"status": "idle", "steps": {}}))

        # --- 3D Mesh Generation & Proxy Cube Replacement (Hunyuan3D-Omni & TRELLIS.2) ---
        try:
            from .mesh3d_service import (
                list_mesh3d_library, start_3d_generation_job, get_3d_job_status, MESH3D_DIR
            )
        except Exception:
            from vfx_nodes.mesh3d_service import (
                list_mesh3d_library, start_3d_generation_job, get_3d_job_status, MESH3D_DIR
            )

        @routes.get("/vfx/mesh3d/list")
        async def vfx_mesh3d_list(request):
            try:
                return add_cors_headers(web.json_response(list_mesh3d_library()))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e), "meshes": []}, status=500))

        @routes.post("/vfx/mesh3d/generate")
        async def vfx_mesh3d_generate(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            try:
                job = start_3d_generation_job(data)
                return add_cors_headers(web.json_response(job))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e), "status": "error"}, status=400))

        @routes.get("/vfx/mesh3d/status/{job_id}")
        async def vfx_mesh3d_status(request):
            job_id = request.match_info.get("job_id", "")
            return add_cors_headers(web.json_response(get_3d_job_status(job_id)))

        @routes.get("/vfx/mesh3d/file/{filename}")
        async def vfx_mesh3d_file(request):
            fname = os.path.basename(request.match_info.get("filename", ""))
            fpath = os.path.join(MESH3D_DIR, fname)
            if os.path.exists(fpath) and os.path.isfile(fpath):
                return add_cors_headers(web.FileResponse(fpath))
            return add_cors_headers(web.Response(status=404, text=f"3D Mesh {fname} not found"))

        # --- GPU VRAM Telemetry & Instant Model Unload ---
        @routes.get("/vfx/vram_status")
        async def vfx_vram_status(request):
            free_gb = 0.0
            total_gb = 24.0
            used_gb = 0.0
            try:
                import torch
                if torch.cuda.is_available():
                    free_b, total_b = torch.cuda.mem_get_info()
                    free_gb = round(free_b / (1024**3), 2)
                    total_gb = round(total_b / (1024**3), 2)
                    used_gb = round(total_gb - free_gb, 2)
            except Exception:
                pass
            return add_cors_headers(web.json_response({
                "free_gb": free_gb,
                "total_gb": total_gb,
                "used_gb": used_gb
            }))

        @routes.post("/vfx/unload_models")
        async def vfx_unload_models(request):
            """Immediately purges all ComfyUI and Ollama models from GPU VRAM."""
            msg_parts = []
            try:
                # 1. Unload all ComfyUI loaded models from VRAM
                try:
                    import comfy.model_management
                    comfy.model_management.unload_all_models()
                    comfy.model_management.soft_empty_cache()
                    msg_parts.append("ComfyUI models unloaded")
                except Exception as ce:
                    msg_parts.append(f"ComfyUI unload: {ce}")

                # 2. Python garbage collection and CUDA cache empty
                import gc, torch
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.ipc_collect()
                    msg_parts.append("CUDA cache cleared")

                # 3. Unload any active Ollama models (keep_alive: 0)
                try:
                    import urllib.request
                    ps_req = urllib.request.Request("http://127.0.0.1:11434/api/ps")
                    with urllib.request.urlopen(ps_req, timeout=1.5) as ps_resp:
                        ps_data = json.loads(ps_resp.read().decode("utf-8"))
                        unloaded_ollama = []
                        for m in ps_data.get("models", []):
                            m_name = m.get("model") or m.get("name")
                            if m_name:
                                unl_req = urllib.request.Request(
                                    "http://127.0.0.1:11434/api/generate",
                                    data=json.dumps({"model": m_name, "keep_alive": 0}).encode("utf-8"),
                                    headers={"Content-Type": "application/json"}
                                )
                                urllib.request.urlopen(unl_req, timeout=1.5)
                                unloaded_ollama.append(m_name)
                        if unloaded_ollama:
                            msg_parts.append(f"Ollama unloaded: {', '.join(unloaded_ollama)}")
                except Exception:
                    pass

                free_gb = 0.0
                total_gb = 24.0
                if torch.cuda.is_available():
                    free_b, total_b = torch.cuda.mem_get_info()
                    free_gb = round(free_b / (1024**3), 2)
                    total_gb = round(total_b / (1024**3), 2)

                return add_cors_headers(web.json_response({
                    "status": "success",
                    "free_gb": free_gb,
                    "total_gb": total_gb,
                    "details": "; ".join(msg_parts),
                    "message": f"VRAM очищена: свободно {free_gb} ГБ из {total_gb} ГБ"
                }))
            except Exception as e:
                return add_cors_headers(web.json_response({"status": "error", "error": str(e)}, status=500))

        # --- 3D Gaussian Splats Generation & Storage (3DGS) ---
        try:
            from .splat3d_service import (
                list_splat3d_library, save_uploaded_splat_file,
                start_splat_generation_job, get_splat_job_status,
                generate_splat_sync
            )
        except Exception:
            from vfx_nodes.splat3d_service import (
                list_splat3d_library, save_uploaded_splat_file,
                start_splat_generation_job, get_splat_job_status,
                generate_splat_sync
            )

        @routes.get("/vfx/splats/list")
        async def vfx_splats_list(request):
            project_id = request.query.get("project_id", "").strip() or None
            try:
                items = list_splat3d_library(project_id)
                return add_cors_headers(web.json_response({"status": "ok", "splats": items}))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e), "splats": []}, status=500))

        @routes.post("/vfx/splats/generate")
        async def vfx_splats_generate(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            try:
                res = generate_splat_sync(data)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e), "status": "error"}, status=400))

        @routes.get("/vfx/splats/status/{job_id}")
        async def vfx_splats_status(request):
            job_id = request.match_info.get("job_id", "")
            return add_cors_headers(web.json_response(get_splat_job_status(job_id)))

        @routes.post("/vfx/splats/upload")
        async def vfx_splats_upload(request):
            project_id = request.query.get("project_id", "sandbox_playground").strip()
            try:
                reader = await request.multipart()
                uploaded = []
                while True:
                    part = await reader.next()
                    if part is None:
                        break
                    fn = part.filename or f"splat_{int(time.time())}.ply"
                    data_bytes = await part.read()
                    item = save_uploaded_splat_file(project_id, fn, data_bytes)
                    uploaded.append(item)
                return add_cors_headers(web.json_response({"ok": True, "uploaded": uploaded}))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=500))

        # --- Per-Process Model Manager, Local Folders, Cloud API & Custom Workflows ---
        try:
            from .pipeline_config_service import (
                get_full_processes_state, save_processes_config,
                scan_directory_for_models, start_model_download,
                get_download_jobs, save_custom_workflow_for_process
            )
        except Exception:
            from vfx_nodes.pipeline_config_service import (
                get_full_processes_state, save_processes_config,
                scan_directory_for_models, start_model_download,
                get_download_jobs, save_custom_workflow_for_process
            )

        @routes.get("/vfx/settings/processes")
        async def vfx_settings_processes_get(request):
            try:
                state = await asyncio.to_thread(get_full_processes_state)
                return add_cors_headers(web.json_response(state))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=500))

        @routes.post("/vfx/settings/processes")
        async def vfx_settings_processes_post(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            try:
                await asyncio.to_thread(save_processes_config, data)
                state = await asyncio.to_thread(get_full_processes_state)
                return add_cors_headers(web.json_response(state))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        # --- 1D image layers: one PNG per layer in projects/<id>/layers/<image_id>/ ---
        @routes.get("/vfx/projects/{project_id}/layers/{image_id}")
        async def vfx_layers_get(request):
            from .layers_service import load_layers
            pid = _sanitize_id(request.match_info["project_id"])
            res = await asyncio.to_thread(load_layers, pid, request.match_info["image_id"])
            return add_cors_headers(web.json_response(res))

        @routes.post("/vfx/projects/{project_id}/layers/{image_id}")
        async def vfx_layers_save(request):
            from .layers_service import save_layers
            pid = _sanitize_id(request.match_info["project_id"])
            try:
                data = await request.json()
                res = await asyncio.to_thread(
                    save_layers, pid, request.match_info["image_id"],
                    data.get("layers") or [], data.get("active_layer_id") or ""
                )
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        # --- 1D object segmentation: YOLO detection (+SAM2 refine) and SAM2 click picking ---
        @routes.post("/vfx/image/detect_objects")
        async def vfx_image_detect(request):
            from .image_segment_service import detect_objects
            try:
                data = await request.json()
                classes = data.get("classes")
                if isinstance(classes, str):
                    classes = [c for c in classes.split(",") if c.strip()]
                res = await asyncio.to_thread(
                    detect_objects, data.get("image", ""), classes or None,
                    float(data.get("conf", 0.25)), bool(data.get("refine_with_sam2", True))
                )
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/image/segment_points")
        async def vfx_image_segment_points(request):
            from .image_segment_service import segment_by_points
            try:
                data = await request.json()
                res = await asyncio.to_thread(segment_by_points, data.get("image", ""), data.get("points") or [], data.get("box"))
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/image/preprocess")
        async def vfx_image_preprocess(request):
            from .image_preprocess_service import run_image_preprocess
            try:
                data = await request.json()
                res = await asyncio.to_thread(run_image_preprocess, str(data.get("kind", "")), data.get("image", ""), data.get("params") or {})
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/image/generate")
        async def vfx_image_generate(request):
            from .image_generate_service import run_image_generate
            try:
                data = await request.json()
                res = await asyncio.to_thread(run_image_generate, data)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        # --- 2D video layers & background jobs (plate, masks, CorridorKey, preprocessors) ---
        @routes.get("/vfx/track/list")
        async def vfx_track_list(request):
            pid = _sanitize_id(request.query.get("project_id", "sandbox_playground"))
            vroot = os.path.join(get_project_dir(pid), "video")
            out = []
            if os.path.isdir(vroot):
                for vid in sorted(os.listdir(vroot)):
                    fp = os.path.join(vroot, vid, "track", "scene_data.json")
                    if not os.path.isfile(fp):
                        continue
                    try:
                        with open(fp, "r", encoding="utf-8") as f:
                            d = json.load(f)
                        cam = d.get("camera") or {}
                        out.append({"video_id": vid, "frames": len(cam.get("matrices") or []), "points": len(d.get("points") or []),
                                    "focal_mm": cam.get("focal_mm"), "start_frame": cam.get("start_frame", 0), "mtime": os.path.getmtime(fp)})
                    except Exception:
                        pass
            return add_cors_headers(web.json_response({"tracks": out}))

        @routes.post("/vfx/track/activate")
        async def vfx_track_activate(request):
            data = await request.json()
            pid = _sanitize_id(data.get("project_id", "sandbox_playground"))
            vid = _sanitize_id(str(data.get("video_id", "")))
            src = os.path.join(get_project_dir(pid), "video", vid, "track", "scene_data.json")
            if not os.path.isfile(src):
                return add_cors_headers(web.json_response({"error": "no track for this video"}, status=404))
            dst = os.path.join(get_project_dir(pid), "passes", "scene_data.json")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            return add_cors_headers(web.json_response({"ok": True}))

        @routes.get("/vfx/track/check")
        async def vfx_track_check(request):
            from .video_layers_service import tracking_readiness
            return add_cors_headers(web.json_response(await asyncio.to_thread(tracking_readiness)))

        @routes.get("/vfx/video/ic_loras")
        async def vfx_video_ic_loras(request):
            from .video_layers_service import list_ic_loras
            res = await asyncio.to_thread(list_ic_loras)
            return add_cors_headers(web.json_response({"loras": res}))

        @routes.get("/vfx/video/{project_id}/{video_id}/state")
        async def vfx_video_state(request):
            from .video_layers_service import get_state
            pid = _sanitize_id(request.match_info["project_id"])
            res = await asyncio.to_thread(get_state, pid, request.match_info["video_id"])
            return add_cors_headers(web.json_response(res))

        @routes.post("/vfx/video/{project_id}/{video_id}/layers")
        async def vfx_video_layers_meta(request):
            from .video_layers_service import update_layers_meta
            pid = _sanitize_id(request.match_info["project_id"])
            data = await request.json()
            res = await asyncio.to_thread(update_layers_meta, pid, request.match_info["video_id"], data.get("layers") or [], data.get("deleted") or [])
            return add_cors_headers(web.json_response(res))

        @routes.get("/vfx/video/{project_id}/{video_id}/frame/{layer_id}/{frame_idx}")
        async def vfx_video_frame(request):
            from .video_layers_service import frame_path, ensure_plate_registered
            pid = _sanitize_id(request.match_info["project_id"])
            vid = request.match_info["video_id"]
            ensure_plate_registered(pid, vid)
            try:
                idx = int(request.match_info["frame_idx"])
            except ValueError:
                idx = 0
            path = frame_path(pid, vid, request.match_info["layer_id"], idx)
            if request.query.get("orig") in ("1", "true") and path:
                _o = os.path.join(os.path.dirname(path), "_orig", os.path.basename(path))
                if os.path.isfile(_o):
                    path = _o
            inv = request.query.get("invert") in ("1", "true")
            if not path:
                if inv:  # an inverted mask with no object on this frame covers everything
                    from .video_layers_service import load_manifest, pps, node_id_for
                    import numpy as _np, cv2 as _cv2
                    info = pps.get_info(node_id_for(vid))
                    lay = next((l for l in load_manifest(pid, vid)["layers"] if l["id"] == request.match_info["layer_id"]), {})
                    c = (lay.get("color") or "#ffffff").lstrip("#")
                    img = _np.zeros((int(info.get("height", 1080)) // 4, int(info.get("width", 1920)) // 4, 4), _np.uint8)
                    img[..., 0], img[..., 1], img[..., 2], img[..., 3] = int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16), 255
                    ok, buf = _cv2.imencode(".png", img)
                    return add_cors_headers(web.Response(body=buf.tobytes(), content_type="image/png"))
                return add_cors_headers(web.Response(status=404, text="no frame"))
            if request.query.get("bw") in ("1", "true") or request.query.get("alpha") in ("1", "true"):
                from .video_layers_service import bw_frame_png
                body = await asyncio.to_thread(bw_frame_png, path, inv, request.query.get("alpha") in ("1", "true"))
                return add_cors_headers(web.Response(body=body, content_type="image/png", headers={"Cache-Control": "max-age=600"}))
            if inv:
                from .video_layers_service import inverted_frame_png
                body = await asyncio.to_thread(inverted_frame_png, path)
                return add_cors_headers(web.Response(body=body, content_type="image/png", headers={"Cache-Control": "max-age=600"}))
            return add_cors_headers(web.FileResponse(path, headers={"Cache-Control": "max-age=3600"}))

        @routes.post("/vfx/video/{project_id}/{video_id}/still")
        async def vfx_video_still(request):
            from .video_layers_service import save_still_layer
            import base64 as _b64
            pid = _sanitize_id(request.match_info["project_id"])
            try:
                data = await request.json()
                raw = str(data.get("data") or "")
                if "," in raw:
                    raw = raw.split(",", 1)[1]
                fr = data.get("frame")
                res = await asyncio.to_thread(save_still_layer, pid, request.match_info["video_id"], data.get("layer_id"), str(data.get("name") or "Paint"), _b64.b64decode(raw),
                                              int(fr) if fr is not None else None, bool(data.get("autokey")))
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/video/{project_id}/{video_id}/layer_frames")
        async def vfx_video_layer_frames(request):
            from .video_layers_service import save_layer_frames
            pid = _sanitize_id(request.match_info["project_id"])
            try:
                data = await request.json()
                res = await asyncio.to_thread(save_layer_frames, pid, request.match_info["video_id"], data)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/video/{project_id}/{video_id}/pose_from_3d")
        async def vfx_video_pose_from_3d(request):
            from .video_layers_service import pose_layer_from_3d
            pid = _sanitize_id(request.match_info["project_id"])
            try:
                data = await request.json()
                res = await asyncio.to_thread(pose_layer_from_3d, pid, request.match_info["video_id"], data)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/video/{project_id}/{video_id}/frame_edit")
        async def vfx_video_frame_edit(request):
            from .video_layers_service import save_layer_frame
            import base64 as _b64
            pid = _sanitize_id(request.match_info["project_id"])
            try:
                data = await request.json()
                raw = str(data.get("data") or "")
                if "," in raw:
                    raw = raw.split(",", 1)[1]
                res = await asyncio.to_thread(save_layer_frame, pid, request.match_info["video_id"], str(data.get("layer_id")), int(data.get("frame", 0)), _b64.b64decode(raw))
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/video/{project_id}/{video_id}/paint_key")
        async def vfx_video_paint_key(request):
            from .video_layers_service import paint_key_op
            pid = _sanitize_id(request.match_info["project_id"])
            try:
                data = await request.json()
                fr = data.get("frames") if isinstance(data.get("frames"), list) else int(data.get("frame", 0))
                res = await asyncio.to_thread(paint_key_op, pid, request.match_info["video_id"], str(data.get("layer_id")), fr, str(data.get("op", "add")))
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/video/{project_id}/{video_id}/paint_morph")
        async def vfx_video_paint_morph(request):
            from .video_layers_service import set_paint_morph
            pid = _sanitize_id(request.match_info["project_id"])
            try:
                data = await request.json()
                res = await asyncio.to_thread(set_paint_morph, pid, request.match_info["video_id"], str(data.get("layer_id")), bool(data.get("on")))
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/video/{project_id}/{video_id}/job")
        async def vfx_video_job_start(request):
            from .video_layers_service import start_job
            pid = _sanitize_id(request.match_info["project_id"])
            try:
                data = await request.json()
                res = await asyncio.to_thread(start_job, str(data.get("kind", "")), pid, request.match_info["video_id"], data.get("params") or {})
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.get("/vfx/video/job/{job_id}")
        async def vfx_video_job_get(request):
            from .video_layers_service import get_job
            return add_cors_headers(web.json_response(get_job(request.match_info["job_id"])))

        @routes.post("/vfx/video/job/{job_id}/cancel")
        async def vfx_video_job_cancel(request):
            from .video_layers_service import cancel_job
            res = await asyncio.to_thread(cancel_job, request.match_info["job_id"])
            return add_cors_headers(web.json_response(res))

        # --- Unified process runner: every process = API-format JSON queued on ComfyUI ---
        @routes.get("/vfx/process/templates")
        async def vfx_process_templates(request):
            from .process_runner import list_templates
            pid = request.query.get("process_id") or None
            return add_cors_headers(web.json_response({"templates": list_templates(pid)}))

        @routes.post("/vfx/process/run")
        async def vfx_process_run(request):
            from .process_runner import run_process, ProcessError
            try:
                data = await request.json()
            except Exception:
                data = {}
            try:
                res = await asyncio.to_thread(
                    run_process,
                    str(data.get("process_id", "")).strip(),
                    data.get("template"),
                    data.get("params") or {},
                    data.get("prompt"),
                )
                return add_cors_headers(web.json_response(res))
            except ProcessError as e:
                return add_cors_headers(web.json_response({"status": "error", "error": str(e)}, status=400))

        @routes.post("/vfx/process/cancel")
        async def vfx_process_cancel(request):
            from .process_runner import cancel_run
            try:
                data = await request.json()
            except Exception:
                data = {}
            try:
                if data.get("all"):
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        await session.post(f"{_COMFY_URL}/queue", json={"clear": True})
                        await session.post(f"{_COMFY_URL}/interrupt", json={})
                    return add_cors_headers(web.json_response({"status": "stopped_all"}))
                res = await asyncio.to_thread(cancel_run, data.get("run_tag"), data.get("prompt_id"))
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=500))

        @routes.post("/vfx/models/download")
        async def vfx_models_download(request):
            from .model_download_service import start_download
            try:
                data = await request.json()
                job = start_download(data.get("items") or [])
                return add_cors_headers(web.json_response(job))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.get("/vfx/models/download/{job_id}")
        async def vfx_models_download_status(request):
            from .model_download_service import get_job
            job = get_job(request.match_info["job_id"])
            if not job:
                return add_cors_headers(web.json_response({"error": "unknown job"}, status=404))
            return add_cors_headers(web.json_response(job))

        @routes.post("/vfx/models/download/{job_id}/cancel")
        async def vfx_models_download_cancel(request):
            from .model_download_service import cancel_job
            job = cancel_job(request.match_info["job_id"])
            return add_cors_headers(web.json_response(job or {"error": "unknown job"}))

        @routes.get("/vfx/process/progress")
        async def vfx_process_progress(request):
            from .progress_tracker import snapshot
            data = snapshot()
            try:
                from .video_layers_service import _JOBS as _VJOBS
                data["video_jobs"] = [
                    {k: j.get(k) for k in ("id", "kind", "status", "progress", "message", "started")}
                    for j in list(_VJOBS.values()) if j.get("status") == "running"
                ]
            except Exception:
                data["video_jobs"] = []
            return add_cors_headers(web.json_response(data))

        @routes.get("/vfx/process/preview/{prompt_id}")
        async def vfx_process_preview(request):
            from .progress_tracker import preview_bytes
            body, mime = preview_bytes(request.match_info["prompt_id"])
            if body is None:
                return add_cors_headers(web.Response(status=404))
            resp = web.Response(body=body, content_type=mime)
            resp.headers["Cache-Control"] = "no-store"
            return add_cors_headers(resp)

        @routes.get("/vfx/process/status/{prompt_id}")
        async def vfx_process_status(request):
            from .process_runner import get_run_status, ProcessError
            try:
                res = await asyncio.to_thread(get_run_status, request.match_info["prompt_id"])
                return add_cors_headers(web.json_response(res))
            except ProcessError as e:
                return add_cors_headers(web.json_response({"status": "error", "error": str(e)}, status=502))

        @routes.post("/vfx/settings/scan_folder")
        async def vfx_settings_scan_folder(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            folder_path = str(data.get("folder_path", "")).strip()
            process_id = str(data.get("process_id", "")).strip()
            try:
                scan_res = await asyncio.to_thread(scan_directory_for_models, folder_path)
                if process_id and data.get("save_to_process", True):
                    await asyncio.to_thread(save_processes_config, {
                        "processes": {process_id: {"custom_model_dir": folder_path}}
                    })
                return add_cors_headers(web.json_response(scan_res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/settings/download_model")
        async def vfx_settings_download_model(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            process_id = str(data.get("process_id", "")).strip()
            model_id = str(data.get("model_id", "")).strip()
            url = str(data.get("url", "")).strip()
            filename = str(data.get("filename", "model.safetensors")).strip()
            target_dir = str(data.get("target_dir", "")).strip()
            if not url:
                return add_cors_headers(web.json_response({"error": "No download URL provided"}, status=400))
            job = await asyncio.to_thread(start_model_download, process_id, model_id, url, filename, target_dir)
            return add_cors_headers(web.json_response(job))

        @routes.get("/vfx/settings/downloads")
        async def vfx_settings_downloads_get(request):
            return add_cors_headers(web.json_response({"downloads": get_download_jobs()}))

        @routes.post("/vfx/settings/bind_workflow")
        async def vfx_settings_bind_workflow(request):
            try:
                data = await request.json()
            except Exception:
                data = {}
            process_id = str(data.get("process_id", "video_gen")).strip()
            wf_name = str(data.get("workflow_name", "Custom_ComfyUI_Workflow.json")).strip()
            wf_json = data.get("workflow_json", {})
            try:
                res = await asyncio.to_thread(save_custom_workflow_for_process, process_id, wf_name, wf_json)
                return add_cors_headers(web.json_response(res))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/system/restart")
        async def vfx_system_restart(request):
            """Restarts the ComfyUI server with the same interpreter, args and env.
            A detached helper waits until port is free, relaunches ComfyUI in a new console, and exits."""
            import subprocess
            import time
            port = int(_COMFY_URL.rsplit(":", 1)[-1]) if _COMFY_URL.rsplit(":", 1)[-1].isdigit() else 8188
            if "--port" in sys.argv:
                try:
                    port = int(sys.argv[sys.argv.index("--port") + 1])
                except Exception:
                    pass
            helper = r"""
import socket, subprocess, sys, time, json
port = int(sys.argv[1]); cmd = json.loads(sys.argv[2]); cwd = sys.argv[3]
deadline = time.time() + 60
while time.time() < deadline:
    s = socket.socket(); s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port)); s.close(); time.sleep(0.5)
    except OSError:
        break
time.sleep(1.0)
flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
subprocess.Popen(cmd, cwd=cwd, creationflags=flags)
"""
            cmd = [sys.executable] + list(sys.argv)
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            try:
                subprocess.Popen(
                    [sys.executable, "-c", helper, str(port), json.dumps(cmd), os.getcwd()],
                    creationflags=flags, close_fds=True,
                )
            except Exception as e:
                return add_cors_headers(web.json_response({"status": "error", "error": str(e)}, status=500))
            LOGGER.warning("[VFX-Suite] Restart requested from Studio UI — shutting down ComfyUI")
            asyncio.get_event_loop().call_later(0.8, lambda: os._exit(0))
            return add_cors_headers(web.json_response({"status": "restarting", "port": port}))

        @routes.get("/vfx/system/ping")
        async def vfx_system_ping(request):
            import time
            return add_cors_headers(web.json_response({
                "status": "ok",
                "service": "ComfyUI-VFX-Suite",
                "timestamp": time.time(),
                "routes_active": True
            }))

        # --- Dubbing / voice (engines run in their own Python env, see vfx_nodes/dubbing_service.py) ---
        def _dub():
            try:
                from . import dubbing_service as ds
            except ImportError:  # pragma: no cover
                from vfx_nodes import dubbing_service as ds
            return ds

        @routes.get("/vfx/dubbing/status")
        async def vfx_dubbing_status(request):
            return add_cors_headers(web.json_response(await asyncio.to_thread(_dub().status)))

        @routes.get("/vfx/dubbing/defaults")
        async def vfx_dubbing_defaults(request):
            try:
                return add_cors_headers(web.json_response(_dub().defaults(request.query.get("mode", "dub"))))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.post("/vfx/dubbing/run")
        async def vfx_dubbing_run(request):
            try:
                data = await request.json()
                job = _dub().start(str(data.get("mode") or "dub"), data.get("config") or {},
                                   int(data.get("start_at_stage", 0) or 0), str(data.get("project_id") or ""))
                return add_cors_headers(web.json_response(job))
            except Exception as e:
                return add_cors_headers(web.json_response({"error": str(e)}, status=400))

        @routes.get("/vfx/dubbing/job/{job_id}")
        async def vfx_dubbing_job(request):
            j = _dub().get_job(request.match_info["job_id"])
            return add_cors_headers(web.json_response(j or {"error": "not found"}, status=200 if j else 404))

        @routes.post("/vfx/dubbing/job/{job_id}/cancel")
        async def vfx_dubbing_cancel(request):
            return add_cors_headers(web.json_response(_dub().cancel_job(request.match_info["job_id"])))

        @routes.get("/vfx/system/policy")
        async def vfx_system_policy(request):
            try:
                from .local_config import cloud_services_allowed
            except ImportError:  # pragma: no cover
                from vfx_nodes.local_config import cloud_services_allowed
            return add_cors_headers(web.json_response({"allow_cloud": cloud_services_allowed()}))

        @routes.post("/vfx/system/reload")
        async def vfx_system_reload(request):
            import importlib
            reloaded = []
            # Services first, server_api last. ComfyUI imports this package under its folder name,
            # so each module may live in sys.modules under several keys: reload every one of them.
            suffixes = [
                "vfx_nodes.pipeline_config_service",
                "vfx_nodes.process_runner",
                "vfx_nodes.layers_service",
                "vfx_nodes.image_segment_service",
                "vfx_nodes.image_preprocess_service",
                "vfx_nodes.image_generate_service",
                "vfx_nodes.video_layers_service",
                "vfx_nodes.model_download_service",
                "vfx_nodes.progress_tracker",
                "vfx_nodes.project_manager",
                "vfx_nodes.vfx_agent_service",
                "vfx_nodes.audio_service",
                "vfx_nodes.plate_prep_service",
                "vfx_nodes.mesh3d_service",
                "vfx_nodes.splat3d_service",
                "vfx_nodes.server_api",
            ]
            for suffix in suffixes:
                keys = [k for k in list(sys.modules) if k == suffix or k.endswith("." + suffix)]
                for mod_name in keys:
                    try:
                        importlib.reload(sys.modules[mod_name])
                        reloaded.append(mod_name)
                    except Exception as ex:
                        reloaded.append(f"{mod_name}: {ex}")
            try:
                from vfx_nodes.server_api import register_server_routes as rsr
                rsr()
            except Exception:
                pass
            return add_cors_headers(web.json_response({"status": "reloaded", "modules": reloaded}))

        try:
            from .progress_tracker import install as _install_progress
            _install_progress()
        except Exception as e:
            print(f"[VFX-Suite] progress tracker not installed: {e}")

        # Mount onto PromptServer.instance.routes (for startup) and directly onto app.router (for live hot-reload)
        app = getattr(PromptServer.instance, "app", None)
        if app is not None and hasattr(app, "router"):
            was_frozen = getattr(app.router, "_frozen", False)
            try:
                app.router._frozen = False
                existing_map = {}
                for r in app.router.routes():
                    info = r.get_info()
                    path_key = info.get("path") or info.get("formatter") or ""
                    existing_map[(r.method, path_key)] = r
                added_count = 0
                updated_count = 0
                for item in routes:
                    key = (item.method, item.path)
                    if key not in existing_map:
                        new_r = app.router.add_route(item.method, item.path, item.handler, **item.kwargs)
                        existing_map[key] = new_r
                        added_count += 1
                    else:
                        try:
                            existing_map[key]._handler = item.handler
                            updated_count += 1
                        except Exception:
                            pass
                print(f"[ComfyUI-VFX-Suite] Live router sync: +{added_count} added, ~{updated_count} updated in-place.")
            except Exception as route_err:
                print(f"[ComfyUI-VFX-Suite] Dynamic router mount note: {route_err}")
            finally:
                app.router._frozen = was_frozen

        if hasattr(PromptServer.instance, "routes"):
            existing_ps = set((r.method, r.path) for r in PromptServer.instance.routes)
            for item in routes:
                if (item.method, item.path) not in existing_ps:
                    PromptServer.instance.routes.route(item.method, item.path, **item.kwargs)(item.handler)

        print("[ComfyUI-VFX-Suite] Registered VFX Suite Server Routes (Scene Data, Actor Projection, Plate Prep, Cocktail Pipeline)")

    except Exception as e:
        print(f"[ComfyUI-VFX-Suite] Note: Could not register server routes: {e}")
