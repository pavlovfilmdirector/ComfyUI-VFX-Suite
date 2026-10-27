"""Front end <-> back end contracts: every job kind / action the UI sends is handled by the server."""
import os
import re
import base64
import json

import pytest

from conftest import load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "web", "standalone", "studio_tabs_controller.js"), encoding="utf-8").read()


def _video_job_kinds():
    src = open(os.path.join(ROOT, "vfx_nodes", "video_layers_service.py"), encoding="utf-8").read()
    body = src[src.index("def start_job("):]
    body = body[:body.index("}.get(kind)")]
    return set(re.findall(r'"([a-z_0-9]+)":\s*_job_', body))


def test_every_2d_job_the_ui_starts_exists():
    kinds = _video_job_kinds()
    ui = set(re.findall(r'runVideoJob2D\("([a-z_0-9]+)"', JS)) | {"montage"}
    assert ui, "no runVideoJob2D calls found - did the helper get renamed?"
    assert not (ui - kinds), f"UI starts unknown 2D jobs: {sorted(ui - kinds)}"


def test_unknown_2d_job_is_rejected(project):
    vl = load("video_layers_service")
    with pytest.raises(vl.ProcessError, match="Unknown"):
        vl.start_job("no_such_kind", project.pid, "v1", {})


def _fn_body(name):
    i = JS.index(f"function {name}(")
    j = JS.find("\n    function ", i + 10)
    k = JS.find("\n    async function ", i + 10)
    ends = [x for x in (j, k) if x > 0]
    return JS[i:min(ends) if ends else len(JS)]


def _route_block(prefix):
    src = open(os.path.join(ROOT, "vfx_nodes", "server_api.py"), encoding="utf-8").read()
    i = src.index(prefix + "/{project_id}/{action}")
    return src[i:i + 3000]


@pytest.mark.parametrize("fn,prefix", [("openNarrationEditor", "/vfx/narration"), ("openVoiceBoard", "/vfx/voiceboard")])
def test_editor_actions_match_routes(fn, prefix):
    used = set(re.findall(r'runJob\("([a-z_]+)"', _fn_body(fn)))
    assert used, f"{fn} posts no actions?"
    block = _route_block(prefix)
    missing = [a for a in used if f'action == "{a}"' not in block]
    assert not missing, f"{fn} sends actions the server does not handle: {missing}"


# ---------------------------------------------------------------- image layers persistence
PNG_1x1 = "data:image/png;base64," + base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()


def test_layers_roundtrip_and_cleanup(project):
    ls = load("layers_service")
    r = ls.save_layers(project.pid, "img1", [
        {"id": "a", "name": "Paint", "data": PNG_1x1, "opacity": 3},
        {"id": "b", "name": "Sel copy", "data": PNG_1x1, "type": "weird"}], "b")
    assert [l["id"] for l in r["layers"]] == ["a", "b"]
    assert r["layers"][0]["opacity"] == 1.0 and r["layers"][1]["type"] == "paint"
    assert r["layers"][0]["src"].startswith(f"/vfx/project_file/{project.pid}/layers/img1/a.png")
    # second save without data keeps file a, drops b (deleted layer)
    r2 = ls.save_layers(project.pid, "img1", [{"id": "a", "name": "Paint"}])
    d = ls.layers_dir(project.pid, "img1")
    assert os.path.isfile(os.path.join(d, "a.png")) and not os.path.exists(os.path.join(d, "b.png"))
    assert ls.load_layers(project.pid, "img1")["layers"][0]["file"] == "a.png"


def test_layers_reject_non_png(project):
    ls = load("layers_service")
    with pytest.raises(ValueError):
        ls.save_layers(project.pid, "img2", [{"id": "x", "data": "data:image/jpeg;base64,AAAA"}])


def test_layer_ids_cannot_escape_folder(project):
    ls = load("layers_service")
    r = ls.save_layers(project.pid, "../../evil", [{"id": "../../x", "data": PNG_1x1}])
    d = ls.layers_dir(project.pid, "../../evil")
    assert os.path.realpath(d).startswith(os.path.realpath(project.dir))
    assert "/" not in r["layers"][0]["id"] and ".." not in r["layers"][0]["id"]
