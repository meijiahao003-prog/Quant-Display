"""Package the existing council plus actual expert text for the user's computer.
Purpose: produce a downloadable local runner without runtime accounts/history.
Real input: investment-agent/ reviewed source + exact uploaded method documents.
Runnable call: python scripts/package-investment-agent.py
Actual output: printed path, file count and bytes; see docs/INVESTMENT_AGENT.md.
Before/after: replaces public/investment-agent.zip; source files remain unchanged.
"""
from pathlib import Path
import zipfile
root=Path(__file__).resolve().parents[1]
source=root/'investment-agent'
output=root/'public/investment-agent.zip'
excluded={'.local','node_modules','.git','__pycache__'}
count=0
with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob('*')):
        if not path.is_file() or path.is_symlink(): continue
        rel=path.relative_to(source)
        if excluded.intersection(rel.parts) or path.name.startswith('.env'): continue
        archive.write(path,'expert-platform/'+rel.as_posix());count+=1
print(f'Investment agent: {count} files, {output.stat().st_size} bytes -> {output}')
