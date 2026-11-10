-- 3ds Max 2027 MCP Live Bridge Startup Script
try (
    python.ExecuteFile @"C:\Users\melih\.gemini\antigravity\scratch\3dsmax-mcp\max_bridge.py"
) catch (
    format "[3dsMax-MCP] Startup Hatasi: %\n" (getCurrentException())
)
