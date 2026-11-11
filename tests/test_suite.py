"""
Automated test suite for dcc-cad-mcp-suite.
Runs cleanly on Windows, macOS, and Linux without requiring proprietary DCC installations.
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import autocad_mcp_server
import blender_mcp_server
import max_mcp_server
import maya_mcp_server
import painter_mcp_server
import pipeline_mcp_server
import zbrush_mcp_server


CLEAN_QUAD_OBJ = """# Clean 180cm Unreal-scale crate mesh with [0,1] UVs
o Crate_low
usemtl M_Crate
v -45.0 0.0 -45.0
v  45.0 0.0 -45.0
v  45.0 180.0 -45.0
v -45.0 180.0 -45.0
vt 0.1 0.1
vt 0.9 0.1
vt 0.9 0.9
vt 0.1 0.9
f 1/1 2/2 3/3 4/4
"""

HIGH_POLY_MATCHING_OBJ = """# Matching High-Poly sculpt
o Crate_high
v -45.2 0.0 -45.0
v  45.2 0.0 -45.0
v  45.2 180.5 -45.0
v -45.2 180.5 -45.0
f 1 2 3 4
"""

BROKEN_NGON_NO_UV_OBJ = """# 1.8m scale mesh with 5-gon and no UVs
o BrokenAsset_low
v 0.0 0.0 0.0
v 1.0 0.0 0.0
v 1.5 0.9 0.0
v 0.5 1.8 0.0
v -0.5 0.9 0.0
f 1 2 3 4 5
"""

SAMPLE_FLOORPLAN_DXF = """0
SECTION
2
ENTITIES
0
LWPOLYLINE
8
A-WALL
10
500000.0
20
500000.0
11
501200.0
21
500800.0
0
TEXT
8
A-DIM-ANNO
10
500100.0
20
500100.0
0
ENDSEC
0
EOF
"""


class TestDccCadMcpSuite(unittest.TestCase):
    def test_all_mcp_servers_import_and_register(self):
        servers = [
            max_mcp_server.mcp,
            maya_mcp_server.mcp,
            zbrush_mcp_server.mcp,
            painter_mcp_server.mcp,
            autocad_mcp_server.mcp,
            blender_mcp_server.mcp,
            pipeline_mcp_server.mcp,
        ]
        for srv in servers:
            self.assertIsNotNone(srv.name)

    def test_no_hardcoded_personal_paths_in_sources(self):
        py_files = [
            f for f in os.listdir(REPO_ROOT)
            if f.endswith((".py", ".ms"))
        ]
        self.assertGreaterEqual(len(py_files), 10)
        for fname in py_files:
            with open(os.path.join(REPO_ROOT, fname), "r", encoding="utf-8") as f:
                text = f.read().lower()
            self.assertNotIn("c:\\users\\", text, f"Hardcoded user path found in {fname}")
            self.assertNotIn("antigravity", text, f"Internal path leak found in {fname}")

    def test_clean_obj_passes_unreal_preflight(self):
        with tempfile.NamedTemporaryFile("w", suffix=".obj", delete=False, encoding="utf-8") as tf:
            tf.write(CLEAN_QUAD_OBJ)
            path = tf.name
        try:
            report = pipeline_mcp_server.inspect_obj_file(path, target_engine="unreal")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["stats"]["quads"], 1)
            self.assertEqual(report["stats"]["ngons"], 0)
            self.assertEqual(report["issues"], [])
        finally:
            os.remove(path)

    def test_broken_obj_catches_ngons_missing_uvs_and_scale(self):
        with tempfile.NamedTemporaryFile("w", suffix=".obj", delete=False, encoding="utf-8") as tf:
            tf.write(BROKEN_NGON_NO_UV_OBJ)
            path = tf.name
        try:
            report = pipeline_mcp_server.inspect_obj_file(path, target_engine="unreal")
            self.assertEqual(report["status"], "fail")
            codes = {item["code"] for item in report["issues"]}
            self.assertIn("NGONS_DETECTED", codes)
            self.assertIn("MISSING_UVS", codes)
            self.assertIn("SUSPECTED_METER_TO_CM_MISMATCH", codes)
        finally:
            os.remove(path)

    def test_bake_pair_ready_and_scale_mismatch_detection(self):
        with tempfile.NamedTemporaryFile("w", suffix=".obj", delete=False, encoding="utf-8") as f_low, \
             tempfile.NamedTemporaryFile("w", suffix=".obj", delete=False, encoding="utf-8") as f_high, \
             tempfile.NamedTemporaryFile("w", suffix=".obj", delete=False, encoding="utf-8") as f_tiny:
            f_low.write(CLEAN_QUAD_OBJ)
            f_high.write(HIGH_POLY_MATCHING_OBJ)
            f_tiny.write(BROKEN_NGON_NO_UV_OBJ)
            low_p, high_p, tiny_p = f_low.name, f_high.name, f_tiny.name

        try:
            ok_pair = pipeline_mcp_server.compare_bake_pair(high_p, low_p)
            self.assertEqual(ok_pair["status"], "ready_to_bake")
            self.assertIn("Crate", ok_pair["matched_submeshes"])

            bad_pair = pipeline_mcp_server.compare_bake_pair(tiny_p, low_p)
            self.assertEqual(bad_pair["status"], "fail")
            codes = {i["code"] for i in bad_pair["issues"]}
            self.assertIn("HIGH_LOW_SCALE_MISMATCH", codes)
        finally:
            for p in (low_p, high_p, tiny_p):
                os.remove(p)

    def test_dxf_floorplan_auditor_catches_far_origin_and_annotations(self):
        with tempfile.NamedTemporaryFile("w", suffix=".dxf", delete=False, encoding="utf-8") as tf:
            tf.write(SAMPLE_FLOORPLAN_DXF)
            dxf_path = tf.name
        try:
            res = pipeline_mcp_server.inspect_ascii_dxf(dxf_path)
            self.assertEqual(res["status"], "ok")
            self.assertIn("A-WALL", res["layers"])
            codes = {i["code"] for i in res["issues"]}
            self.assertIn("FAR_FROM_WORLD_ORIGIN", codes)
            self.assertIn("ANNOTATION_LAYERS_PRESENT", codes)
        finally:
            os.remove(dxf_path)

    def test_zbrush_zscript_wrapper_and_sanitizer(self):
        dirty = 'C:/Temp/"evil"/mesh.obj'
        sanitized = zbrush_mcp_server.sanitize_zscript_path(dirty)
        self.assertNotIn('"', sanitized)

        wrapped = zbrush_mcp_server.build_wrapped_zscript(
            "    [IPress, Tool:Geometry:Divide]",
            "/tmp/out.txt",
        )
        self.assertIn("[MemCreate, MCPOutMem", wrapped)
        self.assertIn("[Mesh3DGet, 0", wrapped)
        self.assertIn("[IPress, Tool:Geometry:Divide]", wrapped)

    def test_check_all_dcc_bridges_returns_valid_json(self):
        raw = pipeline_mcp_server.check_all_dcc_bridges()
        parsed = json.loads(raw)
        self.assertEqual(parsed["status"], "ok")
        self.assertIn("3dsmax", parsed["bridges"])
        self.assertIn("zbrush", parsed["bridges"])
        self.assertIn("substance_painter", parsed["bridges"])


if __name__ == "__main__":
    unittest.main()
