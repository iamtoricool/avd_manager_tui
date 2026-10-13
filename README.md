# avdmanager

A terminal UI for managing the Android SDK, AVDs and emulators — a standalone
successor to the [`vscode-avdmanager`](https://github.com/toroxx/vscode-avdmanager)
VS Code extension.

```
┌ AVD Manager ───────────────────────────────────────────────────────────-┐
│ AVDS            │ Pixel_36_Ultra                                        │
│ [*] Pixel_36…   │ Based on: Android 16.0 ("Baklava")                    │
│                 │ Tag/ABI: google_apis_playstore/x86_64                 │
│ SDK PLATFORMS   │ Path:    ~/.android/avd/Pixel_36_Ultra.avd            │
│ Android 16 (B…  │                                                       │
│ SDK TOOLS       │                                                       │
│ build-tools 3/82│                                                       │
├─────────────────┴─────────────────────────────────────-─────────────────┤
│ Output   Loading packages…  Loaded 636 packages (13 installed)          │
├─────────────────────────────────────────────────────────────────────────┤
│ backend: android CLI   SDK: ok                                          │
│ q Quit  r Refresh  n New AVD  l Launch  d Delete  i Install  ? Help     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Features

**AVDs** — browse with full detail, create via a guided wizard (name, system
image, hardware profile, sdcard), rename, delete, launch, reveal the AVD
folder, open `config.ini` in your editor, and **wipe an AVD's storage**
(userdata, cache, encryption keys, snapshots) without deleting the device.

**SDK platforms** — platforms, system images and sources grouped by Android
release, newest first, with `installed/total` counts per group.

**SDK tools** — build-tools, cmake, ndk, cmdline-tools, emulator, extras and
everything else, grouped by category.

**Install / uninstall** — press `space` to mark several packages, then `i` or `u`
to act on all of them at once. Streamed live output, cancellable at any time
including the process group. Deletion always confirms with the exact
package path, version and a warning about AVDs built on a removed image.

**Also** — accept SDK licences non-interactively, update all packages, filter
packages server-side, monitor connected devices, and a live SDK health view.

## Install

```bash
git clone <this repo> && cd avd_manager_zed
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/avdmanager
```

See [docs/setup.md](docs/setup.md) for Android SDK prerequisites.

## Wipe vs delete

These are deliberately different operations, and the app keeps them apart:

| | `d` delete | `w` wipe |
|---|---|---|
| The AVD itself | **removed** | **kept** |
| `config.ini`, hardware profile, name | gone | kept |
| Userdata, cache, encryption keys, snapshots | gone | erased |
| Next state | AVD no longer exists | boots from a factory image |

`w` erases only files it recognises as device *data*. Anything unrecognised is
left alone, so a file introduced by a future emulator release is never deleted
on a guess. The confirmation dialog lists exactly what will be erased and
repeats that the device itself is kept; `W` additionally requires typing
`WIPE ALL`.

Wiping is refused while an emulator is running — stop it with `x` first.

## Panes

The sidebar and log pane are resizable two ways, and the sizes are remembered
in `~/.config/avdmanager/config.toml`:

- **Drag** the thin divider between the sidebar and the detail pane to change
  the sidebar width, or the divider under the log to change its height.
- **Keys** — `[` and `]` resize the sidebar, `-` and `=` resize the log.

Bounds are dynamic: the sidebar is 24–72 columns and the log 3–40 rows, but
both are further capped so a pane can never squeeze its neighbour out — the
sidebar always leaves at least 30 columns and the log at least 12 rows for
the detail pane and app chrome. Shrinking the terminal re-clamps the panes.
A nonsensical stored value falls back to the default rather than pinning the
pane to a bound.

Both dividers are draggable in both directions; the log is anchored to the
bottom and its divider sits *above* it, so drag that divider **up** to grow the
log and **down** to shrink it.

The three sidebar sections — **AVDS**, **SDK PLATFORMS** and **SDK TOOLS** —
are collapsible: **click a section heading** to hide its tree so the other
sections grow into the freed rows, and click again to restore it. The heading
shows `▼` when expanded and `▶` when collapsed, and the sections collapse
independently (any combination, including all three).

The **Output** pane has a single bar along its top that does both jobs: **drag
it** to resize the log, or **click it** (or press `F4`) to collapse the pane
down to just that bar. The bar stays on screen while collapsed, so the same
click brings the log back.

## Keys

| Key | Action | | Key | Action |
|---|---|---|---|---|
| `n` | New AVD | | `r` | Refresh everything |
| `l` | Launch AVD | | `f` | Filter packages |
| `e` | Rename AVD | | `L` | Accept licences |
| `d` | Delete AVD | | `U` | Update all packages |
| `i` | Install package | | `s` | Settings |
| `u` | Uninstall package | | `?` | Full key list |
| `R` | Reveal AVD folder | | `F4` | Toggle the log pane |
| `c` | Edit `config.ini` | | `ctrl+c` | Cancel the running job |
| `j` / `k` | Switch tree | | `q` | Quit |

`f` opens the package filter box; press `Esc` while it has focus to dismiss it
and clear the filter (the full list reloads). `Esc` anywhere else clears marked
packages.

## Design notes

**Two backends.** The modern `android` CLI (cmdline-tools 23+) is preferred
because `sdkmanager` is deprecated; there is an automatic fallback plus a
manual opt-out (`--legacy`). `avdmanager` remains the tool for AVD CRUD on
both, since `android emulator` does not expose hardware-profile ids, skins or
sdcard sizes.

**Parsing.** All CLI output parsing is pure and lives in `parse.py`, tested
against fixtures captured verbatim from a real SDK. Modern `sdkmanager` /
`android sdk list` emit space-padded columns; the reference extension splits
rows on `|`, which returns nothing against current tooling — see
`tests/test_parse.py` for the regression guard. Other quirks handled: carriage
return progress repaints, the `Based on`/`Tag/ABI` continuation line,
inconsistent key spacing in `list device`, `(+N)` update markers, and the
semicolon form `avdmanager create avd -k` requires.

**Responsiveness.** Nothing blocks the event loop. Subprocesses are drained by
chunked pump coroutines (not `readline`, because the SDK's prompts arrive
without a trailing newline and would hang a line reader), output is batched
into the log on a 100 ms timer, table parsing runs in `asyncio.to_thread`, tree
population yields every 60 nodes, and mutations are serialised behind a lock
because `sdkmanager` takes its own.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest        # 200+ tests
.venv/bin/ruff check src tests
```

## License

MIT. `vscode-avdmanager` is MIT-licensed and credited for the feature set and
its API-level data table.