"""Android XR gates eye and hand tracking behind its own dangerous permissions. The headset app
must both declare each one in its manifest and request it at runtime, or the OpenXR trackers
return nothing: without android.permission.HAND_TRACKING the app streamed no hand skeletons at
all, although the Meta permission of the same name was declared."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PATCH = REPO / "patches" / "alvr-20.13.0-server-instrumentation.patch"
ANDROID_XR_PERMISSIONS = ("android.permission.EYE_TRACKING_FINE", "android.permission.HAND_TRACKING")


def added_lines(path):
    """Lines the patch adds to one file."""
    text = PATCH.read_text(encoding="utf-8")
    sections = re.split(r"^diff --git ", text, flags=re.M)
    section = next(s for s in sections if s.startswith(f"a/{path} b/{path}"))
    return [l[1:] for l in section.splitlines() if l.startswith("+") and not l.startswith("+++")]


def test_the_manifest_declares_each_android_xr_permission():
    manifest = "\n".join(added_lines("alvr/client_openxr/Cargo.toml"))
    for permission in ANDROID_XR_PERMISSIONS:
        assert f'name = "{permission}"' in manifest, permission


def test_the_client_requests_each_android_xr_permission_at_runtime():
    code = "\n".join(added_lines("alvr/client_openxr/src/interaction.rs"))
    for permission in ANDROID_XR_PERMISSIONS:
        assert f'try_get_permission("{permission}")' in code, permission
