#!/usr/bin/env python3
"""Fail if repository contents look like private Matic research artifacts."""

from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 1_000_000
FORBIDDEN_SUFFIXES = {
    ".aab",
    ".ab",
    ".apk",
    ".bin",
    ".cer",
    ".crt",
    ".db",
    ".der",
    ".hprof",
    ".jpeg",
    ".jpg",
    ".jwt",
    ".key",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp4",
    ".p12",
    ".pb",
    ".pcap",
    ".pcapng",
    ".pem",
    ".pfx",
    ".ply",
    ".png",
    ".so",
    ".sqlite",
    ".sqlite3",
    ".wav",
    ".webp",
}
FORBIDDEN_NAMES = {
    "credentials.md",
    "matic-laptop-client-id.txt",
}
TEXT_PATTERNS = {
    "local research path": re.compile(r"/home/[^/\s]+/matic(?:/|\b)"),
    "private LAN address": re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))"
        r"(?:\.\d{1,3}){2}\b"
    ),
    "concrete robot hostname": re.compile(
        r"\bmatic-[a-z0-9]{4}-[a-z0-9]{4}(?:\.local)?\b", re.I
    ),
    "authorization value": re.compile(r"Bearer:\s+[A-Za-z0-9+/]{20,}={0,2}"),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}"),
}
BASE64_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{48,}={0,2}(?![A-Za-z0-9+/])"
)


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in completed.stdout.splitlines() if line]


def entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    return -sum(
        (count / len(value)) * math.log2(count / len(value))
        for count in counts.values()
    )


def scan_data(relative: str, data: bytes, *, location: str | None = None) -> list[str]:
    display = location or relative
    relative_path = Path(relative)
    issues: list[str] = []
    if relative_path.suffix.lower() in FORBIDDEN_SUFFIXES:
        issues.append(f"forbidden artifact suffix: {display}")
    if (
        relative_path.name.lower() in FORBIDDEN_NAMES
        or "bottoken" in relative_path.name.lower()
    ):
        issues.append(f"forbidden credential filename: {display}")
    if len(data) > MAX_FILE_BYTES:
        issues.append(f"file exceeds {MAX_FILE_BYTES} bytes: {display}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        issues.append(f"non-UTF8 or binary file: {display}")
        return issues
    for label, pattern in TEXT_PATTERNS.items():
        if pattern.search(text):
            issues.append(f"{label}: {display}")
    if relative not in {"tools/scan_repository.py", "uv.lock"}:
        for match in BASE64_CANDIDATE.finditer(text):
            if entropy(match.group(0)) >= 4.5:
                issues.append(f"high-entropy Base64-like value: {display}")
                break
    return issues


def scan(path: Path) -> list[str]:
    relative = path.relative_to(ROOT).as_posix()
    if path.is_symlink():
        return [f"symbolic link is not allowed: {relative}"]
    if not path.is_file():
        return []
    try:
        data = path.read_bytes()
    except OSError:
        return [f"unreadable file: {relative}"]
    return scan_data(relative, data)


def scan_history() -> list[str]:
    """Scan every reachable historical blob, including deleted secrets."""

    listing = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    issues: list[str] = []
    seen: set[str] = set()
    for line in listing.stdout.splitlines():
        if " " not in line:
            continue
        object_id, relative = line.split(" ", 1)
        if object_id in seen:
            continue
        seen.add(object_id)
        object_type = subprocess.run(
            ["git", "cat-file", "-t", object_id],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if object_type != "blob":
            continue
        data = subprocess.run(
            ["git", "cat-file", "-p", object_id],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        issues.extend(
            scan_data(
                relative,
                data,
                location=f"history {object_id[:12]}:{relative}",
            )
        )
    return issues


def main() -> int:
    files = tracked_files()
    issues = [issue for path in files for issue in scan(path)]
    issues.extend(scan_history())
    if issues:
        print("Repository safety scan failed:", file=sys.stderr)
        for issue in sorted(set(issues)):
            print(f"- {issue}", file=sys.stderr)
        return 1
    print(f"Repository safety scan passed ({len(files)} working-tree files + history).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
