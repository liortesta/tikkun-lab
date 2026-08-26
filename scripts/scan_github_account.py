"""Scan every repository on a GitHub account for exposed credentials.

    set GH_SCAN_TOKEN=...            (never pass a token as an argument —
    python scripts/scan_github_account.py --public      it lands in shell history)

Reads full git history, not just the current files. A key deleted in a later
commit is still in the repository and still readable by anyone who can clone it;
scanning only the working tree is the single most common way an audit misses a
live leak.

Never prints a secret it finds — only where it is and a four-character prefix,
so the report itself does not become the next leak.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://api.github.com"

# Patterns that identify a credential wherever they appear. Ordered roughly by
# how certain they are, because the first match on a line wins the label.
PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "critical"),
    ("GitHub fine-grained token", re.compile(r"github_pat_[A-Za-z0-9_]{60,}"), "critical"),
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "critical"),
    ("OpenRouter key", re.compile(r"sk-or-v1-[a-f0-9]{40,}"), "critical"),
    ("OpenAI-style key", re.compile(r"sk-(?!ant-|or-)[A-Za-z0-9_\-]{20,}"), "critical"),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}"), "critical"),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "critical"),
    ("Slack token", re.compile(r"xox[abprs]-[0-9A-Za-z\-]{10,}"), "critical"),
    ("Stripe secret key", re.compile(r"sk_live_[0-9A-Za-z]{20,}"), "critical"),
    ("SendGrid key", re.compile(r"SG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}"), "critical"),
    ("Twilio account sid", re.compile(r"AC[a-f0-9]{32}"), "high"),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "critical"),
    ("Google service account", re.compile(r'"type"\s*:\s*"service_account"'), "critical"),
    ("connection string with password",
     re.compile(r"(?i)(mongodb(\+srv)?|postgres(ql)?|mysql|redis|amqp)://[^\s:@/]+:[^\s:@/]{6,}@"), "high"),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "medium"),
    ("assigned secret",
     re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|"
                r"client[_-]?secret|password|passwd)\b\s*[:=]\s*"
                r"['\"][^'\"\s${}<>]{12,}['\"]"), "high"),
    ("bare 32-hex secret", re.compile(r"(?<![a-fA-F0-9])[a-f0-9]{32}(?![a-fA-F0-9])"), "medium"),
]

# Paths that are third-party code or build output. A key there is almost always
# a fixture from someone else's test suite, so it is reported separately rather
# than mixed in with the owner's own leaks.
VENDOR = re.compile(
    r"(^|/)(node_modules|vendor|third_party|dist|build|\.min\.|site-packages|"
    r"bower_components|Pods|Godeps|target/|\.next/|coverage/)", re.IGNORECASE)

# Extensions that never usefully hold a credential in plain text.
BINARY = re.compile(
    r"\.(png|jpe?g|gif|webp|ico|bmp|svg|pdf|zip|gz|tgz|bz2|7z|rar|exe|dll|so|dylib|"
    r"woff2?|ttf|otf|eot|mp3|mp4|avi|mov|wav|ogg|webm|psd|ai|sketch|db|sqlite3?|"
    r"pyc|pyo|class|jar|war|wasm|bin|dat|pack|idx)$", re.IGNORECASE)

# Filenames that should never be committed at all, regardless of content.
FORBIDDEN_NAME = re.compile(
    r"(^|/)(\.env(\.[\w.-]+)?|\.npmrc|\.pypirc|\.netrc|id_rsa|id_dsa|id_ecdsa|id_ed25519|"
    r"credentials\.json|service[-_]account.*\.json|secrets?\.(json|ya?ml|txt)|"
    r"\.aws/credentials)$", re.IGNORECASE)
FORBIDDEN_ALLOW = re.compile(r"\.env\.(example|sample|template|dist)$", re.IGNORECASE)

MAX_BLOB = 2_000_000     # a 2 MB text file is already pathological
FALSE_HEX = re.compile(r"^(0+|f+|[0-9]+|(deadbeef|abcdef|cafebabe|0123456789)[a-f0-9]*)$", re.I)

# "Put your own value here" and "read it from somewhere else". A scanner that
# reports these trains you to skim its output, which is exactly when a real key
# slips past.
PLACEHOLDER = re.compile(
    r"(?i)\byour[-_ ]?\w*[-_ ]?(key|token|secret|password|id|here)\b"
    r"|<[^>]{2,40}>"
    r"|\b(xxx+|yyy+|abc123|changeme|placeholder|example|sample|dummy|redacted|"
    r"insert|replace|todo|fixme|test[-_]?key|fake)\b"
    r"|\.\.\.")
REFERENCE = re.compile(
    r"\$\{[^}]+\}|\$[A-Z_][A-Z0-9_]*|%[A-Z_]+%|os\.(environ|getenv)|"
    r"process\.env|System\.getenv|secrets\.|vars\.")

# A 32-character hex string is a secret only sometimes; far more often it is a
# content hash, an ETag, a commit id or a generated row id. Suppress it where the
# surrounding words say so, and in file types that are output rather than source.
HASH_CONTEXT = re.compile(
    r"(?i)\b(sha1|sha256|sha512|md5|hash|digest|etag|checksum|integrity|"
    r"fingerprint|commit|revision|uuid|guid|nonce|salt|cache[-_]?key|"
    r"content[-_]?hash|bundle|chunk|asset|build[-_]?id|trace[-_]?id|"
    r"request[-_]?id|session[-_]?id|correlation)\b")
HASHY_FILE = re.compile(
    r"\.(lock|lockb|sum|csv|tsv|html?|svg|map|snap|log|ndjson|jsonl)$"
    r"|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|"
    r"Cargo\.lock|composer\.lock|Gemfile\.lock|go\.sum)$", re.IGNORECASE)


@dataclass
class Finding:
    repo: str
    label: str
    severity: str
    path: str
    prefix: str
    length: int
    in_history_only: bool = False
    vendored: bool = False

    def key(self) -> tuple:
        return (self.repo, self.label, self.path, self.prefix)


@dataclass
class RepoReport:
    name: str
    public: bool
    findings: list[Finding] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    error: str = ""
    blobs: int = 0


def api(path: str, token: str) -> list | dict:
    request = urllib.request.Request(
        API + path,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "secret-audit",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def list_repos(token: str) -> list[dict]:
    repos: list[dict] = []
    for page in range(1, 21):
        batch = api(f"/user/repos?per_page=100&page={page}&affiliation=owner", token)
        if not batch:
            break
        repos.extend(batch)
    return repos


def run(args: list[str], cwd: str | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout,
                          text=True, errors="replace")


def scan_blob(text: str, path: str = "") -> list[tuple[str, str, str, int]]:
    """Return (label, severity, prefix, length) for each credential in the text."""
    hits = []
    hashy = bool(HASHY_FILE.search(path))
    for line in text.splitlines():
        if len(line) > 4000:
            continue  # minified bundle; matching inside it is noise
        if PLACEHOLDER.search(line) or REFERENCE.search(line):
            continue
        for label, pattern, severity in PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            value = match.group(0)
            if label == "bare 32-hex secret":
                if FALSE_HEX.match(value) or hashy or HASH_CONTEXT.search(line):
                    continue
            hits.append((label, severity, value[:4], len(value)))
            break  # one finding per line is enough to act on
    return hits


def scan_repo(name: str, public: bool, token: str, workdir: str,
              history: bool = True) -> RepoReport:
    report = RepoReport(name=name, public=public)
    mirror = os.path.join(workdir, name.replace("/", "_") + ".git")

    # A mirror clone is bare — every ref and every commit, no working copy — so
    # it is both complete and far smaller than a normal clone.
    url = f"https://x-access-token:{token}@github.com/{name}.git"
    depth = [] if history else ["--depth", "1"]
    result = run(["git", "clone", "--mirror", "--quiet", *depth, url, mirror])
    if result.returncode != 0:
        report.error = (result.stderr or "clone failed").strip().replace(token, "***")[:200]
        return report

    try:
        listing = run(["git", "rev-list", "--objects", "--all"], cwd=mirror)
        if listing.returncode != 0:
            report.error = "rev-list failed"
            return report

        # sha -> the first path it was ever seen at
        blobs: dict[str, str] = {}
        for line in listing.stdout.splitlines():
            parts = line.split(" ", 1)
            if len(parts) != 2 or not parts[1].strip():
                continue
            sha, path = parts[0], parts[1].strip()
            if BINARY.search(path):
                continue
            blobs.setdefault(sha, path)

        # Match on the blob's own hash, not on whether the path still exists.
        # A secret deleted from a file that is still tracked would otherwise be
        # reported as CURRENT, and whoever checked the live file would find
        # nothing and wrongly conclude they were safe — while the old blob stays
        # readable to anyone who clones the repository.
        head_blobs: set[str] = set()
        head_paths: set[str] = set()
        head = run(["git", "ls-tree", "-r", "HEAD"], cwd=mirror)
        if head.returncode == 0:
            for line in head.stdout.splitlines():
                meta, _, path = line.partition("\t")
                parts = meta.split()
                if len(parts) >= 3:
                    head_blobs.add(parts[2])
                    head_paths.add(path.strip())

        for path in set(blobs.values()) | head_paths:
            if FORBIDDEN_NAME.search(path) and not FORBIDDEN_ALLOW.search(path):
                report.forbidden.append(path)

        seen: set[tuple] = set()
        for sha, path in blobs.items():
            content = run(["git", "cat-file", "-p", sha], cwd=mirror, timeout=120)
            if content.returncode != 0 or len(content.stdout) > MAX_BLOB:
                continue
            report.blobs += 1
            for label, severity, prefix, length in scan_blob(content.stdout, path):
                finding = Finding(
                    repo=name, label=label, severity=severity, path=path,
                    prefix=prefix, length=length,
                    in_history_only=sha not in head_blobs,
                    vendored=bool(VENDOR.search(path)))
                if finding.key() in seen:
                    continue
                seen.add(finding.key())
                report.findings.append(finding)
    finally:
        shutil.rmtree(mirror, ignore_errors=True)

    return report


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}


def render(reports: list[RepoReport]) -> int:
    bar = "=" * 78
    own = [f for r in reports for f in r.findings if not f.vendored]
    vendored = [f for r in reports for f in r.findings if f.vendored]
    forbidden = [(r, p) for r in reports for p in r.forbidden]

    print(bar)
    print("GITHUB ACCOUNT SECRET AUDIT")
    print(bar)
    print(f"  {len(reports)} repositories scanned, "
          f"{sum(r.blobs for r in reports):,} text blobs read from full history")
    errors = [r for r in reports if r.error]
    if errors:
        print(f"  {len(errors)} could not be read:")
        for r in errors:
            print(f"    {r.name}: {r.error}")

    if forbidden:
        print()
        print("FILES THAT SHOULD NEVER BE COMMITTED")
        print("-" * 78)
        for r, path in sorted(forbidden, key=lambda x: (not x[0].public, x[0].name)):
            tag = "PUBLIC " if r.public else "private"
            print(f"  [{tag}] {r.name}  ->  {path}")

    if own:
        print()
        print("CREDENTIALS IN YOUR OWN CODE")
        print("-" * 78)
        own.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9),
                                not next(r.public for r in reports if r.name == f.repo)))
        for f in own:
            public = next(r.public for r in reports if r.name == f.repo)
            tag = "PUBLIC " if public else "private"
            where = "history only" if f.in_history_only else "CURRENT"
            print(f"  [{tag}] {f.severity:<8} {f.label:<28} {f.repo}")
            print(f"           {f.path}  ({where}, {f.length} chars, starts {f.prefix}…)")

    if vendored:
        print()
        print(f"IN THIRD-PARTY CODE ({len(vendored)} — usually other people's test fixtures)")
        print("-" * 78)
        for f in vendored[:15]:
            print(f"  {f.repo}  {f.label}  {f.path}")
        if len(vendored) > 15:
            print(f"  ... and {len(vendored) - 15} more")

    print()
    print(bar)
    public_problems = sum(
        1 for f in own
        if next(r.public for r in reports if r.name == f.repo)) + sum(
        1 for r, _ in forbidden if r.public)
    if not own and not forbidden:
        print("  nothing found")
    else:
        print(f"  {len(own)} credential(s) in your own code, "
              f"{len(forbidden)} forbidden file(s)")
        print(f"  {public_problems} of those are in PUBLIC repositories — rotate those first")
    print(bar)
    return 1 if public_problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", action="store_true", help="public repositories only")
    parser.add_argument("--private", action="store_true", help="private repositories only")
    parser.add_argument("--no-history", action="store_true",
                        help="scan only current files (much faster, misses deleted secrets)")
    parser.add_argument("--max-mb", type=int, default=400,
                        help="skip repositories larger than this")
    parser.add_argument("--only", help="scan a single repo, e.g. owner/name")
    args = parser.parse_args(argv)

    token = os.environ.get("GH_SCAN_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("set GH_SCAN_TOKEN first — do not pass a token as an argument")
        return 2

    repos = list_repos(token)
    if args.only:
        repos = [r for r in repos if r["full_name"].lower() == args.only.lower()]
    if args.public:
        repos = [r for r in repos if not r["private"]]
    if args.private:
        repos = [r for r in repos if r["private"]]

    skipped = [r for r in repos if r["size"] / 1024 > args.max_mb]
    repos = [r for r in repos if r["size"] / 1024 <= args.max_mb]
    if skipped:
        print(f"skipping {len(skipped)} repo(s) over {args.max_mb} MB: "
              + ", ".join(r["name"] for r in skipped))

    workdir = tempfile.mkdtemp(prefix="ghaudit_")
    reports: list[RepoReport] = []
    try:
        for index, repo in enumerate(repos, 1):
            name = repo["full_name"]
            print(f"[{index}/{len(repos)}] {name} "
                  f"({'public' if not repo['private'] else 'private'}, "
                  f"{repo['size'] / 1024:.0f} MB)", flush=True)
            try:
                reports.append(scan_repo(name, not repo["private"], token, workdir,
                                         history=not args.no_history))
            except subprocess.TimeoutExpired:
                reports.append(RepoReport(name=name, public=not repo["private"],
                                          error="timed out"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    return render(reports)


if __name__ == "__main__":
    sys.exit(main())
