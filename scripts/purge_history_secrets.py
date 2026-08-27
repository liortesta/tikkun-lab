"""Scrub secrets out of a repository's git history.

    set GH_SCAN_TOKEN=...
    python scripts/purge_history_secrets.py owner/repo            # inspect only
    python scripts/purge_history_secrets.py owner/repo --rewrite  # rewrite locally
    python scripts/purge_history_secrets.py owner/repo --rewrite --push

Read this before using it:

**Purging does not un-leak.** If the repository was public while the secret was
in it, assume the secret was scraped — public git history is indexed
continuously by bots. Rotating the credential is the fix; this is housekeeping
that stops the next person who clones from finding it.

**This rewrites history.** Every commit after the first touched commit gets a
new hash. Existing clones and forks no longer share ancestry, open pull requests
break, and anyone who pulls will need a fresh clone. GitHub may also keep the
old objects reachable by direct SHA URL until its garbage collection runs —
ask GitHub Support to purge them if that matters.

A full mirror backup is written next to the working directory before anything is
rewritten, so the original is always recoverable.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

SECRETS = [
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenRouter key", re.compile(r"sk-or-v1-[a-f0-9]{40,}")),
    ("OpenAI-style key", re.compile(r"sk-(?!ant-|or-)[A-Za-z0-9_\-]{20,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("SendGrid key", re.compile(r"SG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}")),
    ("Slack token", re.compile(r"xox[abprs]-[0-9A-Za-z\-]{20,}")),
    # Only inside an obvious password assignment, so ordinary hashes survive.
    ("password value", re.compile(
        r"(?i)(?:PGPASSWORD|MYSQL_PWD|DB_PASSWORD|POSTGRES_PASSWORD)\s*=\s*"
        r"\"?([A-Za-z0-9!@#$%^&*_\-]{12,})\"?")),
]

BINARY = re.compile(r"\.(png|jpe?g|gif|webp|ico|pdf|zip|gz|woff2?|ttf|mp[34]|mov|exe|dll|so)$", re.I)
PLACEHOLDER = re.compile(r"(?i)your|xxx+|example|placeholder|changeme|\.\.\.|<[^>]{2,30}>")

MARKER = "***REMOVED-SECRET***"
# The marker has to be excluded explicitly: it sits exactly where the secret was,
# so `PGPASSWORD=***REMOVED-SECRET***` matches the password pattern and the
# post-rewrite verification reports its own handiwork as a surviving secret.
MARKER_CHARS = re.compile(r"^[*\-]*REMOVED[-_]?SECRET[*\-]*$", re.IGNORECASE)


def git(*args: str, cwd: str, check: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                            text=True, errors="replace", timeout=3600)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {result.stderr[:300]}")
    return result


def collect(mirror: str) -> dict[str, str]:
    """Every distinct secret in history -> the label to report it under."""
    listing = git("rev-list", "--objects", "--all", cwd=mirror)
    blobs: dict[str, str] = {}
    for line in listing.stdout.splitlines():
        sha, _, path = line.partition(" ")
        path = path.strip()
        if path and not BINARY.search(path):
            blobs.setdefault(sha, path)

    found: dict[str, str] = {}
    where: dict[str, set[str]] = {}
    for sha, path in blobs.items():
        content = git("cat-file", "-p", sha, cwd=mirror)
        if content.returncode != 0 or len(content.stdout) > 5_000_000:
            continue
        for line in content.stdout.splitlines():
            if len(line) > 6000 or PLACEHOLDER.search(line):
                continue
            for label, pattern in SECRETS:
                for match in pattern.finditer(line):
                    value = match.group(1) if match.groups() else match.group(0)
                    if len(value) < 12 or MARKER in value or MARKER_CHARS.match(value):
                        continue
                    found[value] = label
                    where.setdefault(value, set()).add(path)

    for value, label in found.items():
        paths = ", ".join(sorted(where[value])[:3])
        print(f"  {label:<20} {len(value):>4} chars  starts {value[:4]}…  in {paths}")
    return found


def ensure_filter_repo() -> str | None:
    for candidate in (["git", "filter-repo", "--version"],
                      [sys.executable, "-m", "git_filter_repo", "--version"]):
        try:
            if subprocess.run(candidate, capture_output=True, timeout=60).returncode == 0:
                return "git" if candidate[0] == "git" else "module"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    print("  installing git-filter-repo…")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "git-filter-repo"],
                   capture_output=True, timeout=600)
    try:
        if subprocess.run([sys.executable, "-m", "git_filter_repo", "--version"],
                          capture_output=True, timeout=60).returncode == 0:
            return "module"
    except Exception:
        pass
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/name")
    parser.add_argument("--rewrite", action="store_true", help="actually rewrite history")
    parser.add_argument("--push", action="store_true", help="force-push the rewritten history")
    args = parser.parse_args(argv)

    token = os.environ.get("GH_SCAN_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("set GH_SCAN_TOKEN first")
        return 2

    work = tempfile.mkdtemp(prefix="purge_")
    mirror = os.path.join(work, "repo.git")
    backup = os.path.join(work, "BACKUP-original.git")

    try:
        print(f"cloning {args.repo} (full history)…")
        clone = subprocess.run(
            ["git", "clone", "--mirror", "--quiet",
             f"https://x-access-token:{token}@github.com/{args.repo}.git", mirror],
            capture_output=True, text=True, timeout=3600)
        if clone.returncode != 0:
            print("clone failed:", clone.stderr.replace(token, "***")[:300])
            return 1

        print("\nsecrets found in history:")
        secrets = collect(mirror)
        if not secrets:
            print("  none — nothing to purge")
            return 0

        if not args.rewrite:
            print(f"\n{len(secrets)} distinct secret(s). Re-run with --rewrite to scrub them.")
            return 0

        shutil.copytree(mirror, backup)
        print(f"\nbackup of the original: {backup}")

        flavour = ensure_filter_repo()
        if not flavour:
            print("git-filter-repo unavailable; cannot rewrite safely")
            return 1

        # One replacement per secret. filter-repo reads this file and rewrites
        # every blob in every commit, so the value cannot survive anywhere.
        rules = os.path.join(work, "replacements.txt")
        with open(rules, "w", encoding="utf-8") as handle:
            for value in secrets:
                handle.write(f"{value}==>{MARKER}\n")

        command = (["git", "filter-repo"] if flavour == "git"
                   else [sys.executable, "-m", "git_filter_repo"])
        result = subprocess.run(command + ["--replace-text", rules, "--force"],
                                cwd=mirror, capture_output=True, text=True, timeout=3600)
        os.remove(rules)
        if result.returncode != 0:
            print("rewrite failed:", (result.stderr or result.stdout)[:500])
            return 1
        print("history rewritten")

        print("\nverifying the rewritten history:")
        remaining = collect(mirror)
        if remaining:
            print(f"  {len(remaining)} secret(s) SURVIVED — not pushing")
            return 1
        print("  clean — no secret remains in any commit")

        if not args.push:
            print(f"\nNot pushed. To publish the rewrite:\n"
                  f"  cd {mirror}\n"
                  f"  git push --force --mirror <url>")
            return 0

        print("\nforce-pushing…")
        auth = f"https://x-access-token:{token}@github.com/{args.repo}.git"
        push = subprocess.run(["git", "push", "--force", "--mirror", auth],
                              cwd=mirror, capture_output=True, text=True, timeout=3600)
        if push.returncode != 0:
            print("push failed:", push.stderr.replace(token, "***")[:400])
            return 1
        print("pushed")
        print(f"\nThe original is still at:\n  {backup}\n"
              "Keep it until you have confirmed the remote looks right.")
        return 0
    finally:
        pass  # the working directory is deliberately left in place


if __name__ == "__main__":
    sys.exit(main())
