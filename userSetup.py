"""
Autodesk Maya 2027 MCP Live Bridge Startup Script (userSetup.py)
Automatically starts the Maya MCP Bridge on 127.0.0.1:8767 after Maya UI initializes.
"""

import os
import sys
import traceback
import maya.utils

BRIDGE_DIR = r"C:\Users\melih\.gemini\antigravity\scratch\maya-mcp"


def _init_maya_mcp_bridge():
    try:
        if BRIDGE_DIR not in sys.path:
            sys.path.insert(0, BRIDGE_DIR)
        import maya_bridge
        maya_bridge.start_bridge(force=False)
    except Exception:
        print("[Maya-MCP] Startup Hatasi:")
        traceback.print_exc()


maya.utils.executeDeferred(_init_maya_mcp_bridge)
