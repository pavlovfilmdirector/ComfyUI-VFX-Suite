"""FLUX.3 Klein generation in the Image view: masks -> named boxes, guides -> references,
drawings -> notes (annotated reference) / sketch and collage (baked into the source)."""
import base64

import cv2
import numpy as np
import pytest
from conftest import load


def png_url(img):
    ok, b = cv2.imencode(".png", img)
    return "data:image/png;base64," + base64.b64encode(b.tobytes()).decode()


@pytest.fixture
def gen(fake_comfy, monkeypatch, comfy_dirs):
    m = load("image_generate_service")
    fake_comfy.patch(monkeypatch, m)
    monkeypatch.setattr(m, "_wait", lambda pid, t: open(__import__("os").path.join(comfy_dirs["output"], "x.png"), "rb").read()
                        if False else cv2.imencode(".png", np.full((64, 96, 3), 90, np.uint8))[1].tobytes())
    return m


SRC = np.full((64, 96, 3), 200, np.uint8)


def strokes():
    lay = np.zeros((64, 96, 4), np.uint8)
    cv2.line(lay, (5, 5), (90, 60), (0, 0, 255, 255), 6)
    return png_url(lay)


def test_text_to_image(gen, fake_comfy):
    r = gen.run_image_generate({"mode": "t2i", "prompt": "a red barn", "aspect": "1:1"})
    assert r["image"].startswith("data:image/png") and fake_comfy.runs[-1]["params"]["Output size.width"] == 1024


def test_masks_become_named_boxes(gen, fake_comfy):
    mask = np.zeros((64, 96, 4), np.uint8); mask[10:30, 20:50, 3] = 255
    r = gen.run_image_generate({"mode": "edit", "prompt": "make it blue", "source": png_url(SRC),
                                "masks": [{"name": "car", "data": png_url(mask)}]})
    boxes = fake_comfy.runs[-1]["params"]["Boxes.editor_state"]["__value__"]
    assert r["boxes"] == 1 and boxes[0]["metadata"]["desc"] == "car" and boxes[0]["x"] > 150


def test_notes_drawing_goes_in_as_annotated_reference(gen, fake_comfy):
    r = gen.run_image_generate({"mode": "edit", "prompt": "", "source": png_url(SRC),
                                "drawings": [{"name": "Notes", "mode": "annotation", "note": "a lamp here", "data": strokes()}]})
    assert r["references"] == 1
    assert "hand-drawn notes" in r["prompt_used"] and "a lamp here" in r["prompt_used"]
    prompt = fake_comfy.runs[-1]["prompt"]
    assert any(n["class_type"] == "LoadImage" and n["_meta"]["title"] == "Reference 1" for n in prompt.values())


def test_sketch_is_baked_into_the_source(gen, fake_comfy, comfy_dirs):
    import os
    before = set(os.listdir(comfy_dirs["input"]))
    r = gen.run_image_generate({"mode": "edit", "prompt": "render it", "source": png_url(SRC),
                                "drawings": [{"name": "Sketch", "mode": "sketch", "data": strokes()}]})
    assert "sketch" in r["prompt_used"] and r["references"] == 0
    src_name = fake_comfy.runs[-1]["params"]["Source Image.image"]
    img = cv2.imread(os.path.join(comfy_dirs["input"], src_name))
    assert img[34, 48, 2] > 200 and img[34, 48, 0] < 60   # the red stroke is in the source now


def test_collage_prompt(gen):
    r = gen.run_image_generate({"mode": "edit", "prompt": "", "source": png_url(SRC),
                                "drawings": [{"name": "Collage: cat", "mode": "collage", "data": strokes()}]})
    assert "collage" in r["prompt_used"] and "pasted" in r["prompt_used"]


def test_change_only_inside_masks_uses_the_clean_source(gen):
    mask = np.zeros((64, 96, 4), np.uint8); mask[:, :10, 3] = 255
    r = gen.run_image_generate({"mode": "edit", "prompt": "x", "source": png_url(SRC), "only_inside_masks": True,
                                "masks": [{"name": "m", "data": png_url(mask)}],
                                "drawings": [{"name": "S", "mode": "sketch", "data": strokes()}]})
    out = cv2.imdecode(np.frombuffer(base64.b64decode(r["image"].split(",")[1]), np.uint8), 1)
    assert out[34, 48].tolist() == [200, 200, 200]   # outside the mask: the original, without the sketch strokes


def test_edit_needs_a_source(gen):
    with pytest.raises(Exception):
        gen.run_image_generate({"mode": "edit", "prompt": "x"})
