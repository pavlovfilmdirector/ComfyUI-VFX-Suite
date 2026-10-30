"""
Workflow converter: converts ComfyUI UI (editor) format workflows (with 'nodes' and 'links')
into ComfyUI API prompt format (with node_id mapping to class_type and inputs).
"""
import copy
from typing import Any, Dict, List, Optional


def convert_ui_workflow_to_api_prompt(ui_wf: Dict[str, Any]) -> Dict[str, Any]:
    """Converts a ComfyUI UI workflow format into an executable API prompt format."""
    if not isinstance(ui_wf, dict):
        return {}

    # If it's already an API prompt or contains an embedded prompt:
    if "api_prompt" in ui_wf and isinstance(ui_wf["api_prompt"], dict):
        return ui_wf["api_prompt"]
    if "prompt" in ui_wf and isinstance(ui_wf["prompt"], dict) and not ("nodes" in ui_wf["prompt"]):
        return ui_wf["prompt"]

    nodes = ui_wf.get("nodes", [])
    links = ui_wf.get("links", [])
    if not isinstance(nodes, list):
        return {}

    # link: [link_id, from_node_id, from_output_slot, to_node_id, to_input_slot, type]
    link_map = {}
    if isinstance(links, list):
        for l in links:
            if isinstance(l, (list, tuple)) and len(l) >= 3:
                link_map[l[0]] = (str(l[1]), int(l[2]))

    # Node definitions from ComfyUI registry if in process
    node_classes = {}
    try:
        import nodes as comfy_nodes
        node_classes = getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {})
    except Exception:
        pass

    prompt = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id"))
        ctype = str(node.get("type") or "")
        if not ctype:
            continue

        title = str(node.get("title") or (node.get("properties") or {}).get("Node name for S&R") or ctype)
        inputs = {}

        # 1. Linked inputs
        for inp in node.get("inputs", []) or []:
            if not isinstance(inp, dict):
                continue
            lk = inp.get("link")
            nm = inp.get("name")
            if lk is not None and lk in link_map and nm:
                inputs[nm] = [link_map[lk][0], link_map[lk][1]]

        # 2. Widget values (non-linked parameter values)
        wvals = node.get("widgets_values")
        if wvals is not None:
            if isinstance(wvals, dict):
                for k, v in wvals.items():
                    if k not in inputs:
                        inputs[k] = v
            elif isinstance(wvals, list):
                # Try ComfyUI schema mapping first
                cls = node_classes.get(ctype)
                if cls and hasattr(cls, "INPUT_TYPES"):
                    try:
                        itypes = cls.INPUT_TYPES()
                        req = itypes.get("required", {}) if isinstance(itypes, dict) else {}
                        opt = itypes.get("optional", {}) if isinstance(itypes, dict) else {}
                        w_names = []
                        for group in (req, opt):
                            if isinstance(group, dict):
                                for name in group.keys():
                                    if name not in inputs:
                                        w_names.append(name)
                        for idx, val in enumerate(wvals):
                            if idx < len(w_names):
                                inputs[w_names[idx]] = val
                    except Exception:
                        pass

                # Fallback / heuristic mappings for common classes
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
                elif ctype == "LoadImage":
                    if len(wvals) > 0 and "image" not in inputs: inputs["image"] = str(wvals[0])
                elif ctype in ("LoadVideo", "VHS_LoadVideo", "VHS_LoadVideoFFmpeg"):
                    field = "video" if "video" in ctype else "file"
                    if len(wvals) > 0 and field not in inputs: inputs[field] = str(wvals[0])
                elif "LoraLoader" in ctype or "LoRALoader" in ctype:
                    if len(wvals) > 0 and "lora_name" not in inputs: inputs["lora_name"] = str(wvals[0])
                    if len(wvals) > 1 and "strength_model" not in inputs:
                        try: inputs["strength_model"] = float(wvals[1])
                        except Exception: pass
                    if len(wvals) > 2 and "strength_clip" not in inputs:
                        try: inputs["strength_clip"] = float(wvals[2])
                        except Exception: pass
                elif ctype == "KSampler":
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
                elif ctype in ("SaveImage", "SaveVideo", "VHS_VideoCombine"):
                    if len(wvals) > 0 and "filename_prefix" not in inputs: inputs["filename_prefix"] = str(wvals[0])

        prompt[nid] = {
            "class_type": ctype,
            "inputs": inputs,
            "_meta": {"title": title}
        }

    return prompt
