"""User configuration and SDK path resolution.

Resolution order for every setting is:

1. explicit value from the config file
2. relevant environment variable (``ANDROID_SDK_ROOT`` / ``ANDROID_HOME`` /
   ``ANDROID_AVD_HOME``)
3. the conventional location inside the resolved SDK root
4. a plain ``PATH`` lookup

Step 3 is what the reference extension does with its
``cmdline-tools/<version>/bin`` guess; we keep the behaviour but make it
explicit and verifiable via :class:`ResolvedPaths`.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import tomli_w

APP_NAME = "avdmanager"

#: Binary names as they appear inside the SDK. ``.bat`` variants are only
#: relevant on Windows, which we detect via :func:`_binary_name`.
_UNIX_NAMES = {"avdmanager": "avdmanager", "sdkmanager": "sdkmanager",
                "emulator": "emulator", "adb": "adb", "android": "android"}
_WINDOWS_NAMES = {"avdmanager": "avdmanager.bat", "sdkmanager": "sdkmanager.bat",
                   "emulator": "emulator.exe", "adb": "adb.exe", "android": "android.exe"}


def _binary_name(tool: str) -> str:
    return (_WINDOWS_NAMES if os.name == "nt" else _UNIX_NAMES)[tool]


# --------------------------------------------------------------------------- #
# Stored configuration
# --------------------------------------------------------------------------- #


@dataclass
class Config:
    """User-editable settings, persisted to ``config.toml``.

    Every field defaults to the empty string, meaning "not set" -- resolution
    then falls through to environment variables and conventional locations.
    """

    sdk_path: str = ""
    avd_home: str = ""
    cmdline_tools_version: str = "latest"
    avdmanager_path: str = ""
    sdkmanager_path: str = ""
    emulator_path: str = ""
    android_cli_path: str = ""
    adb_path: str = ""
    emulator_opts: str = ""
    #: Whether `android` CLI is preferred over the legacy `sdkmanager`.
    prefer_android_cli: bool = True
    #: Ring-buffer size for the log pane.
    log_history: int = 5000

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def default_config_path() -> Path:
    """``$XDG_CONFIG_HOME/avdmanager/config.toml`` (or the platform default)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME / "config.toml"


def load_config(path: Path | None = None) -> Config:
    """Load the config file, returning defaults if it is missing or corrupt."""
    target = path or default_config_path()
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return Config()
    return Config.from_dict(data)


def save_config(config: Config, path: Path | None = None) -> None:
    """Atomically write the config file."""
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".toml.tmp")
    tmp.write_text(tomli_w.dumps(_prune(config)), encoding="utf-8")
    tmp.replace(target)


def _prune(config: Config) -> dict:
    """Drop empty values so the file stays readable."""
    return {k: v for k, v in asdict(config).items() if v not in ("", None)}


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


@dataclass
class ResolvedTool:
    """A single resolved executable."""

    name: str
    path: str
    source: str  # config | sdk | env | path | missing
    ok: bool

    @property
    def display(self) -> str:
        return self.path or f"<{self.name} not found>"


@dataclass
class ResolvedPaths:
    """Fully-resolved SDK layout plus any problems found."""

    sdk_root: str = ""
    sdk_root_source: str = "missing"
    avd_home: str = ""
    avd_home_source: str = "default"
    cmdline_tools_version: str = "latest"
    tools: dict[str, ResolvedTool] = field(default_factory=dict)
    directories: dict[str, str] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def tool(self, name: str) -> ResolvedTool:
        return self.tools.get(name, ResolvedTool(name, "", "missing", False))

    @property
    def sdkmanager(self) -> str:
        return self.tool("sdkmanager").path

    @property
    def avdmanager(self) -> str:
        return self.tool("avdmanager").path

    @property
    def emulator(self) -> str:
        return self.tool("emulator").path

    @property
    def adb(self) -> str:
        return self.tool("adb").path

    @property
    def android_cli(self) -> str:
        return self.tool("android").path

    def detail_rows(self) -> list[tuple[str, str, str]]:
        """(label, value, source) triples for the health view."""
        rows: list[tuple[str, str, str]] = [
            ("SDK root", self.sdk_root or "<not set>", self.sdk_root_source),
            ("AVD home", self.avd_home or "<system default>", self.avd_home_source),
        ]
        rows += [(name, tool.display, tool.source) for name, tool in self.tools.items()]
        rows += [(name, path or "<missing>", "dir") for name, path in self.directories.items()]
        return rows


def resolve(config: Config, env: dict[str, str] | None = None) -> ResolvedPaths:
    """Turn stored settings into concrete, validated paths."""
    environ = os.environ if env is None else env
    resolved = ResolvedPaths(cmdline_tools_version=config.cmdline_tools_version or "latest")

    resolved.sdk_root, resolved.sdk_root_source = _resolve_sdk_root(config, environ)
    resolved.avd_home, resolved.avd_home_source = _resolve_avd_home(config, environ)

    overrides = {
        "avdmanager": config.avdmanager_path,
        "sdkmanager": config.sdkmanager_path,
        "emulator": config.emulator_path,
        "android": config.android_cli_path,
        "adb": config.adb_path,
    }
    for tool, override in overrides.items():
        resolved.tools[tool] = _resolve_tool(tool, override, resolved.sdk_root, environ)

    if resolved.sdk_root:
        root = Path(resolved.sdk_root)
        resolved.directories = {
            "cmdline-tools": str(root / "cmdline-tools" / resolved.cmdline_tools_version),
            "build-tools": str(root / "build-tools"),
            "platform-tools": str(root / "platform-tools"),
            "emulator": str(root / "emulator"),
            "system-images": str(root / "system-images"),
        }

    resolved.issues = _collect_issues(resolved)
    return resolved


def _resolve_sdk_root(config: Config, environ: dict[str, str]) -> tuple[str, str]:
    if config.sdk_path and Path(config.sdk_path).is_dir():
        return str(config.sdk_path), "config"
    # ANDROID_HOME is deprecated but still wins over ANDROID_SDK_ROOT, matching
    # the legacy tooling (and the reference extension) on this machine.
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = environ.get(var, "")
        if value and Path(value).is_dir():
            return value, f"env:{var}"
    if config.sdk_path:
        # Set but missing: keep it so the health view can complain about it.
        return config.sdk_path, "config (missing)"
    return "", "missing"


def _resolve_avd_home(config: Config, environ: dict[str, str]) -> tuple[str, str]:
    if config.avd_home:
        return config.avd_home, "config"
    value = environ.get("ANDROID_AVD_HOME", "")
    if value:
        return value, "env:ANDROID_AVD_HOME"
    return "", "default (~/.android/avd)"


def _resolve_tool(
    tool: str, override: str, sdk_root: str, environ: dict[str, str]
) -> ResolvedTool:
    name = _binary_name(tool)

    if override:
        if Path(override).is_file():
            return ResolvedTool(tool, override, "config", os.access(override, os.X_OK))
        return ResolvedTool(tool, override, "config (missing)", False)

    if sdk_root:
        candidate = Path(sdk_root) / "cmdline-tools" / "latest" / "bin" / name
        if not candidate.is_file():
            candidate = Path(sdk_root) / "emulator" / name
        if candidate.is_file():
            return ResolvedTool(tool, str(candidate), "sdk", os.access(candidate, os.X_OK))

    found = shutil.which(name, path=environ.get("PATH"))
    if found:
        return ResolvedTool(tool, found, "path", True)

    return ResolvedTool(tool, "", "missing", False)


def _collect_issues(resolved: ResolvedPaths) -> list[str]:
    issues: list[str] = []
    if not resolved.sdk_root:
        issues.append("Android SDK root is not set.")
    elif resolved.sdk_root_source.endswith("(missing)"):
        issues.append(f"Configured SDK root does not exist: {resolved.sdk_root}")

    for required in ("sdkmanager", "avdmanager"):
        if not resolved.tool(required).ok:
            issues.append(f"{required} could not be located.")

    emulator = resolved.tool("emulator")
    if not emulator.ok:
        issues.append("Emulator not found — install the `emulator` package from the SDK Tools tree.")
    return issues


def parse_emulator_opts(opts: str) -> list[str]:
    """Split the configured emulator option string using shell-like quoting."""
    import shlex

    try:
        return shlex.split(opts)
    except ValueError:
        return opts.split()