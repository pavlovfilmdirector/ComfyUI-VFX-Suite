# KESHIKI for AI agents (MCP)

KESHIKI ships an [MCP](https://modelcontextprotocol.io) server, so any MCP-capable agent - Claude Desktop, Claude Code,
Cursor, Antigravity and others - can run the studio from a plain task ("cut the dancer out of shot 3 and make it night").
The agent calls the same `/vfx/...` HTTP API the studio UI uses; everything runs locally on your GPU.

## Setup

1. Start KESHIKI as usual (ComfyUI with the studio at `http://127.0.0.1:8188`).
2. Add the server to your agent. It needs only Python 3.9+ (no packages):

```json
{
  "mcpServers": {
    "keshiki": {
      "command": "python",
      "args": ["B:/ComfyUI-VFX-Suite/mcp/keshiki_mcp.py"],
      "env": { "KESHIKI_URL": "http://127.0.0.1:8188" }
    }
  }
}
```

- Claude Desktop: Settings → Developer → Edit Config (`claude_desktop_config.json`).
- Claude Code: `claude mcp add keshiki -- python B:/ComfyUI-VFX-Suite/mcp/keshiki_mcp.py`
- Cursor: `.cursor/mcp.json` (same JSON). Other agents: their MCP settings, same command.

Use the path where the repo lives on your machine.

## Tools

| Tool | What it does |
|---|---|
| `studio_status` | studio up?, VRAM, music model, voice engines |
| `list_projects`, `project_overview` | projects; images / videos / audio / texts of one project |
| `video_state` | a video's size, frames and layers |
| `import_media` | copy a local file into a project (videos are prepared for jobs) |
| `list_ic_loras` | installed LTX IC-LoRAs |
| `video_job` | any Video-tab process: depth (Depth Anything V2 / DA3 / Depth Pro), canny, pose, text mask (SAM3), object tracking, CorridorKey, LTX IC-LoRA, LTX alpha, relight, first frame, fill, montage, camera tracking... |
| `job_status`, `cancel_job` | follow / stop long jobs |
| `generate_image` | FLUX text-to-image or edit; saved into the project |
| `generate_music` | ACE-Step 1.5 song / score |
| `voiceover` | long voice-over with a cloned voice (F5-TTS), paragraph cache |
| `ask_studio_agent` | KESHIKI's own agent: how-to answers from the user guide |
| `run_template` | advanced: any API workflow in `workflows/api` |

## Security

The studio listens on `127.0.0.1` only, so only programs on your computer can drive it. If you start ComfyUI with
`--listen 0.0.0.0`, anyone on your network can - don't do that on shared networks.
