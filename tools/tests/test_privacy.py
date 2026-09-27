"""Nothing personal in the tracked files: no owner or machine names, user paths, private network
addresses, device serials or e-mail addresses. The generic patterns are checked everywhere; the
names themselves come from outside the repo, so the list of what to look for is not itself a leak:
`tools/tests/.banned_strings` (untracked, one per line) locally, or the XRWIRED_BANNED_STRINGS
secret (newline- or comma-separated) in CI. Without either, only the generic patterns run."""
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LOCAL_LIST = Path(__file__).with_name(".banned_strings")

# RFC 5737 documentation ranges are the placeholders the scrub uses
DOC_NETS = ("192.0.2.", "198.51.100.", "203.0.113.")
PRIVATE_IPV4 = re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
USER_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]+users[\\/]+|/users/|/home/)(?!<|\$|%|\{|user\b|runner\b|public\b|someone\b)[A-Za-z0-9._-]+")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
ALLOWED_EMAILS = {"noreply@anthropic.com"}
RESERVED_DOMAINS = ("example.com", "example.org", "example.net", ".example", ".test", ".invalid")  # RFC 2606


def tracked_text_files():
    names = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                           check=True).stdout.decode().split("\0")
    for name in filter(None, names):
        path = REPO / name
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        yield name, data.decode("utf-8", errors="replace")


def banned_strings():
    raw = os.environ.get("XRWIRED_BANNED_STRINGS", "")
    if LOCAL_LIST.exists():
        raw += "\n" + LOCAL_LIST.read_text(encoding="utf-8")
    return sorted({s.strip().lower() for s in re.split(r"[\n,]", raw) if s.strip()})


def findings(check, kind):
    """file:line of every match, never the matched text: a failure must not republish the leak."""
    hits = []
    for name, text in tracked_text_files():
        for lineno, line in enumerate(text.splitlines(), 1):
            hits += [f"{name}:{lineno}: {kind}"] * len(check(line))
    return hits


def test_no_private_network_addresses():
    hits = findings(lambda line: PRIVATE_IPV4.findall(line), "private network address")
    assert not hits, "\n".join(hits[:40])


def test_no_personal_user_paths():
    hits = findings(lambda line: USER_PATH.findall(line), "personal user path")
    assert not hits, "\n".join(hits[:40])


def test_no_email_addresses():
    hits = findings(lambda line: [e for e in EMAIL.findall(line)
                                  if e.lower() not in ALLOWED_EMAILS and not e.lower().endswith(RESERVED_DOMAINS)],
                    "e-mail address")
    assert not hits, "\n".join(hits[:40])


def test_no_banned_strings():
    banned = banned_strings()
    if not banned:
        pytest.skip("no banned-strings list (tools/tests/.banned_strings or XRWIRED_BANNED_STRINGS)")
    hits = findings(lambda line: [b for b in banned if b in line.lower()], "banned string")
    assert not hits, "\n".join(hits[:40])
