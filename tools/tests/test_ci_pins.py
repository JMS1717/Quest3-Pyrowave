"""CI rebuilds everything from pinned upstream commits plus patches/. The pins live in three places
-- the workflow, the fetch script's defaults and patches/README.md -- and must agree, or CI would
build something other than what was measured."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def workflow_env():
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    return dict(re.findall(r"^  ([A-Z_]+): \"?([0-9a-f.]+)\"?", text, re.M))


def script_defaults():
    text = (REPO / "tools" / "ci" / "fetch_sources.sh").read_text()
    return dict(re.findall(r'^: "\$\{([A-Z_]+):=([0-9a-f]+)\}"', text, re.M))


def test_workflow_and_fetch_script_pin_the_same_commits():
    env, defaults = workflow_env(), script_defaults()
    for key in ("ALVR_BASE", "PYROWAVE_BASE", "GRANITE_COMMIT"):
        assert len(env[key]) == 40, key
        assert env[key] == defaults[key], key


def test_pins_match_the_patch_bases():
    readme = (REPO / "patches" / "README.md").read_text()
    env = workflow_env()
    assert f"`{env['ALVR_BASE'][:7]}` (v20.13.0)" in readme
    assert f"`{env['PYROWAVE_BASE'][:7]}`" in readme


def test_granite_is_the_one_the_measurements_used():
    assert workflow_env()["GRANITE_COMMIT"].startswith("842d9d5")


def test_the_toolchain_matches_the_local_build_scripts():
    rust = workflow_env()["RUST_TOOLCHAIN"]
    assert f"cargo +{rust} " in (REPO / "tools" / "build_alvr_2013.sh").read_text()
