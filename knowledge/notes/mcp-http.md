# Streamable HTTP 传输

Streamable HTTP 适合把 MCP Server 作为长期服务运行。Host 先初始化会话和协议版本，再通过 tools/list 发现工具，通过 tools/call 发送结构化参数。

浏览器打开根路径得到 404 不代表 MCP 失败；MCP 端点通常是 `/mcp`，健康检查是单独的 `/health`。协议测试应使用 MCP Client，而不是只用浏览器 GET。
