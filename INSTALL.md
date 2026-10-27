# Installing KESHIKI VFX Studio

KESHIKI is a ComfyUI custom node pack with its own studio UI. It runs fully locally.

## Requirements

- Windows 10/11 with an NVIDIA GPU (developed on an RTX 3090 Ti, 24 GB). 16 GB VRAM is the practical minimum for LTX-2.5.
- A working ComfyUI (portable, desktop or a git install) on a recent version.
- [Git](https://git-scm.com/) (the installer uses it to fetch the node packs below).
- Optional: [Ollama](https://ollama.com/) for the AI Director chat (any local chat model, e.g. `ollama pull qwen3:8b`).

## Install

1. Put this folder into `ComfyUI/custom_nodes/`:
   ```
   cd ComfyUI/custom_nodes
   git clone <this repository> ComfyUI-VFX-Suite
   ```
2. Run `install.bat` from that folder (or `python install.py` with the Python that runs ComfyUI).
   It will:
   - add the node packs KESHIKI uses (skipped if you already have them):
     ComfyUI-LTXVideo, comfyui_controlnet_aux, ComfyUI-DepthAnythingV2, ComfyUI-VideoHelperSuite,
     ComfyUI-segment-anything-2, ComfyUI-CorridorKey;
   - install the Python packages from `requirements.txt`;
   - download COLMAP 3.13 (CUDA, ~258 MB) and GLOMAP 1.2.0 (CUDA, ~73 MB) into `tools/colmap` and `tools/glomap`
     for camera tracking (GLOMAP's global mapper on COLMAP's features gives the most accurate solves);
   - list which models are present and where the missing ones go.
3. Models: run `.\install.bat --download-models core` to fetch the core set (~40 GB), or download the files
   listed by the installer yourself. Already have them elsewhere? Point ComfyUI at that folder with
   `extra_model_paths.yaml`. `.\install.bat --check` re-checks without changing anything.
4. Start ComfyUI as usual and open **http://127.0.0.1:8188/vfx/studio/**
   (or use `run_keshiki_studio.bat`, which starts ComfyUI and opens the studio in an app window).

## Model groups

| group | what for | size |
|---|---|---|
| core | LTX-2.5 (int8) + Gemma text encoder, alpha IC-LoRA, SAM2, CorridorKey | ~40 GB |
| iclora | extra LTX-2.5 IC-LoRAs (layout-to-render, refine details, day-to-night, ...) | 0.3-1.7 GB each |
| image | FLUX.3 Klein (int8) for stills and storyboards | ~11 GB |
| auto | Depth Anything V2, DWPose, YOLOv8-seg - downloaded on first use | small |

Optional models used by ComfyUI's own nodes (put them where ComfyUI expects them):

| model | folder | what for |
|---|---|---|
| `sam3.1_multiplex_fp16.safetensors` | `models/checkpoints` | SAM 3.1: masks by text, tracked through the clip |
| `depth_anything_3_mono_large.safetensors` | `models/geometry_estimation` | Depth Anything 3 (alternative depth) |
| `sam_3d_body_dinov3_bf16.safetensors` + `rt_detr_v4-x-hgnet_fp16.safetensors` | `models/detection`, `models/diffusion_models` | SAM 3D Body (skeleton from footage) |
| `qwen_image_edit_2511_bf16.safetensors` + `Qwen-Image-Edit-2511-Lightning-4steps` LoRA + `qwen_2.5_vl_7b_fp8_scaled` + `qwen_image_vae` | `diffusion_models`, `loras/Qwen`, `clip/Qwen`, `vae` | Qwen-Image-Edit (optional first-frame / relight path) |
| `Qwen-Image-Edit-2509-Light-Migration.safetensors` | `models/loras/Qwen` | relight a frame to a light reference |

## Optional modules (own Python environments)

These tools need packages that clash with ComfyUI's, so KESHIKI runs them as separate processes. Point to them in
`keshiki_local.json` (next to this file; the installer creates it, it is never committed):

```json
{
  "dubbing_python": "D:/video_dubbing_studio/python_env/python.exe",
  "dubbing_models": "D:/video_dubbing_studio/models",
  "trellis_root": "D:/TRELLIS.2",
  "depth_pro_root": "D:/ml-depth-pro",
  "trellis_python": "D:/envs/trellis/python.exe",
  "hunyuan3d_root": "D:/Hunyuan3D-Omni",
  "hunyuan3d_python": "D:/envs/hunyuan3d/python.exe",
  "ollama_models": "D:/Ollama/models"
}
```

- **Dubbing & voice** (`dubbing/`): Whisper, Demucs, F5-TTS, RUAccent. Create a Python 3.11
  environment and install `dubbing/requirements.txt` plus the TTS wrapper it lists (ComfyUI-F5-TTS). F5-TTS pins `pydantic` and the engines need older `transformers`, which is why it can't share
  ComfyUI's environment. Models go to `dubbing_models` (`F5-TTS/`, `Whisper/`, `Qwen/`). VibeVoice is not used: Microsoft withdrew the model.
  Translation runs on a local LLM through Ollama by default.
- **TRELLIS.2** (image → 3D, mesh texturing): install Microsoft's TRELLIS.2 repo in its own environment and download
  `TRELLIS.2-4B` into `<trellis_root>/weights/microsoft/TRELLIS.2-4B`. KESHIKI calls `tools/trellis_runner.py` inside it.
- **Depth Pro (optional depth engine):** clone `apple/ml-depth-pro`, run its `get_pretrained_models.sh` (→ `checkpoints/depth_pro.pt`, 1.9 GB) and set `depth_pro_root`. It runs in the ComfyUI env (needs `timm`). ~1 s/frame on a 24 GB GPU; sharp silhouettes and steady over time, but less relief inside faces than Depth Anything V2.
- **LoRA training**: see [docs/LORA_TRAINING.md](docs/LORA_TRAINING.md) (Linux / WSL2).

## Notes

- Everything runs locally with open-weights models; no cloud API is required. Cloud services (Comfy partner
  nodes, cloud LLMs for the agent, edge-tts, Google translation) stay off unless you opt in with
  `KESHIKI_ALLOW_CLOUD=1` or `"allow_cloud": true` in `keshiki_local.json`.
- How to use everything: [docs/USER_GUIDE.md](docs/USER_GUIDE.md) (Russian: [docs/USER_GUIDE_RU.md](docs/USER_GUIDE_RU.md)).
- Third-party components keep their own licenses (see `THIRD_PARTY_NOTICES.md`). CorridorKey in particular
  is non-commercial.
- The AI Director cannot run shell commands unless you set `KESHIKI_AGENT_SHELL=1` yourself.
- Linux: the studio runs, but install COLMAP >= 3.13 from your package manager for tracking.
