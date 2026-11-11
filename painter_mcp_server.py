"""
Adobe Substance 3D Painter MCP Server + Automated PBR Pipeline + Direct CLI Client
Connects MCP clients to the Substance 3D Painter Live Bridge (default 127.0.0.1:8768) on Windows and macOS.
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP

PAINTER_HOST = os.environ.get("PAINTER_MCP_HOST", "127.0.0.1")
PAINTER_PORT = int(os.environ.get("PAINTER_MCP_PORT", "8768"))

mcp = FastMCP("substance-painter-mcp")


def discover_painter_executable() -> str:
    """Auto-discover Adobe Substance 3D Painter across Windows (Adobe CC & Steam) and macOS."""
    env_exe = os.environ.get("PAINTER_EXE", "").strip()
    if env_exe and os.path.exists(env_exe):
        return env_exe

    system = platform.system()
    candidates: List[str] = []
    if system == "Windows":
        prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        prog_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates.extend(
            [
                os.path.join(prog_files, "Adobe", "Adobe Substance 3D Painter", "Adobe Substance 3D Painter.exe"),
                os.path.join(prog_x86, "Steam", "steamapps", "common", "Substance 3D Painter 2025", "Adobe Substance 3D Painter.exe"),
                os.path.join(prog_x86, "Steam", "steamapps", "common", "Substance 3D Painter 2024", "Adobe Substance 3D Painter.exe"),
            ]
        )
    elif system == "Darwin":
        candidates.append("/Applications/Adobe Substance 3D Painter/Adobe Substance 3D Painter.app/Contents/MacOS/Adobe Substance 3D Painter")

    for path in candidates:
        if os.path.exists(path):
            return path
    return env_exe or r"C:\Program Files\Adobe\Adobe Substance 3D Painter\Adobe Substance 3D Painter.exe"


def is_painter_bridge_alive(timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((PAINTER_HOST, PAINTER_PORT), timeout=timeout) as sock:
            sock.sendall(b'{"command": "ping", "params": {}}\n')
            data = sock.recv(4096)
            return b"ok" in data or b"success" in data
    except Exception:
        return False


def ensure_painter_running(wait_seconds: int = 75) -> Dict[str, Any]:
    """Ensure Adobe Substance 3D Painter is running and its TCP bridge is active."""
    if is_painter_bridge_alive():
        return {"status": "ok", "message": f"Substance 3D Painter is already running on {PAINTER_HOST}:{PAINTER_PORT}."}

    painter_exe = discover_painter_executable()
    if not os.path.exists(painter_exe):
        return {
            "status": "error",
            "message": f"Substance 3D Painter executable not found at '{painter_exe}'. Set PAINTER_EXE environment variable.",
        }

    already_starting = False
    if platform.system() == "Windows":
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
        task_name = os.environ.get("PAINTER_LAUNCH_TASK", "").strip()
        if task_name and platform.system() == "Windows":
            subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True, text=True, timeout=10)
        else:
            try:
                if platform.system() == "Windows":
                    os.startfile(painter_exe)  # type: ignore[attr-defined]
                else:
                    subprocess.Popen([painter_exe])
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
            "message": f"Could not connect to Substance 3D Painter on {PAINTER_HOST}:{PAINTER_PORT} ({e}). Ensure painter_bridge.py is installed in Painter's python/startup folder.",
        }


@mcp.tool()
def launch_painter(wait_seconds: int = 75) -> str:
    """Launch Adobe Substance 3D Painter if not already running and wait until the live TCP bridge is ready."""
    return json.dumps(ensure_painter_running(wait_seconds=wait_seconds), ensure_ascii=False, indent=2)


@mcp.tool()
def ping_painter(auto_launch: bool = False) -> str:
    """Check if Adobe Substance 3D Painter live bridge is connected."""
    return json.dumps(send_to_painter("ping", auto_launch=auto_launch), ensure_ascii=False, indent=2)


@mcp.tool()
def get_project_info() -> str:
    """Get full details (Texture Sets, resolutions, channels, stack layers) of the open Substance 3D Painter project."""
    return json.dumps(send_to_painter("get_project_info"), ensure_ascii=False, indent=2)


@mcp.tool()
def open_or_create_project(
    project_path: str = "",
    mesh_path: str = "",
    resolution: int = 2048,
    normal_map_format: str = "DirectX",
    close_existing: bool = True,
) -> str:
    """Open an existing .spp project OR create a new Painter project from a 3D mesh (.fbx, .obj, .usd, .gltf). Use DirectX for Unreal Engine, OpenGL for Unity/Blender."""
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
    """Search Substance 3D Painter shelves for Smart Materials, Materials, Smart Masks, Filters, or Generators."""
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
    """Create a fill, paint, or smart_material layer in the active or specified Texture Set."""
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
    """Add a black/white mask, smart mask, generator (e.g. Curvature/Dirt), or filter to a layer."""
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
    """Export project textures using a preset (e.g. 'Unreal Engine 4 (Packed)', 'Unity HD Render Pipeline (Metallic Standard)', 'PBR Metallic Roughness')."""
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
    target_engine: str = "unreal",
    high_poly_mesh_path: str = "",
    resolution: int = 2048,
    save_spp_path: str = "",
) -> str:
    """End-to-end automated Painter pipeline: create project, optionally bake High-to-Low mesh maps, apply Smart Material, and export engine-packed PBR maps."""
    engine_key = target_engine.lower().strip()
    if engine_key in ("unreal", "ue5", "ue4"):
        normal_fmt = "DirectX"
        export_preset = "Unreal Engine 4 (Packed)"
    elif engine_key in ("unity", "urp", "hdrp"):
        normal_fmt = "OpenGL"
        export_preset = "Unity HD Render Pipeline (Metallic Standard)"
    else:
        normal_fmt = "OpenGL"
        export_preset = "PBR Metallic Roughness"

    steps = []
    p1 = send_to_painter(
        "open_or_create_project",
        {
            "mesh_path": mesh_path,
            "resolution": resolution,
            "normal_map_format": normal_fmt,
            "close_existing": True,
        },
    )
    steps.append({"step": "open_or_create_project", "result": p1})
    if p1.get("status") != "ok":
        return json.dumps({"status": "error", "steps": steps}, ensure_ascii=False, indent=2)

    if high_poly_mesh_path and os.path.exists(high_poly_mesh_path):
        hp_norm = high_poly_mesh_path.replace("\\", "/")
        bake_py = f"""
import substance_painter.baking as baking
import substance_painter.textureset as ts

for tex_set in ts.all_texture_sets():
    params = baking.BakingParameters.from_texture_set(tex_set)
    common = params.common()
    if 'HipolyMesh' in common:
        baking.BakingParameters.set({{common['HipolyMesh']: ['file:///{hp_norm}']}})
baking.bake_selected_textures()
result = {{"baked_from": "{hp_norm}"}}
"""
        p_bake = send_to_painter("execute_python", {"code": bake_py}, timeout=240.0)
        steps.append({"step": "bake_high_to_low_maps", "result": p_bake})

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
            "preset_name": export_preset,
            "file_format": "png",
            "bit_depth": "8",
            "resolution": resolution,
        },
    )
    steps.append({"step": "export_textures", "preset": export_preset, "result": p4})
    return json.dumps({"status": "ok", "target_engine": target_engine, "steps": steps}, ensure_ascii=False, indent=2)


@mcp.tool()
def execute_js_in_painter(code: str) -> str:
    """Execute JavaScript code inside Substance 3D Painter's alg.* JS API engine."""
    return json.dumps(send_to_painter("execute_js", {"code": code}), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_python_in_painter(code: str) -> str:
    """Execute Python code directly inside Substance 3D Painter's substance_painter.* Python API."""
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
    parser.add_argument("--engine", type=str, default="unreal", help="Target engine ('unreal', 'unity', 'pbr') for --cli auto-texture")
    parser.add_argument("--file", type=str, default="", help="Path to script file for --cli js or py")
    args, _ = parser.parse_known_args()

    if args.cli:
        if args.cli == "launch":
            res = ensure_painter_running()
        elif args.cli == "ping":
            res = send_to_painter("ping", auto_launch=False)
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
                    target_engine=args.engine,
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
