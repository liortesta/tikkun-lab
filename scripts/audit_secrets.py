"""Scan the project for anything that must never leave this machine.

Run before every push:  python scripts/audit_secrets.py

Exits non-zero if it finds a credential, so it can gate a commit. It checks the
files git would actually publish — respecting .gitignore — because a key sitting
in an ignored .env is fine and the same key inside a committed file is not.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each pattern is something that is a credential wherever it appears, not merely
# a word that often sits near one.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("OpenAI/Anthropic style key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("OpenRouter key", re.compile(r"sk-or-v1-[a-f0-9]{40,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Slack token", re.compile(r"xox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bare 32-hex secret", re.compile(r"(?<![a-f0-9])[a-f0-9]{32}(?![a-f0-9])")),
    ("assigned secret", re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential)\b\s*[:=]\s*"
        r"['\"][^'\"\s]{12,}['\"]")),
]

# Files whose whole purpose is to describe the shape of a secret, not hold one.
ALLOWLIST_FILES = {".env.example", "scripts/audit_secrets.py"}

# Hex-looking strings that are demonstrably not secrets.
FALSE_POSITIVE = re.compile(r"^(0+|f+|deadbeef.*)$", re.IGNORECASE)

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
                   ".woff", ".woff2", ".ttf", ".zip", ".gz"}


def tracked_files() -> list[Path]:
    """What git would publish: tracked plus untracked-but-not-ignored."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [ROOT / line for line in out.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        # No repository yet — fall back to everything not obviously local.
        skip = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".venv"}
        return [p for p in ROOT.rglob("*")
                if p.is_file() and not any(part in skip for part in p.parts)]


def scan() -> tuple[list[str], int]:
    findings: list[str] = []
    scanned = 0

    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWLIST_FILES or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1

        for line_no, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group(0)
                    if label == "bare 32-hex secret" and FALSE_POSITIVE.match(value):
                        continue
                    # Never print the secret itself — a scanner that echoes what
                    # it found just moves the leak into the build log.
                    findings.append(
                        f"{rel}:{line_no}  {label}  "
                        f"({len(value)} chars, starts {value[:4]}…)")
    return findings, scanned


def check_ignored() -> list[str]:
    """Files that hold real credentials must be ignored, not merely absent."""
    problems = []
    for name in (".env", "experiments.jsonl", "server.log", "server.out"):
        target = ROOT / name
        if not target.exists():
            continue
        try:
            result = subprocess.run(["git", "check-ignore", "-q", name],
                                    cwd=ROOT, capture_output=True)
            if result.returncode != 0:
                problems.append(f"{name} exists and is NOT git-ignored")
        except FileNotFoundError:
            problems.append(f"{name} exists and git is unavailable to check it")
    return problems


def main() -> int:
    findings, scanned = scan()
    ignore_problems = check_ignored()

    bar = "=" * 68
    print(bar)
    print("SECRET AUDIT")
    print(bar)
    print(f"  scanned {scanned} publishable files")

    for problem in ignore_problems:
        print(f"  [FAIL] {problem}")
    for finding in findings:
        print(f"  [FAIL] {finding}")

    print(bar)
    if findings or ignore_problems:
        print(f"  {len(findings) + len(ignore_problems)} problem(s) — DO NOT PUSH")
        print(bar)
        return 1
    print("  nothing publishable contains a credential")
    print(bar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
