"""Backend selection."""

from __future__ import annotations

from avdmanager.backends.android import AndroidCliBackend
from avdmanager.backends.base import Backend
from avdmanager.backends.legacy import SdkManagerBackend
from avdmanager.config import ResolvedPaths


def detect_backend(
    paths: ResolvedPaths, *, prefer_android_cli: bool = True
) -> Backend:
    """Pick the best available backend.

    The ``android`` CLI ships alongside ``sdkmanager`` in cmdline-tools 23+, so
    preferring it only when the user has not opted out keeps us on the
    supported tool path without breaking older SDKs.
    """
    if prefer_android_cli and paths.android_cli:
        return AndroidCliBackend()
    if paths.sdkmanager:
        return SdkManagerBackend()
    if paths.android_cli:
        return AndroidCliBackend()
    raise BackendUnavailable(
        "Neither the `android` CLI nor `sdkmanager` could be found. "
        "Install Android SDK Command-line Tools, or set the SDK path in Settings."
    )


class BackendUnavailable(RuntimeError):
    """Raised when no usable SDK command-line tool is present."""


__all__ = [
    "AndroidCliBackend",
    "Backend",
    "BackendUnavailable",
    "SdkManagerBackend",
    "detect_backend",
]