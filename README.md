# ADB Capture

Screenshot and screen recording tool for ADB-connected Android devices. Handles single or multiple connected devices automatically. Basically just an easy-to-use wrapper on top of `adb screencap` and `adb screenrecord`.

## Prerequisites

- **Python 3.9+**

- **[pipx](https://pipx.pypa.io/)** (recommended)

  ```bash
  brew install pipx && pipx ensurepath
  ```

- **[adb](https://developer.android.com/tools/adb)** (Android Debug Bridge) on your PATH

  ```bash
  brew install --cask android-platform-tools
  ```

  Or download [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) directly. Verify with `adb version`.

- **ffmpeg** (optional, for `--gif`)

  ```bash
  brew install ffmpeg
  ```

## Install

```bash
pipx install git+https://github.com/kpauly2/adbcap.git
```

Or clone and install locally:

```bash
git clone https://github.com/kpauly2/adbcap.git && cd adbcap
pipx install .
```

Uninstall: `pipx uninstall adbcap`

## Usage

| Command | Description |
|---|---|
| `adbcap shot (ss)` | Take a screenshot |
| `adbcap rec (sr)` | Record the screen |
| `adbcap list (ls)` | List connected devices |
| `adbcap open (o)` | Open the output directory |
| `adbcap clean` | Delete old captures (default: >30 days) |
| `adbcap config [key] [value]` | View or set default configuration |

With one device connected, commands run immediately. With multiple devices, you're prompted to choose.

Screenshots support an "All devices" option. Recordings are single-device only.

### Shared flags (`shot` and `rec`)

| Flag | Description |
|---|---|
| `-d, --device <n>` | Select device by number or name (skip prompt) |
| `-o, --open <bool>` | Open output directory after capture (default: true) |
| `--dir <dir>` | Output directory (overrides `adbcap config`, default: `~/Documents/adbcap`) |

### Recording flags (`rec` only)

| Flag | Description |
|---|---|
| `-c, --countdown <sec>` | Countdown before recording (default: 2, 0 to skip) |
| `-t, --touches-shown <bool>` | Show touch indicators (default: true) |
| `-g, --gif [lo\|hi]` | Convert to gif: lo (480p, 15fps, default) or hi (full resolution, 30fps) |
| `-l, --time-limit <sec>` | Max recording time in seconds (default: unlimited) |


### Examples

| Command | Description |
|---|---|
| `adbcap ss -d 1` | Screenshot device #1 |
| `adbcap ss -d Pixel` | Screenshot device matching "Pixel" |
| `adbcap ss -d all` | Screenshot all connected devices |
| `adbcap rec -g` | Record and convert to gif (lo quality) |
| `adbcap rec -g hi` | Record and convert to gif (full quality) |
| `adbcap rec -o false` | Record without opening Finder after |
| `adbcap ss --dir ~/Desktop` | Screenshot to a custom directory |
| `adbcap config` | View all configuration |
| `adbcap config dir ~/Desktop` | Set default output directory |
| `adbcap config countdown 5` | Set default countdown to 5 seconds |
| `adbcap config --reset` | Reset all config to defaults |

### Notes

- **Configuration**: `adbcap config <key> <value>` sets defaults for any flag, stored in `~/.config/adbcap/config.toml`. Keys: `dir`, `open`, `countdown`, `touches_shown`, `gif`. Flags on commands override config. Use `adbcap config --reset` to restore defaults.
- **Touch indicators**: Recordings automatically enable touch indicators (show taps) and restore the original setting when done. Disable with `--touches-shown false`.
- **Auto-scaling**: Recordings on high-resolution devices (>1920px) are automatically scaled down to stay within h264 codec limits, preserving aspect ratio.
- **Screen-off detection**: Commands will error if the device screen is off, preventing empty captures.
- **Static screen**: If a recording has no screen changes, it's automatically saved as a screenshot (PNG) instead of a broken video.
