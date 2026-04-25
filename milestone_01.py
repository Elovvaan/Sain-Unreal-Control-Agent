"""
SANE Agent — Milestone 1 Runner
================================
Creates a Level Sequence named Intro_01, spawns a CineCameraActor,
binds it to Camera Cuts, animates a 5-second forward camera move,
saves the project, and returns screenshot + logs.

Usage:
    python agent/milestone_01.py [--url http://127.0.0.1:8765]
"""

import argparse
import json
import sys
import httpx

DEFAULT_URL = "http://127.0.0.1:8765"
TIMEOUT = 60.0


def call(base_url: str, tool: str, args: dict) -> dict:
    url = f"{base_url}/tool/{tool}"
    r = httpx.post(url, json={"tool": tool, "args": args}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def check(result: dict, label: str) -> None:
    if not result.get("success"):
        print(f"\n✗  FAILED — {label}")
        for e in result.get("errors", []):
            print(f"   ERROR: {e}")
        for l in result.get("logs", [])[-10:]:
            print(f"   LOG: {l}")
        sys.exit(1)
    print(f"✓  {label}")
    if result.get("result"):
        print(f"   → {json.dumps(result['result'], indent=4)}")


def run(base_url: str):
    print(f"\n{'═'*60}")
    print("  SANE Unreal Control Agent — Milestone 1")
    print(f"  Target: {base_url}")
    print(f"{'═'*60}\n")

    # 0. Health check
    try:
        r = httpx.get(f"{base_url}/health", timeout=5)
        assert r.status_code == 200
        print("✓  Plugin reachable\n")
    except Exception as e:
        print(f"✗  Cannot reach plugin at {base_url}: {e}")
        print("   → Start Unreal Editor with UnrealControlPlugin enabled first.")
        sys.exit(1)

    # 1. Inspect editor state
    result = call(base_url, "get_editor_state", {})
    check(result, "get_editor_state")

    # 2. Create Level Sequence: Intro_01 (5 seconds @ 24 fps)
    result = call(base_url, "create_level_sequence", {
        "name": "Intro_01",
        "path": "/Game/Cinematics",
        "duration_seconds": 5.0,
        "frame_rate": 24.0,
    })
    check(result, "create_level_sequence → /Game/Cinematics/Intro_01")
    seq_path = "/Game/Cinematics/Intro_01"

    # 3. Spawn CineCameraActor and bind it to Camera Cuts
    result = call(base_url, "add_cine_camera", {
        "sequence_path": seq_path,
        "camera_name": "CineCamera_01",
        "location": {"x": 0, "y": 0, "z": 100},
    })
    check(result, "add_cine_camera → CineCamera_01 bound to Camera Cuts")

    # 4. Animate: 5-second forward move (X: 0 → 500)
    keyframes = [
        {"time_seconds": 0.0,  "location": {"x": 0,   "y": 0, "z": 100}, "rotation": {"pitch": 0, "yaw": 0, "roll": 0}},
        {"time_seconds": 5.0,  "location": {"x": 500, "y": 0, "z": 100}, "rotation": {"pitch": 0, "yaw": 0, "roll": 0}},
    ]
    result = call(base_url, "animate_camera", {
        "sequence_path": seq_path,
        "camera_name": "CineCamera_01",
        "keyframes": keyframes,
    })
    check(result, "animate_camera → 2 keyframes (0 s → 5 s, forward move)")

    # 5. Save project via Python
    result = call(base_url, "run_editor_python", {
        "code": """
import unreal
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
RESULT = 'project saved'
"""
    })
    check(result, "run_editor_python → save_dirty_packages")

    # 6. Screenshot
    result = call(base_url, "take_screenshot", {
        "filename": "intro_01_result",
        "width": 1920,
        "height": 1080,
    })
    check(result, "take_screenshot → intro_01_result.png")
    screenshot = result.get("screenshot_path", "unknown")

    # 7. Fetch logs
    result = call(base_url, "get_logs", {"lines": 30, "filter": "CineCamera"})
    check(result, "get_logs (CineCamera filter)")

    print(f"\n{'═'*60}")
    print("  MILESTONE 1 COMPLETE")
    print(f"  Screenshot: {screenshot}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SANE Milestone 1 Runner")
    parser.add_argument("--url", default=DEFAULT_URL, help="Plugin HTTP URL")
    args = parser.parse_args()
    run(args.url)
