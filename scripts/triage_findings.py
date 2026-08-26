"""Decide which scanner hits are real credentials and which are placeholders.

    set GH_SCAN_TOKEN=...
    python scripts/triage_findings.py owner/repo path [path ...]

A scan report full of `api_key="your-key-here"` teaches you to ignore the report,
and the one live key hides in the noise. This fetches the offending lines and
classifies each: PLACEHOLDER, REFERENCE (a variable or template expansion) or
LIVE-LOOKING.

Prints a redacted form only — enough to recognise the line, never enough to use.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_github_account import PATTERNS, FALSE_HEX  # noqa: E402

# Words that mean "put your own value here".
PLACEHOLDER = re.compile(
    r"(?i)\b(your|my|the)[-_ ]?(own[-_ ]?)?(api|secret|access|auth|private)?[-_ ]?"
    r"(key|token|secret|password|id)\b"
    r"|<[^>]{2,40}>"
    r"|\byour[-_][\w-]+"
    r"|\b(xxx+|yyy+|zzz+|aaa+|abc123|changeme|placeholder|example|sample|dummy|"
    r"redacted|insert|replace|todo|fixme|foo|bar|test[-_]?key|fake)\b"
    r"|\.\.\.")

# Values that are read from somewhere else rather than written down.
REFERENCE = re.compile(
    r"\$\{[^}]+\}|\$[A-Z_][A-Z0-9_]*|%[A-Z_]+%|os\.(environ|getenv)|"
    r"process\.env|System\.getenv|secrets\.|vars\.|config\[|settings\.")


def redact(line: str) -> str:
    """Keep the shape of the line, remove anything usable from it."""
    out = line.strip()
    for _label, pattern, _sev in PATTERNS:
        out = pattern.sub(lambda m: f"{m.group(0)[:4]}…[{len(m.group(0))} chars]…", out)
    return out[:170]


def fetch(repo: str, path: str, token: str) -> str | None:
    url = (f"https://api.github.com/repos/{repo}/contents/"
           + urllib.parse.quote(path))
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "User-Agent": "triage",
                      "Accept": "application/vnd.github.raw"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        print(f"  could not fetch ({exc.code})")
        return None


def classify(line: str) -> str:
    if REFERENCE.search(line):
        return "REFERENCE"
    if PLACEHOLDER.search(line):
        return "PLACEHOLDER"
    return "LIVE-LOOKING"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    token = os.environ.get("GH_SCAN_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("set GH_SCAN_TOKEN first")
        return 2

    repo, paths = argv[1], argv[2:]
    verdicts = {"LIVE-LOOKING": 0, "PLACEHOLDER": 0, "REFERENCE": 0}

    for path in paths:
        print(f"\n{'=' * 74}\n{repo}  {path}\n{'=' * 74}")
        text = fetch(repo, path, token)
        if text is None:
            continue
        found = False
        for number, line in enumerate(text.splitlines(), 1):
            if len(line) > 4000:
                continue
            for label, pattern, severity in PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                if label == "bare 32-hex secret" and FALSE_HEX.match(match.group(0)):
                    continue
                verdict = classify(line)
                verdicts[verdict] += 1
                found = True
                mark = "!!" if verdict == "LIVE-LOOKING" else "  "
                print(f" {mark} line {number:<5} {verdict:<13} {label}")
                print(f"      {redact(line)}")
                break
        if not found:
            print("  no matches on the current version of this file")

    print(f"\n{'=' * 74}")
    print(f"  LIVE-LOOKING {verdicts['LIVE-LOOKING']}   "
          f"PLACEHOLDER {verdicts['PLACEHOLDER']}   REFERENCE {verdicts['REFERENCE']}")
    print("=" * 74)
    return 1 if verdicts["LIVE-LOOKING"] else 0


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (used inside fetch)
    sys.exit(main(sys.argv))
