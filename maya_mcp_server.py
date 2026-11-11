"""
Autodesk Maya MCP Server + Skeletal/UV Pipeline Validator + Direct CLI Client
Connects MCP clients to the Maya Live Bridge (default 127.0.0.1:8767) across Maya 2022–2027.
"""

import argparse
import json
import os
import socket
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP

MAYA_HOST = os.environ.get("MAYA_MCP_HOST", "127.0.0.1")
MAYA_PORT = int(os.environ.get("MAYA_MCP_PORT", "8767"))

mcp = FastMCP("maya-mcp")


def send_to_maya(command: str, params: Dict[str, Any] | None = None, timeout: float = 90.0) -> Dict[str, Any]:
    payload = {"command": command, "params": params or {}}
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with socket.create_connection((MAYA_HOST, MAYA_PORT), timeout=timeout) as sock:
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
            return {"status": "error", "message": "Empty response from Maya bridge."}
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not connect to Maya on {MAYA_HOST}:{MAYA_PORT} ({e}). Ensure Maya is running and maya_bridge.py is active.",
        }


@mcp.tool()
def ping_maya() -> str:
    """Check if Autodesk Maya live bridge is connected and return current version, linear units, and scene file."""
    return json.dumps(send_to_maya("ping"), ensure_ascii=False, indent=2)


@mcp.tool()
def get_scene_info(include_defaults: bool = False) -> str:
    """List all objects in the current Maya scene with transforms, bounding boxes, types, and hierarchy."""
    return json.dumps(send_to_maya("get_scene_info", {"include_defaults": include_defaults}), ensure_ascii=False, indent=2)


@mcp.tool()
def get_object_details(name: str) -> str:
    """Get detailed properties, transforms, bounding box, mesh stats, materials, history, and attributes of a Maya object."""
    return json.dumps(send_to_maya("get_object_details", {"name": name}), ensure_ascii=False, indent=2)


@mcp.tool()
def create_primitive(
    primitive_type: str,
    name: str = "",
    position: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    scale: Optional[List[float]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a Maya primitive or node (cube, sphere, cylinder, cone, plane, torus, locator, joint, camera, pointLight, directionalLight, areaLight)."""
    return json.dumps(
        send_to_maya(
            "create_primitive",
            {
                "primitive_type": primitive_type,
                "name": name,
                "position": position or [0.0, 0.0, 0.0],
                "rotation": rotation or [0.0, 0.0, 0.0],
                "scale": scale or [1.0, 1.0, 1.0],
                "params": params or {},
            },
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def validate_skeletal_asset(mesh_name: str, max_influences: int = 4, required_uv_set: str = "map1") -> str:
    """Audit a Maya mesh/rig for Unreal Engine 5 & Unity compliance (frozen transforms, UV set name, root joint at origin, max bone influences per vertex)."""
    py_code = f"""
import maya.cmds as cmds

mesh = {repr(mesh_name)}
issues = []

if not cmds.objExists(mesh):
    result = {{"status": "error", "message": f"Mesh '{{mesh}}' does not exist."}}
else:
    tr = cmds.xform(mesh, q=True, t=True, ws=True)
    ro = cmds.xform(mesh, q=True, ro=True, ws=True)
    sc = cmds.xform(mesh, q=True, r=True, s=True)
    if any(abs(v) > 1e-4 for v in tr + ro) or any(abs(v - 1.0) > 1e-4 for v in sc):
        issues.append({{"check": "frozen_transforms", "status": "fail", "translate": tr, "rotate": ro, "scale": sc}})

    uv_sets = cmds.polyUVSet(mesh, q=True, allUVSets=True) or []
    if {repr(required_uv_set)} not in uv_sets:
        issues.append({{"check": "uv_set", "status": "fail", "found_uv_sets": uv_sets, "expected": {repr(required_uv_set)}}})

    hist = cmds.listHistory(mesh, pdo=True) or []
    skins = cmds.ls(hist, type="skinCluster") or []
    excess_verts = 0
    if skins:
        sc_node = skins[0]
        v_count = cmds.polyEvaluate(mesh, v=True)
        for idx in range(min(v_count, 5000)):
            weights = cmds.skinPercent(sc_node, f"{{mesh}}.vtx[{{idx}}]", q=True, value=True) or []
            active = sum(1 for w in weights if w > 1e-4)
            if active > {int(max_influences)}:
                excess_verts += 1
        if excess_verts > 0:
            issues.append({{"check": "max_skin_influences", "status": "fail", "vertices_exceeding_limit": excess_verts, "limit": {int(max_influences)}}})

    linear_unit = cmds.currentUnit(q=True, linear=True)
    if linear_unit != "cm":
        issues.append({{"check": "scene_units", "status": "warning", "unit": linear_unit, "recommended": "cm"}})

    result = {{
        "status": "pass" if not issues else "issues_found",
        "mesh": mesh,
        "uv_sets": uv_sets,
        "skin_clusters": skins,
        "linear_unit": linear_unit,
        "issues": issues,
    }}
"""
    return json.dumps(send_to_maya("execute_python", {"code": py_code}), ensure_ascii=False, indent=2)


@mcp.tool()
def export_fbx_for_pipeline(
    fbx_output_path: str,
    selection_names: Optional[List[str]] = None,
    target_profile: str = "unreal",
    bake_animation: bool = False,
) -> str:
    """Export Maya meshes/skeletons to FBX using clean game-ready presets ('unreal', 'unity', or 'painter')."""
    fbx_norm = fbx_output_path.replace("\\", "/")
    sel_list = selection_names or []
    py_code = f"""
import os
import maya.cmds as cmds
import maya.mel as mel

if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
    cmds.loadPlugin("fbxmaya")

targets = {repr(sel_list)}
if targets:
    cmds.select(targets, replace=True)

out_path = {repr(fbx_norm)}
out_dir = os.path.dirname(out_path)
if out_dir:
    os.makedirs(out_dir, exist_ok=True)

mel.eval("FBXResetExport;")
mel.eval("FBXExportSmoothingGroups -v true;")
mel.eval("FBXExportTangents -v true;")
mel.eval("FBXExportTriangulate -v " + ("true" if {repr(target_profile)} == "painter" else "false") + ";")
mel.eval("FBXExportSkins -v " + ("false" if {repr(target_profile)} == "painter" else "true") + ";")
mel.eval("FBXExportBakeComplexAnimation -v " + ("true" if {repr(bake_animation)} else "false") + ";")
mel.eval(f'FBXExport -f "{{out_path}}" -s;')

result = {{
    "status": "success",
    "fbx_path": out_path,
    "target_profile": {repr(target_profile)},
}}
"""
    return json.dumps(send_to_maya("execute_python", {"code": py_code}), ensure_ascii=False, indent=2)


@mcp.tool()
def capture_viewport(output_path: str = "", width: int = 1280, height: int = 720) -> str:
    """Capture the active Maya 3D viewport to a PNG image file and return the file path."""
    return json.dumps(
        send_to_maya("capture_viewport", {"output_path": output_path, "width": width, "height": height}),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def execute_mel(code: str) -> str:
    """Execute MEL script code directly inside Autodesk Maya (with Undo support and viewport refresh)."""
    return json.dumps(send_to_maya("execute_mel", {"code": code}), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_python_in_maya(code: str) -> str:
    """Execute Python code (with cmds, mel, om pre-imported) directly inside Maya. Set `result = ...` or print to stdout to return data."""
    return json.dumps(send_to_maya("execute_python", {"code": code}), ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Autodesk Maya MCP Server & CLI")
    parser.add_argument(
        "--cli",
        choices=["ping", "scene", "object", "mel", "py", "capture", "validate"],
        help="Run a direct CLI command instead of starting stdio MCP server",
    )
    parser.add_argument("--arg", type=str, default="", help="Object name, output path, or inline code for --cli")
    parser.add_argument("--file", type=str, default="", help="Path to script file for --cli mel or py")
    args, _ = parser.parse_known_args()

    if args.cli:
        if args.cli == "ping":
            res = send_to_maya("ping")
        elif args.cli == "scene":
            res = send_to_maya("get_scene_info")
        elif args.cli == "object":
            res = send_to_maya("get_object_details", {"name": args.arg})
        elif args.cli == "validate":
            print(validate_skeletal_asset(args.arg))
            return
        elif args.cli == "capture":
            res = send_to_maya("capture_viewport", {"output_path": args.arg})
        elif args.cli == "mel":
            code = open(args.file, "r", encoding="utf-8").read() if args.file else args.arg
            res = send_to_maya("execute_mel", {"code": code})
        elif args.cli == "py":
            code = open(args.file, "r", encoding="utf-8").read() if args.file else args.arg
            res = send_to_maya("execute_python", {"code": code})
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
