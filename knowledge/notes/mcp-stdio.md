# stdio 传输

stdio 模式由 Host 启动 Server 子进程，JSON-RPC 通过标准输入输出传输。日志不能写到 stdout，否则会污染协议；应写 stderr。

Claude Code 和 Cursor 常用命令、参数、环境变量配置 stdio Server。它无需监听端口，适合单用户本机工具，但进程生命周期由 Host 管理。
