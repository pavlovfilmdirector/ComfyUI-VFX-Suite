# Rules for AI coding agents working on KESHIKI

Read this before changing anything. The deadline is close: small, safe fixes only.

## Where things are
- UI: `web/standalone/studio_tabs_controller.js` (one big file, ~25k lines; search, don't rewrite), `web/standalone/standalone_studio.js` (3D + global keys).
- Server: `vfx_nodes/server_api.py` (routes) + `vfx_nodes/*_service.py` (logic). ComfyUI workflows: `workflows/api/<process>/*.json` (API format).
- Agent: `core_agent/studio_agent.py`, its knowledge `core_agent/knowledge_studio/*.md`. MCP server: `mcp/keshiki_mcp.py`.
- Docs: `README.md`, `INSTALL.md`, `docs/USER_GUIDE.md` + `docs/USER_GUIDE_RU.md` (keep both in sync).

## Must
- Run the tests before and after every change: `python -m pytest tests` (no GPU needed, ~5 s). All must pass. Check JS: `node --check web/standalone/studio_tabs_controller.js` (copy to a `.mjs` file first if it complains about `import`).
- Every UI string goes through `uiText("English", "Русский")`. No Russian text may show in the English UI.
- Keep edits minimal and local. Fix the bug you were asked about; do not refactor, rename, reformat or "clean up" other code.
- New ComfyUI workflow files: API format, node titles used as `<Title>.<input>` params; the tests check them against `tests/fixtures/object_info.json.gz`.
- New `/vfx/...` URL used by the UI needs a route in `server_api.py` (a test checks this). New agent actions need a UI handler for their `act.type` and a row in `core_agent/knowledge_studio/studio.md` (tests check both).
- Server code changes: apply with the "Hot-Reload Backend API" button (POST `/vfx/system/reload`); new routes and new node classes need a ComfyUI restart. UI changes: F5.
- Commit small, one topic per commit, with a clear message.

## Never
- Never add cloud APIs, partner nodes or closed models: the project is "open weights only" (contest category).
- Never delete or rewrite user projects (`projects/`), models, `keshiki_local.json`, `user_tools/`, or files under `tests/fixtures/` (regenerate only with `tools/refresh_object_info.py`).
- Never change `install_manifest.json` URLs, model file names in workflows, or LoRA paths without checking the file exists in `ComfyUI/models`.
- Never remove features, buttons or routes to "simplify". Never rewrite whole functions or files.
- Never run `git push --force`, rewrite history, or touch the `release` branch; the owner publishes.
- Never download models or install packages without asking the owner (name, source, size).
- Never put secrets, tokens or personal paths into the repo.

## Known fragile spots
- 1D (Image) layers: the server copy (`projects/<id>/layers/<image>/`) is the source of truth; `hydrateLayers1D` restores it on project open. Undo: `push1DUndoSnapshot` / `perform1DUndo` / `perform1DRedo`; a click that creates a layer must push `{createdLayerId}` so undo removes layer and pixels together.
- VRAM (24 GB): big models (Qwen Edit 40 GB, LTX 22B, DubIt LTX-2.3) are freed between prompts with `process_runner.free_memory()`; do not call `comfy.model_management.unload_all_models()` while a prompt may be running.
- ACE-Step music: `vfx_nodes/loader_nodes.py` guards the LM against NaN (CUDA abort); keep it.
