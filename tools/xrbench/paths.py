"""Where the harness finds its code and its runtime.

CODE is the tools folder this package sits in: the repo's tools/ in the workspace, or an older
flat copy of it that the harness used to run from. The runtime root holds what is installed
or produced on the PC rather than versioned: the ALVR install (ALVR-20.13.0), adb
(android-tools), session backups, pyro_env.cmd, the corpus, xrbench-runs. First match wins:
  1. XRBENCH_ROOT
  2. <workspace>\\bench, when the repo sits in the workspace layout (tools\\..\\..\\bench exists)
  3. CODE itself: the old flat layout, where code and runtime shared one folder
"""
import os
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


def runtime_root(code=CODE, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("XRBENCH_ROOT"):
        return Path(environ["XRBENCH_ROOT"])
    bench = Path(code).parents[1] / "bench"
    if bench.is_dir():
        return bench
    return Path(code)


ROOT = runtime_root()
