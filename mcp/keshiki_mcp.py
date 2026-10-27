"""
KESHIKI MCP server - lets any MCP-capable agent (Claude Desktop / Claude Code, Cursor, Antigravity, ...) drive the studio.

    python mcp/keshiki_mcp.py            # stdio transport, no dependencies (Python 3.9+)

The studio (ComfyUI with KESHIKI) must be running; KESHIKI_URL overrides http://127.0.0.1:8188.
Every tool is a thin wrapper over the studio's own HTTP API (/vfx/...), so whatever an agent does here is the
same thing the UI does. Long jobs return a job id; `job_status` (or wait=true) follows them.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("KESHIKI_URL", "http://127.0.0.1:8188").rstrip("/")
PROTOCOL = "2024-11-05"
VERSION = "0.1.0"


# ---------------------------------------------------------------- HTTP
def _req(method, path, body=None, raw=None, ctype=None, timeout=60):
    data, headers = None, {}
    if raw is not None:
        data, headers["Content-Type"] = raw, ctype
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            txt = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"KESHIKI is not reachable at {BASE} ({e.reason}). Start the studio first.")
    try:
        return json.loads(txt)
    except ValueError:
        return {"text": txt[:2000]}


def _get(path, **kw):
    return _req("GET", path, **kw)


def _post(path, body=None, **kw):
    return _req("POST", path, body=body if body is not None else {}, **kw)


def _q(s):
    return urllib.parse.quote(str(s), safe="")


JOB_PATHS = {"video": "/vfx/video/job/{}", "music": "/vfx/music/job/{}", "voice": "/vfx/voiceboard_job/{}",
             "dubbing": "/vfx/dubbing/job/{}", "tool": "/vfx/tools/job/{}"}


def _wait(family, job_id, timeout=3600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = _get(JOB_PATHS[family].format(_q(job_id)))
        if j.get("status") not in ("running", "queued", "starting"):
            return j
        time.sleep(3)
    return {"id": job_id, "status": "running", "message": f"still running after {timeout} s - poll job_status"}


def _project(pid):
    for p in _get("/vfx/projects/list").get("projects", []):
        if p.get("id") == pid:
            return p
    raise RuntimeError(f"Project '{pid}' not found - see list_projects")


# ---------------------------------------------------------------- tools
def t_studio_status(a):
    return {"ping": _get("/vfx/system/ping"), "vram": _get("/vfx/vram_status"), "music": _get("/vfx/music/status").get("ready"),
            "voice_engines": _get("/vfx/dubbing/status")}


def t_list_projects(a):
    return [{k: p.get(k) for k in ("id", "name", "mode", "path", "updated_at")} for p in _get("/vfx/projects/list").get("projects", [])]


def t_project_overview(a):
    pid = a["project_id"]
    st = _get(f"/vfx/project_file/{_q(pid)}/project.json")
    if "text" in st and len(st) == 1:
        raise RuntimeError(f"Project '{pid}' has no project.json")
    short = lambda items, keys: [{k: i.get(k) for k in keys if k in i} for i in (items or [])]
    return {"id": pid, "name": st.get("name"),
            "images": short(st.get("mediaImages1D"), ("id", "title", "url")),
            "videos": short(st.get("mediaVideos2D"), ("id", "title", "videoUrl", "fps")),
            "audio": short(st.get("mediaAudio"), ("id", "title", "duration")),
            "texts": short(st.get("mediaTexts"), ("id", "title"))}


def t_video_state(a):
    s = _get(f"/vfx/video/{_q(a['project_id'])}/{_q(a['video_id'])}/state")
    plate = s.get("plate") or {}
    return {"frames": plate.get("total_frames"), "width": plate.get("width"), "height": plate.get("height"), "fps": plate.get("fps"),
            "layers": [{k: l.get(k) for k in ("id", "name", "type", "frames", "source", "lora")} for l in s.get("layers", [])]}


def t_import_media(a):
    pid, path = a["project_id"], os.path.expanduser(a["file_path"])
    if not os.path.isfile(path):
        raise RuntimeError(f"No such file: {path}")
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        payload = f.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{os.path.basename(path)}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
    up = _req("POST", f"/vfx/projects/{_q(pid)}/upload_media", raw=body, ctype=f"multipart/form-data; boundary={boundary}", timeout=600)
    files = up.get("uploaded") or []
    if not files:
        raise RuntimeError(f"Upload failed: {up}")
    item = files[0]
    out = {"uploaded": item}
    if item["sub"] == "source_video":
        vid = f"vid_mcp_{int(time.time() * 1000)}"
        job = _post(f"/vfx/video/{_q(pid)}/{vid}/job", {"kind": "prepare", "params": {"url": item["url"]}})
        out.update({"video_id": vid, "prepare": _wait("video", job["id"]) if job.get("id") else job,
                    "note": "The video is prepared for jobs. To see it in the media strip, add it in the UI (or reload the project)."})
    return out


def t_list_ic_loras(a):
    return [{k: l.get(k) for k in ("file", "family", "key", "title_en", "desc_ru", "output", "needs")} for l in _get("/vfx/video/ic_loras").get("loras", [])]


def t_video_job(a):
    j = _post(f"/vfx/video/{_q(a['project_id'])}/{_q(a['video_id'])}/job", {"kind": a["kind"], "params": a.get("params") or {}})
    if j.get("error") or not a.get("wait"):
        return j
    return _wait("video", j["id"])


def t_job_status(a):
    return _get(JOB_PATHS[a.get("family", "video")].format(_q(a["job_id"])))


def t_cancel_job(a):
    fam = a.get("family", "video")
    path = {"video": "/vfx/video/job/{}/cancel", "music": "/vfx/music/job/{}/cancel", "voice": "/vfx/voiceboard_job/{}/cancel",
            "dubbing": "/vfx/dubbing/job/{}/cancel", "tool": "/vfx/tools/job/{}/cancel"}[fam]
    return _post(path.format(_q(a["job_id"])))


def t_generate_image(a):
    params = {"mode": "t2i", "prompt": a["prompt"], "aspect": a.get("aspect", "16:9")}
    if a.get("seed"):
        params["seed"] = a["seed"]
    if a.get("source_path"):
        import base64
        with open(os.path.expanduser(a["source_path"]), "rb") as f:
            params.update({"mode": "edit", "source": "data:image/png;base64," + base64.b64encode(f.read()).decode()})
    res = _post("/vfx/image/generate", params, timeout=1200)
    if res.get("error"):
        return res
    import base64
    proj = _project(a.get("project_id") or "sandbox_playground")
    folder = os.path.join(proj["path"], "storyboard")
    os.makedirs(folder, exist_ok=True)
    name = f"mcp_{time.strftime('%Y%m%d_%H%M%S')}_{res.get('seed')}.png"
    with open(os.path.join(folder, name), "wb") as f:
        f.write(base64.b64decode(res["image"].split(",", 1)[1]))
    return {"file": os.path.join(folder, name), "url": f"/vfx/project_file/{proj['id']}/storyboard/{name}",
            "seed": res.get("seed"), "seconds": res.get("seconds"), "prompt_used": res.get("prompt_used")}


def t_generate_music(a):
    body = {k: v for k, v in a.items() if k not in ("wait",)}
    body.setdefault("project_id", "sandbox_playground")
    j = _post("/vfx/music/generate", body)
    if j.get("error") or not a.get("wait", True):
        return j
    return _wait("music", j["id"])


def t_voiceover(a):
    pid = a["project_id"]
    doc = {k: a[k] for k in ("text", "ref_audio", "ref_start", "ref_end", "ref_text", "language", "speed", "pause") if k in a}
    if doc:
        _post(f"/vfx/narration/{_q(pid)}", doc)
    if not a.get("generate", True):
        return _get(f"/vfx/narration/{_q(pid)}")
    j = _post(f"/vfx/narration/{_q(pid)}/generate", {"only": a.get("only") or []})
    if j.get("error") or not a.get("wait", True):
        return j
    return _wait("voice", j["id"])


def t_ask_studio_agent(a):
    """The built-in studio agent: answers how-to questions from the user guide and plans studio actions."""
    res = _post("/vfx/agent/chat", {"message": a["message"], "history": [], "project_id": a.get("project_id", "")}, timeout=600)
    return {"answer": res.get("response_ru") or res.get("response") or res.get("message"),
            "suggested_actions": res.get("studio_actions") or [], "decision": res.get("decision")} if isinstance(res, dict) else res


def t_list_user_tools(a):
    return [{"id": t["id"], "name": t.get("name"), "description": t.get("description"), "tab": t.get("tab"),
             "inputs": [{"id": i["id"], "label": i["label"], "kind": i["kind"]} for i in t.get("inputs", [])],
             "params": [{"id": p["id"], "label": p["label"], "type": p.get("type"), "default": p.get("default")} for p in t.get("params", [])]}
            for t in _get("/vfx/tools").get("tools", [])]


def t_add_user_tool(a):
    path = os.path.expanduser(a["workflow_path"])
    with open(path, encoding="utf-8") as f:
        wf = json.load(f)
    return _post("/vfx/tools/import", {"workflow": wf, "name": a.get("name") or os.path.splitext(os.path.basename(path))[0],
                                       "description": a.get("description", ""), "tab": a.get("tab", "any")})


def t_run_user_tool(a):
    j = _post(f"/vfx/tools/{_q(a['tool_id'])}/run", {"project_id": a.get("project_id") or "sandbox_playground",
                                                    "values": a.get("values") or {}})
    if j.get("error") or not a.get("wait", True):
        return j
    return _wait("tool", j["id"])


def t_run_template(a):
    """Run any API workflow from workflows/api/<process>/<template>.json with '<node title>.<input>' params."""
    return _post("/vfx/process/run", {"process_id": a["process"], "template": a["template"], "params": a.get("params") or {}})


S, O, A, B, N, I = "string", "object", "array", "boolean", "number", "integer"


def _schema(props, required=()):
    return {"type": O, "properties": props, "required": list(required)}


PID = {"type": S, "description": "Project id (list_projects)"}
VID = {"type": S, "description": "Video id (project_overview -> videos)"}
FAM = {"type": S, "enum": list(JOB_PATHS), "description": "Job family", "default": "video"}

TOOLS = [
    ("studio_status", "Is the studio up: VRAM, music model, voice engines.", _schema({}), t_studio_status),
    ("list_projects", "All studio projects (id, name, folder).", _schema({}), t_list_projects),
    ("project_overview", "Images, videos, audio and texts of a project.", _schema({"project_id": PID}, ["project_id"]), t_project_overview),
    ("video_state", "A video's size, frame count and its layers (masks, depth, generated passes...).",
     _schema({"project_id": PID, "video_id": VID}, ["project_id", "video_id"]), t_video_state),
    ("import_media", "Copy a local video/image/audio file into a project; videos are prepared for jobs and get a video_id.",
     _schema({"project_id": PID, "file_path": {"type": S}}, ["project_id", "file_path"]), t_import_media),
    ("list_ic_loras", "LTX IC-LoRAs installed (clean plate, alpha matte, restore, day-to-night, upscale...): pass `file` as params.lora to video_job kind ltx_ic_lora.",
     _schema({}), t_list_ic_loras),
    ("video_job", "Run a Video-tab process on a video. kinds: depth (params.engine v2|da3|depth_pro, exclude_mask), "
                  "normal (params.convention directx|opengl), canny, pose, "
                  "text_mask (params.text), track_object, corridor_key, ltx_ic_lora (params.lora, prompt, start, end, ref_image), "
                  "ltx_alpha, relight_frame (params.frame, prompt or light_layer), first_frame, fill, render, montage (params.videos=[{url}], overlap), "
                  "track_camera, undistort, composite. Each result is a new layer.",
     _schema({"project_id": PID, "video_id": VID, "kind": {"type": S}, "params": {"type": O}, "wait": {"type": B, "default": False}},
             ["project_id", "video_id", "kind"]), t_video_job),
    ("job_status", "Progress / result of a job.", _schema({"job_id": {"type": S}, "family": FAM}, ["job_id"]), t_job_status),
    ("cancel_job", "Stop a job.", _schema({"job_id": {"type": S}, "family": FAM}, ["job_id"]), t_cancel_job),
    ("generate_image", "FLUX image: text-to-image, or an edit of source_path. Saved into the project's storyboard folder.",
     _schema({"prompt": {"type": S}, "project_id": PID, "aspect": {"type": S, "enum": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]},
              "source_path": {"type": S}, "seed": {"type": I}}, ["prompt"]), t_generate_image),
    ("generate_music", "ACE-Step 1.5 song or score: style tags + lyrics ([verse]/[chorus], or [instrumental]); saved to the project's music folder.",
     _schema({"project_id": PID, "tags": {"type": S}, "lyrics": {"type": S}, "duration": {"type": N}, "bpm": {"type": I},
              "keyscale": {"type": S}, "language": {"type": S}, "variants": {"type": I}, "title": {"type": S}, "wait": {"type": B}}, []), t_generate_music),
    ("voiceover", "Voice-over: set the text (paragraphs split by empty lines) and voice sample, then generate; unchanged paragraphs come from cache.",
     _schema({"project_id": PID, "text": {"type": S}, "ref_audio": {"type": S, "description": "project url of a voice sample"},
              "ref_start": {"type": N}, "ref_end": {"type": N}, "ref_text": {"type": S}, "language": {"type": S},
              "only": {"type": A, "items": {"type": I}}, "generate": {"type": B}, "wait": {"type": B}}, ["project_id"]), t_voiceover),
    ("ask_studio_agent", "Ask KESHIKI's own agent how to do something in the studio (answers from the user guide).",
     _schema({"message": {"type": S}, "project_id": PID}, ["message"]), t_ask_studio_agent),
    ("list_user_tools", "The user's own ComfyUI workflows added to the studio ('My tools'): inputs (project media URLs) and params.",
     _schema({}), t_list_user_tools),
    ("add_user_tool", "Add a ComfyUI workflow exported with 'Save (API)' as a studio tool. Loaders titled 'IN: <name>' become inputs, "
                      "nodes titled 'PARAM: <name>' become settings; describe what it does in `description`.",
     _schema({"workflow_path": {"type": S}, "name": {"type": S}, "description": {"type": S},
              "tab": {"type": S, "enum": ["image", "video", "voice", "music", "any"]}}, ["workflow_path"]), t_add_user_tool),
    ("run_user_tool", "Run one of the user's tools: values = {input id: project media URL, param id: value}. Results land in the project.",
     _schema({"tool_id": {"type": S}, "project_id": PID, "values": {"type": O}, "wait": {"type": B}}, ["tool_id"]), t_run_user_tool),
    ("run_template", "Advanced: run a raw API workflow template with '<node title>.<input>' params.",
     _schema({"process": {"type": S}, "template": {"type": S}, "params": {"type": O}}, ["process", "template"]), t_run_template),
]
BY_NAME = {t[0]: t for t in TOOLS}


# ---------------------------------------------------------------- MCP stdio (JSON-RPC 2.0, one message per line)
def _send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {}},
                "serverInfo": {"name": "keshiki", "version": VERSION},
                "instructions": "KESHIKI VFX Studio (local, open-weights). Start with list_projects / project_overview; "
                                "long jobs return ids - use job_status. Ask ask_studio_agent when unsure how a feature works."}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in TOOLS]}
    if method == "tools/call":
        name = params.get("name")
        if name not in BY_NAME:
            raise KeyError(f"Unknown tool {name}")
        try:
            out = BY_NAME[name][3](params.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}]}
        except Exception as e:  # noqa: BLE001 - tool errors are results, not protocol errors
            return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is None:      # notifications (initialized, cancelled...)
        return None
    raise KeyError(f"Method not found: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            res = handle(msg)
            if msg.get("id") is not None:
                _send({"jsonrpc": "2.0", "id": msg["id"], "result": res})
        except KeyError as e:
            if msg.get("id") is not None:
                _send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(e)}})
        except Exception as e:  # noqa: BLE001
            if msg.get("id") is not None:
                _send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
