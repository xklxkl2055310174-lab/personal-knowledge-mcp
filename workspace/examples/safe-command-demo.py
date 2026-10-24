"""Safe run_command demo: no secrets, network, or external file access."""

from pathlib import Path

files = sorted(path.name for path in Path(".").iterdir())
print("workspace entries:", ", ".join(files))
