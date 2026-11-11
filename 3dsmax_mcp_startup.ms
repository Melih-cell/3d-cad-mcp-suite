-- Autodesk 3ds Max MCP Live Bridge Startup Script
-- Copy to %LOCALAPPDATA%\Autodesk\3dsMax\<version> - 64bit\ENU\scripts\startup\
-- Set MAX_MCP_BRIDGE_PATH environment variable or place max_bridge.py in the same folder.
try (
    local bridgePath = systemTools.getEnvVariable "MAX_MCP_BRIDGE_PATH"
    if (bridgePath == undefined or bridgePath == "") do (
        local thisDir = getFilenamePath (getSourceFileName())
        bridgePath = thisDir + "max_bridge.py"
    )
    if (doesFileExist bridgePath) then (
        python.ExecuteFile bridgePath
    ) else (
        format "[3dsMax-MCP] max_bridge.py not found at: %\n" bridgePath
    )
) catch (
    format "[3dsMax-MCP] Startup error: %\n" (getCurrentException())
)
