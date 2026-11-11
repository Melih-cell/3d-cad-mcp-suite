"""
Blender MCP Server + Game-Engine FBX Exporter + Direct CLI Client
Connects MCP clients to the Blender Live Bridge (default 127.0.0.1:8770) across Blender 3.6–5.x.
"""

import argparse
import json
import os
import socket
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP

BLENDER_HOST = os.environ.get("BLENDER_MCP_HOST", "127.0.0.1")
BLENDER_PORT = int(os.environ.get("BLENDER_MCP_PORT", "8770"))

mcp = FastMCP("blender-mcp")


def send_to_blender(command: str, params: Dict[str, Any] | None = None, timeout: float = 120.0) -> Dict[str, Any]:
    payload = {"command": command, "params": dict(params or {})}
    payload["params"]["timeout"] = timeout
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with socket.create_connection((BLENDER_HOST, BLENDER_PORT), timeout=timeout) as sock:
            sock.sendall(raw)
            buf = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    line, _ = buf.split(b"\n", 1)
                    return json.loads(line.decode("utf-8"))
            if buf:
                return json.loads(buf.decode("utf-8"))
            return {"status": "error", "message": "Empty response from Blender bridge."}
    except ConnectionRefusedError:
        return {
            "status": "error",
            "message": f"Could not connect to Blender on {BLENDER_HOST}:{BLENDER_PORT}. Ensure Blender is running and blender_bridge.py is active.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Error communicating with Blender bridge ({e})."}


@mcp.tool()
def ping_blender() -> str:
    """Check if Blender live bridge is connected and return version, active scene, mode, and active object."""
    return json.dumps(send_to_blender("ping"), ensure_ascii=False, indent=2)


@mcp.tool()
def get_scene_info() -> str:
    """List all objects in the current Blender scene with names, types, vertex/polygon counts, and current mode."""
    return json.dumps(send_to_blender("get_scene_info"), ensure_ascii=False, indent=2)


@mcp.tool()
def get_object_details(name: str) -> str:
    """Get detailed transform, dimensions, mesh topology counts, modifiers, and materials of a Blender object."""
    return json.dumps(send_to_blender("get_object_details", {"name": name}), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_python_in_blender(code: str) -> str:
    """Execute Python code directly inside Blender (bpy, ops, context pre-imported). Set `result = ...` or print() to return data."""
    return json.dumps(send_to_blender("execute_python", {"code": code}), ensure_ascii=False, indent=2)


@mcp.tool()
def export_fbx_game_ready(
    fbx_output_path: str,
    object_names: Optional[List[str]] = None,
    target_engine: str = "unreal",
    apply_transforms: bool = True,
) -> str:
    """Export Blender meshes/armatures to FBX with axis/unit conversion applied cleanly for Unreal Engine 5, Unity, or Substance Painter."""
    fbx_norm = fbx_output_path.replace("\\", "/")
    objs = object_names or []
    eng = target_engine.lower().strip()
    axis_fwd = "-Z" if eng in ("unity", "painter") else "-Y"
    axis_up = "Y" if eng in ("unity", "painter") else "Z"
    py_code = f"""
import os
import bpy

if bpy.context.active_object and bpy.context.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")

names = {repr(objs)}
bpy.ops.object.select_all(action="DESELECT")
selected = []
for obj in bpy.context.scene.objects:
    if (not names and obj.type in ("MESH", "ARMATURE")) or obj.name in names:
        obj.select_set(True)
        selected.append(obj.name)

if not selected:
    result = {{"status": "error", "message": "No matching MESH/ARMATURE objects found to export."}}
else:
    bpy.context.view_layer.objects.active = bpy.data.objects[selected[0]]
    if {repr(apply_transforms)}:
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    out_path = {repr(fbx_norm)}
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=out_path,
        use_selection=True,
        apply_scale_options="FBX_SCALE_ALL",
        axis_forward={repr(axis_fwd)},
        axis_up={repr(axis_up)},
        mesh_smooth_type="FACE",
        add_leaf_bones=False,
    )
    result = {{
        "status": "success",
        "exported_objects": selected,
        "fbx_path": out_path,
        "target_engine": {repr(target_engine)},
    }}
"""
    return json.dumps(send_to_blender("execute_python", {"code": py_code}), ensure_ascii=False, indent=2)


@mcp.tool()
def capture_viewport(output_path: str = "", width: int = 1280, height: int = 720) -> str:
    """Capture the Blender 3D viewport via OpenGL render to a PNG image file and return the file path."""
    return json.dumps(
        send_to_blender("capture_viewport", {"output_path": output_path, "width": width, "height": height}),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def set_mode(mode: str = "OBJECT", object_name: str = "") -> str:
    """Switch active object and interaction mode in Blender (e.g. OBJECT, SCULPT, EDIT)."""
    return json.dumps(send_to_blender("set_mode", {"mode": mode, "object_name": object_name}), ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Blender MCP Server & Direct CLI")
    parser.add_argument("--cli", choices=["ping", "scene", "object", "py", "capture", "mode", "export-fbx"], help="Run direct CLI command")
    parser.add_argument("--arg", type=str, default="", help="Argument for CLI command (object name, mode, output path)")
    parser.add_argument("--engine", type=str, default="unreal", help="Target engine for --cli export-fbx")
    parser.add_argument("--file", type=str, default="", help="Path to Python script file for --cli py")
    args, _ = parser.parse_known_args()

    if args.cli:
        if args.cli == "ping":
            res = send_to_blender("ping")
        elif args.cli == "scene":
            res = send_to_blender("get_scene_info")
        elif args.cli == "object":
            res = send_to_blender("get_object_details", {"name": args.arg})
        elif args.cli == "py":
            code = open(args.file, "r", encoding="utf-8").read() if args.file else args.arg
            res = send_to_blender("execute_python", {"code": code})
        elif args.cli == "export-fbx":
            print(export_fbx_game_ready(args.arg, target_engine=args.engine))
            return
        elif args.cli == "capture":
            res = send_to_blender("capture_viewport", {"output_path": args.arg})
        elif args.cli == "mode":
            res = send_to_blender("set_mode", {"mode": args.arg})
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
