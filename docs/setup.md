# Setup

`avdmanager` drives the Android SDK command-line tools, so those need to be
installed and reachable first.

## Requirements

- Python 3.11 or newer
- JDK 17 or newer (the SDK tools are Java programs)
- Android SDK **Command-line Tools**

## 1. Install the JDK

```bash
# Arch
sudo pacman -S jdk17-openjdk
# Debian/Ubuntu
sudo apt install openjdk-17-jdk
# macOS
brew install openjdk@17
```

## 2. Install the Android SDK Command-line Tools

Create an SDK root, e.g. `~/Android/Sdk`, then download and unpack the
command-line tools:

```bash
mkdir -p ~/Android/Sdk/cmdline-tools
cd /tmp
curl -O https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip
unzip -q commandlinetools-linux-*.zip -d ~/Android/Sdk/cmdline-tools
# the archive unpacks to a `cmdline-tools` directory; rename it to `latest`
mv ~/Android/Sdk/cmdline-tools/cmdline-tools ~/Android/Sdk/cmdline-tools/latest
```

The resulting layout must be:

```
~/Android/Sdk/                    <- SDK root
  cmdline-tools/
    latest/
      bin/
        avdmanager
        sdkmanager
        android
  platform-tools/
    adb
  emulator/
    emulator
  build-tools/
  platforms/
  system-images/
```

`avdmanager` looks for the binaries under
`<sdk root>/cmdline-tools/<version>/bin` where `<version>` defaults to
`latest`.

## 3. Point the app at your SDK

Any one of these works; the first match wins:

1. `avdmanager.sdk_path` in the config file
2. the `ANDROID_HOME` environment variable
3. the `ANDROID_SDK_ROOT` environment variable
4. plain `PATH` lookup of `sdkmanager` / `android`

Note that `ANDROID_HOME` deliberately takes precedence over
`ANDROID_SDK_ROOT`, matching the legacy tooling.

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
```

You can also pass it per-run without touching your environment:

```bash
avdmanager --sdk-path ~/Android/Sdk
```

The status bar shows the resolved backend and any problems; press `s` for
Settings, which previews exactly what the current values resolve to.

## 4. Which command-line tool is used

`avdmanager` prefers the modern `android` CLI that ships with cmdline-tools
23+ (`android sdk list/install/remove/update`, `android emulator start/stop`)
because `sdkmanager` is deprecated. If that binary is missing it falls back to
`sdkmanager` automatically.

Force the legacy path with either:

```bash
avdmanager --legacy
```

or untick *Prefer the modern `android` CLI* in Settings.

## 5. Create your first AVD

Inside `avdmanager`, open **SDK Platforms** and install a system image such as
`system-images/android-36/google_apis_playstore/x86_64`, then press `n` to
open the create-AVD wizard.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Neither the android CLI nor sdkmanager could be found` | Install Command-line Tools, or set the SDK path in Settings |
| `Configured SDK root does not exist` | Correct `sdk_path` in Settings |
| `Emulator not found` | Install the `emulator` package from the SDK Tools tree |
| `list avd` hangs | Usually a stale SDK repository lock; the app cancels with `ctrl+c` |
| Licences block | Press `L` — the app answers `y` automatically on the child's stdin |