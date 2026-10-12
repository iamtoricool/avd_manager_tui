# avdmanager

A terminal UI for managing the Android SDK, AVDs and emulators — a standalone
successor to the [`vscode-avdmanager`](https://github.com/toroxx/vscode-avdmanager)
VS Code extension.

> **Status: under construction.**

## Features

- Browse, create, rename, delete and launch Android Virtual Devices
- Browse SDK platforms, system images, build-tools and other packages
- Install, uninstall and update packages with streamed progress
- Accept SDK licenses non-interactively
- Launch, stop and monitor emulators

## Requirements

- Python 3.11+
- JDK 17+
- Android SDK with `cmdline-tools` installed

See [Setup](docs/setup.md) for details.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/python -m avdmanager
```

## License

MIT