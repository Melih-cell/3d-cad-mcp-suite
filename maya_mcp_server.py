"""
Autodesk Maya 2027 MCP Server + Direct CLI Client
Connects to the Maya Live Bridge on 127.0.0.1:8767.
"""

import argparse
import json
import socket
import sys
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP

MAYA_HOST = "127.0.0.1"
MAYA_PORT = 8767

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
            "message": f"Could not connect to Maya on {MAYA_HOST}:{MAYA_PORT} ({e}). Make sure Maya 2027 is running and maya_bridge.py is active.",
        }


@mcp.tool()
def ping_maya() -> str:
    """Check if Autodesk Maya live bridge is connected and return current version, units, and scene file."""
    return json.dumps(send_to_maya("ping"), ensure_ascii=False, indent=2)


@mcp.tool()
def get_scene_info(include_defaults: bool = False) -> str:
    """List all objects in the current Maya scene with transforms, bounding boxes, types, and hierarchy."""
    return json.dumps(send_to_maya("get_scene_info", {"include_defaults": include_defaults}), ensure_ascii=False, indent=2)


@mcp.tool()
def get_object_details(name: str) -> str:
    """Get detailed properties, transforms, bounding box, mesh stats, materials, history, and attributes of a Maya object by name."""
    return json.dumps(send_to_maya("get_object_details", {"name": name}), ensure_ascii=False, indent=2)


@mcp.tool()
def create_primitive(
    primitive_type: str,
    name: str = "",
    position: List[float] = [0.0, 0.0, 0.0],
    rotation: List[float] = [0.0, 0.0, 0.0],
    scale: List[float] = [1.0, 1.0, 1.0],
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a Maya primitive or object (cube, sphere, cylinder, cone, plane, torus, locator, joint, camera, pointLight, directionalLight, areaLight)."""
    return json.dumps(
        send_to_maya(
            "create_primitive",
            {
                "primitive_type": primitive_type,
                "name": name,
                "position": position,
                "rotation": rotation,
                "scale": scale,
                "params": params or {},
            },
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def capture_viewport(output_path: str = "", width: int = 1280, height: int = 720) -> str:
    """Capture the active Maya 3D viewport to a PNG image file and return the file path."""
    return json.dumps(
        send_to_maya(
            "capture_viewport",
            {"output_path": output_path, "width": width, "height": height},
        ),
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
    parser.add_argument("--cli", choices=["ping", "scene", "object", "mel", "py", "capture"], help="Run a direct CLI command instead of starting stdio MCP server")
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
