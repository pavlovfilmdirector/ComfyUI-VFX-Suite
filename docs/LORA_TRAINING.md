# Training LTX LoRAs / IC-LoRAs for KESHIKI

KESHIKI uses LTX-2.5 / LTX-2.3 IC-LoRAs (layout-to-render, union control, clean plate, refine details…).
You can train your own with **Lightricks' LTX-2 trainer** (`ltx-trainer`, Apache-2.0):
https://github.com/Lightricks/LTX-2 → `packages/ltx-trainer`.

> **Read this before you start.**
>
> - **Linux only.** The trainer needs Linux with CUDA (Triton, bitsandbytes, `torch.compile`).
>   On Windows run it inside **WSL2 (Ubuntu)**; your Windows drives are visible there as `/mnt/c`, `/mnt/b`, …
> - **VRAM.** Lightricks recommend an 80 GB GPU (H100/H200) for the standard configs and 32 GB with
>   their low-VRAM config. **It also works on 24 GB (RTX 3090 Ti)** with the settings below: INT8 model
>   quantization, 8-bit text encoder, 8-bit AdamW, gradient checkpointing, batch 1, small validation clips.
>   Expect it to be slow, and expect out-of-memory errors if you raise resolution, frame count or LoRA rank.
> - **RAM / disk.** The 22B checkpoint and the Gemma text encoder are loaded from disk; plan for 64 GB+ system
>   RAM and fast storage. Precomputed latents of the dataset take a lot of space as well.
> - **Cloud option.** For long runs or high resolutions rent an 80 GB GPU (RunPod etc.); the same configs work
>   with `quantization: null`.
> - **Licences.** LTX-2.x weights are under Lightricks' own licence (see the model cards); your dataset must be
>   material you have the rights to.

## 1. Set up (WSL2 / Linux)

```bash
git clone https://github.com/Lightricks/LTX-2 && cd LTX-2/packages/ltx-trainer
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv package manager
uv sync                                             # creates the trainer's virtual env
```

You need locally:
- the LTX checkpoint (e.g. `ltx-2.3-22b-dev.safetensors`, the one ComfyUI uses);
- the Gemma text encoder folder (`gemma-3-12b-it-qat-q4_0-unquantized`).

## 2. Dataset

- **LoRA (text → video):** short clips (3–8 s) + one caption per clip.
- **IC-LoRA (video → video):** pairs *reference / control clip* → *target clip* of the same length, e.g. a
  depth / pose / layout render from KESHIKI → the final shot. KESHIKI layers can be exported as such pairs:
  any layer (`Layers → … → Export video`) is the control, the plate or a rendered layer is the target.

Then precompute latents with the trainer's scripts (`scripts/process_dataset.py`, `scripts/compute_reference.py`
for IC-LoRA references) as described in `docs/dataset-preparation.md` of the trainer.

## 3. Configs from the author's runs

All files are in [`docs/training_configs/`](training_configs/). Replace the `/path/to/...` placeholders.

| file | what it trains | status | key settings |
|---|---|---|---|
| `ltx2_av_lora_rtx3090ti.yaml` | LoRA, text → video with audio, **LTX-2 19B dev fp8** | **complete on 24 GB** (RTX 3090 Ti, WSL2): 15 clips, 1500 steps, ~6.5 s/step, ~2 h 45 min | rank 16, `int8-quanto`, 8-bit text encoder, `adamw8bit`, gradient checkpointing, lr 1e-4, validation 512×320×49 |
| `ltx2_ic_lora_24gb.yaml` | IC-LoRA, video → video (camera control), LTX-2.3 22B dev | **works on 24 GB**: 9 ref/target pairs, a usable LoRA after a few hundred steps over several resumed sessions (stopped by choice) | rank 64, 8-bit text encoder, `adamw8bit`, gradient checkpointing, lr 1e-5, validation 576×320×49 |
| `ltx2_ic_lora_h200.yaml` | the same IC-LoRA for a rented 141 GB H200 | **complete, several runs** on a rented H200 | no quantization, full-precision Gemma, batch 4, no gradient checkpointing, lr 1e-4, cosine, 2000 steps |

### Author's notes

- On 24 GB the **19B fp8** model trains a text-to-video LoRA end to end; the **22B LTX-2.3** model sits at the
  edge of memory: close ComfyUI and other GPU apps first (the card must be almost empty before the trainer starts).
- Video-to-video IC-LoRAs are much heavier (reference + target latents). On 24 GB they run a few hundred steps per
  session - already enough for a usable camera-control LoRA; resume with `model.load_checkpoint`. Long runs
  (2000 steps, batch 4, 960×544 validation) were done several times on a rented H200.
- Checkpoints every 250 steps (T2V) / 50 steps (IC-LoRA on 24 GB) so a crash costs little.
<!-- AUTHOR NOTES: Val, add what worked best: which checkpoint you kept, dataset tips, quality notes. -->

Run:

```bash
uv run python scripts/train.py /path/to/ltx2_av_lora_rtx3090ti.yaml
```

Checkpoints (`lora_weights_step_XXXXX.safetensors`) appear in `output_dir` every `checkpoints.interval` steps.
Copy the one you like to `ComfyUI/models/loras/` - KESHIKI lists it in the 2D *Generate* menu automatically
(IC-LoRAs are recognised by `ic-lora` in the file name).

## 4. Tips

- Out of memory → lower `validation.video_dims`, the LoRA `rank`, or the clip length of the dataset first.
- Keep `skip_initial_validation: true` on 24 GB cards - the first validation pass is the most memory-hungry moment.
- `checkpoints.keep_last_n: -1` keeps every checkpoint so you can pick the best one, not just the last.

## 5. LTX-2.5 (current trainer)

KESHIKI generates with **LTX-2.5**. A LoRA works only with the model it was trained on, so new LoRAs should be
trained on 2.5. What changed compared with the LTX-2.3 configs above (from the current Lightricks trainer docs):

- **Update the trainer.** Use a fresh clone of `Lightricks/LTX-2` (`packages/ltx-trainer`); configs now use the
  `flexible` training strategy (`video:` / `audio:` blocks, `conditions:` for IC-LoRA references) and
  `validation.samples`. The old `text_to_video` / `video_to_video` configs belong to the older trainer.
- **Split checkpoint.** LTX-2.5 ships one file per component, the same files ComfyUI uses:
  `model_path` = `diffusion_models/ltx-2.5-22b-dev-transformer-bf16.safetensors`,
  `text_encoder_path` = `text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors`,
  plus `video_vae_path` and `audio_vae_path`. Train on the **dev** transformer in bf16 (not the distilled one, not
  ComfyUI's int8 repack); the trainer quantizes it itself (`int8-quanto`).
- **Gemma 4, not Gemma 3.** LTX-2.5 needs the LTX-tuned Gemma 4 encoder. Text embeddings from 2.3 datasets
  cannot be reused: preprocess into a new `.precomputed` folder (or pass `--overwrite`).
- **Preprocessing on 24 GB:** add `--load-text-encoder-in-8bit --vae-tiling` (Gemma 4 12B in bf16 is 25 GB).
- **Lite test kit:** [`training_configs/ltx25_t2v_lora_24gb_test.yaml`](training_configs/ltx25_t2v_lora_24gb_test.yaml)
  - 8 clips, 512×288×49, 300 steps, rank 16, INT8, no validation videos. It checks that 2.5 trains on 24 GB at all;
  if it runs out of memory, switch to `int4-quanto`. *Status: prepared, not yet run.*
