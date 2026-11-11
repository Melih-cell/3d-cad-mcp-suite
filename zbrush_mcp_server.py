"""
Maxon ZBrush MCP Server + Live ZScript Memory Bridge + Direct CLI Client
Bridges MCP-compatible AI clients (Claude Desktop, Claude Code, Cursor, VS Code)
with Maxon / Pixologic ZBrush (2022–2027) on Windows and macOS via ZScript [MemCreate] IPC.

Environment Variables:
  ZBRUSH_EXE       Optional explicit path to ZBrush executable
  ZBRUSH_WORK_DIR  Optional IPC scratch directory (defaults to system temp / zbrush_mcp_bridge)
  ZBRUSH_TASK_NAME Optional Windows Scheduled Task name if launching across isolated sessions
"""

import argparse
import glob
import json
import os
import platform
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("zbrush-mcp")


def discover_zbrush_executable() -> str:
    """Auto-discover Maxon or Pixologic ZBrush (2022–2027) across Windows and macOS."""
    env_exe = os.environ.get("ZBRUSH_EXE", "").strip()
    if env_exe and os.path.exists(env_exe):
        return env_exe

    system = platform.system()
    candidates: List[str] = []
    if system == "Windows":
        prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        for year in ("2027", "2026", "2025", "2024", "2023", "2022"):
            candidates.append(os.path.join(prog_files, f"Maxon ZBrush {year}", "ZBrush.exe"))
            candidates.append(os.path.join(prog_files, f"Pixologic\\ZBrush {year}", "ZBrush.exe"))
        candidates.extend(sorted(glob.glob(os.path.join(prog_files, "Maxon ZBrush*", "ZBrush.exe")), reverse=True))
    elif system == "Darwin":
        for year in ("2027", "2026", "2025", "2024", "2023", "2022"):
            candidates.append(f"/Applications/Maxon ZBrush {year}/ZBrush.app/Contents/MacOS/ZBrush")
            candidates.append(f"/Applications/ZBrush {year}/ZBrush.app/Contents/MacOS/ZBrush")

    for path in candidates:
        if os.path.exists(path):
            return path
    return env_exe or r"C:\Program Files\Maxon ZBrush 2027\ZBrush.exe"


def get_work_dir() -> str:
    env_dir = os.environ.get("ZBRUSH_WORK_DIR", "").strip()
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
        return env_dir
    default_dir = os.path.join(tempfile.gettempdir(), "zbrush_mcp_bridge")
    os.makedirs(default_dir, exist_ok=True)
    return default_dir


def sanitize_zscript_path(path: str) -> str:
    """Normalize file paths for ZScript string literals without quote injection."""
    clean = path.replace('"', "").strip()
    if platform.system() == "Windows":
        return clean.replace("/", "\\")
    return clean


def is_zbrush_running() -> bool:
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq ZBrush.exe", "/NH"],
                text=True,
                timeout=5,
            )
            return "zbrush.exe" in out.lower()
        out = subprocess.check_output(["pgrep", "-i", "ZBrush"], text=True, timeout=5)
        return bool(out.strip())
    except Exception:
        return False


def build_wrapped_zscript(zscript_body: str, out_file_path: str, extra_fields: str = "") -> str:
    """
    Wrap ZScript statements inside an [If, 1, ...] block that writes structured key=value
    telemetry (active tool, SubTool count, active polycount, plus optional custom fields)
    out to disk via ZBrush's [MemCreate] / [MemSaveToFile] memory block API.
    """
    out_z = sanitize_zscript_path(out_file_path)
    extra_merge = f', "|", {extra_fields}' if extra_fields else ""
    return f"""[VarDef, mcpToolPath, ""]
[VarDef, mcpSubTools, 0]
[VarDef, mcpPolys, 0]
[VarDef, mcpPoints, 0]
[If, 1,
{zscript_body}
    [VarSet, mcpSubTools, [SubToolGetCount]]
    [VarSet, mcpToolPath, [GetActiveToolPath]]
    [If, [IGet, Tool:Geometry:SDiv] >= 0,
        [Mesh3DGet, 0, , , mcpPoints, mcpPolys]
    ]
    [MemCreate, MCPOutMem, 4096, 0]
    [MemWriteString, MCPOutMem, [StrMerge, "status=ok|tool=", mcpToolPath, "|subtools=", mcpSubTools, "|points=", mcpPoints, "|polys=", mcpPolys{extra_merge}], 0]
    [MemSaveToFile, MCPOutMem, "{out_z}", 1]
    [MemDelete, MCPOutMem]
]
"""


def run_zscript_live(
    zscript_body: str,
    timeout: float = 30.0,
    extra_fields: str = "",
) -> Dict[str, Any]:
    """
    Dispatch a ZScript payload into the active ZBrush session and read back the
    structured response written by ZBrush's [MemSaveToFile] memory buffer.
    """
    zbrush_exe = discover_zbrush_executable()
    work_dir = get_work_dir()
    cmd_txt = os.path.join(work_dir, "mcp_live_cmd.txt")
    cmd_zsc = os.path.join(work_dir, "mcp_live_cmd.zsc")
    out_file = os.path.join(work_dir, "mcp_live_out.txt")

    for fpath in (cmd_zsc, out_file):
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception:
                pass

    wrapped = build_wrapped_zscript(zscript_body, out_file, extra_fields=extra_fields)
    with open(cmd_txt, "w", encoding="ascii", errors="ignore") as f:
        f.write(wrapped)

    task_name = os.environ.get("ZBRUSH_TASK_NAME", "").strip()
    if task_name and platform.system() == "Windows":
        subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True, text=True, timeout=10)
    else:
        if not os.path.exists(zbrush_exe):
            return {
                "status": "error",
                "message": f"ZBrush executable not found at '{zbrush_exe}'. Set ZBRUSH_EXE environment variable.",
            }
        subprocess.Popen([zbrush_exe, cmd_txt], cwd=os.path.dirname(zbrush_exe))

    start_t = time.time()
    while time.time() - start_t < timeout:
        if os.path.exists(out_file):
            time.sleep(0.15)
            try:
                with open(out_file, "r", encoding="utf-8", errors="ignore") as rf:
                    raw = rf.read().strip("\x00 \r\n")
                parts = dict(item.split("=", 1) for item in raw.split("|") if "=" in item)
                return {
                    "status": "ok",
                    "active_tool": parts.get("tool", ""),
                    "subtool_count": int(float(parts.get("subtools", 0) or 0)),
                    "vertex_count": int(float(parts.get("points", 0) or 0)),
                    "poly_count": int(float(parts.get("polys", 0) or 0)),
                    "details": {
                        k: v for k, v in parts.items() if k not in ("status", "tool", "subtools", "points", "polys")
                    },
                    "raw": raw,
                }
            except Exception as e:
                return {"status": "ok", "message": f"Executed (parse note: {e})"}
        time.sleep(0.25)

    return {
        "status": "timeout",
        "message": f"ZScript dispatched to ZBrush, but response buffer was not written within {timeout}s.",
    }


@mcp.tool()
def ping_zbrush() -> str:
    """Check if ZBrush is running and responsive, returning active Tool path, SubTool count, and polygon stats."""
    zbrush_exe = discover_zbrush_executable()
    running = is_zbrush_running()
    if not running and not os.path.exists(zbrush_exe):
        return json.dumps(
            {
                "status": "offline",
                "zbrush_running": False,
                "executable": zbrush_exe,
                "message": "ZBrush is not running and executable was not found at default paths. Set ZBRUSH_EXE if installed elsewhere.",
            },
            ensure_ascii=False,
            indent=2,
        )
    res = run_zscript_live("", timeout=15.0)
    res["zbrush_running"] = is_zbrush_running()
    res["executable"] = zbrush_exe
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def get_scene_info() -> str:
    """Return active ZBrush Tool path, total SubTool count, vertex count, and polygon count."""
    return json.dumps(run_zscript_live("", timeout=15.0), ensure_ascii=False, indent=2)


@mcp.tool()
def list_subtools() -> str:
    """Enumerate all SubTools in the active ZBrush Tool with their names and indices."""
    zcode = """
    [VarDef, stList, ""]
    [VarDef, stIdx, 0]
    [VarDef, totalSt, [SubToolGetCount]]
    [Loop, totalSt,
        [SubToolSelect, stIdx]
        [VarSet, stList, [StrMerge, stList, [If, stIdx > 0, ";", ""], stIdx, ":", [IGetTitle, Tool:Current Tool, 0]]]
        [VarInc, stIdx]
    ]
"""
    res = run_zscript_live(zcode, timeout=25.0, extra_fields='[StrMerge, "subtool_list=", stList]')
    raw_list = res.get("details", {}).get("subtool_list", "")
    parsed = []
    for entry in raw_list.split(";"):
        if ":" in entry:
            idx_s, name_s = entry.split(":", 1)
            parsed.append({"index": int(idx_s), "name": name_s})
    res["subtools"] = parsed
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def select_or_rename_subtool(index: int, new_name: str = "") -> str:
    """Select a SubTool by zero-based index and optionally rename it (e.g. adding _high suffix for Substance Painter baking)."""
    safe_name = new_name.replace('"', "").strip()
    rename_block = ""
    if safe_name:
        rename_block = f"""
    [FileNameSetNext, "{safe_name}"]
    [IPress, Tool:SubTool:Rename]
"""
    zcode = f"""
    [SubToolSelect, {int(index)}]
{rename_block}
"""
    return json.dumps(run_zscript_live(zcode, timeout=20.0), ensure_ascii=False, indent=2)


@mcp.tool()
def import_mesh(file_path: str, as_new_subtool: bool = True) -> str:
    """Import an OBJ, FBX, or ZTL mesh into the active ZBrush Tool (optionally appending as a new SubTool)."""
    norm_path = sanitize_zscript_path(file_path)
    if norm_path.lower().endswith(".ztl"):
        zcode = f"""
    [FileNameSetNext, "{norm_path}"]
    [IPress, Tool:Load Tool]
"""
    elif as_new_subtool:
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
    res = run_zscript_live(zcode, timeout=30.0)
    res["imported_path"] = norm_path
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def export_mesh(file_path: str, subtool_index: int = -1) -> str:
    """Export the active (or specified index) ZBrush SubTool/Tool to OBJ, FBX, or ZTL."""
    norm_path = sanitize_zscript_path(file_path)
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    sel_line = f"    [SubToolSelect, {int(subtool_index)}]\n" if subtool_index >= 0 else ""
    if norm_path.lower().endswith(".ztl"):
        zcode = f"""{sel_line}    [FileNameSetNext, "{norm_path}"]
    [IPress, Tool:Save As]
"""
    else:
        zcode = f"""{sel_line}    [ISet, Tool:Export:Grp, 0]
    [FileNameSetNext, "{norm_path}"]
    [IPress, Tool:Export]
"""
    res = run_zscript_live(zcode, timeout=45.0)
    res["exported_path"] = norm_path
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def run_dynamesh(resolution: int = 256, blur: int = 0, project: bool = False) -> str:
    """Rebuild the active SubTool topology into a uniform sculpting mesh using ZBrush DynaMesh."""
    res_clamped = max(16, min(int(resolution), 2048))
    blur_clamped = max(0, min(int(blur), 100))
    proj_val = 1 if project else 0
    zcode = f"""
    [ISet, Tool:Geometry:Resolution, {res_clamped}]
    [ISet, Tool:Geometry:Blur, {blur_clamped}]
    [ISet, Tool:Geometry:Project, {proj_val}]
    [If, [IGet, Tool:Geometry:DynaMesh] == 1,
        [IPress, Tool:Geometry:DynaMesh]
    ]
    [ISet, Tool:Geometry:DynaMesh, 1]
"""
    return json.dumps(run_zscript_live(zcode, timeout=45.0), ensure_ascii=False, indent=2)


@mcp.tool()
def run_zremesher(
    target_polys_thousands: float = 5.0,
    adaptive_size: int = 50,
    keep_groups: bool = False,
    keep_creases: bool = False,
) -> str:
    """Auto-retopologize the active SubTool into clean quad topology using ZRemesher."""
    target_val = max(0.1, min(float(target_polys_thousands), 100.0))
    adapt_val = max(0, min(int(adaptive_size), 100))
    kg_val = 1 if keep_groups else 0
    kc_val = 1 if keep_creases else 0
    zcode = f"""
    [ISet, Tool:Geometry:Target Polygons Count, {target_val}]
    [ISet, Tool:Geometry:AdaptiveSize, {adapt_val}]
    [ISet, Tool:Geometry:KeepGroups, {kg_val}]
    [ISet, Tool:Geometry:KeepCreases, {kc_val}]
    [IPress, Tool:Geometry:ZRemesher]
"""
    return json.dumps(run_zscript_live(zcode, timeout=90.0), ensure_ascii=False, indent=2)


@mcp.tool()
def run_decimation_master(percent_of_current: float = 20.0, keep_uvs: bool = True) -> str:
    """Pre-process and decimate a high-poly sculpt via Decimation Master for fast Substance Painter / Unreal Nanite export."""
    pct = max(0.1, min(float(percent_of_current), 100.0))
    uv_flag = 1 if keep_uvs else 0
    zcode = f"""
    [ISet, Zplugin:Decimation Master:Keep UVs, {uv_flag}]
    [IPress, Zplugin:Decimation Master:Pre-process Current]
    [ISet, Zplugin:Decimation Master:% of decimation, {pct}]
    [IPress, Zplugin:Decimation Master:Decimate Current]
"""
    return json.dumps(run_zscript_live(zcode, timeout=120.0), ensure_ascii=False, indent=2)


@mcp.tool()
def run_uv_master_unwrap(symmetry: bool = True, polygroups: bool = False) -> str:
    """Automatically generate clean UV seams and unwrap the active low-poly SubTool via ZBrush UV Master."""
    sym_val = 1 if symmetry else 0
    pg_val = 1 if polygroups else 0
    zcode = f"""
    [ISet, Zplugin:UV Master:Symmetry, {sym_val}]
    [ISet, Zplugin:UV Master:Polygroups, {pg_val}]
    [IPress, Zplugin:UV Master:Unwrap]
"""
    return json.dumps(run_zscript_live(zcode, timeout=60.0), ensure_ascii=False, indent=2)


@mcp.tool()
def subdivide_and_crease(divisions: int = 1, crease: bool = True, smooth: bool = True) -> str:
    """Optionally crease hard edges and subdivide the active SubTool (1–5 subdivision levels)."""
    lines = [f"    [ISet, Tool:Geometry:Smt, {1 if smooth else 0}]"]
    if crease:
        lines.append("    [IPress, Tool:Geometry:Crease:Crease]")
    for _ in range(max(1, min(int(divisions), 5))):
        lines.append("    [IPress, Tool:Geometry:Divide]")
    return json.dumps(run_zscript_live("\n".join(lines), timeout=35.0), ensure_ascii=False, indent=2)


@mcp.tool()
def apply_polypaint(r: int, g: int, b: int, material: str = "BasicMaterial") -> str:
    """Fill the active SubTool with an RGB Polypaint base color and ZBrush material."""
    safe_mat = material.replace('"', "").replace("]", "").strip()
    r_c, g_c, b_c = max(0, min(int(r), 255)), max(0, min(int(g), 255)), max(0, min(int(b), 255))
    zcode = f"""
    [ISet, Tool:Polypaint:Colorize, 1]
    [IPress, Draw:Mrgb]
    [IPress, Material:{safe_mat}]
    [IColorSet, {r_c}, {g_c}, {b_c}]
    [IPress, Color:FillObject]
"""
    return json.dumps(run_zscript_live(zcode, timeout=20.0), ensure_ascii=False, indent=2)


@mcp.tool()
def bake_and_export_maps(
    output_dir: str,
    asset_prefix: str = "T_Sculpt",
    map_size: int = 2048,
    export_normal: bool = True,
    export_albedo_from_polypaint: bool = True,
    flip_v: bool = True,
) -> str:
    """Bake Tangent Normal Map and/or Polypaint Albedo Texture from high-subdivision sculpt levels and export PNG/PSD maps."""
    os.makedirs(output_dir, exist_ok=True)
    size_clamped = max(512, min(int(map_size), 8192))
    lines = [f"    [ISet, Tool:UV Map:UV Map Size, {size_clamped}]"]
    exported: Dict[str, str] = {}

    if export_normal:
        norm_file = sanitize_zscript_path(os.path.join(output_dir, f"{asset_prefix}_Normal.tif"))
        exported["normal_map"] = norm_file
        lines.append(
            f"""    [ISet, Tool:Geometry:SDiv, 1]
    [ISet, Tool:Normal Map:Tangent, 1]
    [ISet, Tool:Normal Map:Adaptive, 1]
    [IPress, Tool:Normal Map:Create NormalMap]
    [IPress, Tool:Normal Map:Clone NM]
    {"[IPress, Texture:Flip V]" if flip_v else ""}
    [FileNameSetNext, "{norm_file}"]
    [IPress, Texture:Export]"""
        )

    if export_albedo_from_polypaint:
        alb_file = sanitize_zscript_path(os.path.join(output_dir, f"{asset_prefix}_BaseColor.psd"))
        exported["basecolor_map"] = alb_file
        lines.append(
            f"""    [ISet, Tool:Geometry:SDiv, [IGetMax, Tool:Geometry:SDiv]]
    [IPress, Tool:Texture Map:Create:New From Polypaint]
    [IPress, Tool:Texture Map:Clone Txtr]
    {"[IPress, Texture:Flip V]" if flip_v else ""}
    [FileNameSetNext, "{alb_file}"]
    [IPress, Texture:Export]"""
        )

    res = run_zscript_live("\n".join(lines), timeout=90.0)
    res["exported_maps"] = exported
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def render_bpr_and_capture(output_path: str = "") -> str:
    """Run Best Preview Render (BPR) in ZBrush and export the shaded document render pass to an image file."""
    if not output_path:
        output_path = os.path.join(get_work_dir(), "zbrush_bpr_render.psd")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    norm_out = sanitize_zscript_path(output_path)

    zcode = f"""
    [IPress, Draw:Perspective]
    [IPress, Transform:Fit]
    [IPress, Render:BPR RenderPass:Render Best Preview]
    [FileNameSetNext, "{norm_out}"]
    [IPress, Render:BPR RenderPass:Img]
"""
    res = run_zscript_live(zcode, timeout=45.0)
    res["capture_path"] = norm_out
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def execute_zscript(code: str) -> str:
    """Execute custom ZScript statements inside the active ZBrush session (automatically wrapped with telemetry output)."""
    return json.dumps(run_zscript_live(code, timeout=45.0), ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Maxon ZBrush MCP Server & Direct CLI")
    parser.add_argument("--cli", choices=["ping", "scene", "subtools", "zscript"], help="Run a direct CLI command instead of stdio MCP server")
    parser.add_argument("--arg", type=str, default="", help="Inline ZScript code for --cli zscript")
    args, _ = parser.parse_known_args()

    if args.cli:
        if args.cli == "ping":
            print(ping_zbrush())
        elif args.cli == "scene":
            print(get_scene_info())
        elif args.cli == "subtools":
            print(list_subtools())
        elif args.cli == "zscript":
            print(execute_zscript(args.arg))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
