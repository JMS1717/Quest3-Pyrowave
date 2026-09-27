"""Every relative link in the repo's markdown points at a file or folder that exists, so the
reports stay navigable when folders move."""
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[2]
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def markdown_files():
    names = subprocess.run(["git", "ls-files", "-z", "*.md"], cwd=REPO, capture_output=True,
                           check=True).stdout.decode().split("\0")
    return [REPO / n for n in names if n]


def broken_links():
    broken = []
    for md in markdown_files():
        text = md.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"```.*?```", "", text, flags=re.S)       # code blocks are not links
        text = re.sub(r"`[^`\n]*`", "", text)                  # nor are inline code spans
        for target in LINK.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue                                          # URLs and in-page anchors
            path = unquote(target.split("#", 1)[0])
            if not path:
                continue
            if not (md.parent / path).exists():
                broken.append(f"{md.relative_to(REPO)} -> {target}")
    return broken


def test_relative_markdown_links_resolve():
    broken = broken_links()
    assert not broken, "\n".join(broken)
