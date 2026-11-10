"""
Adobe Substance 3D Painter MCP Server + Direct CLI Client
Connects to the Substance 3D Painter Live Bridge on 127.0.0.1:8768.
Automatically launches Adobe Substance 3D Painter if it is not running.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP

PAINTER_HOST = "127.0.0.1"
PAINTER_PORT = 8768
PAINTER_EXE = r"C:\Program Files\Adobe\Adobe Substance 3D Painter\Adobe Substance 3D Painter.exe"

mcp = FastMCP("substance-painter-mcp")


def is_painter_bridge_alive(timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((PAINTER_HOST, PAINTER_PORT), timeout=timeout) as sock:
            sock.sendall(b'{"command": "ping", "params": {}}\n')
            data = sock.recv(4096)
            return b"ok" in data or b"success" in data
    except Exception:
        return False


def ensure_painter_running(wait_seconds: int = 75) -> Dict[str, Any]:
    """Ensure Adobe Substance 3D Painter is running and its TCP bridge (port 8768) is active. Launches it if closed."""
    if is_painter_bridge_alive():
        return {"status": "ok", "message": "Substance 3D Painter is already running and connected on port 8768."}

    if not os.path.exists(PAINTER_EXE):
        return {"status": "error", "message": f"Substance 3D Painter executable not found at {PAINTER_EXE}"}

    try:
        proc_check = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Adobe Substance 3D Painter.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        already_starting = "substance" in proc_check.stdout.lower()
    except Exception:
        already_starting = False

    if not already_starting:
        try:
            subprocess.run(["schtasks", "/run", "/tn", "LaunchSubstancePainter"], capture_output=True, text=True, timeout=10)
        except Exception:
            try:
                launch_bat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch_painter.bat")
                if os.path.exists(launch_bat):
                    os.startfile(launch_bat)
                else:
                    os.startfile(PAINTER_EXE)
            except Exception as e:
                return {"status": "error", "message": f"Failed to launch Substance 3D Painter: {e}"}

    start_time = time.time()
    while time.time() - start_time < wait_seconds:
        if is_painter_bridge_alive(timeout=2.0):
            return {
                "status": "ok",
                "message": f"Substance 3D Painter launched and bridge connected in {int(time.time() - start_time)}s.",
            }
        time.sleep(2.0)

    return {
        "status": "error",
        "message": f"Substance 3D Painter was launched, but port {PAINTER_PORT} did not respond within {wait_seconds}s.",
    }


def send_to_painter(
    command: str,
    params: Dict[str, Any] | None = None,
    timeout: float = 120.0,
    auto_launch: bool = True,
) -> Dict[str, Any]:
    if auto_launch and not is_painter_bridge_alive():
        launch_res = ensure_painter_running()
        if launch_res.get("status") != "ok":
            return launch_res

    payload = {"command": command, "params": params or {}}
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with socket.create_connection((PAINTER_HOST, PAINTER_PORT), timeout=timeout) as sock:
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
            return {"status": "error", "message": "Empty response from Substance 3D Painter bridge."}
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not connect to Substance 3D Painter on {PAINTER_HOST}:{PAINTER_PORT} ({e}).",
        }


@mcp.tool()
def launch_painter(wait_seconds: int = 75) -> str:
    """Launch Adobe Substance 3D Painter if it is not already running and wait until the live TCP bridge on port 8768 is ready."""
    return json.dumps(ensure_painter_running(wait_seconds=wait_seconds), ensure_ascii=False, indent=2)


@mcp.tool()
def ping_painter(auto_launch: bool = True) -> str:
    """Check if Adobe Substance 3D Painter live bridge is connected."""
    return json.dumps(send_to_painter("ping", auto_launch=auto_launch), ensure_ascii=False, indent=2)


@mcp.tool()
def get_project_info() -> str:
    """Get full details of the currently open Substance 3D Painter project."""
    return json.dumps(send_to_painter("get_project_info"), ensure_ascii=False, indent=2)


@mcp.tool()
def open_or_create_project(
    project_path: str = "",
    mesh_path: str = "",
    resolution: int = 2048,
    normal_map_format: str = "OpenGL",
    close_existing: bool = True,
) -> str:
    """Open an existing .spp project file OR create a new Substance 3D Painter project from a 3D mesh file (.fbx, .obj, .usd, .gltf)."""
    return json.dumps(
        send_to_painter(
            "open_or_create_project",
            {
                "project_path": project_path,
                "mesh_path": mesh_path,
                "resolution": resolution,
                "normal_map_format": normal_map_format,
                "close_existing": close_existing,
            },
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def save_project(file_path: str = "") -> str:
    """Save the current Substance 3D Painter project (.spp)."""
    return json.dumps(send_to_painter("save_project", {"file_path": file_path}), ensure_ascii=False, indent=2)


@mcp.tool()
def search_resources(query: str = "", usage: str = "", limit: int = 30) -> str:
    """Search Substance 3D Painter shelves for resources."""
    return json.dumps(
        send_to_painter("search_resources", {"query": query, "usage": usage, "limit": limit}),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def create_layer(
    layer_type: str = "fill",
    name: str = "",
    texture_set: str = "",
    resource_name: str = "",
    resource_url: str = "",
    base_color: Optional[List[float]] = None,
    metallic: Optional[float] = None,
    roughness: Optional[float] = None,
) -> str:
    """Create a layer in the active or specified Texture Set."""
    return json.dumps(
        send_to_painter(
            "create_layer",
            {
                "layer_type": layer_type,
                "name": name,
                "texture_set": texture_set,
                "resource_name": resource_name,
                "resource_url": resource_url,
                "base_color": base_color,
                "metallic": metallic,
                "roughness": roughness,
            },
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def add_layer_mask_or_effect(
    effect_type: str = "black_mask",
    layer_uid: Optional[int] = None,
    layer_name: str = "",
    texture_set: str = "",
    resource_name: str = "",
    resource_url: str = "",
    stack_type: str = "mask",
) -> str:
    """Add a mask or effect to a layer."""
    return json.dumps(
        send_to_painter(
            "add_layer_mask_or_effect",
            {
                "effect_type": effect_type,
                "layer_uid": layer_uid,
                "layer_name": layer_name,
                "texture_set": texture_set,
                "resource_name": resource_name,
                "resource_url": resource_url,
                "stack_type": stack_type,
            },
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def export_textures(
    export_path: str = "",
    preset_name: str = "PBR Metallic Roughness",
    preset_url: str = "",
    file_format: str = "png",
    bit_depth: str = "8",
    resolution: Optional[int] = None,
) -> str:
    """Export project textures to disk using a Substance 3D Painter export preset."""
    return json.dumps(
        send_to_painter(
            "export_textures",
            {
                "export_path": export_path,
                "preset_name": preset_name,
                "preset_url": preset_url,
                "file_format": file_format,
                "bit_depth": bit_depth,
                "resolution": resolution,
            },
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def auto_texture_and_export_pbr(
    mesh_path: str,
    export_dir: str,
    smart_material_query: str = "Steel",
    resolution: int = 2048,
    save_spp_path: str = "",
) -> str:
    """End-to-end automated Substance 3D Painter pipeline."""
    steps = []
    p1 = send_to_painter(
        "open_or_create_project",
        {
            "mesh_path": mesh_path,
            "resolution": resolution,
            "normal_map_format": "OpenGL",
            "close_existing": True,
        },
    )
    steps.append({"step": "open_or_create_project", "result": p1})
    if p1.get("status") != "ok":
        return json.dumps({"status": "error", "steps": steps}, ensure_ascii=False, indent=2)

    p2 = send_to_painter(
        "create_layer",
        {
            "layer_type": "smart_material",
            "resource_name": smart_material_query,
        },
    )
    if p2.get("status") != "ok":
        p2 = send_to_painter(
            "create_layer",
            {
                "layer_type": "fill",
                "name": f"Auto_{smart_material_query}",
                "resource_name": smart_material_query,
                "base_color": [0.22, 0.24, 0.26],
                "metallic": 0.85,
                "roughness": 0.35,
            },
        )
    steps.append({"step": "apply_material", "result": p2})

    if save_spp_path:
        os.makedirs(os.path.dirname(os.path.abspath(save_spp_path)), exist_ok=True)
        p3 = send_to_painter("save_project", {"file_path": save_spp_path})
        steps.append({"step": "save_project", "result": p3})

    os.makedirs(os.path.abspath(export_dir), exist_ok=True)
    p4 = send_to_painter(
        "export_textures",
        {
            "export_path": os.path.abspath(export_dir),
            "preset_name": "PBR Metallic Roughness",
            "file_format": "png",
            "bit_depth": "8",
            "resolution": resolution,
        },
    )
    steps.append({"step": "export_textures", "result": p4})
    return json.dumps({"status": "ok", "steps": steps}, ensure_ascii=False, indent=2)


@mcp.tool()
def execute_js_in_painter(code: str) -> str:
    """Execute JavaScript code inside Substance 3D Painter's alg.* JS API engine."""
    return json.dumps(send_to_painter("execute_js", {"code": code}), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_python_in_painter(code: str) -> str:
    """Execute Python code directly inside Substance 3D Painter."""
    return json.dumps(send_to_painter("execute_python", {"code": code}), ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Adobe Substance 3D Painter MCP Server & CLI")
    parser.add_argument(
        "--cli",
        choices=["launch", "ping", "project", "search", "js", "py", "export", "auto-texture"],
        help="Run a direct CLI command instead of starting stdio MCP server",
    )
    parser.add_argument("--arg", type=str, default="", help="Argument or inline code for --cli")
    parser.add_argument("--mesh", type=str, default="", help="Mesh path (.fbx/.obj) for --cli auto-texture")
    parser.add_argument("--material", type=str, default="Steel", help="Smart Material query for --cli auto-texture")
    parser.add_argument("--file", type=str, default="", help="Path to script file for --cli js or py")
    args, _ = parser.parse_known_args()

    if args.cli:
        if args.cli == "launch":
            res = ensure_painter_running()
        elif args.cli == "ping":
            res = send_to_painter("ping")
        elif args.cli == "project":
            res = send_to_painter("get_project_info")
        elif args.cli == "search":
            res = send_to_painter("search_resources", {"query": args.arg})
        elif args.cli == "export":
            res = send_to_painter("export_textures", {"export_path": args.arg})
        elif args.cli == "auto-texture":
            print(
                auto_texture_and_export_pbr(
                    mesh_path=args.mesh,
                    export_dir=args.arg,
                    smart_material_query=args.material,
                )
            )
            return
        elif args.cli == "js":
            code = open(args.file, "r", encoding="utf-8").read() if args.file else args.arg
            res = send_to_painter("execute_js", {"code": code})
        elif args.cli == "py":
            code = open(args.file, "r", encoding="utf-8").read() if args.file else args.arg
            res = send_to_painter("execute_python", {"code": code})
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
