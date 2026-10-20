# Local CI metadata

`ci_status(source="local")` always lists workflow files. If another automation writes a
`.ci/status.json` object, the tool also returns it as `last_recorded_status`.

Example:

```json
{"state": "passed", "commit": "abc123", "checked_at": "2026-10-15T08:00:00Z"}
```

Do not treat a stale status file as proof that the current commit passed.
