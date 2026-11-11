"""
Autodesk AutoCAD MCP Server + Persistent STA COM Bridge + CAD-to-3D Pipeline Exporter
Maintains a persistent STA COM connection to AutoCAD (2020–2027) on 127.0.0.1:8766
so MCP and CLI operations execute in milliseconds without spawning duplicate instances.
"""

import argparse
import io
import json
import math
import os
import socket
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stdout
from typing import Any, Dict, List, Optional

try:
    import pythoncom
    import win32com.client
    HAS_WIN32COM = True
except ImportError:
    pythoncom = None  # type: ignore[assignment]
    win32com = None  # type: ignore[assignment]
    HAS_WIN32COM = False

from mcp.server.fastmcp import FastMCP

ACAD_HOST = os.environ.get("ACAD_MCP_HOST", "127.0.0.1")
ACAD_PORT = int(os.environ.get("ACAD_MCP_PORT", "8766"))

PROG_IDS = [
    "AutoCAD.Application",
    "AutoCAD.Application.26",
    "AutoCAD.Application.25",
    "AutoCAD.Application.24",
    "AutoCAD.Application.23",
]

mcp = FastMCP("autocad-mcp")

_ACAD_APP = None


def vt_point(x: float, y: float, z: float = 0.0) -> Any:
    """Create a 3D point VARIANT array of doubles (VT_ARRAY | VT_R8) for AutoCAD COM."""
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8,
        [float(x), float(y), float(z)],
    )


def vt_float_array(values: List[float]) -> Any:
    """Create a 1D VARIANT array of doubles (VT_ARRAY | VT_R8) for AutoCAD COM."""
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8,
        [float(v) for v in values],
    )


def com_retry(func, retries: int = 30, delay: float = 0.4):
    """Retry COM operations if AutoCAD temporarily rejects the call (RPC_E_CALL_REJECTED)."""
    for attempt in range(retries):
        try:
            return func()
        except pythoncom.com_error as e:
            if e.hresult in (-2147418111, -2147417846) and attempt < retries - 1:
                time.sleep(delay)
                continue
            raise


def get_acad(auto_launch: bool = True, ensure_doc: bool = True):
    """Get or create the persistent AutoCAD COM application object in the bridge process."""
    if not HAS_WIN32COM:
        raise RuntimeError("pywin32 is required for live AutoCAD COM automation (Windows only).")
    global _ACAD_APP
    pythoncom.CoInitialize()

    if _ACAD_APP is not None:
        try:
            _ = com_retry(lambda: _ACAD_APP.Version, retries=5, delay=0.2)
        except Exception:
            _ACAD_APP = None

    if _ACAD_APP is None:
        for prog_id in PROG_IDS:
            try:
                _ACAD_APP = win32com.client.GetActiveObject(prog_id)
                break
            except Exception:
                pass

    if _ACAD_APP is None and auto_launch:
        for prog_id in PROG_IDS:
            try:
                _ACAD_APP = win32com.client.Dispatch(prog_id)
                com_retry(lambda: setattr(_ACAD_APP, "Visible", True))
                break
            except Exception:
                pass

    if _ACAD_APP is None:
        raise RuntimeError("Could not establish AutoCAD COM connection. Ensure AutoCAD (2020-2027) is installed.")

    try:
        com_retry(lambda: setattr(_ACAD_APP, "Visible", True), retries=5, delay=0.2)
    except Exception:
        pass

    doc = None
    if ensure_doc:
        def _get_or_create_doc():
            if _ACAD_APP.Documents.Count == 0:
                return _ACAD_APP.Documents.Add()
            return _ACAD_APP.ActiveDocument

        doc = com_retry(_get_or_create_doc)

    return _ACAD_APP, doc


# ============================================================================
# Direct COM Handlers (executed inside the persistent bridge or in-process)
# ============================================================================

def _handle_ping(params: Dict[str, Any]) -> Dict[str, Any]:
    auto_launch = bool(params.get("auto_launch", True))
    acad, _ = get_acad(auto_launch=auto_launch, ensure_doc=True)
    version = com_retry(lambda: str(acad.Version))
    caption = com_retry(lambda: str(acad.Caption))
    doc_count = com_retry(lambda: int(acad.Documents.Count))
    active_doc_name = None
    active_doc_path = None
    if doc_count > 0:
        active_doc = com_retry(lambda: acad.ActiveDocument)
        active_doc_name = com_retry(lambda: str(active_doc.Name))
        active_doc_path = com_retry(lambda: str(active_doc.FullName))

    return {
        "status": "ok",
        "connected": True,
        "version": version,
        "caption": caption,
        "open_documents": doc_count,
        "active_document": active_doc_name,
        "active_document_path": active_doc_path,
    }


def _handle_get_drawing_info(params: Dict[str, Any]) -> Dict[str, Any]:
    max_entities = int(params.get("max_entities", 200))
    _, doc = get_acad(auto_launch=True, ensure_doc=True)
    ms = com_retry(lambda: doc.ModelSpace)
    total_entities = com_retry(lambda: int(ms.Count))

    layers = []
    layers_col = com_retry(lambda: doc.Layers)
    layer_count = com_retry(lambda: int(layers_col.Count))
    for i in range(layer_count):
        try:
            lyr = com_retry(lambda idx=i: layers_col.Item(idx))
            layers.append(
                {
                    "name": str(lyr.Name),
                    "color": int(lyr.color),
                    "linetype": str(lyr.Linetype),
                    "layer_on": bool(lyr.LayerOn),
                    "freeze": bool(lyr.Freeze),
                    "lock": bool(lyr.Lock),
                }
            )
        except Exception:
            pass

    entities = []
    limit = min(total_entities, max_entities)
    for i in range(limit):
        try:
            ent = com_retry(lambda idx=i: ms.Item(idx))
            info: Dict[str, Any] = {
                "handle": str(ent.Handle),
                "object_name": str(ent.ObjectName),
                "layer": str(ent.Layer),
            }
            for prop, key in [
                ("Length", "length"),
                ("Area", "area"),
                ("Radius", "radius"),
                ("TextString", "text"),
                ("EffectiveName", "block_name"),
            ]:
                try:
                    val = getattr(ent, prop)
                    info[key] = round(float(val), 4) if isinstance(val, (int, float)) else str(val)
                except Exception:
                    pass
            entities.append(info)
        except Exception:
            pass

    return {
        "status": "ok",
        "document": str(doc.Name),
        "active_layer": str(doc.ActiveLayer.Name),
        "total_modelspace_entities": total_entities,
        "returned_entities": len(entities),
        "layers": layers,
        "entities": entities,
    }


def _handle_get_entity_details(params: Dict[str, Any]) -> Dict[str, Any]:
    handle = str(params["handle"])
    _, doc = get_acad(auto_launch=True, ensure_doc=True)
    ent = com_retry(lambda: doc.HandleToObject(handle))
    details: Dict[str, Any] = {
        "handle": str(ent.Handle),
        "object_name": str(ent.ObjectName),
        "layer": str(ent.Layer),
        "color": int(ent.color),
        "linetype": str(ent.Linetype),
    }

    try:
        min_pt, max_pt = ent.GetBoundingBox()
        details["bounding_box"] = {
            "min": [round(float(c), 4) for c in min_pt],
            "max": [round(float(c), 4) for c in max_pt],
        }
    except Exception:
        pass

    optional_props = [
        "StartPoint",
        "EndPoint",
        "Center",
        "InsertionPoint",
        "Coordinates",
        "Radius",
        "Length",
        "Area",
        "Volume",
        "Closed",
        "TextString",
        "Height",
        "Rotation",
        "EffectiveName",
    ]
    for prop in optional_props:
        try:
            val = getattr(ent, prop)
            if isinstance(val, (tuple, list)):
                details[prop] = [round(float(x), 4) for x in val]
            elif isinstance(val, float):
                details[prop] = round(val, 4)
            else:
                details[prop] = val
        except Exception:
            pass

    return {"status": "ok", "entity": details}


def _handle_manage_layer(params: Dict[str, Any]) -> Dict[str, Any]:
    action = str(params.get("action", "create_or_update"))
    name = str(params["name"])
    color = int(params.get("color", 7))
    set_active = bool(params.get("set_active", False))

    _, doc = get_acad(auto_launch=True, ensure_doc=True)
    layers = com_retry(lambda: doc.Layers)

    if action in ("create_or_update", "set_active"):
        lyr = com_retry(lambda: layers.Add(name))
        if action == "create_or_update":
            com_retry(lambda: setattr(lyr, "color", int(color)))
        if set_active or action == "set_active":
            com_retry(lambda: setattr(doc, "ActiveLayer", lyr))
        return {
            "status": "ok",
            "layer": str(lyr.Name),
            "color": int(lyr.color),
            "active_layer": str(doc.ActiveLayer.Name),
        }
    elif action == "delete":
        lyr = com_retry(lambda: layers.Item(name))
        com_retry(lambda: lyr.Delete())
        return {"status": "ok", "deleted": name}
    else:
        return {"status": "error", "message": f"Unknown action: {action}"}


def _handle_draw_geometry(params: Dict[str, Any]) -> Dict[str, Any]:
    entity_type = str(params["entity_type"]).lower().strip()
    gparams = params.get("params", {})
    layer = str(params.get("layer", ""))
    color = params.get("color", None)

    _, doc = get_acad(auto_launch=True, ensure_doc=True)
    ms = com_retry(lambda: doc.ModelSpace)

    def _pt(arr):
        if len(arr) == 2:
            return vt_point(arr[0], arr[1], 0.0)
        return vt_point(arr[0], arr[1], arr[2])

    ent = None
    if entity_type == "line":
        p1 = _pt(gparams["start"])
        p2 = _pt(gparams["end"])
        ent = com_retry(lambda: ms.AddLine(p1, p2))
    elif entity_type == "polyline":
        pts = gparams["points"]
        flat: List[float] = []
        for p in pts:
            flat.extend([float(p[0]), float(p[1])])
        arr = vt_float_array(flat)
        ent = com_retry(lambda: ms.AddLightWeightPolyline(arr))
        if gparams.get("closed", False):
            com_retry(lambda: setattr(ent, "Closed", True))
    elif entity_type == "rectangle":
        x1, y1 = float(gparams["corner1"][0]), float(gparams["corner1"][1])
        x2, y2 = float(gparams["corner2"][0]), float(gparams["corner2"][1])
        arr = vt_float_array([x1, y1, x2, y1, x2, y2, x1, y2])
        ent = com_retry(lambda: ms.AddLightWeightPolyline(arr))
        com_retry(lambda: setattr(ent, "Closed", True))
    elif entity_type == "circle":
        c = _pt(gparams["center"])
        r = float(gparams["radius"])
        ent = com_retry(lambda: ms.AddCircle(c, r))
    elif entity_type == "arc":
        c = _pt(gparams["center"])
        r = float(gparams["radius"])
        a1 = math.radians(float(gparams["start_angle_deg"]))
        a2 = math.radians(float(gparams["end_angle_deg"]))
        ent = com_retry(lambda: ms.AddArc(c, r, a1, a2))
    elif entity_type == "text":
        txt = str(gparams["text"])
        pt = _pt(gparams["point"])
        h = float(gparams.get("height", 10.0))
        ent = com_retry(lambda: ms.AddText(txt, pt, h))
    elif entity_type == "box":
        c = _pt(gparams["center"])
        l = float(gparams["length"])
        w = float(gparams["width"])
        h = float(gparams["height"])
        ent = com_retry(lambda: ms.AddBox(c, l, w, h))
    elif entity_type == "cylinder":
        c = _pt(gparams["center"])
        r = float(gparams["radius"])
        h = float(gparams["height"])
        ent = com_retry(lambda: ms.AddCylinder(c, r, h))
    elif entity_type == "sphere":
        c = _pt(gparams["center"])
        r = float(gparams["radius"])
        ent = com_retry(lambda: ms.AddSphere(c, r))
    else:
        return {"status": "error", "message": f"Unsupported entity_type '{entity_type}'"}

    if layer:
        com_retry(lambda: doc.Layers.Add(layer))
        com_retry(lambda: setattr(ent, "Layer", layer))
    if color is not None:
        com_retry(lambda: setattr(ent, "color", int(color)))

    com_retry(lambda: ent.Update())
    return {
        "status": "ok",
        "entity_type": entity_type,
        "handle": str(ent.Handle),
        "object_name": str(ent.ObjectName),
        "layer": str(ent.Layer),
    }


def _handle_execute_command(params: Dict[str, Any]) -> Dict[str, Any]:
    command = str(params["command"])
    _, doc = get_acad(auto_launch=True, ensure_doc=True)
    cmd = command if command.endswith("\n") else command + "\n"
    com_retry(lambda: doc.SendCommand(cmd))
    return {"status": "ok", "sent_command": command}


def _handle_execute_python(params: Dict[str, Any]) -> Dict[str, Any]:
    code = str(params["code"])
    acad, doc = get_acad(auto_launch=True, ensure_doc=True)
    ms = com_retry(lambda: doc.ModelSpace)
    ps = com_retry(lambda: doc.PaperSpace)

    env: Dict[str, Any] = {
        "acad": acad,
        "doc": doc,
        "ms": ms,
        "ps": ps,
        "vt_point": vt_point,
        "vt_float_array": vt_float_array,
        "com_retry": com_retry,
        "pythoncom": pythoncom,
        "win32com": win32com,
        "math": math,
        "result": None,
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        exec(code, env)

    return {
        "status": "ok",
        "result": env.get("result"),
        "stdout": buf.getvalue().strip(),
    }


BRIDGE_HANDLERS = {
    "ping": _handle_ping,
    "get_drawing_info": _handle_get_drawing_info,
    "get_entity_details": _handle_get_entity_details,
    "manage_layer": _handle_manage_layer,
    "draw_geometry": _handle_draw_geometry,
    "execute_command": _handle_execute_command,
    "execute_python": _handle_execute_python,
}


def dispatch_request(command: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    params = params or {}
    handler = BRIDGE_HANDLERS.get(command)
    if not handler:
        return {"status": "error", "message": f"Unknown command: {command}"}
    try:
        return handler(params)
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc(),
        }


def send_to_autocad(command: str, params: Dict[str, Any] | None = None, timeout: float = 120.0) -> Dict[str, Any]:
    """Send command to persistent local bridge on 127.0.0.1:8766, or execute in-process if unavailable."""
    payload = {"command": command, "params": params or {}}
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with socket.create_connection((ACAD_HOST, ACAD_PORT), timeout=timeout) as sock:
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
    except Exception:
        pass

    return dispatch_request(command, params)


def run_bridge_server():
    """Run single-threaded STA COM TCP bridge on 127.0.0.1:8766."""
    pythoncom.CoInitialize()
    print(f"[AutoCAD-Bridge] Connecting to AutoCAD 2027...", flush=True)
    res = dispatch_request("ping", {"auto_launch": True})
    print(f"[AutoCAD-Bridge] Status: {json.dumps(res, ensure_ascii=False)}", flush=True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((ACAD_HOST, ACAD_PORT))
        srv.listen(8)
        print(f"[AutoCAD-Bridge] Listening on {ACAD_HOST}:{ACAD_PORT}", flush=True)
        while True:
            conn, _ = srv.accept()
            with conn:
                try:
                    data = b""
                    while True:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                        if b"\n" in data:
                            break
                    if not data:
                        continue
                    req = json.loads(data.decode("utf-8").strip())
                    resp = dispatch_request(req.get("command", ""), req.get("params", {}))
                    out = (json.dumps(resp, ensure_ascii=False, default=str) + "\n").encode("utf-8")
                    conn.sendall(out)
                except Exception as e:
                    err = (json.dumps({"status": "error", "message": str(e)}) + "\n").encode("utf-8")
                    conn.sendall(err)


# ============================================================================
# MCP Tools
# ============================================================================

@mcp.tool()
def ping_autocad(auto_launch: bool = True) -> str:
    """Check if AutoCAD is running and connected via COM. Set auto_launch=True to start AutoCAD if closed."""
    return json.dumps(send_to_autocad("ping", {"auto_launch": auto_launch}), ensure_ascii=False, indent=2)


@mcp.tool()
def get_drawing_info(max_entities: int = 200) -> str:
    """List layers, blocks, and ModelSpace entities in the active AutoCAD drawing."""
    return json.dumps(send_to_autocad("get_drawing_info", {"max_entities": max_entities}), ensure_ascii=False, indent=2)


@mcp.tool()
def get_entity_details(handle: str) -> str:
    """Get detailed properties and bounding box of an AutoCAD entity by its Handle."""
    return json.dumps(send_to_autocad("get_entity_details", {"handle": handle}), ensure_ascii=False, indent=2)


@mcp.tool()
def manage_layer(
    action: str,
    name: str,
    color: int = 7,
    set_active: bool = False,
) -> str:
    """Create, update, or activate a layer in AutoCAD. action: 'create_or_update' | 'set_active' | 'delete'."""
    return json.dumps(
        send_to_autocad(
            "manage_layer",
            {"action": action, "name": name, "color": color, "set_active": set_active},
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def draw_geometry(
    entity_type: str,
    params: Dict[str, Any],
    layer: str = "",
    color: Optional[int] = None,
) -> str:
    """Draw 2D/3D geometry in AutoCAD ModelSpace."""
    return json.dumps(
        send_to_autocad(
            "draw_geometry",
            {"entity_type": entity_type, "params": params, "layer": layer, "color": color},
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def execute_command(command: str) -> str:
    """Send an AutoCAD command-line string or AutoLISP expression to the active drawing."""
    return json.dumps(send_to_autocad("execute_command", {"command": command}), ensure_ascii=False, indent=2)


@mcp.tool()
def execute_python_in_autocad(code: str) -> str:
    """Execute Python COM automation code against the active AutoCAD session."""
    return json.dumps(send_to_autocad("execute_python", {"code": code}), ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def export_clean_floorplan_for_3d(
    dxf_output_path: str,
    freeze_annotation_layers: bool = True,
    purge_unused: bool = True,
) -> str:
    """Prepare the active AutoCAD architectural drawing for 3ds Max / Blender extrusion (freezes dimension/text layers, purges unused blocks, exports clean DXF)."""
    out_norm = os.path.abspath(dxf_output_path)
    py_code = f"""
import os

os.makedirs(os.path.dirname({repr(out_norm)}), exist_ok=True)
frozen = []
if {repr(freeze_annotation_layers)}:
    for i in range(doc.Layers.Count):
        lyr = doc.Layers.Item(i)
        lname = str(lyr.Name).upper()
        if any(tag in lname for tag in ("DIM", "TEXT", "ANNO", "HATCH", "TITLE", "GRID")):
            try:
                if str(doc.ActiveLayer.Name) != str(lyr.Name):
                    lyr.Freeze = True
                    frozen.append(str(lyr.Name))
            except Exception:
                pass

if {repr(purge_unused)}:
    try:
        doc.PurgeAll()
    except Exception:
        pass

# 25 = ac2013_dxf / standard ASCII DXF compatible with Blender & 3ds Max importers
doc.SaveAs({repr(out_norm)}, 25)
result = {{
    "status": "ok",
    "dxf_path": {repr(out_norm)},
    "frozen_annotation_layers": frozen,
}}
"""
    return json.dumps(send_to_autocad("execute_python", {"code": py_code}), ensure_ascii=False, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(description="AutoCAD MCP Server, Bridge & CLI")
    parser.add_argument("--bridge", action="store_true", help="Run persistent local COM bridge on 127.0.0.1:8766")
    parser.add_argument(
        "--cli",
        choices=["ping", "info", "entity", "cmd", "py"],
        help="Run a direct CLI command instead of starting stdio MCP server",
    )
    parser.add_argument("--arg", type=str, default="", help="Handle, command, or inline code for --cli")
    parser.add_argument("--file", type=str, default="", help="Path to Python script file for --cli py")
    args, _ = parser.parse_known_args()

    if args.bridge:
        run_bridge_server()
        return

    if args.cli:
        if args.cli == "ping":
            res = send_to_autocad("ping", {"auto_launch": True})
        elif args.cli == "info":
            res = send_to_autocad("get_drawing_info", {})
        elif args.cli == "entity":
            res = send_to_autocad("get_entity_details", {"handle": args.arg})
        elif args.cli == "cmd":
            res = send_to_autocad("execute_command", {"command": args.arg})
        elif args.cli == "py":
            code = open(args.file, "r", encoding="utf-8").read() if args.file else args.arg
            res = send_to_autocad("execute_python", {"code": code})
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
