"""
Semantic Workflow Introspector and Auto-Healing Registry
=========================================================
Allows AI Director and Studio execution engine to discover and execute workflows
by their actual semantic structure (inputs, outputs, models, LoRAs, and capabilities)
rather than by hardcoded file paths or magic strings.

Key features:
1. Deep graph introspection: analyzes JSON node graphs to identify required inputs
   (video, image, audio, prompt), produced outputs, and model architecture (LTX-2.5,
   LTX-2.3, FLUX.3, Wan, Trellis, etc.).
2. Capability tagging: automatically tags workflows (layout_to_render, dubit, lipsync,
   first_last_frame, image_to_video, text_to_video, clean_plate, etc.).
3. Semantic discovery: resolves the best matching workflow based on user intent and
   supplied assets (e.g. 3D layout video -> layout_to_render).
4. Auto-healing: validates model files against ComfyUI's installed checkpoints,
   diffusion models, text encoders, and LoRAs, automatically healing missing or
   quantized variants (e.g. mapping bf16 to int8-convrot, or fixing slash directions).
"""

import copy
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

LOGGER = logging.getLogger("keshiki.workflow_inspector")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_TEMPLATES_DIR = os.path.join(BASE_DIR, "workflows", "api")

# Try to import ComfyUI folder_paths if available inside server process
try:
    import folder_paths
except ImportError:
    try:
        import sys
        for cand in [
            os.environ.get("COMFYUI_PATH", ""),
            r"B:\StabilityMatrix2\ComfyUI",
            os.path.join(os.path.dirname(BASE_DIR), "ComfyUI"),
            os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)), "ComfyUI"),
        ]:
            if cand and os.path.isdir(cand) and cand not in sys.path:
                sys.path.insert(0, cand)
        import folder_paths
    except Exception:
        folder_paths = None


@dataclass
class WorkflowSlot:
    """Represents a discovered input or output parameter in the workflow."""
    kind: str           # "video", "image", "audio", "prompt", "negative", "mask", "scalar"
    node_id: str
    node_title: str
    class_type: str
    input_key: str      # e.g. "file", "image", "audio", "text", "value"
    default_val: Any = None


@dataclass
class WorkflowSignature:
    """Semantic passport of a ComfyUI workflow JSON."""
    file_path: str
    category: str       # e.g. "video_fx", "video_gen", "voice", "mesh_gen"
    template_name: str  # e.g. "ltx25_ic_lora_v2v"
    engine: str         # "ltx-2.5", "ltx-2.3", "flux.3", "trellis2", "unknown"
    capabilities: Set[str] = field(default_factory=set)
    inputs: List[WorkflowSlot] = field(default_factory=list)
    outputs: List[WorkflowSlot] = field(default_factory=list)
    models_referenced: Dict[str, str] = field(default_factory=dict) # kind -> filename
    loras_referenced: List[str] = field(default_factory=list)
    node_count: int = 0
    mtime: float = 0.0

    def requires_video(self) -> bool:
        return any(s.kind == "video" for s in self.inputs)

    def requires_image(self) -> bool:
        return any(s.kind == "image" for s in self.inputs)

    def requires_audio(self) -> bool:
        return any(s.kind == "audio" for s in self.inputs)

    def image_input_count(self) -> int:
        return sum(1 for s in self.inputs if s.kind == "image")


def _is_api_prompt(data: Any) -> bool:
    return isinstance(data, dict) and bool(data) and all(
        isinstance(v, dict) and "class_type" in v for v in data.values()
    )


def inspect_workflow_json(file_path: str, category: str = "", name: str = "") -> Optional[WorkflowSignature]:
    """Parses a workflow JSON and extracts its semantic signature."""
    if not os.path.isfile(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        LOGGER.warning(f"Could not read workflow {file_path}: {e}")
        return None

    if not _is_api_prompt(data):
        return None

    mtime = os.path.getmtime(file_path)
    if not category:
        category = os.path.basename(os.path.dirname(file_path))
    if not name:
        name = os.path.splitext(os.path.basename(file_path))[0]

    sig = WorkflowSignature(
        file_path=file_path,
        category=category,
        template_name=name,
        engine="unknown",
        mtime=mtime,
        node_count=len(data)
    )

    # Inspect all nodes
    for nid, node in data.items():
        c_type = node.get("class_type", "")
        inputs = node.get("inputs", {})
        meta = node.get("_meta", {})
        title = meta.get("title", "") or c_type

        # 1. Engine detection
        c_lower = c_type.lower()
        if "ltxv" in c_lower or "ltx" in c_lower:
            sig.engine = "ltx-2.5" if "2.5" in file_path.lower() or any("2.5" in str(v) for v in inputs.values()) else "ltx-2.3"
        elif "flux" in c_lower or "flux" in file_path.lower():
            sig.engine = "flux.3"
        elif "trellis" in c_lower or "trellis" in file_path.lower():
            sig.engine = "trellis2"

        # 2. Models referenced
        if c_type in ("CheckpointLoaderSimple", "CheckpointLoader"):
            ckpt = inputs.get("ckpt_name")
            if isinstance(ckpt, str):
                sig.models_referenced["checkpoint"] = ckpt
                if "2.5" in ckpt:
                    sig.engine = "ltx-2.5"
                elif "2.3" in ckpt:
                    sig.engine = "ltx-2.3"
        elif c_type == "UNETLoader":
            unet = inputs.get("unet_name")
            if isinstance(unet, str):
                sig.models_referenced["diffusion_model"] = unet
                if "ltx-2.5" in unet:
                    sig.engine = "ltx-2.5"
                elif "flux" in unet.lower():
                    sig.engine = "flux.3"
        elif c_type == "CLIPLoader":
            clip = inputs.get("clip_name")
            if isinstance(clip, str):
                sig.models_referenced["clip"] = clip
        elif c_type in ("VAELoader", "LTXVAudioVAELoader"):
            vae = inputs.get("vae_name") or inputs.get("ckpt_name")
            if isinstance(vae, str):
                sig.models_referenced["vae"] = vae

        # 3. LoRAs / Adapters
        if c_type in ("LTXICLoRALoaderModelOnly", "LoraLoader", "LoraLoaderModelOnly"):
            lora = inputs.get("lora_name")
            if isinstance(lora, str):
                sig.loras_referenced.append(lora)
                l_lower = lora.lower()
                if "layout-to-render" in l_lower or "layout" in l_lower:
                    sig.capabilities.add("layout_to_render")
                if "dubit" in l_lower:
                    sig.capabilities.add("dubit")
                if "clean-plate" in l_lower:
                    sig.capabilities.add("clean_plate")
                if "day-to-night" in l_lower:
                    sig.capabilities.add("day_to_night")
                if "restore" in l_lower or "deblur" in l_lower:
                    sig.capabilities.add("restore")
                if "distilled" in l_lower:
                    sig.capabilities.add("distilled")

        # 4. Input slots
        if c_type in ("LoadVideo", "VHS_LoadVideo"):
            f_val = inputs.get("file") or inputs.get("video")
            sig.inputs.append(WorkflowSlot(
                kind="video", node_id=nid, node_title=title, class_type=c_type,
                input_key="file" if "file" in inputs else "video", default_val=f_val
            ))
        elif c_type == "LoadImage":
            img_val = inputs.get("image")
            sig.inputs.append(WorkflowSlot(
                kind="image", node_id=nid, node_title=title, class_type=c_type,
                input_key="image", default_val=img_val
            ))
        elif c_type == "LoadAudio":
            aud_val = inputs.get("audio")
            sig.inputs.append(WorkflowSlot(
                kind="audio", node_id=nid, node_title=title, class_type=c_type,
                input_key="audio", default_val=aud_val
            ))
        elif c_type in ("CLIPTextEncode", "PrimitiveStringMultiline"):
            t_val = inputs.get("text") or inputs.get("value")
            # Determine if positive or negative
            is_neg = any(k in title.lower() for k in ["neg", "отрицат"]) or any(k in str(t_val).lower() for k in ["ugly", "blurry", "bad"])
            sig.inputs.append(WorkflowSlot(
                kind="negative" if is_neg else "prompt",
                node_id=nid, node_title=title, class_type=c_type,
                input_key="text" if "text" in inputs else "value", default_val=t_val
            ))

        # 5. Output slots
        if c_type in ("SaveVideo", "VHS_VideoCombine"):
            sig.outputs.append(WorkflowSlot(
                kind="video", node_id=nid, node_title=title, class_type=c_type, input_key="video"
            ))
        elif c_type in ("SaveImage", "PreviewImage"):
            sig.outputs.append(WorkflowSlot(
                kind="image", node_id=nid, node_title=title, class_type=c_type, input_key="images"
            ))

    # Infer higher-level capabilities from topology
    has_video_out = any(s.kind == "video" for s in sig.outputs)
    has_image_out = any(s.kind == "image" for s in sig.outputs)
    has_video_in = sig.requires_video()
    has_image_in = sig.requires_image()
    has_audio_in = sig.requires_audio()
    img_count = sig.image_input_count()

    if has_video_out:
        if "layout_to_render" in sig.capabilities or (has_video_in and "ltx" in sig.engine):
            sig.capabilities.add("layout_to_render")
            sig.capabilities.add("v2v")
        if "dubit" in sig.capabilities:
            sig.capabilities.add("speech_dubbing")
        if has_audio_in and has_image_in:
            sig.capabilities.add("lipsync_a2v")
            sig.capabilities.add("voice_to_video")
        if img_count >= 2:
            sig.capabilities.add("first_last_frame")
            sig.capabilities.add("flf2v")
        elif img_count == 1 and not has_video_in and not has_audio_in:
            sig.capabilities.add("image_to_video")
            sig.capabilities.add("i2v")
        elif not has_video_in and not has_image_in:
            sig.capabilities.add("text_to_video")
            sig.capabilities.add("t2v")
    elif has_image_out:
        if has_image_in:
            sig.capabilities.add("image_to_image")
            sig.capabilities.add("image_edit")
        else:
            sig.capabilities.add("text_to_image")
            sig.capabilities.add("t2i")

    return sig


class SemanticWorkflowRegistry:
    """Singleton registry indexing all workflows in the studio by semantic capabilities."""

    def __init__(self, root_dir: str = API_TEMPLATES_DIR):
        self.root_dir = root_dir
        self._cache: Dict[str, WorkflowSignature] = {}
        self._last_scan: float = 0.0

    def refresh(self, force: bool = False):
        """Scans the workflows directory and updates cache."""
        if not os.path.isdir(self.root_dir):
            return

        for cat in os.listdir(self.root_dir):
            cat_dir = os.path.join(self.root_dir, cat)
            if not os.path.isdir(cat_dir):
                continue
            for fn in os.listdir(cat_dir):
                if not fn.endswith(".json"):
                    continue
                fp = os.path.join(cat_dir, fn)
                key = f"{cat}/{fn[:-5]}"
                mtime = os.path.getmtime(fp)
                if not force and key in self._cache and self._cache[key].mtime == mtime:
                    continue
                sig = inspect_workflow_json(fp, category=cat, name=fn[:-5])
                if sig:
                    self._cache[key] = sig

    def list_signatures(self) -> List[WorkflowSignature]:
        self.refresh()
        return list(self._cache.values())

    def get_by_key(self, key: str) -> Optional[WorkflowSignature]:
        self.refresh()
        return self._cache.get(key)

    def find_best(
        self,
        intent: str,
        has_video: bool = False,
        has_image: bool = False,
        has_audio: bool = False,
        image_count: int = 0,
        preferred_engine: Optional[str] = None
    ) -> Optional[WorkflowSignature]:
        """
        Finds the most suitable workflow matching the semantic intent and supplied assets.
        Intent can be: "layout_to_render", "dubit", "lipsync", "flf2v", "i2v", "t2v", etc.
        """
        self.refresh()
        candidates: List[Tuple[int, WorkflowSignature]] = []

        intent_lower = intent.lower()

        for sig in self._cache.values():
            score = 0
            # 1. Direct capability match
            if intent_lower in sig.capabilities:
                score += 50
            if any(intent_lower in c for c in sig.capabilities):
                score += 25

            # 2. Asset requirements match
            if has_video:
                if sig.requires_video():
                    score += 30
                else:
                    score -= 40
            else:
                if sig.requires_video():
                    score -= 40

            if has_audio:
                if sig.requires_audio():
                    score += 30
                else:
                    score -= 20
            else:
                if sig.requires_audio():
                    score -= 20

            if image_count >= 2:
                if "first_last_frame" in sig.capabilities:
                    score += 40
            elif image_count == 1:
                if "image_to_video" in sig.capabilities or "lipsync_a2v" in sig.capabilities:
                    score += 30
            elif not has_image and not has_video:
                if "text_to_video" in sig.capabilities or "text_to_image" in sig.capabilities:
                    score += 30

            # 3. Engine preference
            if preferred_engine and preferred_engine in sig.engine:
                score += 15

            # 4. Penalize broken/empty workflows
            if sig.node_count < 5:
                score -= 100

            if score > 0:
                candidates.append((score, sig))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]


# Global singleton
_REGISTRY: Optional[SemanticWorkflowRegistry] = None

def get_semantic_registry() -> SemanticWorkflowRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SemanticWorkflowRegistry()
        _REGISTRY.refresh(force=True)
    return _REGISTRY


# -----------------------------------------------------------------------------
# AUTO-HEALING & MODEL VALIDATION
# -----------------------------------------------------------------------------

def heal_and_validate_prompt(prompt: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Validates all model/LoRA references in a ComfyUI prompt against currently installed
    files in ComfyUI's models directory. Automatically repairs missing or quantized
    variants (e.g. replacing missing distilled/bf16 with installed int8-convrot).
    
    Returns (healed_prompt, list_of_repairs).
    """
    healed = copy.deepcopy(prompt)
    repairs = []

    if folder_paths is None:
        return healed, repairs

    try:
        available_ckpts = folder_paths.get_filename_list("checkpoints")
        available_diff = folder_paths.get_filename_list("diffusion_models")
        available_clips = folder_paths.get_filename_list("text_encoders")
        available_vaes = folder_paths.get_filename_list("vae")
        available_loras = folder_paths.get_filename_list("loras")
    except Exception as e:
        LOGGER.warning(f"Could not retrieve folder_paths lists: {e}")
        return healed, repairs

    def _normalize_slash(p: str) -> str:
        return p.replace("/", "\\") if os.name == "nt" else p.replace("\\", "/")

    def _find_best_match(target: str, candidates: List[str]) -> Optional[str]:
        if not target or not candidates:
            return None
        # Exact match (case insensitive, slash agnostic)
        norm_t = _normalize_slash(target).lower()
        for cand in candidates:
            if _normalize_slash(cand).lower() == norm_t:
                return cand

        # Extract base tokens
        t_base = os.path.basename(target).lower()
        # Prefer exact base filename
        for cand in candidates:
            if os.path.basename(cand).lower() == t_base:
                return cand

        # Fuzzy match for common variants (dev vs distilled, bf16 vs int8-convrot)
        tokens = [tk for tk in re.split(r"[-_.\s]+", t_base) if tk not in ("safetensors", "comfy", "transformer", "22b")]
        best_cand, best_score = None, 0
        for cand in candidates:
            c_base = os.path.basename(cand).lower()
            score = sum(1 for tk in tokens if tk in c_base)
            if "ltx-2.5" in t_base and "ltx-2.5" in c_base:
                score += 5
            elif "ltx-2.3" in t_base and "ltx-2.3" in c_base:
                score += 5
            elif "flux" in t_base and "flux" in c_base:
                score += 5
            if score > best_score:
                best_score = score
                best_cand = cand

        return best_cand if best_score >= 3 else None

    # Inspect and heal each node
    for nid, node in healed.items():
        c_type = node.get("class_type", "")
        inputs = node.setdefault("inputs", {})

        # 1. CheckpointLoaderSimple
        if c_type in ("CheckpointLoaderSimple", "CheckpointLoader"):
            cur = inputs.get("ckpt_name")
            if isinstance(cur, str) and cur not in available_ckpts:
                match = _find_best_match(cur, available_ckpts)
                if match:
                    repairs.append(f"Node {nid} ({c_type}): ckpt_name '{cur}' -> '{match}'")
                    inputs["ckpt_name"] = match
                elif available_ckpts:
                    # Fallback to first available matching engine
                    cand = next((c for c in available_ckpts if "ltx" in c.lower()), available_ckpts[0])
                    repairs.append(f"Node {nid} ({c_type}): fallback ckpt_name '{cur}' -> '{cand}'")
                    inputs["ckpt_name"] = cand

        # 2. UNETLoader
        elif c_type == "UNETLoader":
            cur = inputs.get("unet_name")
            if isinstance(cur, str) and cur not in available_diff:
                match = _find_best_match(cur, available_diff)
                if match:
                    repairs.append(f"Node {nid} ({c_type}): unet_name '{cur}' -> '{match}'")
                    inputs["unet_name"] = match

        # 3. CLIPLoader
        elif c_type == "CLIPLoader":
            cur = inputs.get("clip_name")
            if isinstance(cur, str) and cur not in available_clips:
                match = _find_best_match(cur, available_clips)
                if match:
                    repairs.append(f"Node {nid} ({c_type}): clip_name '{cur}' -> '{match}'")
                    inputs["clip_name"] = match

        # 4. VAELoader
        elif c_type in ("VAELoader", "LTXVAudioVAELoader"):
            for v_key in ("vae_name", "ckpt_name"):
                cur = inputs.get(v_key)
                if isinstance(cur, str) and cur not in available_vaes and cur not in available_ckpts:
                    match = _find_best_match(cur, available_vaes)
                    if match:
                        repairs.append(f"Node {nid} ({c_type}): {v_key} '{cur}' -> '{match}'")
                        inputs[v_key] = match

        # 5. LoRA Loader
        elif c_type in ("LTXICLoRALoaderModelOnly", "LoraLoader", "LoraLoaderModelOnly"):
            cur = inputs.get("lora_name")
            if isinstance(cur, str) and cur not in available_loras:
                # Fix slash direction first
                cur_norm = _normalize_slash(cur)
                if cur_norm in available_loras:
                    inputs["lora_name"] = cur_norm
                else:
                    match = _find_best_match(cur, available_loras)
                    if match:
                        repairs.append(f"Node {nid} ({c_type}): lora_name '{cur}' -> '{match}'")
                        inputs["lora_name"] = match

    if repairs:
        LOGGER.info(f"[heal_and_validate_prompt] Auto-healed {len(repairs)} model reference(s):\n" + "\n".join(f"  • {r}" for r in repairs))

    return healed, repairs
