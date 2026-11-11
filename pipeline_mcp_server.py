"""
Cross-DCC & CAD Pipeline Orchestrator MCP Server (`dcc-pipeline-mcp`)
Connects ZBrush, Maya, 3ds Max, Blender, Substance 3D Painter, and AutoCAD into a deterministic
asset production pipeline with standalone pre-flight geometry/UV/DXF validation and automated handoffs.

Works in two modes:
  1. Standalone Pre-Flight Mode (Zero external DCC required, cross-platform macOS/Linux/Windows):
     - Parses Wavefront .OBJ meshes to audit polycount, N-gons, degenerate faces, non-manifold edges,
       missing/out-of-bounds UVs, UDIM tile layout, and game-engine scale compliance (UE5 cm vs Unity m).
     - Audits high-poly / low-poly mesh pairs (`_high` / `_low`) before Substance Painter baking to catch
       bounding-box misalignment, scale mismatches, and naming collisions early.
     - Parses ASCII .DXF CAD floorplans to audit layers, entity counts, and world-origin offsets before
       importing into 3ds Max or Blender.
  2. Live Studio Orchestration Mode:
     - Checks live TCP/IPC bridge health across all 6 DCC/CAD servers simultaneously.
     - Orchestrates Sculpt/Model -> Pre-Flight Audit -> Substance 3D Painter High-to-Low Bake & Engine PBR Export.
     - Orchestrates AutoCAD Clean Floorplan DXF -> Blender / 3ds Max 3D Architectural Wall Extrusion.
"""

import argparse
import json
import math
import os
import socket
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("dcc-pipeline-mcp")

DCC_ENDPOINTS: Dict[str, Tuple[str, int]] = {
    "3dsmax": (os.environ.get("MAX_MCP_HOST", "127.0.0.1"), int(os.environ.get("MAX_MCP_PORT", "8765"))),
    "autocad": (os.environ.get("ACAD_MCP_HOST", "127.0.0.1"), int(os.environ.get("ACAD_MCP_PORT", "8766"))),
    "maya": (os.environ.get("MAYA_MCP_HOST", "127.0.0.1"), int(os.environ.get("MAYA_MCP_PORT", "8767"))),
    "substance_painter": (os.environ.get("PAINTER_MCP_HOST", "127.0.0.1"), int(os.environ.get("PAINTER_MCP_PORT", "8768"))),
    "blender": (os.environ.get("BLENDER_MCP_HOST", "127.0.0.1"), int(os.environ.get("BLENDER_MCP_PORT", "8770"))),
}


# ============================================================================
# Standalone Geometry & CAD Pre-Flight Parsers (Pure Python, Cross-Platform)
# ============================================================================

def inspect_obj_file(
    file_path: str,
    target_engine: str = "unreal",
    allow_udims: bool = False,
    max_tris_budget: int = 100000,
) -> Dict[str, Any]:
    """
    Parse a Wavefront .OBJ file and run studio pre-flight topology, UV, manifold,
    and scale checks without needing a running DCC host.
    """
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"OBJ file not found: {file_path}"}

    vertices: List[Tuple[float, float, float]] = []
    uvs: List[Tuple[float, float]] = []
    objects: List[str] = []
    materials: List[str] = []

    tri_count = 0
    quad_count = 0
    ngon_count = 0
    face_count = 0
    faces_missing_uvs = 0
    degenerate_faces = 0
    edge_usage: Counter[Tuple[int, int]] = Counter()

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("v "):
                parts = line.split()
                if len(parts) >= 4:
                    vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif line.startswith("vt "):
                parts = line.split()
                if len(parts) >= 3:
                    uvs.append((float(parts[1]), float(parts[2])))
            elif line.startswith(("o ", "g ")):
                obj_name = line[2:].strip()
                if obj_name and obj_name not in objects:
                    objects.append(obj_name)
            elif line.startswith("usemtl "):
                mat_name = line[7:].strip()
                if mat_name and mat_name not in materials:
                    materials.append(mat_name)
            elif line.startswith("f "):
                tokens = line.split()[1:]
                n = len(tokens)
                if n < 3:
                    continue
                face_count += 1
                if n == 3:
                    tri_count += 1
                elif n == 4:
                    quad_count += 1
                    tri_count += 2
                else:
                    ngon_count += 1
                    tri_count += n - 2

                v_indices: List[int] = []
                has_face_uv = True
                for tok in tokens:
                    sub = tok.split("/")
                    vi = int(sub[0])
                    if vi < 0:
                        vi = len(vertices) + 1 + vi
                    v_indices.append(vi - 1)
                    if len(sub) < 2 or not sub[1]:
                        has_face_uv = False

                if not has_face_uv:
                    faces_missing_uvs += 1

                if len(set(v_indices)) < 3:
                    degenerate_faces += 1
                else:
                    p0 = vertices[v_indices[0]] if 0 <= v_indices[0] < len(vertices) else (0.0, 0.0, 0.0)
                    p1 = vertices[v_indices[1]] if 0 <= v_indices[1] < len(vertices) else (0.0, 0.0, 0.0)
                    p2 = vertices[v_indices[2]] if 0 <= v_indices[2] < len(vertices) else (0.0, 0.0, 0.0)
                    ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
                    vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
                    cx = uy * vz - uz * vy
                    cy = uz * vx - ux * vz
                    cz = ux * vy - uy * vx
                    area2 = math.sqrt(cx * cx + cy * cy + cz * cz)
                    if area2 < 1e-9:
                        degenerate_faces += 1

                for idx in range(n):
                    a = v_indices[idx]
                    b = v_indices[(idx + 1) % n]
                    if a != b:
                        edge_key = (min(a, b), max(a, b))
                        edge_usage[edge_key] += 1

    if not vertices:
        return {"status": "error", "message": "OBJ file contains zero vertices."}

    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    bbox_min = [round(min(xs), 5), round(min(ys), 5), round(min(zs), 5)]
    bbox_max = [round(max(xs), 5), round(max(ys), 5), round(max(zs), 5)]
    dimensions = [
        round(bbox_max[0] - bbox_min[0], 5),
        round(bbox_max[1] - bbox_min[1], 5),
        round(bbox_max[2] - bbox_min[2], 5),
    ]

    boundary_edges = sum(1 for c in edge_usage.values() if c == 1)
    non_manifold_edges = sum(1 for c in edge_usage.values() if c > 2)

    uv_out_of_bounds = 0
    udim_tiles = set()
    for u, v in uvs:
        if u < -1e-4 or v < -1e-4 or (not allow_udims and (u > 1.0001 or v > 1.0001)):
            uv_out_of_bounds += 1
        if u >= 0.0 and v >= 0.0:
            udim = 1001 + int(u) + int(v) * 10
            udim_tiles.add(udim)

    issues: List[Dict[str, Any]] = []
    if ngon_count > 0:
        issues.append(
            {
                "severity": "error",
                "code": "NGONS_DETECTED",
                "count": ngon_count,
                "fix": "Triangulate or quadrangulate faces > 4 vertices before Substance Painter baking or game engine import.",
            }
        )
    if degenerate_faces > 0:
        issues.append(
            {
                "severity": "warning",
                "code": "DEGENERATE_ZERO_AREA_FACES",
                "count": degenerate_faces,
                "fix": "Run Mesh Cleanup / Merge by Distance to eliminate zero-area slivers.",
            }
        )
    if non_manifold_edges > 0:
        issues.append(
            {
                "severity": "error",
                "code": "NON_MANIFOLD_EDGES",
                "count": non_manifold_edges,
                "fix": "Fix edges shared by 3+ faces; non-manifold geometry causes baking artifacts.",
            }
        )
    if not uvs or faces_missing_uvs > 0:
        issues.append(
            {
                "severity": "error",
                "code": "MISSING_UVS",
                "faces_missing_uvs": faces_missing_uvs,
                "fix": "Unwrap UVs into [0, 1] space before texturing in Substance 3D Painter.",
            }
        )
    elif uv_out_of_bounds > 0:
        issues.append(
            {
                "severity": "warning",
                "code": "UV_OUT_OF_0_1_BOUNDS",
                "count": uv_out_of_bounds,
                "fix": "Pack UV islands inside [0, 1] (or enable allow_udims=True for UDIM workflows).",
            }
        )
    if tri_count > max_tris_budget:
        issues.append(
            {
                "severity": "warning",
                "code": "TRIANGLE_BUDGET_EXCEEDED",
                "triangles": tri_count,
                "budget": max_tris_budget,
                "fix": "Decimate or retopologize mesh via ZRemesher / Decimation Master.",
            }
        )

    max_extent = max(dimensions)
    eng = target_engine.lower().strip()
    if eng in ("unreal", "ue5") and 0 < max_extent < 2.5:
        issues.append(
            {
                "severity": "warning",
                "code": "SUSPECTED_METER_TO_CM_MISMATCH",
                "max_dimension": max_extent,
                "fix": "Asset max dimension is < 2.5 units while targeting Unreal Engine (1 unit = 1 cm). Check 100x meter-to-cm export scale.",
            }
        )
    elif eng == "unity" and max_extent > 250.0:
        issues.append(
            {
                "severity": "warning",
                "code": "SUSPECTED_CM_TO_METER_MISMATCH",
                "max_dimension": max_extent,
                "fix": "Asset max dimension is > 250 units while targeting Unity (1 unit = 1 m). Verify 0.01x cm-to-meter scale factor.",
            }
        )

    has_errors = any(i["severity"] == "error" for i in issues)
    return {
        "status": "fail" if has_errors else ("warnings" if issues else "pass"),
        "file_path": os.path.abspath(file_path),
        "target_engine": target_engine,
        "objects": objects,
        "materials": materials,
        "stats": {
            "vertices": len(vertices),
            "uv_coords": len(uvs),
            "faces": face_count,
            "triangles_estimated": tri_count,
            "quads": quad_count,
            "ngons": ngon_count,
            "boundary_open_edges": boundary_edges,
            "non_manifold_edges": non_manifold_edges,
            "degenerate_faces": degenerate_faces,
            "udim_tiles": sorted(udim_tiles),
        },
        "bounding_box": {
            "min": bbox_min,
            "max": bbox_max,
            "dimensions": dimensions,
        },
        "issues": issues,
    }


def compare_bake_pair(high_poly_obj: str, low_poly_obj: str, tolerance_ratio: float = 0.08) -> Dict[str, Any]:
    """
    Validate a High-Poly and Low-Poly OBJ pair before Substance 3D Painter baking:
    checks spatial bounding-box alignment, scale ratio mismatches (e.g. 100x ZBrush/Maya unit bug),
    low-poly UV completeness, and `_high` / `_low` SubTool/object naming correspondence.
    """
    hp = inspect_obj_file(high_poly_obj, max_tris_budget=50_000_000)
    lp = inspect_obj_file(low_poly_obj, max_tris_budget=250_000)
    if hp.get("status") == "error":
        return {"status": "error", "stage": "high_poly", "detail": hp}
    if lp.get("status") == "error":
        return {"status": "error", "stage": "low_poly", "detail": lp}

    hp_dim = hp["bounding_box"]["dimensions"]
    lp_dim = lp["bounding_box"]["dimensions"]
    hp_min, hp_max = hp["bounding_box"]["min"], hp["bounding_box"]["max"]
    lp_min, lp_max = lp["bounding_box"]["min"], lp["bounding_box"]["max"]

    hp_center = [(hp_min[i] + hp_max[i]) * 0.5 for i in range(3)]
    lp_center = [(lp_min[i] + lp_max[i]) * 0.5 for i in range(3)]

    max_lp_size = max(max(lp_dim), 1e-6)
    max_hp_size = max(max(hp_dim), 1e-6)
    scale_ratio = round(max_hp_size / max_lp_size, 4)
    center_delta = math.sqrt(sum((hp_center[i] - lp_center[i]) ** 2 for i in range(3)))

    bake_issues: List[Dict[str, Any]] = []
    if abs(scale_ratio - 1.0) > tolerance_ratio:
        bake_issues.append(
            {
                "severity": "error",
                "code": "HIGH_LOW_SCALE_MISMATCH",
                "high_to_low_scale_ratio": scale_ratio,
                "fix": "High-poly and low-poly meshes differ in size (common 10x/100x ZBrush Export Scale mismatch). Match bounding boxes before baking.",
            }
        )
    if center_delta > max_lp_size * tolerance_ratio:
        bake_issues.append(
            {
                "severity": "error",
                "code": "HIGH_LOW_OFFSET_MISALIGNMENT",
                "center_distance": round(center_delta, 5),
                "fix": "High-poly and low-poly bounding box centers do not overlap in world space; normal/AO rays will miss.",
            }
        )

    hp_bases = {name[:-5] if name.lower().endswith("_high") else name for name in hp["objects"]}
    lp_bases = {name[:-4] if name.lower().endswith("_low") else name for name in lp["objects"]}
    unmatched_low = sorted(lp_bases - hp_bases) if (hp_bases and lp_bases) else []
    if unmatched_low:
        bake_issues.append(
            {
                "severity": "warning",
                "code": "NAME_PAIR_MISMATCH",
                "unmatched_low_objects": unmatched_low,
                "fix": "Ensure matching '<part>_low' and '<part>_high' object names if baking with Match 'By Mesh Name' in Substance Painter.",
            }
        )

    for issue in lp["issues"]:
        if issue["severity"] == "error":
            bake_issues.append(issue)

    has_err = any(i["severity"] == "error" for i in bake_issues)
    return {
        "status": "fail" if has_err else ("warnings" if bake_issues else "ready_to_bake"),
        "high_poly_tris": hp["stats"]["triangles_estimated"],
        "low_poly_tris": lp["stats"]["triangles_estimated"],
        "scale_ratio": scale_ratio,
        "center_offset": round(center_delta, 5),
        "matched_submeshes": sorted(hp_bases & lp_bases),
        "issues": bake_issues,
    }


def inspect_ascii_dxf(file_path: str) -> Dict[str, Any]:
    """
    Lightweight pure-Python ASCII DXF floorplan inspector: extracts layers, entity type breakdown,
    bounding extents, and checks for far-from-origin coordinates that break 3ds Max / Blender precision.
    """
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"DXF file not found: {file_path}"}

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [ln.strip() for ln in f]

    entity_counts: Counter[str] = Counter()
    layers = set()
    xs: List[float] = []
    ys: List[float] = []

    in_entities = False
    current_section = ""
    i = 0
    total = len(lines)
    while i + 1 < total:
        code = lines[i]
        val = lines[i + 1]
        i += 2

        if code == "0" and val == "SECTION" and i + 1 < total and lines[i] == "2":
            current_section = lines[i + 1].upper()
            in_entities = current_section == "ENTITIES"
            i += 2
            continue
        if code == "0" and val == "ENDSEC":
            in_entities = False
            current_section = ""
            continue

        if in_entities:
            if code == "0":
                entity_counts[val] += 1
            elif code == "8":
                layers.add(val)
            elif code in ("10", "11"):
                try:
                    xs.append(float(val))
                except ValueError:
                    pass
            elif code in ("20", "21"):
                try:
                    ys.append(float(val))
                except ValueError:
                    pass

    issues: List[Dict[str, Any]] = []
    bounds = None
    if xs and ys:
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        cx, cy = (min_x + max_x) * 0.5, (min_y + max_y) * 0.5
        origin_dist = math.hypot(cx, cy)
        bounds = {
            "min": [round(min_x, 3), round(min_y, 3)],
            "max": [round(max_x, 3), round(max_y, 3)],
            "size": [round(max_x - min_x, 3), round(max_y - min_y, 3)],
            "distance_from_origin": round(origin_dist, 3),
        }
        if origin_dist > 100_000.0:
            issues.append(
                {
                    "severity": "warning",
                    "code": "FAR_FROM_WORLD_ORIGIN",
                    "distance_from_origin": round(origin_dist, 2),
                    "fix": "Move CAD geometry near (0,0,0) before importing into 3ds Max / Blender / Unreal to prevent floating-point jitter.",
                }
            )

    anno_layers = sorted(l for l in layers if any(k in l.upper() for k in ("DIM", "TEXT", "ANNO", "HATCH")))
    if anno_layers:
        issues.append(
            {
                "severity": "info",
                "code": "ANNOTATION_LAYERS_PRESENT",
                "layers": anno_layers,
                "fix": "Use export_clean_floorplan_for_3d in autocad-mcp to freeze dimension/text layers prior to 3D wall extrusion.",
            }
        )

    return {
        "status": "ok" if not any(x["severity"] == "error" for x in issues) else "fail",
        "file_path": os.path.abspath(file_path),
        "layer_count": len(layers),
        "layers": sorted(layers),
        "entities": dict(entity_counts),
        "bounds_2d": bounds,
        "issues": issues,
    }


def probe_tcp_port(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


# ============================================================================
# MCP Tools
# ============================================================================

@mcp.tool()
def check_all_dcc_bridges() -> str:
    """Check live connection status of all 6 DCC/CAD servers (3ds Max, AutoCAD, Maya, Substance 3D Painter, Blender, ZBrush)."""
    import zbrush_mcp_server

    report: Dict[str, Any] = {}
    for name, (host, port) in DCC_ENDPOINTS.items():
        online = probe_tcp_port(host, port)
        report[name] = {"host": host, "port": port, "online": online}

    zb_exe = zbrush_mcp_server.discover_zbrush_executable()
    report["zbrush"] = {
        "ipc_mode": "zscript_mem_bridge",
        "process_running": zbrush_mcp_server.is_zbrush_running(),
        "executable_found": os.path.exists(zb_exe),
        "executable": zb_exe,
    }
    return json.dumps({"status": "ok", "bridges": report}, ensure_ascii=False, indent=2)


@mcp.tool()
def audit_obj_mesh(
    file_path: str,
    target_engine: str = "unreal",
    allow_udims: bool = False,
    max_tris_budget: int = 100000,
) -> str:
    """Run standalone pre-flight game/PBR topology, N-gon, non-manifold, UV [0,1], and scale validation on an .OBJ mesh."""
    return json.dumps(
        inspect_obj_file(
            file_path=file_path,
            target_engine=target_engine,
            allow_udims=allow_udims,
            max_tris_budget=max_tris_budget,
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def validate_high_low_bake_pair(
    high_poly_obj: str,
    low_poly_obj: str,
    tolerance_ratio: float = 0.08,
) -> str:
    """Validate High-Poly and Low-Poly .OBJ meshes before Substance Painter baking (checks scale ratio, world alignment, UVs, and _high/_low naming)."""
    return json.dumps(
        compare_bake_pair(high_poly_obj=high_poly_obj, low_poly_obj=low_poly_obj, tolerance_ratio=tolerance_ratio),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def audit_dxf_floorplan(file_path: str) -> str:
    """Inspect an ASCII .DXF architectural CAD drawing for layers, entities, annotation clutter, and world-origin distance before 3D import."""
    return json.dumps(inspect_ascii_dxf(file_path), ensure_ascii=False, indent=2)


@mcp.tool()
def handoff_sculpt_to_painter(
    low_poly_mesh_path: str,
    export_textures_dir: str,
    high_poly_mesh_path: str = "",
    smart_material_query: str = "Steel",
    target_engine: str = "unreal",
    resolution: int = 2048,
    save_spp_path: str = "",
) -> str:
    """
    Orchestrate the full Sculpt/Retopo -> Pre-Flight Validation -> Substance 3D Painter Bake & PBR Texture Export pipeline.
    Automatically blocks broken bakes if OBJ pre-flight detects fatal UV or scale-mismatch errors.
    """
    import painter_mcp_server

    pipeline_log: List[Dict[str, Any]] = []

    if low_poly_mesh_path.lower().endswith(".obj") and high_poly_mesh_path.lower().endswith(".obj"):
        pair_check = compare_bake_pair(high_poly_mesh_path, low_poly_mesh_path)
        pipeline_log.append({"stage": "pre_flight_bake_pair_audit", "result": pair_check})
        if pair_check.get("status") == "fail":
            return json.dumps(
                {
                    "status": "aborted_pre_flight",
                    "message": "Pre-flight High/Low mesh audit found critical baking blockers.",
                    "pipeline_log": pipeline_log,
                },
                ensure_ascii=False,
                indent=2,
            )
    elif low_poly_mesh_path.lower().endswith(".obj"):
        lp_check = inspect_obj_file(low_poly_mesh_path, target_engine=target_engine)
        pipeline_log.append({"stage": "pre_flight_low_poly_audit", "result": lp_check})
        if lp_check.get("status") == "fail":
            return json.dumps(
                {
                    "status": "aborted_pre_flight",
                    "message": "Low-poly OBJ failed pre-flight validation (fix N-gons/UVs before Substance Painter).",
                    "pipeline_log": pipeline_log,
                },
                ensure_ascii=False,
                indent=2,
            )

    painter_raw = painter_mcp_server.auto_texture_and_export_pbr(
        mesh_path=low_poly_mesh_path,
        export_dir=export_textures_dir,
        smart_material_query=smart_material_query,
        target_engine=target_engine,
        high_poly_mesh_path=high_poly_mesh_path,
        resolution=resolution,
        save_spp_path=save_spp_path,
    )
    painter_res = json.loads(painter_raw)
    pipeline_log.append({"stage": "substance_painter_bake_and_export", "result": painter_res})
    return json.dumps(
        {
            "status": painter_res.get("status", "ok"),
            "target_engine": target_engine,
            "export_textures_dir": os.path.abspath(export_textures_dir),
            "pipeline_log": pipeline_log,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def handoff_cad_to_blender_walls(
    dxf_path: str,
    wall_height_meters: float = 3.0,
    wall_layer_filter: str = "WALL",
) -> str:
    """Audit an AutoCAD .DXF floorplan and send it to the live Blender bridge to import and extrude 3D architectural walls."""
    import blender_mcp_server

    dxf_audit = inspect_ascii_dxf(dxf_path)
    if dxf_audit.get("status") == "error":
        return json.dumps(dxf_audit, ensure_ascii=False, indent=2)

    dxf_norm = os.path.abspath(dxf_path).replace("\\", "/")
    py_code = f"""
import bpy

try:
    bpy.ops.preferences.addon_enable(module="io_import_dxf")
except Exception:
    pass

before = set(bpy.data.objects.keys())
bpy.ops.import_scene.dxf(filepath={repr(dxf_norm)})
imported = [bpy.data.objects[n] for n in (set(bpy.data.objects.keys()) - before)]

extruded = []
for obj in imported:
    if not {repr(wall_layer_filter)} or {repr(wall_layer_filter)}.upper() in obj.name.upper():
        if obj.type == "CURVE" and obj.data:
            obj.data.extrude = float({float(wall_height_meters)}) * 0.5
            extruded.append(obj.name)
        elif obj.type == "MESH":
            mod = obj.modifiers.new(name="WallSolidify", type="SOLIDIFY")
            mod.thickness = float({float(wall_height_meters)})
            extruded.append(obj.name)

result = {{
    "imported_count": len(imported),
    "extruded_objects": extruded,
    "wall_height_meters": {float(wall_height_meters)},
}}
"""
    blender_res = blender_mcp_server.send_to_blender("execute_python", {"code": py_code})
    return json.dumps(
        {
            "status": "ok" if blender_res.get("status") == "success" else "error",
            "dxf_pre_flight": dxf_audit,
            "blender_import": blender_res,
        },
        ensure_ascii=False,
        indent=2,
    )


def main():
    parser = argparse.ArgumentParser(description="Cross-DCC & CAD Pipeline Orchestrator MCP Server")
    parser.add_argument(
        "--cli",
        choices=["status", "audit-obj", "audit-pair", "audit-dxf"],
        help="Run standalone CLI validation or studio bridge check",
    )
    parser.add_argument("--arg", type=str, default="", help="Primary file path for --cli")
    parser.add_argument("--low", type=str, default="", help="Low-poly OBJ path for --cli audit-pair")
    parser.add_argument("--engine", type=str, default="unreal", help="Target engine ('unreal' or 'unity')")
    args, _ = parser.parse_known_args()

    if args.cli:
        if args.cli == "status":
            print(check_all_dcc_bridges())
        elif args.cli == "audit-obj":
            print(audit_obj_mesh(args.arg, target_engine=args.engine))
        elif args.cli == "audit-pair":
            print(validate_high_low_bake_pair(args.arg, args.low))
        elif args.cli == "audit-dxf":
            print(audit_dxf_floorplan(args.arg))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
