"""Core plumbing: template runner, local settings, dubbing config, install manifest, docs, front-end <-> routes."""
import glob
import json
import os
import re
import shutil
import subprocess

import pytest

from conftest import load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- process_runner
def test_inject_params_by_title_and_id():
    pr = load("process_runner")
    prompt = {"1": {"class_type": "A", "inputs": {"seed": 1, "img": ["2", 0]}, "_meta": {"title": "Sampler"}},
              "2": {"class_type": "B", "inputs": {"text": "x"}}}
    out = pr.inject_params(prompt, {"Sampler.seed": 7, "2.text": "y"})
    assert out["1"]["inputs"]["seed"] == 7 and out["2"]["inputs"]["text"] == "y"
    assert prompt["1"]["inputs"]["seed"] == 1, "inject_params must not mutate the template"


@pytest.mark.parametrize("key,msg", [("seed", "Bad param key"), ("Nope.seed", "not found in template"),
                                     ("Sampler.nope", "not found on node"), ("Sampler.img", "is a link")])
def test_inject_params_rejects_typos(key, msg):
    pr = load("process_runner")
    prompt = {"1": {"class_type": "A", "inputs": {"seed": 1, "img": ["2", 0]}, "_meta": {"title": "Sampler"}}}
    with pytest.raises(pr.ProcessError, match=msg):
        pr.inject_params(prompt, {key: 1})


def test_load_template_errors(tmp_path, monkeypatch):
    pr = load("process_runner")
    monkeypatch.setattr(pr, "TEMPLATES_DIR", str(tmp_path))
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "ui.json").write_text(json.dumps({"nodes": [], "links": []}))
    with pytest.raises(pr.ProcessError, match="not found"):
        pr.load_template("p", "missing")
    with pytest.raises(pr.ProcessError, match="API-format"):
        pr.load_template("p", "ui")
    # path traversal is neutralised
    with pytest.raises(pr.ProcessError, match="not found"):
        pr.load_template("p", "../../etc/passwd")


def test_list_templates_covers_every_folder():
    pr = load("process_runner")
    t = pr.list_templates()
    for d in glob.glob(os.path.join(ROOT, "workflows", "api", "*")):
        if os.path.isdir(d) and glob.glob(os.path.join(d, "*.json")):
            assert os.path.basename(d) in t


def _service_sources():
    for p in glob.glob(os.path.join(ROOT, "vfx_nodes", "*.py")):
        with open(p, encoding="utf-8") as f:
            yield p, f.read()


def test_every_run_process_template_exists():
    """run_process(<id>, template="x") and load_template("<id>", "x") must point at real files."""
    pr = load("process_runner")
    have = pr.list_templates()
    consts = {}
    missing = []
    for p, src in _service_sources():
        consts = dict(re.findall(r'^([A-Z_]+)\s*=\s*"([a-z0-9_]+)"', src, re.M))
        for pid, tpl in re.findall(r'run_process\(\s*([A-Za-z_"0-9]+)\s*,\s*template\s*=\s*"([^"]+)"', src):
            pid = pid.strip('"') if pid.startswith('"') else consts.get(pid)
            if pid and tpl not in have.get(pid, []):
                missing.append(f"{os.path.basename(p)}: {pid}/{tpl}")
    assert not missing, missing


# ---------------------------------------------------------------- local settings / cloud gating
def test_cloud_off_by_default(monkeypatch, tmp_path):
    lc = load("local_config")
    monkeypatch.setattr(lc, "_PATH", str(tmp_path / "none.json"))
    monkeypatch.delenv("KESHIKI_ALLOW_CLOUD", raising=False)
    assert lc.cloud_services_allowed() is False
    monkeypatch.setenv("KESHIKI_ALLOW_CLOUD", "1")
    assert lc.cloud_services_allowed() is True


def test_local_setting_file_and_env(monkeypatch, tmp_path):
    lc = load("local_config")
    f = tmp_path / "k.json"
    f.write_text(json.dumps({"depth_pro_root": "G:/x"}))
    monkeypatch.setattr(lc, "_PATH", str(f))
    monkeypatch.delenv("KESHIKI_DEPTH_PRO_ROOT", raising=False)
    assert lc.local_setting("depth_pro_root") == "G:/x"
    monkeypatch.setenv("KESHIKI_DEPTH_PRO_ROOT", "H:/y")
    assert lc.local_setting("depth_pro_root") == "H:/y"
    assert lc.local_setting("nothing", "d") == "d"


# ---------------------------------------------------------------- dubbing
def test_dubbing_scripts_exist_and_open_engines_only():
    ds = load("dubbing_service")
    for mode, script in ds.SCRIPTS.items():
        assert os.path.isfile(os.path.join(ds.DUB_DIR, script)), f"{mode}: dubbing/{script} missing"
    assert "text" in ds.SCRIPTS
    assert ds.defaults("text") == {}
    ds.cloud_services_allowed = lambda: False
    st = ds.status()
    assert st["engines"] == ["f5tts"], "VibeVoice (withdrawn) and Qwen3-TTS (untested) stay hidden"


def test_dubbing_deep_merge():
    ds = load("dubbing_service")
    assert ds._deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}}) == {"a": {"b": 1, "c": 3}}


# ---------------------------------------------------------------- install manifest
def test_install_manifest_well_formed():
    m = json.load(open(os.path.join(ROOT, "install_manifest.json"), encoding="utf-8"))
    for p in m["node_packs"]:
        assert p["git"].startswith("https://github.com/") and p["dir"]
    for t in m["tools_windows"]:
        assert t["url"].startswith("https://") and t["dest"]
    groups = {x["group"] for x in m["models"]}
    assert {"music"} <= groups
    seen = set()
    for x in m["models"]:
        link = x.get("url") or x.get("page")
        assert (link and link.startswith("https://")) or x.get("note"), x
        assert not x["dir"].startswith(("/", "\\")) and ".." not in x["dir"], x
        assert (x["dir"], x["file"]) not in seen, f"duplicate {x['file']}"
        seen.add((x["dir"], x["file"]))


def test_music_manifest_matches_service():
    m = json.load(open(os.path.join(ROOT, "install_manifest.json"), encoding="utf-8"))
    files = {x["file"] for x in m["models"] if x["group"] == "music"}
    ms = load("music_service")
    for lst in ms.MODEL_FILES.values():
        for f in lst:
            assert f in files, f"music model {f} is not in install_manifest.json"


# ---------------------------------------------------------------- docs
DOCS = [os.path.join(ROOT, f) for f in ("README.md", "INSTALL.md", "THIRD_PARTY_NOTICES.md")] + \
    glob.glob(os.path.join(ROOT, "docs", "*.md"))


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: os.path.relpath(p, ROOT))
def test_doc_relative_links_resolve(doc):
    src = open(doc, encoding="utf-8").read()
    bad = []
    for link in re.findall(r"\]\(([^)\s]+)\)", src):
        if re.match(r"^(https?:|mailto:|#)", link):
            continue
        target = os.path.normpath(os.path.join(os.path.dirname(doc), link.split("#")[0]))
        if not os.path.exists(target):
            bad.append(link)
    assert not bad, bad


# ---------------------------------------------------------------- front end
JS = [p for p in glob.glob(os.path.join(ROOT, "web", "**", "*.js"), recursive=True) if "/lib/" not in p.replace("\\", "/")]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("js", JS, ids=lambda p: os.path.relpath(p, ROOT))
def test_js_syntax(js, tmp_path):
    dst = tmp_path / "x.mjs"
    shutil.copy(js, dst)
    r = subprocess.run(["node", "--check", str(dst)], capture_output=True, text=True)
    if r.returncode and "import" in r.stderr:   # classic scripts
        dst2 = tmp_path / "x.js"
        shutil.copy(js, dst2)
        r = subprocess.run(["node", "--check", str(dst2)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]


def _routes():
    src = open(os.path.join(ROOT, "vfx_nodes", "server_api.py"), encoding="utf-8").read()
    pats = []
    for path in re.findall(r'@routes\.(?:get|post|put|delete)\("([^"]+)"', src):
        rx = re.escape(path)
        rx = re.sub(r"\\\{[a-z_]+:\\\.\\\+\\\}", ".+", rx)
        rx = re.sub(r"\\\{[^}]+\\\}", "[^/]+", rx)
        pats.append(re.compile("^" + rx + "$"))
    # static mounts
    for path in re.findall(r'add_static\("([^"]+)"', src):
        pats.append(re.compile("^" + re.escape(path.rstrip("/")) + "/.*$"))
    return pats


def test_no_dlss_leftovers():
    """The fake "DLSS 5" upscaler (no backend, not open weights) was removed for good."""
    for j in JS:
        assert "dlss" not in open(j, encoding="utf-8").read().lower(), j


def test_every_frontend_vfx_url_has_a_route():
    pats = _routes()
    missing = set()
    for js in JS:
        src = open(js, encoding="utf-8").read()
        for m in re.finditer(r'[`"\'](/vfx/[^`"\'\s]*)', src):
            url = m.group(1).split("?")[0]
            url = re.sub(r"\$\{[^}]*\}", "X", url)
            if url.endswith("/") or "X" in url.split("/")[-1] and url.split("/")[-1] != "X":
                continue  # prefix built by concatenation
            if url.endswith("/X") and any(p.pattern.startswith("^" + re.escape(url[:-1])) for p in pats):
                continue  # /prefix/${endpoint}: some route lives under the prefix
            if not any(p.match(url) for p in pats):
                missing.add(f"{os.path.basename(js)}: {url}")
    assert not missing, sorted(missing)


def test_project_chat_history_persistence(tmp_path, monkeypatch):
    pm = load("project_manager")
    monkeypatch.setattr(pm, "PROJECTS_ROOT", str(tmp_path))
    pid = "test_chat_proj"
    pm.create_or_open_project(pid, project_id=pid)

    assert pm.get_project_chat_history(pid) == []

    hist = [{"role": "user", "text": "Hello Keshiki"}, {"role": "assistant", "text": "Ready to direct."}]
    assert pm.save_project_chat_history(pid, hist) is True

    loaded = pm.get_project_chat_history(pid)
    assert len(loaded) == 2
    assert loaded[0]["text"] == "Hello Keshiki"
    assert loaded[1]["text"] == "Ready to direct."

    # Test clear
    assert pm.clear_project_chat_history(pid) is True
    assert pm.get_project_chat_history(pid) == []
