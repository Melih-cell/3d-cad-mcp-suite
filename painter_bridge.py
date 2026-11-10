"""
Adobe Substance 3D Painter MCP Live Bridge (Runs inside Substance 3D Painter Python 3.13 environment)
Listens on 127.0.0.1:8768 on the main Qt event loop using PySide6.QtNetwork.QTcpServer.
"""

import builtins
import io
import json
import math
import os
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr

from PySide6 import QtCore, QtNetwork, QtWidgets

import substance_painter as sp
import substance_painter.baking
import substance_painter.colormanagement
import substance_painter.display
import substance_painter.event
import substance_painter.export
import substance_painter.js
import substance_painter.layerstack
import substance_painter.logging
import substance_painter.project
import substance_painter.properties
import substance_painter.resource
import substance_painter.source
import substance_painter.textureset
import substance_painter.ui

HOST = "127.0.0.1"
PORT = 8768


def _serialize_effect_node(node) -> dict:
    info = {
        "uid": int(node.uid()),
        "name": node.get_name(),
        "type": node.get_type().name if hasattr(node.get_type(), "name") else str(node.get_type()),
        "visible": bool(node.is_visible()),
    }
    return info


def _serialize_layer_node(node) -> dict:
    node_type = node.get_type()
    type_name = node_type.name if hasattr(node_type, "name") else str(node_type)
    info = {
        "uid": int(node.uid()),
        "name": node.get_name(),
        "type": type_name,
        "visible": bool(node.is_visible()),
        "has_mask": False,
        "mask_background": None,
        "content_effects": [],
        "mask_effects": [],
    }
    try:
        info["has_mask"] = bool(node.has_mask())
        if info["has_mask"]:
            mb = node.get_mask_background()
            info["mask_background"] = mb.name if hasattr(mb, "name") else str(mb)
    except Exception:
        pass

    try:
        info["content_effects"] = [_serialize_effect_node(e) for e in node.content_effects()]
    except Exception:
        pass

    try:
        info["mask_effects"] = [_serialize_effect_node(e) for e in node.mask_effects()]
    except Exception:
        pass

    if node_type == sp.layerstack.NodeType.GroupLayer:
        try:
            info["sub_layers"] = [_serialize_layer_node(sub) for sub in node.sub_layers()]
        except Exception:
            info["sub_layers"] = []

    return info


def get_ping_info() -> dict:
    app_ver = QtCore.QCoreApplication.applicationVersion() or "12.2.0"
    api_ver = getattr(sp, "__version__", "0.3.5")
    is_open = bool(sp.project.is_open())
    proj_name = None
    proj_path = None
    needs_saving = False
    ts_count = 0

    if is_open:
        try:
            proj_name = sp.project.name()
            proj_path = sp.project.file_path()
            needs_saving = bool(sp.project.needs_saving())
            ts_count = len(sp.textureset.all_texture_sets())
        except Exception:
            pass

    return {
        "status": "success",
        "app": "Adobe Substance 3D Painter",
        "app_version": str(app_ver),
        "python_api_version": str(api_ver),
        "project_open": is_open,
        "project_name": proj_name,
        "project_file": proj_path,
        "needs_saving": needs_saving,
        "texture_set_count": ts_count,
    }


def get_project_info() -> dict:
    if not sp.project.is_open():
        return {
            "status": "success",
            "project_open": False,
            "message": "No project is currently open in Substance 3D Painter.",
        }

    in_edition = bool(sp.project.is_in_edition_state())
    active_ts_name = None
    active_stack_name = None
    try:
        active_stack = sp.textureset.get_active_stack()
        if active_stack:
            active_stack_name = active_stack.name()
            active_ts_name = active_stack.material().name()
    except Exception:
        pass

    texture_sets_data = []
    for ts in sp.textureset.all_texture_sets():
        res = ts.get_resolution()
        try:
            uv_tiles = ts.all_uv_tiles()
            uv_count = len(uv_tiles)
        except Exception:
            uv_count = 0

        stacks_data = []
        for stack in ts.all_stacks():
            channels_dict = {}
            try:
                for ch_type, ch_obj in stack.all_channels().items():
                    ch_key = ch_type.name if hasattr(ch_type, "name") else str(ch_type)
                    fmt = ch_obj.format()
                    channels_dict[ch_key] = {
                        "format": fmt.name if hasattr(fmt, "name") else str(fmt),
                        "bit_depth": ch_obj.bit_depth(),
                        "is_color": bool(ch_obj.is_color()),
                    }
            except Exception:
                pass

            layers_list = []
            if in_edition:
                try:
                    root_nodes = sp.layerstack.get_root_layer_nodes(stack)
                    layers_list = [_serialize_layer_node(n) for n in root_nodes]
                except Exception:
                    pass

            stacks_data.append({
                "name": stack.name() or "Main",
                "channels": channels_dict,
                "layer_count": len(layers_list),
                "layers": layers_list,
            })

        texture_sets_data.append({
            "name": ts.name(),
            "resolution": {"width": res.width, "height": res.height},
            "is_layered": bool(ts.is_layered_material()),
            "uv_tiles_count": uv_count,
            "stacks": stacks_data,
        })

    return {
        "status": "success",
        "project_open": True,
        "in_edition_state": in_edition,
        "project_name": sp.project.name(),
        "project_file": sp.project.file_path(),
        "needs_saving": bool(sp.project.needs_saving()),
        "active_texture_set": active_ts_name,
        "active_stack": active_stack_name,
        "texture_set_count": len(texture_sets_data),
        "texture_sets": texture_sets_data,
    }


def open_or_create_project(params: dict) -> dict:
    project_path = params.get("project_path") or ""
    mesh_path = params.get("mesh_path") or ""
    resolution = int(params.get("resolution", 2048))
    normal_format_str = str(params.get("normal_map_format", "OpenGL")).lower()
    close_existing = bool(params.get("close_existing", True))

    if sp.project.is_open() and close_existing:
        sp.project.close()

    if project_path:
        project_path = os.path.abspath(project_path)
        if not os.path.exists(project_path):
            return {"status": "error", "message": f"Project file not found: {project_path}"}
        sp.project.open(project_path)
        return {
            "status": "success",
            "action": "opened",
            "project_name": sp.project.name(),
            "project_file": sp.project.file_path(),
        }

    if mesh_path:
        mesh_path = os.path.abspath(mesh_path)
        if not os.path.exists(mesh_path):
            return {"status": "error", "message": f"Mesh file not found: {mesh_path}"}
        normal_fmt = (
            sp.project.NormalMapFormat.DirectX
            if "direct" in normal_format_str or "dx" in normal_format_str
            else sp.project.NormalMapFormat.OpenGL
        )
        settings = sp.project.Settings(
            default_texture_resolution=resolution,
            normal_map_format=normal_fmt,
        )
        sp.project.create(mesh_file_path=mesh_path, settings=settings)
        return {
            "status": "success",
            "action": "created",
            "mesh_path": mesh_path,
            "resolution": resolution,
            "project_name": sp.project.name(),
        }

    return {"status": "error", "message": "Provide either 'project_path' (.spp) or 'mesh_path' (.fbx/.obj/.usd/.gltf)."}


def save_project(params: dict) -> dict:
    if not sp.project.is_open():
        return {"status": "error", "message": "No project is currently open."}
    file_path = params.get("file_path") or ""
    if file_path:
        file_path = os.path.abspath(file_path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        sp.project.save_as(file_path, sp.project.ProjectSaveMode.Full)
    else:
        curr = sp.project.file_path()
        if not curr:
            return {"status": "error", "message": "Project has not been saved yet; please provide 'file_path'."}
        sp.project.save(sp.project.ProjectSaveMode.Incremental)

    return {
        "status": "success",
        "project_name": sp.project.name(),
        "project_file": sp.project.file_path(),
        "needs_saving": bool(sp.project.needs_saving()),
    }


def search_resources(params: dict) -> dict:
    query = params.get("query") or ""
    usage_filter = (params.get("usage") or "").upper().strip()
    limit = int(params.get("limit", 30))

    found = sp.resource.search(query)
    results = []
    for res in found:
        try:
            usages = [u.name if hasattr(u, "name") else str(u) for u in res.usages()]
            if usage_filter and usage_filter not in [u.upper() for u in usages]:
                continue
            rid = res.identifier()
            res_type = res.type()
            results.append({
                "name": rid.name,
                "gui_name": res.gui_name(),
                "context": rid.context,
                "url": rid.url(),
                "type": res_type.name if hasattr(res_type, "name") else str(res_type),
                "usages": usages,
                "category": res.category(),
            })
            if len(results) >= limit:
                break
        except Exception:
            continue

    return {
        "status": "success",
        "query": query,
        "usage_filter": usage_filter or None,
        "count": len(results),
        "resources": results,
    }


def _resolve_stack(texture_set_name: str = "") -> sp.textureset.Stack:
    if texture_set_name:
        for ts in sp.textureset.all_texture_sets():
            if ts.name() == texture_set_name:
                stacks = ts.all_stacks()
                if stacks:
                    return stacks[0]
        raise ValueError(f"Texture Set '{texture_set_name}' not found.")
    stack = sp.textureset.get_active_stack()
    if not stack:
        all_ts = sp.textureset.all_texture_sets()
        if not all_ts:
            raise ValueError("No Texture Sets available in project.")
        stack = all_ts[0].all_stacks()[0]
    return stack


def _find_resource_id(resource_url: str = "", resource_name: str = "", usage_hint: str = "") -> sp.resource.ResourceID | None:
    if resource_url:
        return sp.resource.ResourceID.from_url(resource_url)
    if resource_name:
        matches = sp.resource.search(resource_name)
        best = None
        for r in matches:
            rid = r.identifier()
            usages = [u.name.upper() for u in r.usages()]
            if usage_hint and usage_hint.upper() not in usages:
                continue
            if rid.name.lower() == resource_name.lower() or r.gui_name().lower() == resource_name.lower():
                return rid
            if best is None:
                best = rid
        if best is not None:
            return best
        raise ValueError(f"Resource '{resource_name}' (usage={usage_hint}) not found in shelves.")
    return None


def _find_layer_by_uid_or_name(stack: sp.textureset.Stack, layer_uid: int | None = None, layer_name: str = ""):
    if layer_uid is not None and int(layer_uid) > 0:
        return sp.layerstack.get_node_by_uid(int(layer_uid))

    def _walk(nodes):
        for n in nodes:
            if not layer_name or n.get_name() == layer_name:
                return n
            if n.get_type() == sp.layerstack.NodeType.GroupLayer:
                found = _walk(n.sub_layers())
                if found:
                    return found
        return None

    roots = sp.layerstack.get_root_layer_nodes(stack)
    if not roots:
        raise ValueError("Layer stack is empty.")
    if not layer_name:
        return roots[0]
    target = _walk(roots)
    if not target:
        raise ValueError(f"Layer '{layer_name}' not found in stack.")
    return target


def create_layer(params: dict) -> dict:
    if not sp.project.is_open() or not sp.project.is_in_edition_state():
        return {"status": "error", "message": "Project must be open and in edition state."}

    layer_type = str(params.get("layer_type", "fill")).lower().strip()
    name = params.get("name") or ""
    texture_set_name = params.get("texture_set") or ""
    resource_url = params.get("resource_url") or ""
    resource_name = params.get("resource_name") or ""
    base_color = params.get("base_color")
    metallic = params.get("metallic")
    roughness = params.get("roughness")

    stack = _resolve_stack(texture_set_name)
    pos = sp.layerstack.InsertPosition.from_textureset_stack(stack)

    with sp.layerstack.ScopedModification(f"MCP Create Layer ({layer_type})"):
        if layer_type == "smart_material":
            rid = _find_resource_id(resource_url, resource_name, "SMART_MATERIAL")
            if not rid:
                return {"status": "error", "message": "smart_material requires 'resource_name' or 'resource_url'."}
            node = sp.layerstack.insert_smart_material(pos, rid)
            if name:
                node.set_name(name)
        elif layer_type == "paint":
            node = sp.layerstack.insert_paint(pos)
            if name:
                node.set_name(name)
        elif layer_type == "group":
            node = sp.layerstack.insert_group(pos)
            if name:
                node.set_name(name)
        elif layer_type == "fill":
            node = sp.layerstack.insert_fill(pos)
            if name:
                node.set_name(name)
            if resource_url or resource_name:
                rid = _find_resource_id(resource_url, resource_name, "BASE_MATERIAL")
                if rid:
                    node.set_material_source(rid)
            else:
                Color = sp.colormanagement.Color
                ChannelType = sp.textureset.ChannelType
                active_chs = set(node.active_channels)
                stack_chs = set(stack.all_channels().keys())

                if base_color is not None and len(base_color) >= 3:
                    for ch_cand in (ChannelType.BaseColor, ChannelType.Diffuse):
                        if ch_cand in stack_chs:
                            active_chs.add(ch_cand)
                            node.active_channels = active_chs
                            node.set_source(ch_cand, Color(float(base_color[0]), float(base_color[1]), float(base_color[2])))
                            break

                if metallic is not None:
                    m_val = float(metallic)
                    for ch_cand in (ChannelType.Metallic, ChannelType.BaseMetalness):
                        if ch_cand in stack_chs:
                            active_chs.add(ch_cand)
                            node.active_channels = active_chs
                            node.set_source(ch_cand, Color(m_val, m_val, m_val))
                            break

                if roughness is not None:
                    r_val = float(roughness)
                    for ch_cand in (ChannelType.Roughness, ChannelType.SpecularRoughness):
                        if ch_cand in stack_chs:
                            active_chs.add(ch_cand)
                            node.active_channels = active_chs
                            node.set_source(ch_cand, Color(r_val, r_val, r_val))
                            break
        else:
            return {"status": "error", "message": f"Unsupported layer_type '{layer_type}'."}

    return {
        "status": "success",
        "layer": _serialize_layer_node(node),
    }


def add_layer_mask_or_effect(params: dict) -> dict:
    if not sp.project.is_open() or not sp.project.is_in_edition_state():
        return {"status": "error", "message": "Project must be open and in edition state."}

    texture_set_name = params.get("texture_set") or ""
    layer_uid = params.get("layer_uid")
    layer_name = params.get("layer_name") or ""
    effect_type = str(params.get("effect_type", "black_mask")).lower().strip()
    resource_url = params.get("resource_url") or ""
    resource_name = params.get("resource_name") or ""
    target_stack_type = str(params.get("stack_type", "mask")).lower().strip()

    stack = _resolve_stack(texture_set_name)
    layer = _find_layer_by_uid_or_name(stack, layer_uid, layer_name)

    with sp.layerstack.ScopedModification(f"MCP Add Effect ({effect_type})"):
        if effect_type == "black_mask":
            layer.add_mask(sp.layerstack.MaskBackground.Black)
            return {"status": "success", "layer": _serialize_layer_node(layer)}
        elif effect_type == "white_mask":
            layer.add_mask(sp.layerstack.MaskBackground.White)
            return {"status": "success", "layer": _serialize_layer_node(layer)}
        elif effect_type == "smart_mask":
            if not layer.has_mask():
                layer.add_mask(sp.layerstack.MaskBackground.Black)
            rid = _find_resource_id(resource_url, resource_name, "SMART_MASK")
            if not rid:
                return {"status": "error", "message": "smart_mask requires 'resource_name' or 'resource_url'."}
            pos = sp.layerstack.InsertPosition.inside_node(layer, sp.layerstack.NodeStack.Mask)
            sp.layerstack.insert_smart_mask(pos, rid)
            return {"status": "success", "layer": _serialize_layer_node(layer)}

        node_stack = (
            sp.layerstack.NodeStack.Mask
            if target_stack_type == "mask"
            else sp.layerstack.NodeStack.Content
        )
        if node_stack == sp.layerstack.NodeStack.Mask and not layer.has_mask():
            layer.add_mask(sp.layerstack.MaskBackground.Black)

        pos = sp.layerstack.InsertPosition.inside_node(layer, node_stack)

        if effect_type == "generator":
            rid = _find_resource_id(resource_url, resource_name, "GENERATOR")
            sp.layerstack.insert_generator_effect(pos, rid)
        elif effect_type == "filter":
            rid = _find_resource_id(resource_url, resource_name, "FILTER")
            sp.layerstack.insert_filter_effect(pos, rid)
        elif effect_type == "levels":
            sp.layerstack.insert_levels_effect(pos)
        elif effect_type == "fill":
            sp.layerstack.insert_fill(pos)
        elif effect_type == "paint":
            sp.layerstack.insert_paint(pos)
        else:
            return {"status": "error", "message": f"Unsupported effect_type: {effect_type}"}

    return {
        "status": "success",
        "layer": _serialize_layer_node(layer),
    }


def export_textures(params: dict) -> dict:
    if not sp.project.is_open():
        return {"status": "error", "message": "No project is currently open."}

    export_path = params.get("export_path") or sp.export.get_default_export_path()
    export_path = os.path.abspath(export_path)
    os.makedirs(export_path, exist_ok=True)

    preset_name = params.get("preset_name") or "PBR Metallic Roughness"
    preset_url = params.get("preset_url") or ""
    file_format = params.get("file_format") or "png"
    bit_depth = str(params.get("bit_depth") or "8")
    resolution = params.get("resolution")

    if not preset_url:
        if preset_name.startswith("resource://") or preset_name.startswith("export-preset-generator://"):
            preset_url = preset_name
        else:
            matched_url = None
            for p in sp.export.list_resource_export_presets():
                if p.resource_id.name.lower() == preset_name.lower():
                    matched_url = p.resource_id.url()
                    break
            if not matched_url:
                for p in sp.export.list_resource_export_presets():
                    if preset_name.lower() in p.resource_id.name.lower():
                        matched_url = p.resource_id.url()
                        break
            if matched_url:
                preset_url = matched_url
            else:
                preset_rid = sp.resource.ResourceID(context="starter_assets", name=preset_name)
                preset_url = preset_rid.url()

    export_list = [{"rootPath": ts.name()} for ts in sp.textureset.all_texture_sets()]
    param_dict = {
        "fileFormat": file_format,
        "bitDepth": bit_depth,
        "dithering": True,
        "paddingAlgorithm": "infinite",
    }
    if resolution:
        size_log2 = int(round(math.log2(int(resolution))))
        param_dict["sizeLog2"] = size_log2

    export_config = {
        "exportShaderParams": False,
        "exportPath": export_path,
        "defaultExportPreset": preset_url,
        "exportList": export_list,
        "exportParameters": [{"parameters": param_dict}],
    }

    res = sp.export.export_project_textures(export_config)
    exported_files = {}
    for k, v in res.textures.items():
        key_str = f"{k[0]}/{k[1]}" if k[1] else str(k[0])
        exported_files[key_str] = list(v)

    status_name = res.status.name if hasattr(res.status, "name") else str(res.status)
    return {
        "status": "success" if status_name == "Success" else "warning",
        "export_status": status_name,
        "message": res.message,
        "export_path": export_path,
        "preset_url": preset_url,
        "textures": exported_files,
    }


def execute_js(code: str) -> dict:
    try:
        res = sp.js.evaluate(code)
        return {"status": "success", "result": res}
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc(),
        }


def execute_python(code: str) -> dict:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    local_ns = {
        "sp": sp,
        "substance_painter": sp,
        "project": sp.project,
        "textureset": sp.textureset,
        "layerstack": sp.layerstack,
        "resource": sp.resource,
        "export": sp.export,
        "baking": sp.baking,
        "display": sp.display,
        "ui": sp.ui,
        "js": sp.js,
        "source": sp.source,
        "colormanagement": sp.colormanagement,
        "result": None,
    }
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, local_ns, local_ns)
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


class PainterMCPBridgeServer(QtCore.QObject):
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
            sp.logging.info(f"[SubstancePainter-MCP] Canli kopru aktif! Adres: {HOST}:{PORT}")
        else:
            sp.logging.error(f"[SubstancePainter-MCP] HATA: Port {PORT} dinlenemedi: {self.server.errorString()}")
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
                resp = get_ping_info()
            elif cmd == "get_project_info":
                resp = get_project_info()
            elif cmd == "open_or_create_project":
                resp = open_or_create_project(params)
            elif cmd == "save_project":
                resp = save_project(params)
            elif cmd == "search_resources":
                resp = search_resources(params)
            elif cmd == "create_layer":
                resp = create_layer(params)
            elif cmd == "add_layer_mask_or_effect":
                resp = add_layer_mask_or_effect(params)
            elif cmd == "export_textures":
                resp = export_textures(params)
            elif cmd == "execute_js":
                resp = execute_js(params.get("code", ""))
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


_BRIDGE_INSTANCE = None


def start_bridge(force: bool = False):
    global _BRIDGE_INSTANCE
    old = getattr(builtins, "__painter_mcp_bridge__", None) or _BRIDGE_INSTANCE
    if old is not None:
        try:
            if not force and old.server.isListening():
                return True
            old.stop()
        except Exception:
            pass

    bridge = PainterMCPBridgeServer(parent=None)
    if bridge.start():
        _BRIDGE_INSTANCE = bridge
        builtins.__painter_mcp_bridge__ = bridge
        return True
    return False


def stop_bridge():
    global _BRIDGE_INSTANCE
    old = getattr(builtins, "__painter_mcp_bridge__", None) or _BRIDGE_INSTANCE
    if old is not None:
        try:
            old.stop()
        except Exception:
            pass
        _BRIDGE_INSTANCE = None
        builtins.__painter_mcp_bridge__ = None
