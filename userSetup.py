"""
Autodesk Maya MCP Live Bridge Startup Script (userSetup.py)
Copy userSetup.py and maya_bridge.py into Documents/maya/<version>/scripts/
(or set MAYA_MCP_BRIDGE_DIR to the repository path).
Automatically starts the Maya MCP Bridge on 127.0.0.1:8767 after Maya UI initializes.
"""

import inspect
import os
import sys
import traceback
import maya.utils


def _resolve_bridge_dir() -> str:
    env_dir = os.environ.get("MAYA_MCP_BRIDGE_DIR", "").strip()
    if env_dir and os.path.isdir(env_dir):
        return env_dir
    try:
        src_file = inspect.getfile(inspect.currentframe())
        local_dir = os.path.dirname(os.path.abspath(src_file))
        if os.path.isdir(local_dir):
            return local_dir
    except Exception:
        pass
    return ""


def _init_maya_mcp_bridge():
    try:
        bridge_dir = _resolve_bridge_dir()
        if bridge_dir and bridge_dir not in sys.path:
            sys.path.insert(0, bridge_dir)
        import maya_bridge
        maya_bridge.start_bridge(force=False)
    except Exception:
        print("[Maya-MCP] Failed to initialize live bridge:")
        traceback.print_exc()


maya.utils.executeDeferred(_init_maya_mcp_bridge)
