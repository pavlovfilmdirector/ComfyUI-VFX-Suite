"""
KESHIKI VFX Studio - installer
==============================
Run with the SAME Python that runs your ComfyUI (install.bat finds it for you):

    python install.py                      # node packs + pip packages + tools + model check
    python install.py --download-models core    # also download the core models (~40 GB)
    python install.py --comfy D:/ComfyUI        # if this repo is not inside ComfyUI/custom_nodes

Nothing is deleted. Existing node packs, tools and models are left as they are.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "install_manifest.json")
LOCAL_CFG = os.path.join(HERE, "keshiki_local.json")
IS_WIN = platform.system() == "Windows"


LOG_PATH = os.path.join(HERE, "install_log.txt")


def say(msg=""):
    print(msg, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass


def find_comfy(arg):
    cands = [arg, os.environ.get("KESHIKI_COMFY_DIR", "")]
    # a repo linked into custom_nodes (symlink / junction) runs from its real folder: use the saved ComfyUI path
    try:
        with open(os.path.join(HERE, "keshiki_local.json"), encoding="utf-8") as f:
            cands.append(json.load(f).get("comfy_dir", ""))
    except (OSError, ValueError):
        pass
    parent = os.path.dirname(HERE)
    if os.path.basename(parent).lower() == "custom_nodes":
        cands.append(os.path.dirname(parent))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "main.py")) and os.path.isdir(os.path.join(c, "custom_nodes")):
            return os.path.abspath(c)
    return ""


def run(cmd, cwd=None):
    say("  > " + " ".join(cmd))
    # stream to the console as it happens (pip can take minutes), keep the full text in the log
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    for line in p.stdout:
        line = line.rstrip()
        if line:
            say("    " + line)
    p.wait()
    if p.returncode != 0:
        say(f"    (exit code {p.returncode})")
    return p.returncode


def pip_install_req(req):
    if os.path.isfile(req):
        return run([sys.executable, "-m", "pip", "install", "-r", req]) == 0
    return True


def download(url, dest, label):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "keshiki-installer/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, -1
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = int(done * 100 / total)
                if pct != last and pct % 5 == 0:
                    say(f"    {label}: {pct}% ({done >> 20} / {total >> 20} MB)")
                    last = pct
    os.replace(tmp, dest)


def find_dir_ci(root, name):
    """custom_nodes folders differ in case between installs (ComfyUI-Manager lowercases some)."""
    if not os.path.isdir(root):
        return ""
    for d in os.listdir(root):
        if d.lower().replace("-", "_") == name.lower().replace("-", "_"):
            return os.path.join(root, d)
    return ""


def step_node_packs(comfy, packs):
    say("\n[1/4] ComfyUI node packs")
    cn = os.path.join(comfy, "custom_nodes")
    git = shutil.which("git")
    ok = True
    for p in packs:
        have = find_dir_ci(cn, p["dir"])
        if have:
            say(f"  ok   {os.path.basename(have)}  ({p['for']})")
            continue
        if not git:
            say(f"  MISSING {p['dir']} - install Git (https://git-scm.com) or add it with ComfyUI-Manager: {p['git']}")
            ok = False
            continue
        say(f"  +    {p['dir']}  ({p['for']})")
        if run([git, "clone", "--depth", "1", p["git"], os.path.join(cn, p["dir"])]) != 0:
            ok = False
            continue
        ok &= pip_install_req(os.path.join(cn, p["dir"], "requirements.txt"))
    return ok


def step_pip():
    say("\n[2/4] Python packages for KESHIKI")
    return pip_install_req(os.path.join(HERE, "requirements.txt"))


def step_tools(tools, allow_download):
    say("\n[3/4] Camera tracking tools (COLMAP + GLOMAP)")
    if not IS_WIN:
        say("  Linux/macOS: install COLMAP 3.13 and GLOMAP 1.2 (build from source or your package manager); "
            "set VFX_COLMAP / VFX_GLOMAP if they are not on PATH.")
        return True
    ok = True
    for t in tools:
        dest = os.path.join(HERE, t["dest"])
        exe = None
        for root, _, files in os.walk(dest) if os.path.isdir(dest) else []:
            if t["exe"] in files:
                exe = os.path.join(root, t["exe"])
                break
        found = exe or (shutil.which(t["which"]) if t.get("which") else None)
        if found:
            say(f"  ok   {found}")
            continue
        if not allow_download:
            say(f"  MISSING {t['name']} ({t['size_mb']} MB): {t['url']}  (re-run without --no-tools)")
            ok = False
            continue
        # release assets share file names across versions (colmap-x64-windows-cuda.zip): keep the tag in the name
        parts = t["url"].rstrip("/").split("/")
        zpath = os.path.join(HERE, "tools", "_downloads", f"{parts[-2]}_{parts[-1]}")
        try:
            if not os.path.isfile(zpath):
                say(f"  downloading {t['name']} ({t['size_mb']} MB)")
                download(t["url"], zpath, os.path.basename(zpath))
            tmp = dest + "_unpack"
            with zipfile.ZipFile(zpath) as z:
                z.extractall(tmp)
            entries = os.listdir(tmp)
            # unwrap a single top folder ("COLMAP-3.13-windows-cuda/"), but keep layouts that start with bin/
            single = len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])) and entries[0].lower() != "bin"
            src = os.path.join(tmp, entries[0]) if single else tmp
            shutil.move(src, dest)
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            say(f"  +    {dest}")
        except Exception as e:
            say(f"  FAILED {t['name']}: {e}")
            ok = False
    return ok


def model_path(comfy, m):
    d = m["dir"]
    if d.startswith("@"):
        return os.path.join(comfy, d[1:])
    return os.path.join(comfy, "models", d)


def extra_model_dirs(comfy):
    """{folder_type: [dirs]} from ComfyUI's extra_model_paths.yaml (models kept in another install)."""
    out = {}
    path = os.path.join(comfy, "extra_model_paths.yaml")
    if not os.path.isfile(path):
        return out
    try:
        import yaml
        conf = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except Exception:
        return out
    for section in conf.values():
        if not isinstance(section, dict):
            continue
        base = os.path.expanduser(str(section.get("base_path", "")))
        for key, val in section.items():
            if key in ("base_path", "is_default") or not isinstance(val, str):
                continue
            for sub in val.splitlines():
                sub = sub.strip()
                if sub:
                    out.setdefault(key, []).append(os.path.join(base, sub))
    return out


_EXTRA = None


def have_model(comfy, m):
    global _EXTRA
    folder = model_path(comfy, m)
    if m.get("any_pth"):
        return os.path.isdir(folder) and any(f.endswith(".pth") for f in os.listdir(folder))
    if os.path.isfile(os.path.join(folder, m["file"])):
        return True
    # tolerate models kept in a different sub-folder of the same type, here or in extra_model_paths.yaml
    if _EXTRA is None:
        _EXTRA = extra_model_dirs(comfy)
    kind = m["dir"].split("/")[0]
    tops = [os.path.join(comfy, "models", kind)] + _EXTRA.get(kind, [])
    for top in tops:
        for root, _, files in os.walk(top) if os.path.isdir(top) else []:
            if m["file"] in files:
                return True
    return False


def step_models(comfy, models, download_groups):
    say("\n[4/4] Models")
    say("  (models you keep elsewhere can be linked with ComfyUI's extra_model_paths.yaml)")
    missing = []
    for m in models:
        if m["group"] == "auto":
            say(f"  auto {m['file']}  - {m.get('note', '')}")
            continue
        if not m.get("url") and m["file"].startswith("("):
            say(f"  more {m['file']}: {m['page']}")
            continue
        if have_model(comfy, m):
            say(f"  ok   {m['file']}")
            continue
        missing.append(m)
        size = f"{m['size_gb']} GB" if m.get("size_gb") else ""
        if m.get("url") and (m["group"] in download_groups or "all" in download_groups):
            dest = os.path.join(model_path(comfy, m), m["file"])
            say(f"  downloading {m['file']} {size}")
            try:
                download(m["url"], dest, m["file"])
                missing.pop()
            except Exception as e:
                say(f"  FAILED {m['file']}: {e}")
        else:
            src = m.get("url") or m.get("page")
            where = m["dir"][1:] if m["dir"].startswith("@") else "models/" + m["dir"]
            say(f"  MISSING [{m['group']}] {m['file']} {size}\n           -> ComfyUI/{where}\n           {src}")
    return missing


def main():
    ap = argparse.ArgumentParser(description="KESHIKI VFX Studio installer")
    ap.add_argument("--comfy", default="", help="ComfyUI folder (the one with main.py)")
    ap.add_argument("--no-nodes", action="store_true", help="skip node packs")
    ap.add_argument("--no-pip", action="store_true", help="skip pip packages")
    ap.add_argument("--no-tools", action="store_true", help="do not download COLMAP")
    ap.add_argument("--download-models", default="", help="comma list of groups to download: core,iclora,image,music,all")
    ap.add_argument("--check", action="store_true", help="only report what is installed, change nothing")
    a = ap.parse_args()

    say("KESHIKI VFX Studio - installer")
    say(f"Python: {sys.executable} ({platform.python_version()})")
    comfy = find_comfy(a.comfy)
    if not comfy:
        say("ComfyUI not found. Put this folder into ComfyUI/custom_nodes or pass --comfy <ComfyUI folder>.")
        return 2
    say(f"ComfyUI: {comfy}")
    man = json.load(open(MANIFEST, encoding="utf-8"))

    ok = True
    if a.check:
        a.no_nodes = a.no_pip = a.no_tools = True
        a.download_models = ""
    if not a.no_nodes:
        ok &= step_node_packs(comfy, man["node_packs"])
    else:
        cn = os.path.join(comfy, "custom_nodes")
        for p in man["node_packs"]:
            say(f"  {'ok  ' if find_dir_ci(cn, p['dir']) else 'MISSING'} {p['dir']}")
    if not a.no_pip:
        ok &= step_pip()
    ok &= step_tools(man["tools_windows"], allow_download=not a.no_tools)
    groups = [g.strip() for g in a.download_models.split(",") if g.strip()]
    missing = step_models(comfy, man["models"], groups)

    if not a.check:
        cfg = {}
        if os.path.isfile(LOCAL_CFG):
            try:
                cfg = json.load(open(LOCAL_CFG, encoding="utf-8"))
            except Exception:
                cfg = {}
        cfg.setdefault("comfy_dir", comfy)
        cfg.setdefault("python", sys.executable)
        cfg.setdefault("port", 8188)
        json.dump(cfg, open(LOCAL_CFG, "w", encoding="utf-8"), indent=2)

    say("\n" + ("Done." if ok and not missing else "Done, with items to finish (see MISSING above)."))
    say("Start the studio with run_keshiki_studio.bat (or launch ComfyUI and open http://127.0.0.1:8188/vfx/studio/).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
