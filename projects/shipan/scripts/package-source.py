"""Build the downloadable source archive without runtime data or credentials.

Runnable example: python scripts/package-source.py
Real input: current tracked/unignored project source, excluding deployment identity,
runtime databases, dependencies, and build outputs. Actual output is printed by the
command. Before/after: source files are unchanged; public/market-lens-source.zip is
replaced with a compressed copy. No QMT/database data is read.
"""
from pathlib import Path
import subprocess
import zipfile

root = Path(__file__).resolve().parents[1]
output = root / "public/market-lens-source.zip"
paths = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
).decode().split("\0")
excluded = {".git", ".openai", ".sites-runtime", "node_modules", "dist", ".venv", ".wrangler", "__pycache__", ".local"}
count = 0
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in sorted(set(paths)):
        path = root / name
        if not name or not path.is_file() or path == output:
            continue
        if excluded.intersection(Path(name).parts) or name.startswith("backend/data/") or Path(name).name.startswith(".env"):
            continue
        archive.write(path, "market-lens/" + name)
        count += 1
    archive.writestr("market-lens/.openai/hosting.json", '{"d1":"DB","r2":null}\n')
print(f"Source archive: {count + 1} files, {output.stat().st_size} bytes -> {output}")
