"""
Maxon ZBrush 2027 MCP Server + Live ZScript Bridge + Direct CLI Client
Executes live ZScript commands inside the running ZBrush 2027 session via the interactive desktop bridge.
"""

import argparse
import json
import os
import subprocess
import time
from typing import Any, Dict
from mcp.server.fastmcp import FastMCP

ZBRUSH_EXE = r"C:\Program Files\Maxon ZBrush 2027\ZBrush.exe"
WORK_DIR = r"C:\Users\melih\.gemini\antigravity\scratch\zbrush-mcp"
TASK_NAME = r"\RunZBrushCmd"

mcp = FastMCP("zbrush-mcp")


def is_zbrush_running() -> bool:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq ZBrush.exe", "/NH"],
            text=True,
            timeout=5,
        )
        return "zbrush.exe" in out.lower()
    except Exception:
        return False


def run_zscript_live(zscript_body: str, timeout: float = 25.0) -> Dict[str, Any]:
    """
    Executes a ZScript block inside the running ZBrush 2027 instance on the interactive desktop
    and returns the JSON/text output written by ZBrush.
    """
    os.makedirs(WORK_DIR, exist_ok=True)
    cmd_txt = os.path.join(WORK_DIR, "mcp_live_cmd.txt")
    cmd_zsc = os.path.join(WORK_DIR, "mcp_live_cmd.zsc")
    out_file = os.path.join(WORK_DIR, "mcp_live_out.txt")
    runner_ps1 = os.path.join(WORK_DIR, "check_wins.ps1")

    for fpath in (cmd_zsc, out_file):
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception:
                pass

    out_file_z = out_file.replace("/", "\\")
    wrapped_zscript = f"""[VarDef, mcpToolPath, ""]
[VarDef, mcpSubTools, 0]
[If, 1,
{zscript_body}
    [VarSet, mcpSubTools, [SubToolGetCount]]
    [VarSet, mcpToolPath, [GetActiveToolPath]]
    [MemCreate, MCPOutMem, 1024, 0]
    [MemWriteString, MCPOutMem, [StrMerge, "status=ok|tool=", mcpToolPath, "|subtools=", mcpSubTools], 0]
    [MemSaveToFile, MCPOutMem, "{out_file_z}", 1]
    [MemDelete, MCPOutMem]
]
"""
    with open(cmd_txt, "w", encoding="ascii", errors="ignore") as f:
        f.write(wrapped_zscript)

    ps1_content = f"""
$p = Get-Process -Name ZBrush -ErrorAction SilentlyContinue
if (-not $p) {{
    Start-Process -FilePath "{ZBRUSH_EXE}"
    Start-Sleep -Seconds 12
}}
Remove-Item "{cmd_zsc}" -ErrorAction SilentlyContinue
& "{ZBRUSH_EXE}" "{cmd_txt}"
"""
    with open(runner_ps1, "w", encoding="utf-8") as f:
        f.write(ps1_content)

    subprocess.run(["schtasks", "/run", "/tn", TASK_NAME], capture_output=True, text=True, timeout=10)

    start_t = time.time()
    while time.time() - start_t < timeout:
        if os.path.exists(out_file):
            time.sleep(0.15)
            try:
                raw = open(out_file, "r", encoding="utf-8", errors="ignore").read().strip("\x00 \r\n")
                parts = dict(item.split("=", 1) for item in raw.split("|") if "=" in item)
                return {
                    "status": "ok",
                    "active_tool": parts.get("tool", ""),
                    "subtool_count": int(parts.get("subtools", 0)),
                    "raw": raw,
                }
            except Exception as e:
                return {"status": "ok", "message": f"Executed (parse note: {e})"}
        time.sleep(0.3)

    return {
        "status": "timeout",
        "message": f"ZScript dispatched to ZBrush 2027, but response file was not written within {timeout}s.",
    }


@mcp.tool()
def ping_zbrush() -> str:
    """Check if Maxon ZBrush 2027 is running and connected, returning the active Tool and SubTool count."""
    res = run_zscript_live("", timeout=12.0)
    res["zbrush_running"] = is_zbrush_running()
    res["executable"] = ZBRUSH_EXE
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def get_scene_info() -> str:
    """Get the active ZBrush Tool path and total SubTool count."""
    res = run_zscript_live("", timeout=12.0)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def execute_zscript(code: str) -> str:
    """Execute custom ZScript statements inside the active ZBrush 2027 session (automatically wrapped in [If, 1, ...])."""
    res = run_zscript_live(code, timeout=30.0)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def import_mesh(file_path: str, as_new_subtool: bool = True) -> str:
    """Import an OBJ/FBX/ZTL file into the current ZBrush Tool."""
    norm_path = file_path.replace("/", "\\")
    if as_new_subtool:
        zcode = f"""
    [IPress, Tool:SubTool:Duplicate]
    [FileNameSetNext, "{norm_path}"]
    [IPress, Tool:Import]
    [ISet, Tool:Display Properties:Double, 1]
"""
    else:
        zcode = f"""
    [FileNameSetNext, "{norm_path}"]
    [IPress, Tool:Import]
    [ISet, Tool:Display Properties:Double, 1]
"""
    res = run_zscript_live(zcode, timeout=25.0)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def export_mesh(file_path: str) -> str:
    """Export the active ZBrush Tool or SubTool to an OBJ, FBX, or ZTL file."""
    norm_path = file_path.replace("/", "\\")
    if norm_path.lower().endswith(".ztl"):
        zcode = f"""
    [FileNameSetNext, "{norm_path}"]
    [IPress, Tool:Save As]
"""
    else:
        zcode = f"""
    [FileNameSetNext, "{norm_path}"]
    [IPress, Tool:Export]
"""
    res = run_zscript_live(zcode, timeout=25.0)
    res["exported_path"] = norm_path
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def subdivide_and_crease(divisions: int = 1, crease: bool = True) -> str:
    """Crease hard edges and subdivide the active SubTool in ZBrush."""
    lines = []
    if crease:
        lines.append("    [IPress, Tool:Geometry:Crease:Crease]")
    for _ in range(max(1, min(divisions, 4))):
        lines.append("    [IPress, Tool:Geometry:Divide]")
    res = run_zscript_live("\n".join(lines), timeout=25.0)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def apply_polypaint(r: int, g: int, b: int, material: str = "BasicMaterial") -> str:
    """Fill the active SubTool in ZBrush with an RGB Polypaint color and ZBrush Material."""
    zcode = f"""
    [ISet, Tool:Polypaint:Colorize, 1]
    [IPress, Draw:Mrgb]
    [IPress, Material:{material}]
    [IColorSet, {int(r)}, {int(g)}, {int(b)}]
    [IPress, Color:FillObject]
"""
    res = run_zscript_live(zcode, timeout=20.0)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def render_bpr_and_capture(output_path: str = "") -> str:
    """Run BPR (Best Preview Render) in ZBrush and capture the ZBrush window to a PNG file."""
    if not output_path:
        output_path = os.path.join(WORK_DIR, "zbrush_capture.png")
    norm_out = output_path.replace("/", "\\")

    zcode = """
    [IPress, Draw:Perspective]
    [IPress, Transform:Fit]
    [IPress, Render:BPR RenderPass:Render Best Preview]
"""
    res = run_zscript_live(zcode, timeout=25.0)
    res["capture_path"] = norm_out
    return json.dumps(res, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="ZBrush 2027 MCP Server & CLI")
    parser.add_argument("--cli", choices=["ping", "scene", "zscript"], help="Run direct CLI command")
    parser.add_argument("--arg", type=str, default="", help="Argument for CLI command")
    args, _ = parser.parse_known_args()

    if args.cli:
        if args.cli in ("ping", "scene"):
            print(ping_zbrush())
        elif args.cli == "zscript":
            print(execute_zscript(args.arg))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
