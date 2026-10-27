"""
Loader nodes KESHIKI workflows need beyond ComfyUI's stock ones.

KeshikiDualCLIPLoaderDtype - DualCLIPLoader with an explicit weight dtype. ComfyUI loads every text encoder in fp16
by default; ACE-Step 1.5's 4B language model (it writes the audio codes) overflows in fp16, the sampling
probabilities turn into NaN and CUDA aborts the whole ComfyUI process ("TensorCompare.cu: input[0] != 0").
bf16 (the dtype the weights ship in) keeps fp32's range.
"""
import folder_paths

DTYPES = ["bf16", "fp32", "fp16", "default"]


class KeshikiDualCLIPLoaderDtype:
    @classmethod
    def INPUT_TYPES(cls):
        import comfy.sd
        files = folder_paths.get_filename_list("text_encoders")
        types = [t.name.lower() for t in comfy.sd.CLIPType]
        return {"required": {"clip_name1": (files,), "clip_name2": (files,), "type": (types, {"default": "ace"}),
                             "dtype": (DTYPES, {"default": "bf16"})}}

    RETURN_TYPES = ("CLIP",)
    FUNCTION = "load"
    CATEGORY = "KESHIKI/loaders"

    def load(self, clip_name1, clip_name2, type, dtype="bf16"):
        import torch
        import comfy.sd
        clip_type = getattr(comfy.sd.CLIPType, type.upper(), comfy.sd.CLIPType.STABLE_DIFFUSION)
        opts = {}
        if dtype != "default":
            opts["dtype"] = {"bf16": torch.bfloat16, "fp32": torch.float32, "fp16": torch.float16}[dtype]
        clip = comfy.sd.load_clip(ckpt_paths=[folder_paths.get_full_path_or_raise("text_encoders", clip_name1),
                                              folder_paths.get_full_path_or_raise("text_encoders", clip_name2)],
                                  embedding_directory=folder_paths.get_folder_paths("embeddings"),
                                  clip_type=clip_type, model_options=opts)
        return (clip,)


NODE_CLASS_MAPPINGS = {"KeshikiDualCLIPLoaderDtype": KeshikiDualCLIPLoaderDtype}
NODE_DISPLAY_NAME_MAPPINGS = {"KeshikiDualCLIPLoaderDtype": "KESHIKI Dual CLIP Loader (dtype)"}


# ---------------------------------------------------------------------------------------------------------------
# ACE-Step 1.5 LM guard. On this setup (torch 2.6) the audio-code LM can return NaN logits; torch.multinomial then
# fires a CUDA device assert and the whole ComfyUI process aborts. The guard checks the logits every step:
# NaN -> rerun the LM in fp32 once; still NaN -> a normal Python error (the node fails, ComfyUI stays up).
class _AceNaN(RuntimeError):
    pass


def _install_ace_guard():
    import logging
    try:
        import torch
        import comfy.text_encoders.ace15 as ace
    except Exception:  # noqa: BLE001 - older ComfyUI without ACE-Step 1.5
        return False
    if not hasattr(ace, "_keshiki_orig"):          # keep ComfyUI's originals across module reloads
        ace._keshiki_orig = (ace._audio_logits, ace.sample_manual_loop_no_classes)
    orig_logits, orig_loop = ace._keshiki_orig

    def audio_logits(*a, **kw):
        logits, eos = orig_logits(*a, **kw)
        bad = ~torch.isfinite(logits)
        if bool(bad.any()):
            raise _AceNaN(f"ACE-Step LM: {int(bad.sum())} non-finite logits (dtype {logits.dtype})")
        return logits, eos

    def loop(model, ids=None, execution_dtype=None, **kw):
        # 1) as ComfyUI does it  2) without comfy_kitchen's flash-attention decode kernel (the NaN source seen with
        # torch 2.6)  3) the same in fp32. The first that gives finite logits wins.
        inner = model.transformer.model
        had = inner.__dict__.get("fixed_kv", None)
        attempts = [("default", execution_dtype, None), ("no flash decode", execution_dtype, False),
                    ("no flash decode, fp32", torch.float32, False)]
        last = None
        start = getattr(ace, "_keshiki_mode", 0)       # remember the mode that worked: no wasted retries next time
        for n, (label, dt, fixed) in enumerate(attempts):
            if n < start:
                continue
            try:
                if fixed is not None:
                    inner.fixed_kv = fixed
                out = orig_loop(model, ids, execution_dtype=dt, **kw)
                ace._keshiki_mode = n
                return out
            except _AceNaN as e:
                last = e
                logging.warning("[KESHIKI] %s (%s) - trying the next ACE-Step LM mode", e, label)
            finally:
                if had is None:
                    inner.__dict__.pop("fixed_kv", None)
                else:
                    inner.fixed_kv = had
        raise RuntimeError(f"{last}. The audio-code LM fails on this GPU/PyTorch; turn off 'generate audio codes'.")

    ace._audio_logits = audio_logits
    ace.sample_manual_loop_no_classes = loop
    ace._keshiki_guard = True
    logging.info("[KESHIKI] ACE-Step LM NaN guard installed")
    return True


try:
    _install_ace_guard()
except Exception as _e:  # noqa: BLE001
    print(f"[ComfyUI-VFX-Suite] ACE-Step guard not installed: {_e}")
