"""Tests for configuration loading, persistence and path resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from avdmanager import config as cfg


@pytest.fixture
def fake_sdk(tmp_path: Path) -> Path:
    """A directory that looks like a real Android SDK."""
    sdk = tmp_path / "sdk"
    bin_dir = sdk / "cmdline-tools" / "latest" / "bin"
    bin_dir.mkdir(parents=True)
    for tool in ("avdmanager", "sdkmanager", "android"):
        exe = bin_dir / tool
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    emu = sdk / "emulator" / "emulator"
    emu.parent.mkdir(parents=True)
    emu.write_text("#!/bin/sh\n")
    emu.chmod(0o755)
    (sdk / "build-tools").mkdir()
    (sdk / "platform-tools").mkdir()
    return sdk


@pytest.fixture
def no_path_env():
    """Environment with an empty PATH so shutil.which cannot rescue us."""
    return {"PATH": "", "ANDROID_SDK_ROOT": "", "ANDROID_HOME": ""}


# --------------------------------------------------------------------------- #
# Config file round-trip
# --------------------------------------------------------------------------- #


def test_defaults_are_empty():
    conf = cfg.Config()
    assert conf.sdk_path == ""
    assert conf.cmdline_tools_version == "latest"
    assert conf.prefer_android_cli is True


def test_save_and_load_roundtrip(tmp_path: Path):
    target = tmp_path / "nested" / "config.toml"
    original = cfg.Config(sdk_path="/opt/sdk", emulator_opts="-no-window -gpu swiftshader_indirect")
    cfg.save_config(original, target)

    assert target.is_file()
    loaded = cfg.load_config(target)
    assert loaded.sdk_path == "/opt/sdk"
    assert loaded.emulator_opts == "-no-window -gpu swiftshader_indirect"


def test_save_prunes_empty_values(tmp_path: Path):
    target = tmp_path / "config.toml"
    cfg.save_config(cfg.Config(sdk_path="/opt/sdk"), target)
    body = target.read_text()
    assert "sdk_path" in body
    assert "avd_home" not in body


def test_load_missing_file_returns_defaults(tmp_path: Path):
    assert cfg.load_config(tmp_path / "nope.toml") == cfg.Config()


def test_load_corrupt_file_returns_defaults(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text("this is not = = toml")
    assert cfg.load_config(target) == cfg.Config()


def test_load_ignores_unknown_keys(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text('sdk_path = "/opt/sdk"\nsome_future_key = 3\n')
    assert cfg.load_config(target).sdk_path == "/opt/sdk"


def test_default_config_path_respects_xdg(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg-test")
    assert cfg.default_config_path() == Path("/tmp/xdg-test/avdmanager/config.toml")


def test_default_config_path_falls_back_to_home(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert cfg.default_config_path() == Path.home() / ".config" / "avdmanager" / "config.toml"


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def test_resolve_prefers_config_sdk_root(fake_sdk: Path, no_path_env):
    resolved = cfg.resolve(cfg.Config(sdk_path=str(fake_sdk)), no_path_env)
    assert resolved.sdk_root == str(fake_sdk)
    assert resolved.sdk_root_source == "config"


def test_resolve_falls_back_to_android_sdk_root(fake_sdk: Path, no_path_env):
    env = {**no_path_env, "ANDROID_SDK_ROOT": str(fake_sdk)}
    resolved = cfg.resolve(cfg.Config(), env)
    assert resolved.sdk_root == str(fake_sdk)
    assert resolved.sdk_root_source == "env:ANDROID_SDK_ROOT"


def test_resolve_android_home_takes_priority_over_sdk_root(fake_sdk: Path, tmp_path: Path):
    other = tmp_path / "other-sdk"
    other.mkdir()
    env = {
        "PATH": "",
        "ANDROID_SDK_ROOT": str(fake_sdk),
        "ANDROID_HOME": str(other),
    }
    resolved = cfg.resolve(cfg.Config(), env)
    assert resolved.sdk_root == str(other)
    assert resolved.sdk_root_source == "env:ANDROID_HOME"


def test_resolve_keeps_missing_configured_sdk_root(tmp_path: Path, no_path_env):
    resolved = cfg.resolve(cfg.Config(sdk_path=str(tmp_path / "ghost")), no_path_env)
    assert resolved.sdk_root == str(tmp_path / "ghost")
    assert "missing" in resolved.sdk_root_source
    assert not resolved.ok


def test_resolve_finds_tools_inside_sdk(fake_sdk: Path, no_path_env):
    resolved = cfg.resolve(cfg.Config(sdk_path=str(fake_sdk)), no_path_env)
    assert resolved.avdmanager == str(fake_sdk / "cmdline-tools/latest/bin/avdmanager")
    assert resolved.sdkmanager == str(fake_sdk / "cmdline-tools/latest/bin/sdkmanager")
    assert resolved.android_cli == str(fake_sdk / "cmdline-tools/latest/bin/android")
    # the emulator lives in its own directory, not under cmdline-tools
    assert resolved.emulator == str(fake_sdk / "emulator/emulator")


def test_resolve_reports_missing_sdkmanager(fake_sdk: Path, no_path_env):
    (fake_sdk / "cmdline-tools/latest/bin/sdkmanager").unlink()
    resolved = cfg.resolve(cfg.Config(sdk_path=str(fake_sdk)), no_path_env)
    assert resolved.sdkmanager == ""
    assert resolved.tool("sdkmanager").source == "missing"
    assert any("sdkmanager" in issue for issue in resolved.issues)


def test_resolve_honours_explicit_tool_override(fake_sdk: Path, tmp_path: Path, no_path_env):
    custom = tmp_path / "my-sdkmanager"
    custom.write_text("#!/bin/sh\n")
    custom.chmod(0o755)

    resolved = cfg.resolve(
        cfg.Config(sdk_path=str(fake_sdk), sdkmanager_path=str(custom)), no_path_env
    )
    assert resolved.sdkmanager == str(custom)
    assert resolved.tool("sdkmanager").source == "config"


def test_resolve_falls_back_to_path_lookup(fake_sdk: Path):
    """When no SDK root is known, a bare binary on PATH is used."""
    env = {"PATH": str(fake_sdk / "cmdline-tools/latest/bin"), "ANDROID_SDK_ROOT": ""}
    resolved = cfg.resolve(cfg.Config(), env)
    assert resolved.sdk_root == ""
    assert resolved.sdkmanager.endswith("sdkmanager")
    assert resolved.tool("sdkmanager").source == "path"
    # no SDK root is still an issue worth surfacing
    assert any("SDK root" in issue for issue in resolved.issues)


def test_resolve_avd_home_precedence(fake_sdk: Path, tmp_path: Path, no_path_env):
    env = {**no_path_env, "ANDROID_AVD_HOME": str(tmp_path / "from-env")}
    assert cfg.resolve(cfg.Config(), env).avd_home == str(tmp_path / "from-env")

    conf = cfg.Config(avd_home=str(tmp_path / "from-config"))
    resolved = cfg.resolve(conf, env)
    assert resolved.avd_home == str(tmp_path / "from-config")
    assert resolved.avd_home_source == "config"


def test_resolve_avd_home_default_is_system_default(fake_sdk: Path, no_path_env):
    resolved = cfg.resolve(cfg.Config(sdk_path=str(fake_sdk)), no_path_env)
    assert resolved.avd_home == ""
    assert resolved.avd_home_source.startswith("default")


def test_resolve_exposes_conventional_directories(fake_sdk: Path, no_path_env):
    resolved = cfg.resolve(cfg.Config(sdk_path=str(fake_sdk)), no_path_env)
    assert resolved.directories["cmdline-tools"] == str(
        fake_sdk / "cmdline-tools" / "latest"
    )
    assert resolved.directories["build-tools"] == str(fake_sdk / "build-tools")
    assert resolved.directories["system-images"] == str(fake_sdk / "system-images")


def test_resolve_reports_healthy_sdk(fake_sdk: Path, no_path_env, tmp_path: Path):
    (tmp_path / "adb").write_text("#!/bin/sh\n")
    (tmp_path / "adb").chmod(0o755)
    env = {**no_path_env, "PATH": str(tmp_path)}
    resolved = cfg.resolve(cfg.Config(sdk_path=str(fake_sdk)), env)
    assert resolved.issues == []


def test_resolve_warns_about_missing_emulator(fake_sdk: Path, no_path_env):
    (fake_sdk / "emulator/emulator").unlink()
    resolved = cfg.resolve(cfg.Config(sdk_path=str(fake_sdk)), no_path_env)
    assert any("Emulator not found" in issue for issue in resolved.issues)


def test_detail_rows_shape(fake_sdk: Path, no_path_env):
    resolved = cfg.resolve(cfg.Config(sdk_path=str(fake_sdk)), no_path_env)
    rows = resolved.detail_rows()
    assert ("SDK root", str(fake_sdk), "config") in rows
    assert all(len(row) == 3 for row in rows)


# --------------------------------------------------------------------------- #
# Emulator option parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("-no-window", ["-no-window"]),
        ("-no-window -gpu swiftshader_indirect", ["-no-window", "-gpu", "swiftshader_indirect"]),
        ('-prop "a b c"', ["-prop", "a b c"]),
    ],
)
def test_parse_emulator_opts(raw, expected):
    assert cfg.parse_emulator_opts(raw) == expected


def test_parse_emulator_opts_tolerates_unbalanced_quotes():
    assert cfg.parse_emulator_opts('-prop "unclosed') == ["-prop", '"unclosed']


def test_tool_names_match_platform():
    assert cfg._binary_name("sdkmanager") in ("sdkmanager", "sdkmanager.bat")
    assert cfg._binary_name("emulator") in ("emulator", "emulator.exe")
    assert os.name in ("nt", "posix")