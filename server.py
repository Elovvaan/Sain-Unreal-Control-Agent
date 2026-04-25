"""
SANE Unreal Control Agent — Railway-ready MCP/Bridge Server.

This process exposes HTTP endpoints for remote testing and proxies tool calls
to the Unreal Editor bridge plugin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("sane-unreal-agent")


def _get_float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        log.warning("Invalid %s value %r; falling back to default %s", name, value, default)
        return default


def _get_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        log.warning("Invalid %s value %r; falling back to default %s", name, value, default)
        return default


UNREAL_BRIDGE_URL = os.environ.get("UNREAL_BRIDGE_URL") or os.environ.get("UNREAL_PLUGIN_URL") or "http://127.0.0.1:8765"
UNREAL_AUTH_TOKEN = os.environ.get("UNREAL_AUTH_TOKEN", "").strip()
REQUEST_TIMEOUT = _get_float_env("REQUEST_TIMEOUT", 60.0)
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = _get_int_env("PORT", 8000)

mcp_app = Server("sane-unreal-agent")
api = FastAPI(title="SANE Unreal Agent Server", version="1.0.0")


TOOL_DEFINITIONS: list[Tool] = [
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
                "path": {"type": "string", "default": "/Game", "description": "Content Browser path, e.g. /Game/Maps"},
                "filter": {"type": "string", "default": "", "description": "Optional asset class filter, e.g. StaticMesh"},
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
                "path": {"type": "string", "default": "/Game/Maps", "description": "Destination content path"},
                "template": {"type": "string", "default": "Empty", "description": "Template: Empty | Default | VR-Basic"},
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
                "class_path": {
                    "type": "string",
                    "description": "Full class path, e.g. /Script/CinematicCamera.CineCameraActor",
                },
                "name": {"type": "string", "description": "Actor label in the level"},
                "location": {
                    "type": "object",
                    "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
                    "default": {"x": 0, "y": 0, "z": 0},
                },
                "rotation": {
                    "type": "object",
                    "properties": {
                        "pitch": {"type": "number"},
                        "yaw": {"type": "number"},
                        "roll": {"type": "number"},
                    },
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
                "sequence_path": {"type": "string", "description": "Content path of the Level Sequence"},
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
                "parent_class": {
                    "type": "string",
                    "default": "Actor",
                    "description": "Unreal class name, e.g. Actor | Pawn | Character",
                },
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
                "lines": {"type": "integer", "default": 100, "description": "How many recent log lines to return"},
                "filter": {"type": "string", "default": "", "description": "Optional substring filter"},
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
                "filename": {"type": "string", "default": "screenshot", "description": "Output filename without extension"},
                "width": {"type": "integer", "default": 1920},
                "height": {"type": "integer", "default": 1080},
            },
            "required": [],
        },
    ),
]
TOOL_NAMES = {tool.name for tool in TOOL_DEFINITIONS}
LAST_BRIDGE_ERROR: str | None = None


def _bridge_host(url: str) -> str:
    try:
        normalized_url = url.strip()
        if not normalized_url:
            return ""
        if "://" not in normalized_url:
            normalized_url = f"http://{normalized_url}"
        return (urlparse(normalized_url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _is_loopback_bridge(url: str) -> bool:
    return _bridge_host(url) in {"127.0.0.1", "localhost", "::1"}


def _running_in_railway() -> bool:
    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))


def _localhost_bridge_misconfigured() -> bool:
    return _running_in_railway() and _is_loopback_bridge(UNREAL_BRIDGE_URL)


def _localhost_bridge_message() -> str:
    return "UNREAL_BRIDGE_URL points to container localhost, not local Unreal."


def _headers() -> dict[str, str]:
    if not UNREAL_AUTH_TOKEN:
        return {}
    return {
        "Authorization": f"Bearer {UNREAL_AUTH_TOKEN}",
        "X-Unreal-Auth-Token": UNREAL_AUTH_TOKEN,
    }


async def call_plugin(endpoint: str, payload: dict) -> dict:
    """POST to the Unreal plugin HTTP server and return parsed JSON."""
    global LAST_BRIDGE_ERROR
    if _localhost_bridge_misconfigured():
        LAST_BRIDGE_ERROR = _localhost_bridge_message()
        return _err(LAST_BRIDGE_ERROR)

    url = f"{UNREAL_BRIDGE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(url, json=payload, headers=_headers())
            response.raise_for_status()
            LAST_BRIDGE_ERROR = None
            return response.json()
    except httpx.ConnectError:
        LAST_BRIDGE_ERROR = (
            f"Cannot reach Unreal bridge at {url}. "
            "Set UNREAL_BRIDGE_URL to your editor/plugin bridge URL."
        )
        return _err(LAST_BRIDGE_ERROR)
    except httpx.HTTPStatusError as exc:
        LAST_BRIDGE_ERROR = f"Bridge returned HTTP {exc.response.status_code}: {exc.response.text}"
        return _err(LAST_BRIDGE_ERROR)
    except Exception as exc:  # noqa: BLE001
        LAST_BRIDGE_ERROR = str(exc)
        return _err(LAST_BRIDGE_ERROR)


async def bridge_health() -> dict[str, Any]:
    global LAST_BRIDGE_ERROR
    health_url = f"{UNREAL_BRIDGE_URL.rstrip('/')}/health"
    if _localhost_bridge_misconfigured():
        LAST_BRIDGE_ERROR = _localhost_bridge_message()
        return {
            "reachable": False,
            "http_status": None,
            "url": health_url,
            "error": LAST_BRIDGE_ERROR,
        }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(health_url, headers=_headers())
            payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code == 200:
                LAST_BRIDGE_ERROR = None
            else:
                LAST_BRIDGE_ERROR = f"Bridge health returned HTTP {resp.status_code}."
            return {
                "reachable": resp.status_code == 200,
                "http_status": resp.status_code,
                "url": health_url,
                "payload": payload,
            }
    except Exception as exc:  # noqa: BLE001
        LAST_BRIDGE_ERROR = str(exc)
        return {
            "reachable": False,
            "url": health_url,
            "error": LAST_BRIDGE_ERROR,
        }


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


@mcp_app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOL_DEFINITIONS


@mcp_app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    log.info("MCP tool called: %s args=%s", name, arguments)
    if name not in TOOL_NAMES:
        return _json(_err(f"Unknown tool: {name}", action=name))
    result = await call_plugin(f"tool/{name}", {"tool": name, "args": arguments})
    return _json(result)


@api.on_event("startup")
async def startup_event() -> None:
    log.info("Starting SANE Unreal Agent server")
    log.info("HTTP bind: %s:%s", HOST, PORT)
    log.info("Unreal bridge URL: %s", UNREAL_BRIDGE_URL)
    log.info("Unreal auth token configured: %s", bool(UNREAL_AUTH_TOKEN))


@api.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "sane-unreal-agent",
        "status": "ok",
        "endpoints": ["/health", "/status", "/tools", "/tool/{name}", "/mcp/tools", "/mcp/call_tool"],
    }


@api.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "sane-unreal-agent",
    }


@api.get("/status")
async def status() -> dict[str, Any]:
    bridge = await bridge_health()
    return {
        "status": "ok",
        "service": "sane-unreal-agent",
        "unreal_bridge_url": UNREAL_BRIDGE_URL,
        "bridge_reachable": bridge.get("reachable", False),
        "last_bridge_error": LAST_BRIDGE_ERROR,
        "bridge": bridge,
        "config": {
            "unreal_bridge_url": UNREAL_BRIDGE_URL,
            "unreal_auth_token_configured": bool(UNREAL_AUTH_TOKEN),
            "request_timeout_seconds": REQUEST_TIMEOUT,
        },
    }


@api.get("/tools")
@api.get("/mcp/tools")
async def tools() -> dict[str, Any]:
    return {
        "count": len(TOOL_DEFINITIONS),
        "tools": [
            {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
            for t in TOOL_DEFINITIONS
        ],
    }


@api.post("/tool/{name}")
async def tool_proxy(name: str, payload: dict | None = None) -> JSONResponse:
    payload = payload or {}
    args = payload.get("args", payload)
    if name not in TOOL_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
    result = await call_plugin(f"tool/{name}", {"tool": name, "args": args})
    status_code = 200 if result.get("success") else 502
    return JSONResponse(content=result, status_code=status_code)


@api.post("/mcp/call_tool")
async def mcp_call_tool(payload: dict) -> JSONResponse:
    name = payload.get("name")
    args = payload.get("arguments", {})
    if not name:
        raise HTTPException(status_code=400, detail="Field 'name' is required")
    return await tool_proxy(name, {"args": args})


async def run_stdio_mcp() -> None:
    log.info("Starting stdio MCP mode (for local desktop MCP clients)")
    async with stdio_server() as (read, write):
        await mcp_app.run(read, write, mcp_app.create_initialization_options())


if __name__ == "__main__":
    mode = os.environ.get("SERVER_MODE", "http").lower()
    if mode == "stdio":
        asyncio.run(run_stdio_mcp())
    else:
        import uvicorn

        uvicorn.run(api, host=HOST, port=PORT, log_level=os.environ.get("UVICORN_LOG_LEVEL", "info"))
