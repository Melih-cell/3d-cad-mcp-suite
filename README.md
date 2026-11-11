# 3D & CAD Cross-DCC Pipeline MCP Suite

[![CI](https://github.com/Melih-cell/3d-cad-mcp-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/Melih-cell/3d-cad-mcp-suite/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](./pyproject.toml)
[![MCP](https://img.shields.io/badge/Protocol-MCP-6E56CF.svg)](https://modelcontextprotocol.io/)

An open-source suite of **7 Model Context Protocol (MCP) servers** connecting industry-standard 3D sculpting, modeling, rigging, PBR texturing, and CAD applications directly with **Claude Code**, **Claude Desktop**, **Cursor**, and any MCP-compatible client.

Unlike isolated single-app wrappers, `3d-cad-mcp-suite` is built around **cross-DCC handoffs and deterministic pre-flight validation**—catching broken UVs, N-gons, `100x` ZBrush/Maya/Unreal scale mismatches, unaligned `_high`/`_low` bake cages, and far-from-origin CAD drawings *before* they fail downstream.

---

## Architecture & Pipeline Flow

```text
  [AutoCAD 2020-2027] ──(Clean DXF Floorplan)──┐
                                               ▼
  [Maxon ZBrush 2022-2027] ──(High-Poly)──► [dcc-pipeline-mcp] ◄──(Low-Poly/Rig)── [Maya / 3ds Max / Blender]
     (ZScript MemBridge)                    Pre-Flight Auditor                     (Main-Thread TCP Bridges)
                                       (Topology, UV [0,1], Scale,
                                         _high/_low Bake Match)
                                               │
                                               ▼
                                  [Adobe Substance 3D Painter]
                                (High-to-Low Bake + Smart Material
                                 + UE5 ORM / Unity URP Export)
```

## Included MCP Servers

| Server Command | Host Software | Transport / Bridge | Key Capabilities |
| :--- | :--- | :--- | :--- |
| **`dcc-pipeline-mcp`** | Cross-DCC Orchestrator (Standalone + Live) | Pure Python + TCP Orchestration | Standalone `.obj` & `.dxf` pre-flight audits, `_high`/`_low` bake-pair validation, live 6-host status table, automated Sculpt $\rightarrow$ Painter PBR handoff, CAD $\rightarrow$ Blender wall extrusion |
| **`zbrush-mcp`** | Maxon ZBrush (2022–2027, Win/macOS) | ZScript `[MemCreate]` IPC Bridge | **15 tools:** DynaMesh, ZRemesher, Decimation Master, UV Master, SubTool enumeration & renaming, Polypaint, Tangent Normal/Albedo map baking, BPR render capture |
| **`substance-painter-mcp`** | Adobe Substance 3D Painter (Win/macOS) | Main-Thread TCP (`:8768`) | Project creation, High-to-Low mesh map baking, shelf resource search, Smart Material & mask generators, Unreal Engine Packed ORM / Unity HDRP/URP export |
| **`maya-mcp`** | Autodesk Maya (2022–2027) | `executeInMainThreadWithResult` (`:8767`) | Skeletal rig & skinCluster validation (max 4 influences, frozen transforms, `map1` UVs), game-ready FBX export (`unreal`/`unity`/`painter`), MEL/Python execution |
| **`3dsmax-mcp`** | Autodesk 3ds Max (2022–2027) | `pymxs` + Qt Timer Bridge (`:8765`) | Scene XForm & UV Channel 1 validation, automatic Reset XForm + UVW unwrap + FBX export for Painter, MAXScript & `pymxs` execution with Undo support |
| **`blender-mcp`** | Blender (3.6–5.x, Cross-Platform) | `bpy.app.timers` Bridge (`:8770`) | Thread-safe main-thread `bpy` execution, axis/scale-corrected FBX exporter for Unreal/Unity, mode switching, OpenGL viewport capture |
| **`autocad-mcp`** | Autodesk AutoCAD (2020–2027) | Persistent STA COM Bridge (`:8766`) | `RPC_E_CALL_REJECTED` auto-retry COM layer, 2D/3D entity drafting, layer management, clean floorplan DXF export (auto-freezes dimension/text clutter for 3D extrusion) |

---

## Quick Start

### 1. Install the Package

```bash
git clone https://github.com/Melih-cell/3d-cad-mcp-suite.git
cd 3d-cad-mcp-suite
pip install -e .
```

### 2. Register with Claude Code or Claude Desktop

**Claude Code (CLI):**

```bash
claude mcp add dcc-pipeline -- dcc-pipeline-mcp
claude mcp add zbrush -- zbrush-mcp
claude mcp add substance-painter -- substance-painter-mcp
claude mcp add maya -- maya-mcp
claude mcp add 3dsmax -- 3dsmax-mcp
claude mcp add blender -- blender-mcp
claude mcp add autocad -- autocad-mcp
```

**Claude Desktop (`claude_desktop_config.json`):**

Copy [`examples/claude_desktop_config.json`](./examples/claude_desktop_config.json) into your Claude Desktop configuration file:
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

---

## DCC Host Bridge Setup

> **`dcc-pipeline-mcp` pre-flight mesh/CAD validation tools work immediately out of the box** on Windows, macOS, and Linux with zero DCC setup. To enable live in-app control, copy the lightweight startup bridge for each application you use:

- **ZBrush (`zbrush-mcp`):** Zero plugin installation required. Auto-discovers ZBrush `2022–2027` (or set `ZBRUSH_EXE`). Communicates via live ZScript `[MemCreate]` memory buffers.
- **AutoCAD (`autocad-mcp`):** Zero plugin installation required on Windows. Connects directly to running or new AutoCAD instances via STA COM (`AutoCAD.Application`). Run `autocad-mcp --bridge` for a persistent sub-millisecond socket daemon.
- **Autodesk Maya (`maya-mcp`):** Copy [`maya_bridge.py`](./maya_bridge.py) and [`userSetup.py`](./userSetup.py) to `Documents/maya/<version>/scripts/` (or set `MAYA_MCP_BRIDGE_DIR`). Listens on `127.0.0.1:8767`.
- **Autodesk 3ds Max (`3dsmax-mcp`):** Copy [`3dsmax_mcp_startup.ms`](./3dsmax_mcp_startup.ms) and [`max_bridge.py`](./max_bridge.py) into `%LOCALAPPDATA%\Autodesk\3dsMax\<version> - 64bit\ENU\scripts\startup\` (or set `MAX_MCP_BRIDGE_PATH`). Listens on `127.0.0.1:8765`.
- **Adobe Substance 3D Painter (`substance-painter-mcp`):** Copy [`painter_bridge.py`](./painter_bridge.py) into `Documents/Adobe/Adobe Substance 3D Painter/python/startup/`. Listens on `127.0.0.1:8768`.
- **Blender (`blender-mcp`):** Start Blender with `blender --python blender_bridge.py` or run [`blender_bridge.py`](./blender_bridge.py) inside Blender's Scripting tab. Listens on `127.0.0.1:8770`.

---

## Standalone CLI & Pre-Flight Auditing

Every MCP server also exposes a direct `--cli` flag for CI pipelines and terminal scripts:

```bash
# Check live connection status of all 6 DCC/CAD bridges
dcc-pipeline-mcp --cli status

# Audit an OBJ mesh for N-gons, non-manifold edges, missing UVs, and Unreal cm scale
dcc-pipeline-mcp --cli audit-obj --arg ./assets/HeroProp_low.obj --engine unreal

# Validate a High-Poly / Low-Poly mesh pair before Substance Painter baking
dcc-pipeline-mcp --cli audit-pair --arg ./assets/HeroProp_high.obj --low ./assets/HeroProp_low.obj

# Inspect an AutoCAD DXF floorplan for origin offset and annotation layer clutter
dcc-pipeline-mcp --cli audit-dxf --arg ./cad/Floorplan_Level01.dxf
```

---

## Running Tests

The automated test suite validates all MCP server registrations, ZScript buffer generation, OBJ topology/UV/scale checkers, bake-pair alignment math, and DXF floorplan auditing without requiring proprietary DCC installations:

```bash
pip install -e ".[dev]"
pytest -v tests/test_suite.py
```

---

## Author & License

Built and maintained by **Melih Dokuzlar** ([GitHub](https://github.com/Melih-cell) · [Google Play — Veloce Labs](https://play.google.com/store/apps/developer?id=Veloce+Labs) · [itch.io](https://melih-cell.itch.io/)).

Released under the [MIT License](./LICENSE).
