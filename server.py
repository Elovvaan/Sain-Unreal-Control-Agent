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
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import BaseModel

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
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3:latest")
UNREAL_AUTH_TOKEN = os.environ.get("UNREAL_AUTH_TOKEN", "").strip()
REQUEST_TIMEOUT = _get_float_env("REQUEST_TIMEOUT", 60.0)
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = _get_int_env("PORT", 3000)

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
    probe_url = UNREAL_BRIDGE_URL.rstrip("/")
    route_unverified = False
    if _localhost_bridge_misconfigured():
        LAST_BRIDGE_ERROR = _localhost_bridge_message()
        return {
            "reachable": False,
            "http_status": None,
            "url": probe_url,
            "route_unverified": route_unverified,
            "error": LAST_BRIDGE_ERROR,
        }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(probe_url, headers=_headers())
            payload: dict[str, Any] = {}
            if resp.headers.get("content-type", "").startswith("application/json"):
                try:
                    parsed_payload = resp.json()
                    if isinstance(parsed_payload, dict):
                        payload = parsed_payload
                except Exception:  # noqa: BLE001
                    payload = {}

            is_unreal_http_route_miss = (
                resp.status_code == 404
                and payload.get("errorCode") == "errors.com.epicgames.httpserver.route_handler_not_found"
            )
            if resp.status_code == 200 or is_unreal_http_route_miss:
                LAST_BRIDGE_ERROR = None
                route_unverified = is_unreal_http_route_miss
            else:
                LAST_BRIDGE_ERROR = f"Bridge probe returned HTTP {resp.status_code}."
            return {
                "reachable": resp.status_code == 200 or is_unreal_http_route_miss,
                "http_status": resp.status_code,
                "url": probe_url,
                "route_unverified": route_unverified,
                "payload": payload,
            }
    except Exception as exc:  # noqa: BLE001
        LAST_BRIDGE_ERROR = str(exc)
        return {
            "reachable": False,
            "url": probe_url,
            "route_unverified": route_unverified,
            "error": LAST_BRIDGE_ERROR,
        }


async def ollama_generate(prompt: str) -> dict[str, Any]:
    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"ok": True, "response": data.get("response", ""), "raw": data}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "response": "", "error": str(exc)}


async def ollama_model_status() -> dict[str, Any]:
    tags_url = f"{OLLAMA_URL}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(tags_url)
            resp.raise_for_status()
            payload = resp.json()
            models = payload.get("models", [])
            available = [m.get("name", "") for m in models]
            return {
                "status": "ok",
                "ollama_url": OLLAMA_URL,
                "configured_model": OLLAMA_MODEL,
                "configured_model_available": OLLAMA_MODEL in available,
                "available_models": available,
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "down",
            "ollama_url": OLLAMA_URL,
            "configured_model": OLLAMA_MODEL,
            "error": str(exc),
        }


def detect_unreal_editor_process() -> bool:
    """Best-effort local process detection for Unreal Editor."""
    if os.name == "nt":
        command = ["tasklist"]
        needles = ("UnrealEditor.exe", "UE4Editor.exe")
    else:
        command = ["ps", "-A", "-o", "comm="]
        needles = ("UnrealEditor", "UE4Editor")

    try:
        proc_output = subprocess.run(command, capture_output=True, text=True, check=False, timeout=2)
        process_list = proc_output.stdout
        return any(needle in process_list for needle in needles)
    except Exception:  # noqa: BLE001
        return False


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


class AgentChatRequest(BaseModel):
    message: str
    confirm: bool = False


async def _tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return await call_plugin(f"tool/{name}", {"tool": name, "args": args})


def _extract_move(message: str) -> tuple[str, float, float, float] | None:
    actor_match = re.search(r"\bactor\s+['\"]?([A-Za-z0-9_\-]+)['\"]?", message, re.IGNORECASE)
    xyz_match = re.search(
        r"(?:to|x)\s*\(?\s*(-?\d+(?:\.\d+)?)\s*[, ]+\s*(-?\d+(?:\.\d+)?)\s*[, ]+\s*(-?\d+(?:\.\d+)?)\s*\)?",
        message,
        re.IGNORECASE,
    )
    if not actor_match or not xyz_match:
        return None
    return (
        actor_match.group(1),
        float(xyz_match.group(1)),
        float(xyz_match.group(2)),
        float(xyz_match.group(3)),
    )


def _is_list_actors_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:list|show|get|display)\s+(?:all\s+)?actors\b"
            r"|\bactors?\s+list\b"
            r"|\bactors?\s+in\s+(?:my\s+)?(?:unreal\s+)?scene\b"
            r"|\bwhat\s+actors?\s+(?:are\s+)?in\b",
            text,
            re.IGNORECASE,
        )
    )


def _is_cube_create_intent(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:create|spawn|add)\b(?:\s+\w+){0,3}\s+\bcube\b",
            text,
            re.IGNORECASE,
        )
    )


async def _spawn_cube_command() -> dict[str, Any]:
    return await _tool_call(
        "run_editor_python",
        {
            "code": (
                "import unreal\n"
                "loc=unreal.Vector(0,0,100)\n"
                "actor=unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, loc)\n"
                "mesh=unreal.load_asset('/Engine/BasicShapes/Cube.Cube')\n"
                "actor.static_mesh_component.set_static_mesh(mesh)\n"
                "actor.set_actor_label('AI_Cube')\n"
                "print({'created_actor':actor.get_actor_label()})"
            )
        },
    )


async def _route_safe_command(message: str, confirm: bool) -> dict[str, Any] | None:
    text = message.lower()
    if "inspect scene" in text or "what is in my unreal scene" in text or "scene" in text:
        return await _tool_call("get_editor_state", {})

    if _is_list_actors_request(text):
        return await _tool_call(
            "run_editor_python",
            {
                "code": (
                    "import unreal\n"
                    "actors=[a.get_actor_label() for a in unreal.EditorLevelLibrary.get_all_level_actors()]\n"
                    "print({'actors':actors,'count':len(actors)})"
                )
            },
        )

    if _is_cube_create_intent(text):
        return await _spawn_cube_command()

    if "move actor" in text:
        move = _extract_move(message)
        if not move:
            return _err("For move actor, provide actor name and XYZ, e.g. move actor Cube to 100, 0, 200.", action="move_actor")
        actor_name, x, y, z = move
        return await _tool_call(
            "run_editor_python",
            {
                "code": (
                    "import unreal\n"
                    f"name={actor_name!r}\n"
                    f"target=unreal.Vector({x},{y},{z})\n"
                    "actors=unreal.EditorLevelLibrary.get_all_level_actors()\n"
                    "match=[a for a in actors if a.get_actor_label()==name]\n"
                    "if not match:\n"
                    "    print({'moved':False,'error':'actor_not_found','name':name})\n"
                    "else:\n"
                    "    match[0].set_actor_location(target, False, False)\n"
                    "    print({'moved':True,'name':name,'location':[target.x,target.y,target.z]})"
                )
            },
        )

    if "save level" in text or "save map" in text:
        if not confirm:
            return _err("Save level is blocked unless confirm=true is provided.", action="save_level")
        return await _tool_call(
            "run_editor_python",
            {
                "code": (
                    "import unreal\n"
                    "ok=unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)\n"
                    "print({'saved':bool(ok)})"
                )
            },
        )

    return None


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
        "endpoints": [
            "/health",
            "/status",
            "/tools",
            "/tool/{name}",
            "/mcp/tools",
            "/mcp/call_tool",
            "/ollama/health",
            "/agent/chat",
        ],
    }


@api.get("/health")
async def health() -> dict[str, Any]:
    bridge = await bridge_health()
    editor_detected = await asyncio.to_thread(detect_unreal_editor_process)
    bridge_up = bridge.get("reachable", False)
    route_unverified = bool(bridge.get("route_unverified", False))
    message = None
    if not bridge_up:
        message = "Unreal plugin bridge is not running."
    elif not editor_detected:
        message = "Open Unreal Editor first."

    return {
        "status": "ok" if bridge_up and editor_detected else "degraded",
        "service": "sane-unreal-agent",
        "mcp": "up",
        "unreal_bridge": "up" if bridge_up else "down",
        "route_unverified": route_unverified,
        "unreal_editor_detected": editor_detected,
        "message": message,
        "bridge_url": UNREAL_BRIDGE_URL,
        "bridge_error": bridge.get("error"),
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


@api.get("/ollama/health")
async def ollama_health() -> dict[str, Any]:
    return await ollama_model_status()


@api.post("/agent/chat")
async def agent_chat(payload: AgentChatRequest) -> dict[str, Any]:
    text = payload.message.lower()
    routed_intent: str = "chat_only"
    command_executed = False
    command_result: dict[str, Any] | None = None
    error: str | None = None

    bridge = await bridge_health()
    bridge_reachable = bool(bridge.get("reachable", False))

    if _is_cube_create_intent(text):
        routed_intent = "spawn_cube"
        if bridge_reachable:
            command_executed = True
            command_result = await _spawn_cube_command()
    else:
        if bridge_reachable:
            command_result = await _route_safe_command(payload.message, payload.confirm)

    if not command_executed and command_result is not None:
        routed_intent = "safe_command"
        command_executed = True

    if command_result is None and routed_intent in {"spawn_cube", "safe_command"} and not bridge_reachable:
        error = bridge.get("error") or LAST_BRIDGE_ERROR or "Unreal bridge is unreachable."

    if command_executed and (not isinstance(command_result, dict) or not command_result.get("success", False)):
        if isinstance(command_result, dict):
            command_errors = command_result.get("errors") or []
            if command_errors:
                error = str(command_errors[0])
        if not error:
            error = bridge.get("error") or LAST_BRIDGE_ERROR or "Command execution failed."

    editor_state: Any | None = None
    if isinstance(command_result, dict):
        routed_name = (
            command_result.get("tool")
            or command_result.get("name")
            or command_result.get("command")
        )
        if routed_name == "get_editor_state":
            editor_state = (
                command_result.get("result")
                or command_result.get("data")
                or command_result.get("response")
                or command_result
            )

    if editor_state is None:
        editor_state = await _tool_call("get_editor_state", {})
    prompt = (
        "You are a local Unreal assistant. Be concise and safe.\n"
        f"User message: {payload.message}\n"
        f"Unreal bridge health: {json.dumps(bridge)}\n"
        f"Editor state: {json.dumps(editor_state)}\n"
        f"Executed command result: {json.dumps(command_result)}\n"
        "If command is blocked, explain confirm=true requirement."
    )
    llm = await ollama_generate(prompt)
    assistant_text = llm.get("response", "").strip() or "I could not get a response from Ollama."
    return {
        "assistant": assistant_text,
        "model": OLLAMA_MODEL,
        "ollama_ok": llm.get("ok", False),
        "routed_intent": routed_intent,
        "command_executed": command_executed,
        "error": error,
        "bridge": bridge,
        "editor_state": editor_state,
        "command_result": command_result,
    }


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
