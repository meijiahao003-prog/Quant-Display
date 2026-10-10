"""Fail on known disclosure patterns without printing suspect values.

This scanner covers the newly integrated public system; it is not a proof that
arbitrary intellectual property is safe to publish. Existing results are an
explicit, separately reviewed public archive.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SCOPES = ["src/futures_research", "src/quant_research", "docs/futures", "docs/system",
          "tests/futures", "tests/test_factor_bridge.py", "configs", "README.md",
          ".github", "pyproject.toml", "MANIFEST.in", ".gitignore", "requirements",
          "examples/run_research_pipeline.py", "examples/run_factor_bridge_demo.py",
          "tools/check_public_release.py", "tests/test_public_release.py"]
PATTERNS = {
    "private_absolute_path": re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/|[A-Za-z]:[\\/]Users[\\/]"),
    "ipv4_literal": re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    "email_address": re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    "github_token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "url_credentials": re.compile(r"https?://[^\s/:]+:[^\s/@]+@"),
}
SENSITIVE_NAMES = re.compile(r"^(?:password|passwd|token|api_key|secret|access_key|secret_key)$", re.I)
PLACEHOLDERS = {"", "REDACTED", "CHANGE_ME", "YOUR_TOKEN", "YOUR_API_KEY"}
FORBIDDEN_SUFFIXES = {".parquet", ".feather", ".pkl", ".pickle", ".joblib", ".pt", ".pth", ".ipynb"}


def inspect_text(text: str, suffix: str) -> list[tuple[int, str]]:
    issues = []
    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            issues.append((text.count("\n", 0, match.start()) + 1, name))
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError as error:
            return issues + [(error.lineno or 0, "python_syntax")]
        for node in ast.walk(tree):
            pairs = []
            if isinstance(node, ast.Assign):
                pairs = [(n.id, node.value) for n in node.targets if isinstance(n, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                pairs = [(node.target.id, node.value)]
            elif isinstance(node, ast.Dict):
                pairs = [(k.value, v) for k, v in zip(node.keys, node.values)
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            elif isinstance(node, ast.keyword) and node.arg:
                pairs = [(node.arg, node.value)]
            for name, value in pairs:
                if SENSITIVE_NAMES.match(name) and isinstance(value, ast.Constant):
                    if isinstance(value.value, str) and value.value not in PLACEHOLDERS:
                        issues.append((value.lineno, "literal_credential"))
    if suffix == ".json":
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if SENSITIVE_NAMES.match(str(key)) and isinstance(item, str) and item not in PLACEHOLDERS:
                        issues.append((1, "literal_credential"))
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        try:
            walk(json.loads(text))
        except json.JSONDecodeError:
            issues.append((1, "invalid_json"))
    return sorted(set(issues))


def scan(root: Path = ROOT):
    findings, count = [], 0
    for scope in SCOPES:
        item = root / scope
        paths = sorted(item.rglob("*")) if item.is_dir() else [item]
        for path in paths:
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            count += 1
            rel = str(path.relative_to(root))
            if path.is_symlink():
                findings.append({"file": rel, "line": 0, "category": "symlink"})
                continue
            if path.suffix in FORBIDDEN_SUFFIXES:
                findings.append({"file": rel, "line": 0, "category": "nonpublic_artifact"})
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                findings.append({"file": rel, "line": 0, "category": "binary_artifact"})
                continue
            findings.extend({"file": rel, "line": line, "category": category}
                            for line, category in inspect_text(text, path.suffix))
    return {"checked_files": count, "findings": findings, "passed": not findings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = scan()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Public-release check: {result['checked_files']} files, {len(result['findings'])} findings")
        for item in result["findings"]:
            print(f"{item['file']}:{item['line']} {item['category']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
