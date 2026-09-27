"""The legacy PowerShell scripts in tools/ were written for a flat folder (ALVR, android-tools and
backups beside the scripts) that no longer exists. Guessing it from the script's own location
resolves one level off from the repo, and one backup script would then silently archive only
tools/. So every script that takes -Workspace must require it rather than default it."""
import re
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
DECLARES = re.compile(r"\[string\]\s*\$Workspace\b", re.I)
DEFAULTED = re.compile(r"\[string\]\s*\$Workspace\s*=", re.I)
# the attribute's arguments may hold quoted strings with parentheses in them
_ARG = r"(?:[^()'\"]|'[^']*'|\"[^\"]*\")*"
MANDATORY = re.compile(r"\[Parameter\(" + _ARG + r"Mandatory" + _ARG + r"\)\]\s*\[string\]\s*\$Workspace\b", re.I)


def scripts_with_workspace():
    return [p for p in TOOLS.glob("*.ps1") if DECLARES.search(p.read_text(encoding="utf-8", errors="replace"))]


def test_there_are_legacy_scripts_to_check():
    assert len(scripts_with_workspace()) >= 30


def test_workspace_is_required_never_guessed():
    bad = [p.name for p in scripts_with_workspace()
           if DEFAULTED.search(p.read_text(encoding="utf-8", errors="replace"))
           or not MANDATORY.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert not bad, bad
