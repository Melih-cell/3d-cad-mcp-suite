"""
3ds Max 2027 MCP Live Bridge (Runs inside 3ds Max Python 3.13 environment)
Listens on 127.0.0.1:8765 on the main Qt event loop using PySide6.QtNetwork.QTcpServer.
"""

import builtins
import io
import json
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr

import pymxs
from PySide6 import QtCore, QtNetwork, QtWidgets

rt = pymxs.runtime

HOST = "127.0.0.1"
PORT = 8765


def _vec3_to_list(v):
    try:
        return [round(float(v.x), 4), round(float(v.y), 4), round(float(v.z), 4)]
    except Exception:
        return [0.0, 0.0, 0.0]


def get_scene_info():
    objs = []
    for obj in rt.objects:
        try:
            cls_name = str(rt.classOf(obj))
            super_cls = str(rt.superClassOf(obj))
            mods = []
            try:
                for m in obj.modifiers:
                    mods.append(str(m.name))
            except Exception:
                pass

            parent_name = None
            try:
                if obj.parent is not None:
                    parent_name = str(obj.parent.name)
            except Exception:
                pass

            bbox_min = _vec3_to_list(obj.min)
            bbox_max = _vec3_to_list(obj.max)
            size = [
                round(bbox_max[0] - bbox_min[0], 4),
                round(bbox_max[1] - bbox_min[1], 4),
                round(bbox_max[2] - bbox_min[2], 4),
            ]

            objs.append({
                "name": str(obj.name),
                "class": cls_name,
                "superClass": super_cls,
                "pos": _vec3_to_list(obj.pos),
                "bbox_min": bbox_min,
                "bbox_max": bbox_max,
                "size": size,
                "parent": parent_name,
                "modifiers": mods,
                "isHidden": bool(obj.isHidden),
                "isSelected": bool(obj.isSelected),
            })
        except Exception as e:
            objs.append({"name": str(getattr(obj, "name", "unknown")), "error": str(e)})

    scene_path = str(rt.maxFilePath) + str(rt.maxFileName)
    return {
        "status": "success",
        "scene_file": scene_path if scene_path else "Untitled.max",
        "object_count": len(objs),
        "objects": objs,
    }


def get_object_details(name: str):
    obj = rt.getNodeByName(name)
    if obj is None:
        return {"status": "error", "message": f"Object '{name}' not found in scene."}

    info = {
        "name": str(obj.name),
        "class": str(rt.classOf(obj)),
        "superClass": str(rt.superClassOf(obj)),
        "pos": _vec3_to_list(obj.pos),
        "pivot": _vec3_to_list(obj.pivot),
        "bbox_min": _vec3_to_list(obj.min),
        "bbox_max": _vec3_to_list(obj.max),
        "rotation_euler": [
            round(float(rt.quatToEuler(obj.rotation).x), 4),
            round(float(rt.quatToEuler(obj.rotation).y), 4),
            round(float(rt.quatToEuler(obj.rotation).z), 4),
        ],
        "scale": _vec3_to_list(obj.scale),
        "modifiers": [str(m.name) for m in obj.modifiers],
        "properties": {},
    }

    try:
        prop_names = rt.getPropNames(obj)
        for p in prop_names:
            p_str = str(p)
            try:
                val = getattr(obj, p_str)
                info["properties"][p_str] = str(val)
            except Exception:
                pass
    except Exception:
        pass

    try:
        num_verts = rt.getNumVerts(obj.mesh)
        num_faces = rt.getNumFaces(obj.mesh)
        info["mesh_stats"] = {"num_verts": int(num_verts), "num_faces": int(num_faces)}
    except Exception:
        pass

    return {"status": "success", "object": info}


def _cleanup_active_dialogs():
    dlg = getattr(builtins, "__active_walk_dlg__", None)
    if dlg is not None:
        try:
            if hasattr(dlg, "timer") and dlg.timer is not None:
                dlg.timer.stop()
            dlg.cam = None
            dlg.tgt = None
            dlg.flashlight = None
            dlg.close()
            dlg.deleteLater()
        except Exception:
            pass
        builtins.__active_walk_dlg__ = None


def _should_use_undo(code: str) -> bool:
    unsafe_keywords = (
        "resetMaxFile",
        "loadMaxFile",
        "saveMaxFile",
        "saveNodes",
        "exportFile",
        "render",
        "exec(",
        "open(",
        "Unwrap_UVW",
        "flattenMap",
        "maxOps.CollapseNode",
        "collapseStack",
    )
    lower_code = code.lower()
    return not any(k.lower() in lower_code for k in unsafe_keywords)


def execute_maxscript(code: str):
    res = None
    err_info = None
    try:
        if "resetmaxfile" in code.lower() or "loadmaxfile" in code.lower():
            _cleanup_active_dialogs()
        if _should_use_undo(code):
            with pymxs.undo(True, "MCP MaxScript"):
                try:
                    res = rt.execute(code)
                except Exception as inner_e:
                    err_info = (str(inner_e), traceback.format_exc())
                    raise
        else:
            try:
                res = rt.execute(code)
            except Exception as inner_e:
                err_info = (str(inner_e), traceback.format_exc())
                raise
        rt.redrawViews()
        if err_info is not None:
            return {"status": "error", "message": err_info[0], "traceback": err_info[1]}
        return {
            "status": "success",
            "result": str(res) if res is not None else "OK",
        }
    except Exception as e:
        return {
            "status": "error",
            "message": err_info[0] if err_info else str(e),
            "traceback": err_info[1] if err_info else traceback.format_exc(),
        }


def execute_python(code: str):
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    local_ns = {"rt": rt, "pymxs": pymxs, "result": None}
    try:
        if "resetmaxfile" in code.lower() or "loadmaxfile" in code.lower():
            _cleanup_active_dialogs()
        if _should_use_undo(code):
            with pymxs.undo(True, "MCP Python"), redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(code, local_ns, local_ns)
        else:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(code, local_ns, local_ns)
        rt.redrawViews()
        return {
            "status": "success",
            "result": local_ns.get("result"),
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


class MaxMCPBridgeServer(QtCore.QObject):
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
            rt.format("[3dsMax-MCP] Canli kopru aktif! Adres: 127.0.0.1:%\n", PORT)
        else:
            rt.format("[3dsMax-MCP] HATA: Port % dinlenemedi: %\n", PORT, self.server.errorString())
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
                    "app": "3ds Max",
                    "max_version": str(rt.maxVersion()),
                    "scene_file": str(rt.maxFilePath) + str(rt.maxFileName),
                }
            elif cmd == "get_scene_info":
                resp = get_scene_info()
            elif cmd == "get_object_details":
                resp = get_object_details(params.get("name", ""))
            elif cmd == "execute_maxscript":
                resp = execute_maxscript(params.get("code", ""))
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


def start_bridge():
    old = getattr(builtins, "__max_mcp_bridge__", None)
    if old is not None:
        try:
            old.stop()
        except Exception:
            pass

    app = QtWidgets.QApplication.instance()
    bridge = MaxMCPBridgeServer(parent=app)
    if bridge.start():
        builtins.__max_mcp_bridge__ = bridge
        return True
    return False


if __name__ == "__main__":
    start_bridge()
else:
    start_bridge()
