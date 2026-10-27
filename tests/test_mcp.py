"""The MCP server speaks the protocol and every tool maps onto a real studio route."""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "mcp", "keshiki_mcp.py")


def _talk(*msgs, env=None):
    inp = "\n".join(json.dumps(m) for m in msgs) + "\n"
    r = subprocess.run([sys.executable, SERVER], input=inp, capture_output=True, text=True, timeout=30,
                       env={**os.environ, "KESHIKI_URL": "http://127.0.0.1:9", **(env or {})})
    return [json.loads(l) for l in r.stdout.splitlines() if l.strip()]


def test_handshake_and_tool_list():
    out = _talk({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert [m["id"] for m in out] == [1, 2], "notifications must not be answered"
    assert out[0]["result"]["serverInfo"]["name"] == "keshiki"
    tools = out[1]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"list_projects", "video_job", "generate_music", "voiceover", "generate_image"} <= names
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


def test_studio_down_is_a_tool_error_not_a_crash():
    out = _talk({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}})
    assert out[0]["result"]["isError"] and "not reachable" in out[0]["result"]["content"][0]["text"]


def test_unknown_method():
    out = _talk({"jsonrpc": "2.0", "id": 5, "method": "nope"})
    assert out[0]["error"]["code"] == -32601


def test_mcp_paths_exist_in_server_api():
    src = open(SERVER, encoding="utf-8").read()
    api = open(os.path.join(ROOT, "vfx_nodes", "server_api.py"), encoding="utf-8").read()
    routes = []
    for p in re.findall(r'@routes\.(?:get|post)\("([^"]+)"', api):
        rx = re.sub(r"\{[a-z_]+:\.\+\}", "\x00", p)            # {subpath:.+} matches slashes
        rx = re.escape(rx).replace("\x00", ".+")
        rx = re.sub(r"\\\{[^}]+\\\}", "[^/]+", rx)
        routes.append(re.compile("^" + rx + "$"))
    used = set(re.findall(r'"(/vfx/[^"]*)"', src)) | set(re.findall(r"f\"(/vfx/[^\"]*)\"", src))
    missing = []
    for u in used:
        url = re.sub(r"\{[^}]*\}", "X", u)
        if not any(r.match(url) for r in routes):
            missing.append(u)
    assert not missing, missing


def test_video_job_kinds_documented_in_tool():
    from conftest import load
    src = open(SERVER, encoding="utf-8").read()
    desc = src[src.index('("video_job"'):src.index('("job_status"')]
    vl = open(os.path.join(ROOT, "vfx_nodes", "video_layers_service.py"), encoding="utf-8").read()
    body = vl[vl.index("def start_job("):vl.index("}.get(kind)")]
    kinds = set(re.findall(r'"([a-z_0-9]+)":\s*_job_', body)) - {"prepare", "detect_all", "mask_grow", "layer_to_video", "canny", "pose"}
    missing = [k for k in kinds if k not in desc]
    assert not missing, f"video_job description lacks: {missing}"
