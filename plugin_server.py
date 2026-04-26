"""
UnrealControlPlugin — HTTP Bridge
Listens on 127.0.0.1:8765, receives MCP tool calls, executes them via
Unreal Editor Python scripting, and returns structured JSON.

Loaded automatically by the C++ plugin module on editor startup via:
    unreal.PythonScriptLibrary.execute_python_script("...plugin_server.py")
"""

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import unreal

PORT = 8765
LOG_LINES: list[str] = []

_eal = unreal.EditorAssetLibrary
_ell = unreal.EditorLevelLibrary
_eul = unreal.EditorUtilityLibrary
_al  = unreal.AssetLibrary if hasattr(unreal, "AssetLibrary") else None


# ─── structured response helpers ──────────────────────────────────────────────

def ok(action: str, result: Any, affected_assets: list[str] | None = None,
       screenshot_path: str | None = None) -> dict:
    return {
        "success": True,
        "action": action,
        "result": result,
        "logs": list(LOG_LINES[-50:]),
        "errors": [],
        "screenshot_path": screenshot_path,
        "affected_assets": affected_assets or [],
    }


def err(action: str, message: str, tb: str = "") -> dict:
    return {
        "success": False,
        "action": action,
        "result": None,
        "logs": list(LOG_LINES[-50:]),
        "errors": [message, tb] if tb else [message],
        "screenshot_path": None,
        "affected_assets": [],
    }


# ─── tool implementations ──────────────────────────────────────────────────────

def tool_get_editor_state(_args: dict) -> dict:
    world = _ell.get_editor_world()
    world_name = world.get_name() if world else "none"
    return ok("get_editor_state", {
        "world": world_name,
        "is_playing": unreal.EditorLevelLibrary.get_game_view() if hasattr(_ell, "get_game_view") else False,
        "dirty": world.get_outer_most() is not None if world else False,
    })


def tool_list_level_actors(args: dict) -> dict:
    filter_text = str(args.get("filter", "")).lower().strip()
    actors = []
    for actor in _ell.get_all_level_actors():
        label = actor.get_actor_label()
        if filter_text and filter_text not in label.lower():
            continue
        actors.append(
            {
                "label": label,
                "class": actor.get_class().get_name() if actor.get_class() else "",
                "path": actor.get_path_name(),
            }
        )
    return ok("list_level_actors", {"count": len(actors), "actors": actors})


def tool_list_assets(args: dict) -> dict:
    path    = args.get("path", "/Game")
    filter_ = args.get("filter", "")
    assets  = _eal.list_assets(path, recursive=True, include_folder=False)
    if filter_:
        assets = [a for a in assets if filter_.lower() in a.lower()]
    return ok("list_assets", {"path": path, "assets": list(assets)})


def tool_create_level(args: dict) -> dict:
    name     = args["name"]
    path     = args.get("path", "/Game/Maps")
    template = args.get("template", "Empty")
    full     = f"{path}/{name}"
    template_map = {
        "Empty":    "/Engine/Maps/Templates/Template_Default",
        "Default":  "/Engine/Maps/Templates/Template_Default",
        "VR-Basic": "/Engine/VREditor/VRTemplateMap",
    }
    tpl_path = template_map.get(template, "/Engine/Maps/Templates/Template_Default")
    ok_ = _eal.duplicate_asset(tpl_path, full)
    if not ok_:
        return err("create_level", f"Failed to create level at {full}")
    _ell.load_level(full)
    return ok("create_level", {"path": full}, affected_assets=[full])


def tool_spawn_actor(args: dict) -> dict:
    class_path = args["class_path"]
    name_      = args["name"]
    loc_d      = args.get("location", {"x": 0, "y": 0, "z": 0})
    rot_d      = args.get("rotation", {"pitch": 0, "yaw": 0, "roll": 0})

    actor_class = unreal.load_class(None, class_path)
    if not actor_class:
        return err("spawn_actor", f"Class not found: {class_path}")

    loc = unreal.Vector(loc_d.get("x", 0), loc_d.get("y", 0), loc_d.get("z", 0))
    rot = unreal.Rotator(rot_d.get("pitch", 0), rot_d.get("yaw", 0), rot_d.get("roll", 0))
    actor = _ell.spawn_actor_from_class(actor_class, loc, rot)
    if not actor:
        return err("spawn_actor", "spawn_actor_from_class returned None")
    actor.set_actor_label(name_)
    return ok("spawn_actor", {"actor": name_, "class": class_path,
                               "location": loc_d, "rotation": rot_d})


def tool_import_asset(args: dict) -> dict:
    src  = args["source_path"]
    dest = args["destination_path"]
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", src)
    task.set_editor_property("destination_path", dest)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    imported = task.get_editor_property("imported_object_paths") or []
    return ok("import_asset", {"imported": list(imported)}, affected_assets=list(imported))


def tool_create_level_sequence(args: dict) -> dict:
    name     = args["name"]
    path     = args.get("path", "/Game/Cinematics")
    duration = float(args.get("duration_seconds", 5.0))
    fps      = float(args.get("frame_rate", 24.0))
    full     = f"{path}/{name}"

    seq = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, path, unreal.LevelSequence, unreal.LevelSequenceFactoryNew()
    )
    if not seq:
        return err("create_level_sequence", f"Failed to create LevelSequence at {full}")

    frame_rate  = unreal.FrameRate(int(fps), 1)
    total_frames = int(duration * fps)
    seq.set_editor_property("display_rate", frame_rate)
    seq.set_editor_property("playback_range",
        unreal.SequencerScriptingRange(has_start_value=True, inclusive_start=0,
                                        has_end_value=True, exclusive_end=total_frames))
    _eal.save_asset(full)
    return ok("create_level_sequence",
              {"path": full, "duration_seconds": duration, "frame_rate": fps,
               "total_frames": total_frames},
              affected_assets=[full])


def tool_add_cine_camera(args: dict) -> dict:
    seq_path    = args["sequence_path"]
    camera_name = args.get("camera_name", "CineCamera_01")
    loc_d       = args.get("location", {"x": 0, "y": 0, "z": 100})

    # Load sequence
    seq = unreal.load_asset(seq_path)
    if not seq or not isinstance(seq, unreal.LevelSequence):
        return err("add_cine_camera", f"LevelSequence not found: {seq_path}")

    # Spawn camera actor
    loc = unreal.Vector(loc_d.get("x", 0), loc_d.get("y", 0), loc_d.get("z", 100))
    cam_class = unreal.load_class(None, "/Script/CinematicCamera.CineCameraActor")
    cam_actor = _ell.spawn_actor_from_class(cam_class, loc)
    if not cam_actor:
        return err("add_cine_camera", "Failed to spawn CineCameraActor")
    cam_actor.set_actor_label(camera_name)

    # Bind actor to sequence
    binding = seq.add_possessable(cam_actor)

    # Add Camera Cuts track
    cuts_track = seq.add_master_track(unreal.MovieSceneCameraCutTrack)
    cuts_section = cuts_track.add_section()
    cuts_section.set_editor_property("camera_binding_id",
        unreal.MovieSceneObjectBindingID(binding.get_id(), unreal.MovieSceneObjectBindingSpace.LOCAL))
    pr = seq.get_editor_property("playback_range")
    cuts_section.set_range(pr.inclusive_start, pr.exclusive_end - 1)

    _eal.save_asset(seq_path)
    return ok("add_cine_camera",
              {"camera": camera_name, "sequence": seq_path},
              affected_assets=[seq_path])


def tool_animate_camera(args: dict) -> dict:
    seq_path    = args["sequence_path"]
    camera_name = args["camera_name"]
    keyframes   = args["keyframes"]  # [{time_seconds, location, rotation}, …]

    seq = unreal.load_asset(seq_path)
    if not seq:
        return err("animate_camera", f"Sequence not found: {seq_path}")

    fps = seq.get_editor_property("display_rate").numerator

    # Find camera binding
    binding = None
    for b in seq.get_bindings():
        if b.get_name() == camera_name:
            binding = b
            break
    if not binding:
        return err("animate_camera", f"No binding named {camera_name} in {seq_path}")

    # Add transform track
    transform_track = binding.add_track(unreal.MovieScene3DTransformTrack)
    section = transform_track.add_section()
    pr = seq.get_editor_property("playback_range")
    section.set_range(pr.inclusive_start, pr.exclusive_end - 1)

    channels = section.get_editor_property("channels")

    def set_key(channel_idx: int, frame: int, value: float):
        channels[channel_idx].add_key(unreal.FrameNumber(frame), value)

    for kf in keyframes:
        t     = float(kf["time_seconds"])
        frame = int(t * fps)
        loc   = kf.get("location", {})
        rot   = kf.get("rotation", {})
        # Channels: 0=Tx 1=Ty 2=Tz 3=Rx 4=Ry 5=Rz 6=Sx 7=Sy 8=Sz
        set_key(0, frame, loc.get("x", 0))
        set_key(1, frame, loc.get("y", 0))
        set_key(2, frame, loc.get("z", 0))
        set_key(3, frame, rot.get("pitch", 0))
        set_key(4, frame, rot.get("yaw", 0))
        set_key(5, frame, rot.get("roll", 0))

    _eal.save_asset(seq_path)
    return ok("animate_camera",
              {"sequence": seq_path, "camera": camera_name,
               "keyframe_count": len(keyframes)},
              affected_assets=[seq_path])


def tool_create_blueprint(args: dict) -> dict:
    name         = args["name"]
    path         = args.get("path", "/Game/Blueprints")
    parent_class = args.get("parent_class", "Actor")

    cls = unreal.load_class(None, f"/Script/Engine.{parent_class}")
    if not cls:
        return err("create_blueprint", f"Parent class not found: {parent_class}")

    bp_factory = unreal.BlueprintFactory()
    bp_factory.set_editor_property("parent_class", cls)
    bp = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, path, unreal.Blueprint, bp_factory
    )
    if not bp:
        return err("create_blueprint", f"Failed to create Blueprint {name}")
    full = f"{path}/{name}"
    _eal.save_asset(full)
    return ok("create_blueprint", {"path": full}, affected_assets=[full])


def tool_run_editor_python(args: dict) -> dict:
    code = args["code"]
    namespace: dict = {}
    try:
        exec(code, {"unreal": unreal, "__builtins__": __builtins__}, namespace)  # noqa: S102
    except Exception as e:
        return err("run_editor_python", str(e), traceback.format_exc())
    result = namespace.get("RESULT", "executed (no RESULT variable set)")
    return ok("run_editor_python", {"result": str(result)})


def tool_get_logs(args: dict) -> dict:
    lines  = int(args.get("lines", 100))
    filter_ = args.get("filter", "").lower()
    all_lines = list(LOG_LINES)
    if filter_:
        all_lines = [l for l in all_lines if filter_ in l.lower()]
    return ok("get_logs", {"lines": all_lines[-lines:]})


def tool_take_screenshot(args: dict) -> dict:
    filename = args.get("filename", "screenshot")
    width    = int(args.get("width", 1920))
    height   = int(args.get("height", 1080))

    import os
    project_dir = unreal.Paths.project_saved_dir()
    out_path = os.path.join(project_dir, "Screenshots", f"{filename}.png")

    # Use the AutomationLibrary high-res screenshot
    unreal.AutomationLibrary.take_high_res_screenshot(width, height, f"{filename}.png")

    return ok("take_screenshot", {"path": out_path}, screenshot_path=out_path)


# ─── dispatch table ────────────────────────────────────────────────────────────

TOOLS = {
    "get_editor_state":      tool_get_editor_state,
    "list_level_actors":     tool_list_level_actors,
    "list_assets":           tool_list_assets,
    "create_level":          tool_create_level,
    "spawn_actor":           tool_spawn_actor,
    "import_asset":          tool_import_asset,
    "create_level_sequence": tool_create_level_sequence,
    "add_cine_camera":       tool_add_cine_camera,
    "animate_camera":        tool_animate_camera,
    "create_blueprint":      tool_create_blueprint,
    "run_editor_python":     tool_run_editor_python,
    "get_logs":              tool_get_logs,
    "take_screenshot":       tool_take_screenshot,
}


# ─── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):  # silence default access log
        pass

    def _respond(self, status: int, body: dict):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok", "plugin": "UnrealControlPlugin"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        length  = int(self.headers.get("Content-Length", 0))
        raw     = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as e:
            self._respond(400, err("parse", f"Bad JSON: {e}"))
            return

        # Route: /tool/<name>
        if self.path.startswith("/tool/"):
            tool_name = self.path[len("/tool/"):]
            handler   = TOOLS.get(tool_name)
            if not handler:
                self._respond(404, err(tool_name, f"Unknown tool: {tool_name}"))
                return
            try:
                result = handler(body.get("args", {}))
                self._respond(200, result)
            except Exception as e:
                self._respond(500, err(tool_name, str(e), traceback.format_exc()))
        else:
            self._respond(404, err("route", f"Unknown path: {self.path}"))


# ─── log capture ──────────────────────────────────────────────────────────────

class LogCapture:
    """Hooks into unreal.log to capture output."""
    def write(self, msg: str):
        LOG_LINES.append(msg.rstrip())
        if len(LOG_LINES) > 2000:
            del LOG_LINES[:500]


# ─── server startup (called from C++ plugin on editor init) ───────────────────

_server_thread: threading.Thread | None = None
_httpd: HTTPServer | None = None


def start():
    global _server_thread, _httpd
    if _server_thread and _server_thread.is_alive():
        unreal.log("UnrealControlPlugin: server already running")
        return

    _httpd = HTTPServer(("127.0.0.1", PORT), Handler)
    _server_thread = threading.Thread(target=_httpd.serve_forever, daemon=True)
    _server_thread.start()
    unreal.log(f"UnrealControlPlugin: HTTP bridge listening on 127.0.0.1:{PORT}")


def stop():
    global _httpd
    if _httpd:
        _httpd.shutdown()
        unreal.log("UnrealControlPlugin: HTTP bridge stopped")


# Auto-start when exec'd by the plugin
start()
