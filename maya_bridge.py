"""
Autodesk Maya 2027 MCP Live Bridge (Runs inside Maya Python 3.13 environment)
Listens on 127.0.0.1:8767 on the main Qt event loop using PySide6.QtNetwork.QTcpServer.
"""

import builtins
import io
import json
import os
import sys
import tempfile
import traceback
from contextlib import redirect_stdout, redirect_stderr

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
from PySide6 import QtCore, QtNetwork, QtWidgets

HOST = "127.0.0.1"
PORT = 8767

DEFAULT_CAMERAS = {"persp", "top", "front", "side", "bottom", "left", "right", "back"}


def _round_list(vals, digits=4):
    try:
        return [round(float(v), digits) for v in vals]
    except Exception:
        return [0.0, 0.0, 0.0]


def _get_shape_type(transform_node: str) -> str:
    try:
        shapes = cmds.listRelatives(transform_node, shapes=True, fullPath=True) or []
        if shapes:
            return str(cmds.nodeType(shapes[0]))
        return str(cmds.nodeType(transform_node))
    except Exception:
        return "transform"


def get_scene_info(include_defaults: bool = False):
    objs = []
    selected_set = set(cmds.ls(selection=True, long=True) or [])
    transforms = cmds.ls(type=("transform", "joint"), long=True) or []

    for long_name in transforms:
        short_name = long_name.split("|")[-1]
        shape_type = _get_shape_type(long_name)

        if not include_defaults and short_name in DEFAULT_CAMERAS and shape_type == "camera":
            continue

        try:
            pos = _round_list(cmds.xform(long_name, query=True, worldSpace=True, translation=True))
            rot = _round_list(cmds.xform(long_name, query=True, worldSpace=True, rotation=True))
            scale = _round_list(cmds.xform(long_name, query=True, relative=True, scale=True))

            try:
                bbox = cmds.exactWorldBoundingBox(long_name)
                bbox_min = _round_list(bbox[0:3])
                bbox_max = _round_list(bbox[3:6])
                size = [
                    round(bbox_max[0] - bbox_min[0], 4),
                    round(bbox_max[1] - bbox_min[1], 4),
                    round(bbox_max[2] - bbox_min[2], 4),
                ]
            except Exception:
                bbox_min = [0.0, 0.0, 0.0]
                bbox_max = [0.0, 0.0, 0.0]
                size = [0.0, 0.0, 0.0]

            parents = cmds.listRelatives(long_name, parent=True, fullPath=False) or []
            parent_name = parents[0] if parents else None

            visible = True
            try:
                visible = bool(cmds.getAttr(f"{long_name}.visibility"))
            except Exception:
                pass

            objs.append({
                "name": short_name,
                "long_name": long_name,
                "type": shape_type,
                "pos": pos,
                "rotation": rot,
                "scale": scale,
                "bbox_min": bbox_min,
                "bbox_max": bbox_max,
                "size": size,
                "parent": parent_name,
                "isHidden": not visible,
                "isSelected": long_name in selected_set,
            })
        except Exception as e:
            objs.append({
                "name": short_name,
                "long_name": long_name,
                "error": str(e),
            })

    scene_path = cmds.file(query=True, sceneName=True) or "Untitled"
    return {
        "status": "success",
        "scene_file": scene_path,
        "up_axis": cmds.upAxis(query=True, axis=True),
        "linear_unit": cmds.currentUnit(query=True, linear=True),
        "time_unit": cmds.currentUnit(query=True, time=True),
        "object_count": len(objs),
        "objects": objs,
    }


def get_object_details(name: str):
    matches = cmds.ls(name, long=True) or []
    if not matches:
        return {"status": "error", "message": f"Object '{name}' not found in Maya scene."}

    node = matches[0]
    short_name = node.split("|")[-1]
    node_type = str(cmds.nodeType(node))
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    shape_types = [str(cmds.nodeType(s)) for s in shapes]
    parents = cmds.listRelatives(node, parent=True, fullPath=False) or []
    children = cmds.listRelatives(node, children=True, type="transform", fullPath=False) or []

    info = {
        "name": short_name,
        "long_name": node,
        "node_type": node_type,
        "shapes": [s.split("|")[-1] for s in shapes],
        "shape_types": shape_types,
        "parent": parents[0] if parents else None,
        "children": children,
    }

    try:
        info["pos"] = _round_list(cmds.xform(node, query=True, worldSpace=True, translation=True))
        info["rotation"] = _round_list(cmds.xform(node, query=True, worldSpace=True, rotation=True))
        info["scale"] = _round_list(cmds.xform(node, query=True, relative=True, scale=True))
        info["pivot"] = _round_list(cmds.xform(node, query=True, worldSpace=True, rotatePivot=True))
        bbox = cmds.exactWorldBoundingBox(node)
        info["bbox_min"] = _round_list(bbox[0:3])
        info["bbox_max"] = _round_list(bbox[3:6])
        info["size"] = [
            round(info["bbox_max"][0] - info["bbox_min"][0], 4),
            round(info["bbox_max"][1] - info["bbox_min"][1], 4),
            round(info["bbox_max"][2] - info["bbox_min"][2], 4),
        ]
    except Exception:
        pass

    if "mesh" in shape_types or node_type == "mesh":
        try:
            info["mesh_stats"] = {
                "num_verts": cmds.polyEvaluate(node, vertex=True),
                "num_edges": cmds.polyEvaluate(node, edge=True),
                "num_faces": cmds.polyEvaluate(node, face=True),
                "num_tris": cmds.polyEvaluate(node, triangle=True),
                "uv_sets": cmds.polyUVSet(node, query=True, allUVSets=True) or [],
            }
        except Exception:
            pass

    materials = []
    shading_groups = []
    try:
        for s in shapes:
            sgs = cmds.listConnections(s, type="shadingEngine") or []
            for sg in sgs:
                if sg not in shading_groups:
                    shading_groups.append(sg)
                    shaders = cmds.ls(cmds.listConnections(sg), materials=True) or []
                    for sh in shaders:
                        if sh not in materials:
                            materials.append(sh)
    except Exception:
        pass
    info["shading_groups"] = shading_groups
    info["materials"] = materials

    try:
        hist = cmds.listHistory(node, pruneDagObjects=True) or []
        info["history"] = [str(h) for h in hist[:25]]
    except Exception:
        info["history"] = []

    attrs = {}
    try:
        attr_list = (cmds.listAttr(node, keyable=True) or []) + (cmds.listAttr(node, userDefined=True) or [])
        for a in dict.fromkeys(attr_list):
            try:
                val = cmds.getAttr(f"{node}.{a}")
                attrs[a] = val
            except Exception:
                pass
    except Exception:
        pass
    info["attributes"] = attrs

    return {"status": "success", "object": info}


def create_primitive(params: dict):
    prim_type = str(params.get("primitive_type", "cube")).lower()
    name = params.get("name") or None
    pos = params.get("position", [0.0, 0.0, 0.0])
    rot = params.get("rotation", [0.0, 0.0, 0.0])
    scale = params.get("scale", [1.0, 1.0, 1.0])
    extra = params.get("params") or {}

    cmds.undoInfo(openChunk=True, chunkName="MCP Create Primitive")
    try:
        kwargs = dict(extra)
        if name:
            kwargs["name"] = name

        if prim_type in ("cube", "box", "polycube"):
            res = cmds.polyCube(**kwargs)
        elif prim_type in ("sphere", "polysphere"):
            res = cmds.polySphere(**kwargs)
        elif prim_type in ("cylinder", "polycylinder"):
            res = cmds.polyCylinder(**kwargs)
        elif prim_type in ("cone", "polycone"):
            res = cmds.polyCone(**kwargs)
        elif prim_type in ("plane", "polyplane"):
            res = cmds.polyPlane(**kwargs)
        elif prim_type in ("torus", "polytorus"):
            res = cmds.polyTorus(**kwargs)
        elif prim_type == "locator":
            res = cmds.spaceLocator(**kwargs)
        elif prim_type == "joint":
            cmds.select(clear=True)
            j = cmds.joint(**kwargs)
            res = [j]
        elif prim_type == "camera":
            res = cmds.camera(**kwargs)
        elif prim_type in ("pointlight", "point_light"):
            shape = cmds.pointLight(**kwargs)
            res = cmds.listRelatives(shape, parent=True) or [shape]
        elif prim_type in ("directionallight", "directional_light"):
            shape = cmds.directionalLight(**kwargs)
            res = cmds.listRelatives(shape, parent=True) or [shape]
        elif prim_type in ("arealight", "area_light"):
            shape = cmds.shadingNode("areaLight", asLight=True, **kwargs)
            res = [shape]
        else:
            return {"status": "error", "message": f"Unsupported primitive_type: {prim_type}"}

        transform_node = res[0] if isinstance(res, (list, tuple)) else str(res)
        if pos and len(pos) == 3:
            cmds.xform(transform_node, worldSpace=True, translation=[float(pos[0]), float(pos[1]), float(pos[2])])
        if rot and len(rot) == 3:
            cmds.xform(transform_node, worldSpace=True, rotation=[float(rot[0]), float(rot[1]), float(rot[2])])
        if scale and len(scale) == 3:
            cmds.xform(transform_node, relative=True, scale=[float(scale[0]), float(scale[1]), float(scale[2])])

        cmds.refresh()
        return {
            "status": "success",
            "node": transform_node,
            "created": list(res) if isinstance(res, (list, tuple)) else [str(res)],
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}
    finally:
        cmds.undoInfo(closeChunk=True)


def capture_viewport(params: dict):
    output_path = params.get("output_path") or ""
    width = int(params.get("width", 1280))
    height = int(params.get("height", 720))

    if not output_path:
        output_path = os.path.join(tempfile.gettempdir(), "maya_mcp_viewport.png")

    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    try:
        cmds.refresh()
        frame = cmds.currentTime(query=True)
        saved = cmds.playblast(
            frame=frame,
            format="image",
            compression="png",
            completeFilename=output_path,
            widthHeight=(width, height),
            percent=100,
            viewer=False,
            showOrnaments=True,
            offScreen=True,
            forceOverwrite=True,
        )
        return {
            "status": "success",
            "output_path": saved or output_path,
            "width": width,
            "height": height,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc(),
        }


def _should_use_undo(code: str) -> bool:
    lower_code = code.lower()
    unsafe_patterns = (
        "cmds.file(",
        "file -f",
        "file -new",
        "file -open",
        "undoInfo",
    )
    return not any(p.lower() in lower_code for p in unsafe_patterns)


def execute_mel(code: str):
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    use_undo = _should_use_undo(code)
    if use_undo:
        cmds.undoInfo(openChunk=True, chunkName="MCP MEL")
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            res = mel.eval(code)
        try:
            cmds.refresh()
        except Exception:
            pass
        return {
            "status": "success",
            "result": res if res is not None else "OK",
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
            "traceback": traceback.format_exc(),
        }
    finally:
        if use_undo:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass


def execute_python(code: str):
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    local_ns = {
        "cmds": cmds,
        "mel": mel,
        "om": om,
        "result": None,
    }
    use_undo = _should_use_undo(code)
    if use_undo:
        cmds.undoInfo(openChunk=True, chunkName="MCP Python")
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, local_ns, local_ns)
        try:
            cmds.refresh()
        except Exception:
            pass
        res = local_ns.get("result")
        try:
            json.dumps(res)
        except Exception:
            res = str(res)
        return {
            "status": "success",
            "result": res,
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
            "traceback": traceback.format_exc(),
        }
    finally:
        if use_undo:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass


class MayaMCPBridgeServer(QtCore.QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QtNetwork.QTcpServer(self)
        self.server.newConnection.connect(self.on_new_connection)
        self.buffers = {}

    def start(self):
        if self.server.isListening():
            self.server.close()
        ok = self.server.listen(QtNetwork.QHostAddress.SpecialAddress.LocalHost, PORT)
        if ok:
            print(f"[Maya-MCP] Canli kopru aktif! Adres: {HOST}:{PORT}")
            try:
                cmds.inViewMessage(
                    amg=f"<hl>Maya-MCP</hl> Canlı Köprü Aktif ({HOST}:{PORT})",
                    pos="topCenter",
                    fade=True,
                )
            except Exception:
                pass
        else:
            print(f"[Maya-MCP] HATA: Port {PORT} dinlenemedi: {self.server.errorString()}")
        return ok

    def stop(self):
        if self.server.isListening():
            self.server.close()

    def on_new_connection(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            if sock is not None:
                self.buffers[sock] = b""
                sock.readyRead.connect(self.on_ready_read)
                sock.disconnected.connect(self.on_disconnected)

    def on_disconnected(self):
        sock = self.sender()
        if sock is not None:
            self.buffers.pop(sock, None)

    def on_ready_read(self):
        sock = self.sender()
        if sock is None:
            return
        data = bytes(sock.readAll())
        self.buffers[sock] = self.buffers.get(sock, b"") + data
        buf = self.buffers[sock]

        if b"\n" in buf:
            line, rest = buf.split(b"\n", 1)
            self.buffers[sock] = rest
            self.process_message(sock, line)
        else:
            try:
                json.loads(buf.decode("utf-8"))
                self.buffers[sock] = b""
                self.process_message(sock, buf)
            except Exception:
                pass

    def process_message(self, sock, raw_bytes):
        try:
            req = json.loads(raw_bytes.decode("utf-8"))
            cmd = req.get("command", "ping")
            params = req.get("params", {})

            if cmd == "ping":
                resp = {
                    "status": "success",
                    "app": "Autodesk Maya",
                    "maya_version": str(cmds.about(version=True)),
                    "api_version": str(cmds.about(apiVersion=True)),
                    "scene_file": cmds.file(query=True, sceneName=True) or "Untitled",
                    "up_axis": cmds.upAxis(query=True, axis=True),
                    "linear_unit": cmds.currentUnit(query=True, linear=True),
                }
            elif cmd == "get_scene_info":
                resp = get_scene_info(bool(params.get("include_defaults", False)))
            elif cmd == "get_object_details":
                resp = get_object_details(params.get("name", ""))
            elif cmd == "create_primitive":
                resp = create_primitive(params)
            elif cmd == "capture_viewport":
                resp = capture_viewport(params)
            elif cmd == "execute_mel":
                resp = execute_mel(params.get("code", ""))
            elif cmd == "execute_python":
                resp = execute_python(params.get("code", ""))
            else:
                resp = {"status": "error", "message": f"Unknown command: {cmd}"}
        except Exception as e:
            resp = {
                "status": "error",
                "message": str(e),
                "traceback": traceback.format_exc(),
            }

        out_bytes = (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")
        sock.write(out_bytes)
        sock.flush()


def start_bridge(force: bool = False):
    old = getattr(builtins, "__maya_mcp_bridge__", None)
    if old is not None:
        try:
            if not force and old.server.isListening():
                return True
            old.stop()
        except Exception:
            pass

    app = QtWidgets.QApplication.instance() or QtCore.QCoreApplication.instance()
    bridge = MayaMCPBridgeServer(parent=app)
    if bridge.start():
        builtins.__maya_mcp_bridge__ = bridge
        return True
    return False


if __name__ == "__main__":
    start_bridge(force=True)
else:
    start_bridge(force=False)
