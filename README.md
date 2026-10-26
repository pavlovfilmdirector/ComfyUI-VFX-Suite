# KESHIKI (景色) VFX Studio

**A local VFX studio inside ComfyUI. Footage in, finished shot out. It runs on open-weights models only.**

You load your footage and get masks, depth, pose and the camera track. From there you block the scene in 3D, generate new backgrounds and characters with **LTX-2.5 + IC-LoRAs**, design first frames with **FLUX.3 Klein**, then composite, relight, refine and dub. All of this happens in one browser studio. An AI director answers "how do I…?" and runs the steps for you.

No cloud APIs are used. Every model runs on your own GPU and is tuned for 24 GB of VRAM.

---

## What it does

| Area | What you get |
|---|---|
| **2D: video** | Text masks with SAM 3.1 (`person`, `red car`), click-and-track SAM2, YOLO, CorridorKey, LTX Alpha · depth (Depth Anything V2 / 3, Apple Depth Pro) · DWPose · Canny · lens undistortion · camera tracking (COLMAP + GLOMAP) |
| **Generation** | LTX-2.5 22B with IC-LoRAs: union-control (pose / depth / edges), layout-to-render, clean-plate, refine-details, restore, upscaler and more. Supports a first-frame guide, two stacked IC-LoRAs, one pass up to 97 frames, and longer clips in overlapping chunks with a smooth cross-fade |
| **1D: images** | FLUX.3 Klein: text-to-image, editing with masks and references, pose transfer ("scene + skeleton + character" recipe). Optional Qwen-Image-Edit-2511 |
| **3D** | Characters with FBX / Mixamo animation (auto retarget) or a built-in mannequin, primitives for set blocking, camera moves, tracked cameras. Pose, clay and environment renders are sent straight to the 2D clip as control layers |
| **Composite** | A subject from one source over another background, with edge erode / feather, colour harmonization and light wrap, then an optional LTX refine pass |
| **Voice** | Video dubbing: Whisper → local LLM translation (Ollama) → F5-TTS / VibeVoice / Qwen3-TTS voice clone → timing and mix. Also voice cloning and long-text narration |
| **3D assets** | TRELLIS.2 image → textured GLB, and texturing an existing mesh |
| **Agent** | A local LLM through Ollama (Qwen, Gemma). It sees the open clip and its layers, explains each step from the user guide, asks before running heavy jobs and works one step at a time |
| **LoRA training** | Configs and notes for LTX-2 LoRAs and IC-LoRAs on 24 GB ([docs/LORA_TRAINING.md](docs/LORA_TRAINING.md)) |

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
| Optional | Qwen-Image-Edit-2511, SAM 3.1, SAM2, Depth Anything V2 / 3, Depth Pro, DWPose, YOLO, CorridorKey, TRELLIS.2, F5-TTS / VibeVoice / Qwen3-TTS, Whisper | see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) |

Weights are not stored in this repository. The installer and the Settings tab show where to download each model, or you can point the studio at a folder you already have.

## Install

Requirements: Windows 10/11, a working ComfyUI, Git, and an NVIDIA GPU (developed on an RTX 3090 Ti, 24 GB; 16 GB is the practical minimum for LTX-2.5). The core model set is about 40 GB. Ollama is optional, for the agent.

```bat
cd ComfyUI\custom_nodes
git clone https://github.com/<your-account>/ComfyUI-VFX-Suite.git
cd ComfyUI-VFX-Suite
install.bat                          :: node packs, Python packages, COLMAP + GLOMAP
install.bat --download-models core   :: core models (~40 GB), or point to your own folders
run_keshiki_studio.bat
```

The studio opens at `http://127.0.0.1:8188/vfx/studio/`. Full instructions, optional modules (dubbing, TRELLIS.2, Depth Pro) and `keshiki_local.json` are in [INSTALL.md](INSTALL.md).

## Documentation

- [User guide (EN)](docs/USER_GUIDE.md) · [Инструкция (RU)](docs/USER_GUIDE_RU.md): views, every video process, step-by-step recipes, talking to the agent, troubleshooting
- [INSTALL.md](INSTALL.md): setup and optional modules
- [docs/LORA_TRAINING.md](docs/LORA_TRAINING.md): training your own LTX LoRAs (Linux / WSL2, 24 GB configs)
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md): licenses of bundled libraries, tools and models

## Open weights only

KESHIKI uses no partner nodes and no closed APIs. Code paths for cloud services stay switched off unless you opt in with `KESHIKI_ALLOW_CLOUD=1`, and the studio never enables them by itself.

## License

GPL-3.0 (same as ComfyUI). Bundled web libraries and every model keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
