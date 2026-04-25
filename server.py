"""
SANE Unreal Control Agent — MCP Server
Exposes editor tools over JSON-RPC / MCP protocol.
Talks to the Unreal plugin via HTTP (Remote Control API + custom endpoints).
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger("sane-mcp")

PLUGIN_BASE_URL = os.environ.get("UNREAL_PLUGIN_URL", "http://127.0.0.1:8765")
REQUEST_TIMEOUT = 60.0  # seconds — editor ops can be slow

app = Server("sane-unreal-agent")


# ─── helpers ──────────────────────────────────────────────────────────────────

async def call_plugin(endpoint: str, payload: dict) -> dict:
    """POST to the Unreal plugin HTTP server and return parsed JSON."""
    url = f"{PLUGIN_BASE_URL}/{endpoint.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        return _err(f"Cannot reach Unreal plugin at {url}. "
                    "Is the editor running with UnrealControlPlugin enabled?")
    except httpx.HTTPStatusError as e:
        return _err(f"Plugin returned HTTP {e.response.status_code}: {e.response.text}")
    except Exception as e:
        return _err(str(e))


def _ok(action: str, result: Any, logs: list[str] | None = None,
        screenshot_path: str | None = None,
        affected_assets: list[str] | None = None) -> dict:
    return {
        "success": True,
        "action": action,
        "result": result,
        "logs": logs or [],
        "errors": [],
        "screenshot_path": screenshot_path,
        "affected_assets": affected_assets or [],
    }


def _err(msg: str, action: str = "unknown", logs: list[str] | None = None) -> dict:
    return {
        "success": False,
        "action": action,
        "result": None,
        "logs": logs or [],
        "errors": [msg],
        "screenshot_path": None,
        "affected_assets": [],
    }


def _json(d: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(d, indent=2))]


# ─── tool declarations ─────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_editor_state",
            description="Return the current Unreal Editor state: open level, PIE status, dirty flag, viewport info.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="list_assets",
            description="List assets in the Content Browser under a given path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "/Game",
                             "description": "Content Browser path, e.g. /Game/Maps"},
                    "filter": {"type": "string", "default": "",
                               "description": "Optional asset class filter, e.g. StaticMesh"},
                },
                "required": [],
            },
        ),
        Tool(
            name="create_level",
            description="Create and open a new level (map) in the editor.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Level asset name, e.g. MyLevel"},
                    "path": {"type": "string", "default": "/Game/Maps",
                             "description": "Destination content path"},
                    "template": {"type": "string", "default": "Empty",
                                 "description": "Template: Empty | Default | VR-Basic"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="spawn_actor",
            description="Spawn an actor of a given class into the current level.",
            inputSchema={
                "type": "object",
                "properties": {
                    "class_path": {"type": "string",
                                   "description": "Full class path, e.g. /Script/CinematicCamera.CineCameraActor"},
                    "name": {"type": "string", "description": "Actor label in the level"},
                    "location": {
                        "type": "object",
                        "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
                        "default": {"x": 0, "y": 0, "z": 0},
                    },
                    "rotation": {
                        "type": "object",
                        "properties": {"pitch": {"type": "number"}, "yaw": {"type": "number"}, "roll": {"type": "number"}},
                        "default": {"pitch": 0, "yaw": 0, "roll": 0},
                    },
                },
                "required": ["class_path", "name"],
            },
        ),
        Tool(
            name="import_asset",
            description="Import an external file (FBX, PNG, WAV, …) into the Content Browser.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "Absolute path on disk"},
                    "destination_path": {"type": "string", "description": "Content Browser destination"},
                },
                "required": ["source_path", "destination_path"],
            },
        ),
        Tool(
            name="create_level_sequence",
            description="Create a Level Sequence asset at the given content path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Sequence asset name, e.g. Intro_01"},
                    "path": {"type": "string", "default": "/Game/Cinematics"},
                    "duration_seconds": {"type": "number", "default": 5.0},
                    "frame_rate": {"type": "number", "default": 24.0},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="add_cine_camera",
            description="Spawn a CineCameraActor and bind it to a Level Sequence's Camera Cuts track.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sequence_path": {"type": "string",
                                     "description": "Content path of the Level Sequence"},
                    "camera_name": {"type": "string", "default": "CineCamera_01"},
                    "location": {
                        "type": "object",
                        "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
                        "default": {"x": 0, "y": 0, "z": 100},
                    },
                },
                "required": ["sequence_path"],
            },
        ),
        Tool(
            name="animate_camera",
            description="Add transform keyframes to a camera track in a Level Sequence.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sequence_path": {"type": "string"},
                    "camera_name": {"type": "string"},
                    "keyframes": {
                        "type": "array",
                        "description": "List of {time_seconds, location, rotation} dicts",
                        "items": {
                            "type": "object",
                            "properties": {
                                "time_seconds": {"type": "number"},
                                "location": {"type": "object"},
                                "rotation": {"type": "object"},
                            },
                        },
                    },
                },
                "required": ["sequence_path", "camera_name", "keyframes"],
            },
        ),
        Tool(
            name="create_blueprint",
            description="Create a Blueprint class asset from a given parent class.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "path": {"type": "string", "default": "/Game/Blueprints"},
                    "parent_class": {"type": "string", "default": "Actor",
                                     "description": "Unreal class name, e.g. Actor | Pawn | Character"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="run_editor_python",
            description="Execute arbitrary Python inside the Unreal Editor scripting environment.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source code to execute"},
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="get_logs",
            description="Retrieve recent Output Log entries from the editor.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lines": {"type": "integer", "default": 100,
                              "description": "How many recent log lines to return"},
                    "filter": {"type": "string", "default": "",
                               "description": "Optional substring filter"},
                },
                "required": [],
            },
        ),
        Tool(
            name="take_screenshot",
            description="Capture a high-res screenshot of the active editor viewport.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "default": "screenshot",
                                 "description": "Output filename without extension"},
                    "width": {"type": "integer", "default": 1920},
                    "height": {"type": "integer", "default": 1080},
                },
                "required": [],
            },
        ),
    ]


# ─── tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    log.info("Tool called: %s  args=%s", name, arguments)

    result = await call_plugin(f"tool/{name}", {"tool": name, "args": arguments})
    return _json(result)


# ─── entrypoint ────────────────────────────────────────────────────────────────

async def main():
    log.info("SANE Unreal MCP server starting (plugin URL: %s)", PLUGIN_BASE_URL)
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
