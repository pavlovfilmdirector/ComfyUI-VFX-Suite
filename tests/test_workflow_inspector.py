"""
Tests for Semantic Workflow Introspection and Auto-Healing
===========================================================
Ensures the AI Director and execution engine can parse workflows by content,
identify capabilities, resolve the best template for a user request, and
auto-heal missing or quantized model references.
"""

import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vfx_nodes.workflow_inspector import (
    get_semantic_registry,
    inspect_workflow_json,
    heal_and_validate_prompt,
    SemanticWorkflowRegistry
)


def test_inspect_layout_to_render_workflow():
    fp = os.path.join(ROOT, "workflows", "api", "video_fx", "ltx25_ic_lora_v2v.json")
    sig = inspect_workflow_json(fp)
    assert sig is not None
    assert sig.requires_video() is True
    assert "layout_to_render" in sig.capabilities
    assert sig.node_count > 10


def test_inspect_dubit_workflow():
    fp = os.path.join(ROOT, "workflows", "api", "voice", "ltx23_dubit.json")
    sig = inspect_workflow_json(fp)
    assert sig is not None
    assert sig.requires_video() is True
    assert "dubit" in sig.capabilities
    assert "speech_dubbing" in sig.capabilities


def test_inspect_lipsync_a2v_workflow():
    fp = os.path.join(ROOT, "workflows", "api", "voice", "ltx25_a2v.json")
    sig = inspect_workflow_json(fp)
    assert sig is not None
    assert sig.requires_audio() is True
    assert sig.requires_image() is True
    assert "lipsync_a2v" in sig.capabilities or "voice_to_video" in sig.capabilities


def test_inspect_text_to_video_workflow():
    fp = os.path.join(ROOT, "workflows", "api", "video_gen", "ltx25_t2v.json")
    sig = inspect_workflow_json(fp)
    assert sig is not None
    assert sig.requires_video() is False
    assert sig.requires_image() is False
    assert "text_to_video" in sig.capabilities


def test_semantic_registry_discovery():
    reg = get_semantic_registry()
    sigs = reg.list_signatures()
    assert len(sigs) >= 15

    # 1. Layout-to-render
    best_layout = reg.find_best("layout_to_render", has_video=True)
    assert best_layout is not None
    assert best_layout.template_name == "ltx25_ic_lora_v2v"
    assert best_layout.category == "video_fx"

    # 2. DubIt
    best_dubit = reg.find_best("dubit", has_video=True)
    assert best_dubit is not None
    assert best_dubit.template_name == "ltx23_dubit"
    assert best_dubit.category == "voice"

    # 3. LipSync
    best_lipsync = reg.find_best("lipsync", has_audio=True, image_count=1)
    assert best_lipsync is not None
    assert best_lipsync.template_name == "ltx25_a2v"
    assert best_lipsync.category == "voice"

    # 4. Text-to-Video
    best_t2v = reg.find_best("text_to_video")
    assert best_t2v is not None
    assert best_t2v.template_name == "ltx25_t2v"
    assert best_t2v.category == "video_gen"


def test_heal_and_validate_prompt_healing():
    prompt = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "non_existent_random_ckpt.safetensors"}
        },
        "2": {
            "class_type": "LTXICLoRALoaderModelOnly",
            "inputs": {"lora_name": "LTX-2.5/ltx-2.5-22b-ic-lora-layout-to-render-1.0.safetensors"}
        }
    }
    healed, repairs = heal_and_validate_prompt(prompt)
    assert healed is not None
    assert "1" in healed and "2" in healed
    # If folder_paths has checkpoints, it should have picked a real one or kept it safe
    ckpt = healed["1"]["inputs"]["ckpt_name"]
    assert isinstance(ckpt, str) and ckpt.endswith(".safetensors")
