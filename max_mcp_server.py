"""
Autodesk 3ds Max MCP Server + Auto-Launcher + Game-Ready Asset Pipeline
Connects MCP clients to the 3ds Max Live Bridge (default 127.0.0.1:8765) across 3ds Max 2022–2027.
"""

import argparse
import glob
import json
import os
import socket
import subprocess
import time
from typing import Any, Dict, List
from mcp.server.fastmcp import FastMCP

MAX_HOST = os.environ.get("MAX_MCP_HOST", "127.0.0.1")
MAX_PORT = int(os.environ.get("MAX_MCP_PORT", "8765"))

mcp = FastMCP("3dsmax-mcp")


def discover_3dsmax_executable() -> str:
    """Auto-discover Autodesk 3ds Max (2022–2027) on Windows or use MAX_EXE_PATH override."""
    env_exe = os.environ.get("MAX_EXE_PATH", "").strip()
    if env_exe and os.path.exists(env_exe):
        return env_exe

    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates: List[str] = []
    for year in ("2027", "2026", "2025", "2024", "2023", "2022"):
        candidates.append(os.path.join(prog_files, "Autodesk", f"3ds Max {year}", "3dsmax.exe"))
    candidates.extend(sorted(glob.glob(os.path.join(prog_files, "Autodesk", "3ds Max*", "3dsmax.exe")), reverse=True))

    for path in candidates:
        if os.path.exists(path):
            return path
    return env_exe or r"C:\Program Files\Autodesk\3ds Max 2027\3dsmax.exe"


def is_max_bridge_online(timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((MAX_HOST, MAX_PORT), timeout=timeout):
            return True
    except Exception:
        return False


def ensure_3dsmax_running(max_wait_seconds: int = 90) -> Dict[str, Any]:
    """Ensure Autodesk 3ds Max is running and its live TCP bridge is online."""
    if is_max_bridge_online():
        return {"status": "online", "message": f"3ds Max bridge is already online on {MAX_HOST}:{MAX_PORT}."}

    max_exe = discover_3dsmax_executable()
    if not os.path.exists(max_exe):
        return {
            "status": "error",
            "message": f"3ds Max executable not found at '{max_exe}'. Set MAX_EXE_PATH environment variable.",
        }

    try:
        out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq 3dsmax.exe"], text=True, timeout=5)
        already_starting = "3dsmax.exe" in out.lower()
    except Exception:
        already_starting = False

    if not already_starting:
        task_name = os.environ.get("MAX_LAUNCH_TASK", "").strip()
        if task_name:
            subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True, text=True, timeout=10)
        else:
            try:
                os.startfile(max_exe)  # type: ignore[attr-defined]
            except Exception:
                subprocess.Popen([max_exe], cwd=os.path.dirname(max_exe))

    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        if is_max_bridge_online(timeout=2.0):
            time.sleep(1.0)
            return {
                "status": "online",
                "message": f"3ds Max launched and connected in {int(time.time() - start_time)}s.",
            }
        time.sleep(2.0)

    return {
        "status": "timeout",
        "message": f"3ds Max was started, but bridge port {MAX_PORT} did not respond within {max_wait_seconds}s.",
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

    extra_py_path = os.environ.get("MAX_EXTRA_PYTHONPATH", "").strip()
    if command == "execute_python" and params and "code" in params and extra_py_path:
        preamble = (
            "import sys as _sys\n"
            f"if {repr(extra_py_path)} not in _sys.path:\n"
            f"    _sys.path.insert(0, {repr(extra_py_path)})\n"
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
    """Launch Autodesk 3ds Max if not already open and wait until the live bridge is ready."""
    res = ensure_3dsmax_running(max_wait_seconds=90)
    if res.get("status") == "online":
        ping_res = send_to_max("ping", auto_launch=False)
        return json.dumps({"launch": res, "ping": ping_res}, ensure_ascii=False, indent=2)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def ping_3dsmax() -> str:
    """Check if 3ds Max live bridge is connected and return current version and scene file."""
    return json.dumps(send_to_max("ping", auto_launch=False), ensure_ascii=False, indent=2)


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
    """Execute MAXScript code directly inside 3ds Max (includes Undo support and viewport redraw)."""
    return json.dumps(send_to_max("execute_maxscript", {"code": code}, auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_python_in_max(code: str) -> str:
    """Execute Python (pymxs) code directly inside 3ds Max. Set `result = ...` or print to stdout to return data."""
    return json.dumps(send_to_max("execute_python", {"code": code}, auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_script_file_in_max(file_path: str) -> str:
    """Execute a local .py (pymxs) or .ms (MAXScript) file directly inside 3ds Max."""
    if not os.path.exists(file_path):
        return json.dumps({"status": "error", "message": f"Script file not found: {file_path}"})
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()
    if file_path.lower().endswith(".ms"):
        return json.dumps(send_to_max("execute_maxscript", {"code": code}, auto_launch=True), ensure_ascii=False, indent=2)
    return json.dumps(send_to_max("execute_python", {"code": code}, auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def validate_scene_for_engine(prefix_filter: str = "") -> str:
    """Audit 3ds Max geometry for game-engine readiness (non-uniform scale / XForm issues, missing UV Channel 1, and _high/_low naming)."""
    py_code = f"""
import pymxs
rt = pymxs.runtime

issues = []
checked = 0
for o in list(rt.objects):
    if not (rt.isValidNode(o) and rt.superClassOf(o) == rt.GeometryClass):
        continue
    name = str(o.name)
    if {repr(prefix_filter)} and not name.startswith({repr(prefix_filter)}):
        continue
    checked += 1
    sc = o.scale
    if abs(sc.x - 1.0) > 1e-3 or abs(sc.y - 1.0) > 1e-3 or abs(sc.z - 1.0) > 1e-3:
        issues.append({{"object": name, "issue": "non_unit_scale", "scale": [float(sc.x), float(sc.y), float(sc.z)], "fix": "Apply Reset XForm and collapse stack"}})
    try:
        has_uv = bool(rt.meshop.getMapSupport(o.mesh, 1))
        if not has_uv:
            issues.append({{"object": name, "issue": "missing_uv_channel_1", "fix": "Add Unwrap UVW or UVW Map modifier"}})
    except Exception:
        pass

result = {{
    "status": "pass" if not issues else "warnings",
    "checked_objects": checked,
    "issue_count": len(issues),
    "issues": issues,
}}
"""
    return json.dumps(send_to_max("execute_python", {"code": py_code}, auto_launch=True), ensure_ascii=False, indent=2)


@mcp.tool()
def prepare_and_export_for_painter(
    fbx_output_path: str,
    prefix_filter: str = "",
    auto_unwrap_uvs: bool = True,
    reset_xform: bool = True,
) -> str:
    """Prepare 3ds Max scene meshes (optional Reset XForm + automatic Box/Flatten UVW mapping) and export FBX ready for Substance 3D Painter."""
    fbx_norm = fbx_output_path.replace("\\", "/")
    py_code = f"""
import os, pymxs
rt = pymxs.runtime

target_objs = []
for o in list(rt.objects):
    if rt.isValidNode(o) and rt.superClassOf(o) == rt.GeometryClass:
        if not {repr(prefix_filter)} or str(o.name).startswith({repr(prefix_filter)}):
            target_objs.append(o)

if not target_objs:
    result = {{"status": "error", "message": "No matching geometry objects found in scene."}}
else:
    for o in target_objs:
        if {repr(reset_xform)}:
            try:
                rt.ResetXForm(o)
                rt.convertToPoly(o)
            except Exception:
                pass
        if {repr(auto_unwrap_uvs)}:
            try:
                uv_mod = rt.UVWMap(maptype=4, length=50.0, width=50.0, height=50.0)
                rt.addModifier(o, uv_mod)
            except Exception:
                pass
    rt.select(target_objs)
    out_dir = os.path.dirname({repr(fbx_norm)})
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    rt.exportFile({repr(fbx_norm)}, rt.name("noPrompt"), selectedOnly=True, using=rt.FBXEXP)
    result = {{
        "status": "success",
        "exported_objects": len(target_objs),
        "fbx_path": {repr(fbx_norm)},
    }}
"""
    return json.dumps(send_to_max("execute_python", {"code": py_code}, auto_launch=True), ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Autodesk 3ds Max MCP Server & CLI")
    parser.add_argument(
        "--cli",
        choices=["launch", "ping", "scene", "object", "mxs", "py", "validate", "export-fbx"],
        help="Run a direct CLI command instead of starting stdio MCP server",
    )
    parser.add_argument("--arg", type=str, default="", help="Object name, output path, or inline code for --cli")
    parser.add_argument("--prefix", type=str, default="", help="Prefix filter for --cli validate or export-fbx")
    parser.add_argument("--file", type=str, default="", help="Path to script file for --cli mxs or py")
    args, _ = parser.parse_known_args()

    if args.cli:
        if args.cli == "launch":
            res = ensure_3dsmax_running()
        elif args.cli == "ping":
            res = send_to_max("ping", auto_launch=False)
        elif args.cli == "scene":
            res = send_to_max("get_scene_info", auto_launch=True)
        elif args.cli == "object":
            res = send_to_max("get_object_details", {"name": args.arg}, auto_launch=True)
        elif args.cli == "validate":
            print(validate_scene_for_engine(prefix_filter=args.prefix))
            return
        elif args.cli == "mxs":
            code = open(args.file, "r", encoding="utf-8").read() if args.file else args.arg
            res = send_to_max("execute_maxscript", {"code": code}, auto_launch=True)
        elif args.cli == "py":
            code = open(args.file, "r", encoding="utf-8").read() if args.file else args.arg
            res = send_to_max("execute_python", {"code": code}, auto_launch=True)
        elif args.cli == "export-fbx":
            print(prepare_and_export_for_painter(args.arg, prefix_filter=args.prefix))
            return
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
