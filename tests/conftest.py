"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    """Read a captured CLI output fixture verbatim."""
    return (FIXTURE_DIR / name).read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="session")
def fixture_text():
    return read_fixture


@pytest.fixture(scope="session")
def avd_list_output() -> str:
    return read_fixture("avdmanager_list_avd.txt")


@pytest.fixture(scope="session")
def device_list_output() -> str:
    return read_fixture("avdmanager_list_device.txt")


@pytest.fixture(scope="session")
def target_list_output() -> str:
    return read_fixture("avdmanager_list_target.txt")


@pytest.fixture(scope="session")
def package_list_output() -> str:
    return read_fixture("sdkmanager_list.txt")


@pytest.fixture(scope="session")
def installed_list_output() -> str:
    return read_fixture("sdkmanager_list_installed.txt")


@pytest.fixture(scope="session")
def android_cli_list_output() -> str:
    return read_fixture("android_sdk_list.txt")