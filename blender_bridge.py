"""
Blender Live Bridge for Model Context Protocol (MCP)
Run inside Blender's Scripting workspace or via `blender --python blender_bridge.py`.
Listens on 127.0.0.1:8770 and executes commands safely on the Blender main thread via bpy.app.timers.
"""

import io
import json
import os
import queue
import socket
import sys
import tempfile
import threading
import time
import traceback
import bpy

HOST = os.environ.get("BLENDER_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("BLENDER_MCP_PORT", "8770"))

LOG_FILE = os.path.join(tempfile.gettempdir(), "blender_mcp_bridge.log")


def _log(msg: str):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(f"[Blender-MCP] {msg}")


_server_sock = None
_server_thread = None
_running = False

if "_mcp_task_queue" not in bpy.app.driver_namespace:
    bpy.app.driver_namespace["_mcp_task_queue"] = queue.Queue()
_task_queue = bpy.app.driver_namespace["_mcp_task_queue"]


def _main_thread_timer():
    if not _running:
        return None

    processed = 0
    while not _task_queue.empty() and processed < 10:
        processed += 1
        try:
            req_func, result_event, response_holder = _task_queue.get_nowait()
            try:
                response_holder["result"] = req_func()
                response_holder["status"] = "success"
            except Exception as e:
                response_holder["status"] = "error"
                response_holder["message"] = str(e)
                response_holder["traceback"] = traceback.format_exc()
            finally:
                result_event.set()
        except queue.Empty:
            break
        except Exception as e:
            print(f"[Blender MCP Timer Error] {e}", file=sys.stderr)

    return 0.02


def _cmd_ping():
    active_obj = bpy.context.active_object.name if bpy.context.active_object else None
    mode = bpy.context.mode
    scene = bpy.context.scene.name if bpy.context.scene else None
    filepath = bpy.data.filepath
    return {
        "blender_version": ".".join(map(str, bpy.app.version)),
        "status": "connected",
        "scene": scene,
        "active_object": active_obj,
        "mode": mode,
        "filepath": filepath or "Untitled (Unsaved)",
    }


def _cmd_get_scene_info():
    objects = []
    for obj in bpy.context.scene.objects:
        info = {
            "name": obj.name,
            "type": obj.type,
            "location": [round(float(v), 4) for v in obj.location],
            "visible": obj.visible_get(),
        }
        if obj.type == "MESH" and obj.data:
            info["vertices"] = len(obj.data.vertices)
            info["polygons"] = len(obj.data.polygons)
        objects.append(info)
    return {
        "objects": objects,
        "total_count": len(objects),
        "active_object": bpy.context.active_object.name if bpy.context.active_object else None,
        "mode": bpy.context.mode,
        "scene_name": bpy.context.scene.name,
    }


def _cmd_get_object_details(name: str):
    obj = bpy.data.objects.get(name)
    if not obj:
        raise ValueError(f"Object '{name}' not found.")
    details = {
        "name": obj.name,
        "type": obj.type,
        "location": [round(float(v), 4) for v in obj.location],
        "rotation_euler": [round(float(v), 4) for v in obj.rotation_euler],
        "scale": [round(float(v), 4) for v in obj.scale],
        "dimensions": [round(float(v), 4) for v in obj.dimensions],
        "modifiers": [m.name + f" ({m.type})" for m in obj.modifiers],
        "materials": [slot.material.name for slot in obj.material_slots if slot.material],
    }
    if obj.type == "MESH" and obj.data:
        details["vertices"] = len(obj.data.vertices)
        details["edges"] = len(obj.data.edges)
        details["polygons"] = len(obj.data.polygons)
    return details


def _cmd_execute_python(code: str):
    old_stdout, old_stderr = sys.stdout, sys.stderr
    redirected_stdout, redirected_stderr = io.StringIO(), io.StringIO()
    sys.stdout, sys.stderr = redirected_stdout, redirected_stderr

    globals_dict = {
        "bpy": bpy,
        "context": bpy.context,
        "data": bpy.data,
        "ops": bpy.ops,
        "__name__": "__main__",
    }
    try:
        exec(code, globals_dict)
        return {
            "stdout": redirected_stdout.getvalue(),
            "stderr": redirected_stderr.getvalue(),
            "result": globals_dict.get("result", None),
        }
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr


def _cmd_capture_viewport(output_path: str = "", width: int = 1280, height: int = 720):
    if not output_path:
        capture_dir = os.environ.get(
            "BLENDER_MCP_CAPTURE_DIR",
            os.path.join(tempfile.gettempdir(), "blender_mcp_captures"),
        )
        os.makedirs(capture_dir, exist_ok=True)
        output_path = os.path.join(capture_dir, f"viewport_{int(time.time() * 1000)}.png")

    scene = bpy.context.scene
    old_res_x, old_res_y = scene.render.resolution_x, scene.render.resolution_y
    old_res_pct, old_filepath = scene.render.resolution_percentage, scene.render.filepath

    try:
        scene.render.resolution_x = width
        scene.render.resolution_y = height
        scene.render.resolution_percentage = 100
        scene.render.filepath = output_path
        bpy.ops.render.opengl(write_still=True)
        return {"path": output_path, "width": width, "height": height}
    finally:
        scene.render.resolution_x, scene.render.resolution_y = old_res_x, old_res_y
        scene.render.resolution_percentage, scene.render.filepath = old_res_pct, old_filepath


def _cmd_set_mode(mode: str = "OBJECT", object_name: str = ""):
    if object_name:
        obj = bpy.data.objects.get(object_name)
        if obj:
            bpy.context.view_layer.objects.active = obj
    if bpy.context.active_object:
        bpy.ops.object.mode_set(mode=mode)
    return {
        "mode": bpy.context.mode,
        "active_object": bpy.context.active_object.name if bpy.context.active_object else None,
    }


def dispatch_command(command: str, params: dict):
    if command == "ping":
        return _cmd_ping()
    if command == "get_scene_info":
        return _cmd_get_scene_info()
    if command == "get_object_details":
        return _cmd_get_object_details(params.get("name", ""))
    if command == "execute_python":
        return _cmd_execute_python(params.get("code", ""))
    if command == "capture_viewport":
        return _cmd_capture_viewport(
            output_path=params.get("output_path", ""),
            width=params.get("width", 1280),
            height=params.get("height", 720),
        )
    if command == "set_mode":
        return _cmd_set_mode(mode=params.get("mode", "OBJECT"), object_name=params.get("object_name", ""))
    raise ValueError(f"Unknown command: '{command}'")


def _handle_client(conn: socket.socket):
    try:
        conn.settimeout(120.0)
        buf = b""
        line = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                line, _ = buf.split(b"\n", 1)
                break
        if not buf:
            return
        if not line:
            line = buf

        request = json.loads(line.decode("utf-8"))
        command = request.get("command", "")
        params = request.get("params", {})

        result_event = threading.Event()
        response_holder = {}

        def run_on_main():
            return dispatch_command(command, params)

        if bpy.app.background:
            try:
                response_holder["result"] = run_on_main()
                response_holder["status"] = "success"
            except Exception as e:
                response_holder["status"] = "error"
                response_holder["message"] = str(e)
                response_holder["traceback"] = traceback.format_exc()
        else:
            _task_queue.put((run_on_main, result_event, response_holder))
            timeout_sec = float(params.get("timeout", 90.0))
            if not result_event.wait(timeout=timeout_sec):
                response_holder = {
                    "status": "error",
                    "message": f"Command '{command}' timed out after {timeout_sec}s on Blender main thread.",
                }

        conn.sendall((json.dumps(response_holder, ensure_ascii=False) + "\n").encode("utf-8"))
    except Exception as e:
        try:
            conn.sendall((json.dumps({"status": "error", "message": f"Bridge error: {e}"}) + "\n").encode("utf-8"))
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _server_loop():
    global _server_sock, _running
    while _running and _server_sock:
        try:
            conn, _ = _server_sock.accept()
            threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()
        except Exception as e:
            if not _running:
                break
            _log(f"Server loop accept error: {e}")


def start_bridge(force: bool = False):
    global _server_sock, _server_thread, _running
    if _running and not force:
        return True

    stop_bridge()
    try:
        _server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _server_sock.bind((HOST, PORT))
        _server_sock.listen(5)
        _running = True

        _server_thread = threading.Thread(target=_server_loop, daemon=True)
        _server_thread.start()

        if not bpy.app.background:
            if "_mcp_timer_func" in bpy.app.driver_namespace:
                try:
                    if bpy.app.timers.is_registered(bpy.app.driver_namespace["_mcp_timer_func"]):
                        bpy.app.timers.unregister(bpy.app.driver_namespace["_mcp_timer_func"])
                except Exception:
                    pass
            bpy.app.driver_namespace["_mcp_timer_func"] = _main_thread_timer
            bpy.app.timers.register(_main_thread_timer, first_interval=0.02, persistent=True)

        _log(f"Bridge listening on {HOST}:{PORT}")
        return True
    except Exception as e:
        _log(f"Failed to start bridge on {HOST}:{PORT}: {e}")
        stop_bridge()
        return False


def stop_bridge():
    global _server_sock, _running
    _running = False
    if _server_sock:
        try:
            _server_sock.close()
        except Exception:
            pass
        _server_sock = None
    if "_mcp_timer_func" in bpy.app.driver_namespace:
        try:
            if bpy.app.timers.is_registered(bpy.app.driver_namespace["_mcp_timer_func"]):
                bpy.app.timers.unregister(bpy.app.driver_namespace["_mcp_timer_func"])
        except Exception:
            pass


if __name__ == "__main__":
    start_bridge(force=True)
