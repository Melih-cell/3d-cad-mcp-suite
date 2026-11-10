"""
3ds Max 2027 MCP Server + Auto-Launcher + Game-Ready Asset Pipeline
Connects to the 3ds Max Live Bridge on 127.0.0.1:8765 and automatically launches 3ds Max 2027 if closed.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict
from mcp.server.fastmcp import FastMCP

MAX_HOST = "127.0.0.1"
MAX_PORT = 8765
MAX_EXE_PATH = r"C:\Program Files\Autodesk\3ds Max 2027\3dsmax.exe"

mcp = FastMCP("3dsmax-mcp")


def is_max_bridge_online(timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((MAX_HOST, MAX_PORT), timeout=timeout):
            return True
    except Exception:
        return False


def ensure_3dsmax_running(max_wait_seconds: int = 90) -> Dict[str, Any]:
    """Ensure 3ds Max 2027 is running and the live bridge on port 8765 is online."""
    if is_max_bridge_online():
        return {"status": "online", "message": "3ds Max 2027 bridge is already online."}

    if not os.path.exists(MAX_EXE_PATH):
        return {"status": "error", "message": f"3ds Max executable not found at {MAX_EXE_PATH}"}

    # Check if 3dsmax.exe process is already starting up
    try:
        out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq 3dsmax.exe"], text=True)
        already_starting = "3dsmax.exe" in out.lower()
    except Exception:
        already_starting = False

    if not already_starting:
        try:
            subprocess.run(["schtasks", "/run", "/tn", "Launch3dsMax2027"], capture_output=True, text=True, timeout=10)
        except Exception:
            try:
                os.startfile(MAX_EXE_PATH)
            except Exception:
                subprocess.Popen(["cmd.exe", "/c", "start", "", MAX_EXE_PATH], cwd=os.path.dirname(MAX_EXE_PATH))

    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        if is_max_bridge_online(timeout=2.0):
            time.sleep(1.0)
            return {
                "status": "online",
                "message": f"3ds Max 2027 launched and connected in {int(time.time() - start_time)}s.",
            }
        time.sleep(2.0)

    return {
        "status": "timeout",
        "message": f"3ds Max 2027 was started, but bridge port {MAX_PORT} did not respond within {max_wait_seconds}s.",
    }


def send_to_max(
    command: str,
    params: Dict[str, Any] | None = None,
    timeout: float = 120.0,
    auto_launch: bool = True,
) -> Dict[str, Any]:
    if auto_launch and not is_max_bridge_online():
        launch_res = ensure_3dsmax_running(max_wait_seconds=90)
        if launch_res.get("status") != "online":
            return launch_res

    if command == "execute_python" and params and "code" in params:
        preamble = (
            "import sys as _sys, importlib as _importlib\n"
            "if r'C:\\Users\\melih\\Downloads\\3ds Max Antigravity_Project' not in _sys.path:\n"
            "    _sys.path.insert(0, r'C:\\Users\\melih\\Downloads\\3ds Max Antigravity_Project')\n"
            "if 'ultra_3d_engine' in _sys.modules:\n"
            "    _importlib.reload(_sys.modules['ultra_3d_engine'])\n"
            "__name__ = '__main__'\n"
        )
        params = dict(params)
        params["code"] = preamble + "\n" + str(params["code"])

    payload = {"command": command, "params": params or {}}
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with socket.create_connection((MAX_HOST, MAX_PORT), timeout=timeout) as sock:
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
            return {"status": "error", "message": "Empty response from 3ds Max bridge."}
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not connect to 3ds Max on {MAX_HOST}:{MAX_PORT} ({e}).",
        }


@mcp.tool()
def launch_3dsmax() -> str:
    """Launch Autodesk 3ds Max 2027 if not already open and wait until the live bridge (port 8765) is ready."""
    res = ensure_3dsmax_running(max_wait_seconds=90)
    if res.get("status") == "online":
        ping_res = send_to_max("ping", auto_launch=False)
        return json.dumps({"launch": res, "ping": ping_res}, ensure_ascii=False, indent=2)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def ping_3dsmax() -> str:
    """Check if 3ds Max live bridge is connected (auto-launches 3ds Max 2027 if closed) and return current version and scene file."""
    return json.dumps(send_to_max("ping", auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def get_scene_info() -> str:
    """List all objects in the current 3ds Max scene with positions, bounding boxes, and modifiers."""
    return json.dumps(send_to_max("get_scene_info", auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def get_object_details(name: str) -> str:
    """Get detailed properties, position, pivot, bounding box, mesh stats, and modifiers of a 3ds Max object by name."""
    return json.dumps(send_to_max("get_object_details", {"name": name}, auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_maxscript(code: str) -> str:
    """Execute MaxScript code directly inside 3ds Max (auto-launches 3ds Max if closed, includes Undo support and viewport redraw)."""
    return json.dumps(send_to_max("execute_maxscript", {"code": code}, auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_python_in_max(code: str) -> str:
    """Execute Python (pymxs) code directly inside 3ds Max (auto-launches 3ds Max if closed). Set `result = ...` or print to stdout to return data."""
    return json.dumps(send_to_max("execute_python", {"code": code}, auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_script_file_in_max(file_path: str) -> str:
    """Execute a local .py (pymxs) or .ms (MaxScript) file directly inside 3ds Max 2027."""
    if not os.path.exists(file_path):
        if file_path.lower().endswith(".py"):
            auto_code = (
                "import ultra_3d_engine\n"
                f"ultra_3d_engine.build_from_keyword({repr(file_path)})\n"
            )
            try:
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(auto_code)
            except Exception:
                pass
            return json.dumps(send_to_max("execute_python", {"code": auto_code}, auto_launch=True), ensure_ascii=False, indent=2)
        return json.dumps({"status": "error", "message": f"File not found: {file_path}"})
    code = open(file_path, "r", encoding="utf-8").read()
    if file_path.lower().endswith(".ms"):
        return json.dumps(send_to_max("execute_maxscript", {"code": code}, auto_launch=True), ensure_ascii=False, indent=2)
    return json.dumps(send_to_max("execute_python", {"code": code}, auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def prepare_and_export_for_painter(
    fbx_output_path: str,
    prefix_filter: str = "",
    auto_unwrap_uvs: bool = True,
) -> str:
    """Prepare 3ds Max scene objects (optional prefix filter, automatic Flatten UVW Unwrap for clean texture baking/painting) and export as FBX for Substance 3D Painter."""
    fbx_norm = fbx_output_path.replace("\\", "/")
    py_code = f"""
import os, pymxs
rt = pymxs.runtime

target_objs = []
for o in list(rt.objects):
    if rt.isValidNode(o) and rt.superClassOf(o) == rt.GeometryClass:
        if not "{prefix_filter}" or str(o.name).startswith("{prefix_filter}"):
            target_objs.append(o)

if not target_objs:
    result = {{"status": "error", "message": "No matching geometry objects found in scene."}}
else:
    if {repr(auto_unwrap_uvs)}:
        # Apply clean UVW Map / Flatten UVW so Substance Painter has non-overlapping UVs
        for o in target_objs:
            try:
                uv_mod = rt.UVWMap(maptype=4, length=50.0, width=50.0, height=50.0)
                rt.addModifier(o, uv_mod)
            except Exception:
                pass
    rt.select(target_objs)
    os.makedirs(os.path.dirname("{fbx_norm}"), exist_ok=True)
    rt.exportFile("{fbx_norm}", rt.name("noPrompt"), selectedOnly=True, using=rt.FBXEXP)
    result = {{
        "status": "success",
        "exported_objects": len(target_objs),
        "fbx_path": "{fbx_norm}"
    }}
"""
    return json.dumps(send_to_max("execute_python", {"code": py_code}, auto_launch=True), ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="3ds Max MCP Server & CLI")
    parser.add_argument("--cli", choices=["launch", "ping", "scene", "object", "mxs", "py", "export-fbx"], help="Run a direct CLI command instead of starting stdio MCP server")
    parser.add_argument("--arg", type=str, default="", help="Object name, output path, or inline code for --cli")
    parser.add_argument("--prefix", type=str, default="", help="Prefix filter for --cli export-fbx")
    parser.add_argument("--file", type=str, default="", help="Path to script file for --cli mxs or py")
    args, _ = parser.parse_known_args()

    if args.cli:
        if args.cli == "launch":
            res = ensure_3dsmax_running()
        elif args.cli == "ping":
            res = send_to_max("ping", auto_launch=True)
        elif args.cli == "scene":
            res = send_to_max("get_scene_info", auto_launch=True)
        elif args.cli == "object":
            res = send_to_max("get_object_details", {"name": args.arg}, auto_launch=True)
        elif args.cli == "mxs":
            code = open(args.file, "r", encoding="utf-8").read() if args.file else args.arg
            res = send_to_max("execute_maxscript", {"code": code}, auto_launch=True)
        elif args.cli == "py":
            if args.file:
                if not os.path.exists(args.file):
                    code = (
                        "import ultra_3d_engine\n"
                        f"ultra_3d_engine.build_from_keyword({repr(args.file)})\n"
                    )
                    try:
                        os.makedirs(os.path.dirname(args.file), exist_ok=True)
                        with open(args.file, "w", encoding="utf-8") as f:
                            f.write(code)
                    except Exception:
                        pass
                else:
                    code = open(args.file, "r", encoding="utf-8").read()
            else:
                code = args.arg
            res = send_to_max("execute_python", {"code": code}, auto_launch=True)
        elif args.cli == "export-fbx":
            print(prepare_and_export_for_painter(args.arg, prefix_filter=args.prefix))
            return
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
