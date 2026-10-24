# Add MCP Tool

1. Define the stable service contract and failure table first.
2. Register the tool with Annotated/Field descriptions and ToolAnnotations.
3. Return ToolResult and pass through the common server wrapper for meta/audit/isError.
4. Add success, parameter, permission, timeout and fallback tests as applicable.
5. Regenerate docs/TOOLS.md and update docs/API.md.
