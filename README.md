# SANE Unreal Control Agent

> **S**criptable **A**utomation with **N**o desktop **E**mulation  
> Agent → MCP Server → Unreal Plugin → Unreal Editor

Zero mouse/keyboard automation. Every operation goes through structured JSON tool calls.

---

## Repo Structure

```
sane-unreal-agent/
├── mcp-server/
│   ├── pyproject.toml          # Python package (mcp, httpx)
│   └── src/
│       └── server.py           # MCP server — 12 tools over stdio
│
├── unreal-plugin/
│   ├── UnrealControlPlugin.uplugin
│   ├── Content/
│   │   └── plugin_server.py    # Python HTTP bridge (runs INSIDE editor)
│   └── Source/UnrealControlPlugin/
│       ├── UnrealControlPlugin.Build.cs
│       ├── Public/UnrealControlPlugin.h
│       └── Private/UnrealControlPlugin.cpp
│
├── agent/
│   └── milestone_01.py         # End-to-end Milestone 1 runner
│
├── claude_desktop_config.json  # Drop into Claude Desktop config
└── README.md
```

---

## Architecture

```
┌─────────────────────────────┐
│  Claude Desktop / Agent     │  ← you talk here
└────────────┬────────────────┘
             │  MCP stdio (JSON-RPC)
┌────────────▼────────────────┐
│  mcp-server/src/server.py   │  ← 12 tool declarations + httpx relay
└────────────┬────────────────┘
             │  HTTP POST 127.0.0.1:8765/tool/<name>
┌────────────▼────────────────┐
│  plugin_server.py           │  ← Python running INSIDE the editor
│  (loaded by C++ module)     │    Direct access to unreal.* API
└────────────┬────────────────┘
             │  unreal.* Python API
┌────────────▼────────────────┐
│  Unreal Editor 5.3+         │
└─────────────────────────────┘
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Unreal Engine | 5.3 or 5.4 |
| Python (host) | 3.11+ |
| uv (optional, recommended) | latest |
| Claude Desktop | latest |

Unreal Editor must have these plugins enabled (handled by `.uplugin`):
- **PythonScriptPlugin** — mandatory
- **EditorScriptingUtilities**
- **SequencerScripting**
- **CinematicCamera**
- **RemoteControl**

---

## Install Steps

### 1 — Copy the plugin into your Unreal project

```bash
# From the repo root:
cp -r unreal-plugin/ /path/to/YourProject/Plugins/UnrealControlPlugin
```

Then in your `.uproject` file add (or let the editor prompt you):

```json
{
  "Name": "UnrealControlPlugin",
  "Enabled": true
}
```

### 2 — Regenerate project files and build

```bash
# Windows (adjust UE install path)
"C:\Program Files\Epic Games\UE_5.3\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe" \
  YourProject Win64 Development "YourProject.uproject" -projectfiles -game -rocket -progress

# Mac / Linux
/path/to/UE_5.3/Engine/Build/BatchFiles/GenerateProjectFiles.sh \
  -project="/path/to/YourProject/YourProject.uproject" -game
```

Or simply right-click the `.uproject` → **Generate Visual Studio project files**.

Build the plugin (Development Editor target) — or let the editor build it on first launch.

### 3 — Install the MCP server

```bash
cd mcp-server
pip install -e .
# or with uv:
uv sync
```

### 4 — Configure Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "sane-unreal": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/absolute/path/to/sane-unreal-agent/mcp-server",
      "env": {
        "UNREAL_BRIDGE_URL": "https://your-public-tunnel-url",
        "UNREAL_PLUGIN_URL": "https://your-public-tunnel-url"
      }
    }
  }
}
```

For Railway deployments, set this to your tunnel URL (Cloudflare Tunnel/ngrok), not localhost.

### 5 — Launch the editor

Open your project in Unreal Editor. You should see in the Output Log:

```
LogTemp: UnrealControlPlugin: HTTP bridge listening on 127.0.0.1:8765
```

---

## Test Commands

### Health check (curl)

```bash
curl http://127.0.0.1:8765/health
# → {"status": "ok", "plugin": "UnrealControlPlugin"}
```

### Milestone 1 — end-to-end runner

```bash
cd sane-unreal-agent
python agent/milestone_01.py --url http://127.0.0.1:8765
```

Expected output:

```
════════════════════════════════════════════════════════════
  SANE Unreal Control Agent — Milestone 1
  Target: http://127.0.0.1:8765
════════════════════════════════════════════════════════════

✓  Plugin reachable

✓  get_editor_state
   → {"world": "Untitled", ...}
✓  create_level_sequence → /Game/Cinematics/Intro_01
✓  add_cine_camera → CineCamera_01 bound to Camera Cuts
✓  animate_camera → 2 keyframes (0 s → 5 s, forward move)
✓  run_editor_python → save_dirty_packages
✓  take_screenshot → intro_01_result.png
✓  get_logs (CineCamera filter)

════════════════════════════════════════════════════════════
  MILESTONE 1 COMPLETE
  Screenshot: /path/to/Project/Saved/Screenshots/intro_01_result.png
════════════════════════════════════════════════════════════
```

### Via Claude Desktop (natural language)

Once MCP is configured and the editor is running:

```
Create a Level Sequence called Intro_01 with a CineCameraActor on 
Camera Cuts, animate a 5-second forward dolly, save, and screenshot it.
```

---

## Railway Remote Bridge Setup (Tunnel Required)

When this server runs on Railway, `127.0.0.1` points to the Railway container itself — **not** your PC.  
That means `UNREAL_BRIDGE_URL=http://127.0.0.1:8765` cannot reach your local Unreal Editor.

Set `UNREAL_BRIDGE_URL` to a **public HTTPS tunnel URL** that forwards to your local bridge (`http://127.0.0.1:8765` on your PC).

### Option A: Cloudflare Tunnel (recommended)

On your PC (where Unreal Editor is running):

```bash
# Install cloudflared, then run:
cloudflared tunnel --url http://127.0.0.1:8765
```

Cloudflare prints a URL like:

```
https://random-name.trycloudflare.com
```

Use that URL in Railway:

```bash
UNREAL_BRIDGE_URL=https://random-name.trycloudflare.com
```

### Option B: ngrok

On your PC:

```bash
ngrok http 8765
```

ngrok prints a forwarding URL like:

```
https://abc123.ngrok-free.app
```

Use that URL in Railway:

```bash
UNREAL_BRIDGE_URL=https://abc123.ngrok-free.app
```

### Required env var behavior

- `UNREAL_BRIDGE_URL` **must** be the public tunnel URL to your PC-hosted Unreal bridge.
- `UNREAL_BRIDGE_URL=http://127.0.0.1:8765` only works when the agent runs on the same machine as Unreal Editor.

### `/status` diagnostics

`GET /status` now reports:

- configured `unreal_bridge_url`
- `bridge_reachable` boolean
- `last_bridge_error` string (if any)

If Railway is configured with localhost bridge URL, tool calls return:

```
UNREAL_BRIDGE_URL points to container localhost, not local Unreal.
```

---

## Failure Diagnostics

### ✗ `Cannot reach plugin at http://127.0.0.1:8765`

1. Confirm Unreal Editor is open with your project.
2. Check Output Log for `UnrealControlPlugin:` lines.
3. If missing: Editor → Edit → Plugins → search **UnrealControlPlugin** → enable → restart.
4. If plugin is enabled but no log line:
   - Confirm `PythonScriptPlugin` is also enabled.
   - Check `Saved/Logs/YourProject.log` for Python errors.

### ✗ `Plugin returned HTTP 500` on a sequencer tool

- Python `unreal.SequencerScripting` module may not be available if the
  **SequencerScripting** plugin is disabled. Enable it and restart.
- The `errors` field in the JSON response always includes the Python traceback.

### ✗ `Class not found: /Script/CinematicCamera.CineCameraActor`

- **CinematicCamera** plugin is disabled. Enable via Edit → Plugins.

### ✗ MCP server not appearing in Claude Desktop

- Verify the `cwd` in `claude_desktop_config.json` is an **absolute** path.
- Run `python -m src.server` manually from `mcp-server/` to see startup errors.
- Ensure `mcp>=1.0.0` is installed in the same Python that Claude Desktop invokes.

### ✗ Screenshot path not found after `take_screenshot`

- UE's `AutomationLibrary.take_high_res_screenshot` is async; the file may appear
  ~1 second after the tool returns. Add a `time.sleep(2)` before reading the file,
  or poll until it exists.
- Default output: `<Project>/Saved/Screenshots/WindowsEditor/`

### ✗ `save_dirty_packages` hangs

- A modal dialog may be blocking the editor thread. Run the save from the
  `run_editor_python` tool with `unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)`.
  The first `True` skips prompts; the second saves map packages.

---

## Extending

### Add a new tool

1. Add a `Tool(...)` entry in `mcp-server/src/server.py → list_tools()`.
2. Add `tool_<name>(args) -> dict` in `unreal-plugin/Content/plugin_server.py`.
3. Register it in the `TOOLS` dispatch dict.
4. No C++ changes needed unless you require engine APIs not exposed to Python.

### C++ escape hatch

For operations unavailable in Python (e.g. custom render passes, low-level RHI):
add a static `UBlueprintFunctionLibrary` in the plugin, expose it to Python via
`UFUNCTION(BlueprintCallable, Category="UnrealControlPlugin")`, then call it from
`plugin_server.py` via `unreal.UnrealControlHelpers.your_function(...)`.

---

## Tool Reference

| Tool | Key Args | Returns |
|---|---|---|
| `get_editor_state` | — | world, PIE status, dirty flag |
| `list_assets` | path, filter | asset path list |
| `create_level` | name, path, template | level path |
| `spawn_actor` | class_path, name, location, rotation | actor label |
| `import_asset` | source_path, destination_path | imported paths |
| `create_level_sequence` | name, path, duration_seconds, frame_rate | sequence path |
| `add_cine_camera` | sequence_path, camera_name, location | camera + binding |
| `animate_camera` | sequence_path, camera_name, keyframes | keyframe count |
| `create_blueprint` | name, path, parent_class | blueprint path |
| `run_editor_python` | code | RESULT variable value |
| `get_logs` | lines, filter | log line array |
| `take_screenshot` | filename, width, height | screenshot_path |

Every response envelope:

```json
{
  "success": true,
  "action": "tool_name",
  "result": { "...": "..." },
  "logs": ["..."],
  "errors": [],
  "screenshot_path": null,
  "affected_assets": ["/Game/Cinematics/Intro_01"]
}
```

---


## Railway Deployment (MCP/Bridge Server Only)

This deploy target is **only** the SANE server (`server.py`) for remote MCP/tool testing.
Do **not** deploy Unreal Engine or the Unreal plugin to Railway.

### Start Command

```bash
uvicorn server:api --host 0.0.0.0 --port $PORT
```

A `Procfile` is included with this exact command.

### Required Environment Variables

- `PORT` (provided by Railway automatically)
- `UNREAL_BRIDGE_URL` (URL for your Unreal plugin bridge, e.g. `https://<your-tunnel>/`)

### Optional Environment Variables

- `UNREAL_AUTH_TOKEN` (token forwarded to the Unreal bridge as `Authorization: Bearer ...` and `X-Unreal-Auth-Token`)
- `REQUEST_TIMEOUT` (defaults to `60`)
- `LOG_LEVEL` (defaults to `INFO`)
- `SERVER_MODE` (`http` default; set `stdio` only for local desktop MCP)

### Safe Diagnostics Endpoints

- `GET /health` → liveness check for Railway
- `GET /status` → server config + Unreal bridge reachability (token never returned)
- `GET /tools` → supported tool list

### MCP/Tool Endpoints

- `GET /mcp/tools`
- `POST /mcp/call_tool`
- `POST /tool/{name}`

### Post-Deploy Test URLs

Replace `<app>` with your Railway domain:

- `https://<app>.up.railway.app/health`
- `https://<app>.up.railway.app/status`
- `https://<app>.up.railway.app/tools`
- `https://<app>.up.railway.app/mcp/tools`

Example tool call:

```bash
curl -X POST "https://<app>.up.railway.app/tool/get_editor_state" \
  -H "content-type: application/json" \
  -d '{"args": {}}'
```
