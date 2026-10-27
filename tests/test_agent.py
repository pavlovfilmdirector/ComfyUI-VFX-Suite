"""The AI Director: every action it can choose reaches a handler in the studio UI, and help questions are
answered from the user guide."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core_agent"))
import studio_agent as sa  # noqa: E402

JS = open(os.path.join(ROOT, "web", "standalone", "studio_tabs_controller.js"), encoding="utf-8").read()
HANDLED = set(re.findall(r'act\.type === "([a-z_0-9]+)"', JS))

SAMPLE = {"generate_image": {"prompt": "x"}, "generate_video": {"prompt": "x"}, "edit_image": {"prompt": "x"},
          "video_process": {"kind": "depth"}, "dubbing": {"mode": "dub"}, "mesh3d": {"image": "x"},
          "deconstruct": {"passes": "depth"}, "music": {"tags": "piano", "lyrics": ""}, "voice": {"text": "Привет"},
          "show_workflow": {}}


@pytest.mark.parametrize("action", sorted(sa.ACTIONS))
def test_every_agent_action_has_a_ui_handler(action):
    acts, _ = sa.to_studio_actions(action, SAMPLE.get(action, {}), {})
    assert acts, f"{action} produces no studio action"
    for a in acts:
        assert a["type"] in HANDLED, f"{action} -> '{a['type']}' has no handler in studio_tabs_controller.js"


def test_every_video_process_kind_is_documented_for_the_agent():
    import importlib
    vl = importlib.import_module("vfx_nodes.video_layers_service")
    src = open(os.path.join(ROOT, "vfx_nodes", "video_layers_service.py"), encoding="utf-8").read()
    kinds = re.findall(r'"([a-z_0-9]+)": _job_', src[src.index("def start_job"):src.index("def start_job") + 2000])
    know = open(os.path.join(ROOT, "core_agent", "knowledge_studio", "studio.md"), encoding="utf-8").read()
    internal = {"track_object", "fill", "mask_grow", "layer_to_video", "detect_all", "undistort"}   # UI-driven helpers
    missing = [k for k in kinds if k not in internal and f"| {k}" not in know and f"{k} |" not in know and f"/ {k}" not in know]
    assert not missing, f"not in core_agent/knowledge_studio/studio.md: {missing}"


@pytest.mark.parametrize("q,expect", [
    ("как сделать дубляж видео", "Дубляж"), ("как заменить фон", "фон"), ("как сделать музыку", "Музыка"),
    ("how do I dub a video", "Dub"), ("как озвучить длинный текст закадровым голосом", "Закадровый"),
    ("как сделать липсинк по фото", "липсинк")])
def test_help_questions_are_answered_from_the_guide(q, expect):
    s = sa.guide_snippets(q)
    assert expect.lower() in s.lower(), s[:400]
