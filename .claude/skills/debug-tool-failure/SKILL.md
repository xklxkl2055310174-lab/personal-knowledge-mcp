# Debug Tool Failure

1. Locate trace_id in `.runtime/traces.sqlite3`.
2. Inspect tool_call then attempts; do not print redacted secrets.
3. Reproduce with the smallest MCP tools/call request.
4. Check category: parameter/permission/conflict require input or authorization changes;
   timeout/transient may use bounded retry.
5. Add a regression test before changing classification or fallback behavior.
