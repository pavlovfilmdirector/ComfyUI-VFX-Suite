# KESHIKI (景色) VFX Studio — Technology Preview v0.1

**A local VFX studio inside ComfyUI. Footage in, finished shot out. It runs on open-weights models only.**

> **Release Status:** `v0.1.0-preview` (Technology Preview v0.1)

You load your footage and get masks, depth, pose and the camera track. From there you block the scene in 3D, generate new backgrounds and characters with **LTX-2.5 + IC-LoRAs**, design first frames with **FLUX.3 Klein**, then composite, relight, refine and dub. All of this happens in one browser studio. An AI director answers "how do I…?" and runs the steps for you.

No cloud APIs are used. Every model runs on your own GPU and is tuned for 24 GB of VRAM.

---

## What it does

| Area | What you get |
|---|---|
| **Video & Layers** | Text masks with SAM 3.1 (`person`, `red car`), click-and-track SAM2, YOLO, CorridorKey, LTX Alpha · **Cutout Alpha (`video_cutout`)** to extract RGBA video/sequences · depth (Depth Anything V2 / 3, Apple Depth Pro) · normals (MoGe-2) · DWPose · Canny · lens undistortion · camera tracking (COLMAP + GLOMAP) · keyframe timeline with brush auto-key and **Ghost / Onion-skinning** |
| **Generation** | LTX-2.5 22B with IC-LoRAs: union-control (pose / depth / edges), layout-to-render, clean-plate, refine-details, restore, upscaler and more. Supports a first-frame guide, two stacked IC-LoRAs, one pass up to 97 frames, and longer clips in overlapping chunks with a smooth cross-fade |
| **Image** | FLUX.3 Klein: text-to-image, editing with masks and references, pose transfer ("scene + skeleton + character" recipe), non-destructive layer & selection transforms. Optional Qwen-Image-Edit-2511 |
| **3D Scene & Camera** | Structured 3-tier 3D toolbar: Primitives · Cameras, Sun/Point Lights, 3-Point Ground Plane & toggleable 3D Cursor · Skeleton mannequin & FBX/Mixamo animation (with Root Motion & Root-to-Pivot baking), **3D Pose (OpenPose) to 2D**, **3D Character Render Pass to 2D**, manual **Actor Cutout Plane** (custom video/image + BiRefNet background removal), and **Monitor Camera Render (`🎬 Render Camera`)** straight to the Gallery |
| **Composite** | A subject from one source over another background, with edge erode / feather, colour harmonization and light wrap, then an optional LTX refine pass |
| **Voice & Music** | Video dubbing: Whisper → local LLM translation (Ollama) → F5-TTS voice clone → timing and mix. Voice board (lip-sync with LTX-2.5 / DubIt), long-text voice-over with stress marks, and ACE-Step 1.5 music generation |
| **3D Assets** | TRELLIS.2 image → textured GLB, and texturing an existing mesh with live progress in both viewport and top header chip, auto-synced to Gallery & 3D Library |
| **Asset Gallery** | Unified right-aligned Top Gallery with color-coded category borders (Images, Videos, 3D Scenes/Meshes, Audio/Music) and uniform `200×42 px` cards |
| **Agent & My Tools** | A local LLM through Ollama (Qwen, Gemma) with stage confirmation & action visualization, plus custom ComfyUI API/UI workflow import (**My Tools**) |
| **AI agents (MCP)** | Any MCP agent can run the studio from a plain task ([docs/MCP.md](docs/MCP.md)) |
| **LoRA training** | CLI configs and notes for LTX-2 LoRAs and IC-LoRAs on 24 GB ([docs/LORA_TRAINING.md](docs/LORA_TRAINING.md)) |

## Tested recipes

All three recipes were run on real footage (1920×1088) with an RTX 3090 Ti (24 GB):

- **New background, same person.** SAM 3.1 mask → depth → a FLUX.3 Klein first frame of the new place → LTX union-control on depth (97 frames in one pass, about 2 min) → composite with harmonize and light wrap → refine-details.
- **New character, same background.** LTX clean-plate → DWPose → a Klein first frame (scene + skeleton + character reference) → LTX union-control on pose → SAM 3.1 mask of the new character → composite over the clean plate.
- **Character driven by 3D animation.** A Mixamo animation on the mannequin, a set blocked out with primitives and an orbit camera → pose and environment renders → LTX layout-to-render plus union-control on pose.

Lessons learned are written into the guide. For example, depth control does not hold a face, so use pose only when the character must stay recognisable. Two stacked IC-LoRAs fit 49 frames per run on 24 GB.

## Models

| Role | Model | Notes |
|---|---|---|
| Video (core) | **LTX-2.5 22B** + Lightricks IC-LoRAs (LTX-2.5 and LTX-2.3 union-control) | open weights, Lightricks license |
| Images (core) | **FLUX.3 Klein** + Qwen3-VL-4B encoder | FLUX Kommunity License |
| Optional | Qwen-Image-Edit-2511, SAM 3.1, SAM2, Depth Anything V2 / 3, Depth Pro, DWPose, YOLO, CorridorKey, TRELLIS.2, F5-TTS, Whisper | see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) |

Weights are not stored in this repository. The installer and the Settings tab show where to download each model, or you can point the studio at a folder you already have.

## Install

Requirements: Windows 10/11, a working ComfyUI, Git, and an NVIDIA GPU (developed on an RTX 3090 Ti, 24 GB; 16 GB is the practical minimum for LTX-2.5). The core model set is about 40 GB. Ollama is optional, for the agent.

Supports two installation approaches:
1. **Existing ComfyUI Integration**: run `install.bat` targeting your current ComfyUI Python.
2. **Clean Dedicated Environment (`keshiki_vfx`)**: run in an isolated Python 3.12 + PyTorch 2.6.0+cu124 environment to guarantee zero dependency conflicts with older custom nodes.

See [INSTALL.md](INSTALL.md) for full step-by-step instructions and dependency specifications.

```bash
cd ComfyUI\custom_nodes
git clone https://github.com/pavlovfilmdirector/ComfyUI-VFX-Suite.git
cd ComfyUI-VFX-Suite
.\install.bat
.\install.bat --download-models core
.\run_keshiki_studio.bat
```

`install.bat` adds the node packs, Python packages and COLMAP + GLOMAP; `--download-models core` fetches the core models (~40 GB), or point ComfyUI at folders you already have. Works in cmd and PowerShell.

The studio opens at `http://127.0.0.1:8188/vfx/studio/`. Full instructions, optional modules (dubbing, TRELLIS.2, Depth Pro) and `keshiki_local.json` are in [INSTALL.md](INSTALL.md).

## Documentation

- [User guide (EN)](docs/USER_GUIDE.md) · [Инструкция (RU)](docs/USER_GUIDE_RU.md): views, every video process, step-by-step recipes, talking to the agent, troubleshooting
- [INSTALL.md](INSTALL.md): setup and optional modules
- [docs/LORA_TRAINING.md](docs/LORA_TRAINING.md): training your own LTX LoRAs (Linux / WSL2, 24 GB configs)
- [docs/MCP.md](docs/MCP.md): drive the studio from any AI agent (Claude, Cursor, Antigravity...) over MCP
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md): licenses of bundled libraries, tools and models

## Tests

```
pip install pytest
python -m pytest tests
```

No GPU, ComfyUI or models needed: ComfyUI and the dubbing engines are replaced by fakes. The suite checks every
API workflow in `workflows/api` against ComfyUI's node registry (node classes, inputs, links, enum values), the
services (music, voice-over, voice board, image generation with drawings, video layers, montage, image layers),
the agent's actions and help, every `/vfx/...` URL the UI calls, the install manifest, doc links and JS syntax.
After updating ComfyUI or node packs run `python tools/refresh_object_info.py` with ComfyUI running; it also
enables a local check that every model the workflows name is installed on this machine.

## Open weights only

KESHIKI uses no partner nodes and no closed APIs. Code paths for cloud services stay switched off unless you opt in with `KESHIKI_ALLOW_CLOUD=1`, and the studio never enables them by itself.

## License

GPL-3.0 (same as ComfyUI). Bundled web libraries and every model keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
