"""ADB Capture — Screenshot and screen recording tool for ADB-connected Android devices."""

from __future__ import annotations

import argparse
import platform
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console

from adbcap import __version__ as VERSION

console = Console(highlight=False)
CONFIG_DIR = Path.home() / ".config" / "adbcap"
CONFIG_FILE = CONFIG_DIR / "config.toml"
FALLBACK_OUTPUT_DIR = Path.home() / "Documents" / "adbcap"


DEFAULTS = {
    "dir": str(FALLBACK_OUTPUT_DIR),
    "open": "true",
    "countdown": "2",
    "touches_shown": "true",
    "gif": "",
}


def _load_config() -> dict[str, str]:
    config = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key in DEFAULTS:
                    config[key] = value
    return config


def _save_config(config: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in config.items():
        if value != DEFAULTS[key]:
            if key == "dir":
                lines.append(f'{key} = "{value}"')
            else:
                lines.append(f"{key} = {value}")
    CONFIG_FILE.write_text("\n".join(lines) + "\n" if lines else "")


def _parse_bool(value: str) -> bool:
    return value.lower() in ("true", "1", "yes")


@dataclass
class Device:
    id: str
    name: str

    @property
    def safe_name(self) -> str:
        return self.name.replace(" ", "-")


class CLIError(Exception):
    """Raised to exit with an error message."""


def _adb(*args: str, quiet: bool = False, check: bool = False) -> None:
    result = subprocess.run(["adb", *args], capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    if output and not quiet:
        for line in output.splitlines():
            console.print(f"  [dim]{line}[/dim]")
    if check and result.returncode != 0:
        raise CLIError(f"adb command failed: {' '.join(args)}")


def _adb_capture(*args: str) -> str:
    result = subprocess.run(["adb", *args], capture_output=True, text=True)
    return result.stdout.strip()


def _adb_devices() -> list[Device]:
    output = _adb_capture("devices")
    found = []
    for line in output.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].strip() == "device":
            device_id = parts[0].strip()
            name = _adb_capture(
                "-s", device_id, "shell", "settings", "get", "global", "device_name",
            ).strip("\r\n")
            if not name or name == "null":
                name = device_id
            found.append(Device(id=device_id, name=name))
    return found


def _adb_screencap(device: Device, remote_path: str) -> None:
    _adb("-s", device.id, "shell", "screencap", "-p", remote_path, check=True)


def _adb_pull(device: Device, remote_path: str, local_path: Path) -> None:
    _adb("-s", device.id, "pull", remote_path, str(local_path), check=True)


def _adb_rm(device: Device, remote_path: str) -> None:
    _adb("-s", device.id, "shell", "rm", remote_path, quiet=True)


MAX_RECORD_DIMENSION = 1920


def _get_screen_size(device: Device) -> tuple[int, int] | None:
    output = _adb_capture("-s", device.id, "shell", "wm", "size")
    for line in output.splitlines():
        if "Physical size" in line:
            try:
                w, h = line.split(":")[-1].strip().split("x")
                return int(w), int(h)
            except ValueError:
                return None
    return None


def _scaled_size(width: int, height: int) -> str | None:
    if max(width, height) <= MAX_RECORD_DIMENSION:
        return None
    scale = MAX_RECORD_DIMENSION / max(width, height)
    new_w = int(width * scale) & ~1  # h264 requires even dimensions
    new_h = int(height * scale) & ~1
    return f"{new_w}x{new_h}"


def _get_recording_res(device: Device) -> tuple[str, bool]:
    """Returns (resolution_string, needs_scaling)."""
    size = _get_screen_size(device)
    if not size:
        return "unknown", False
    scaled = _scaled_size(*size)
    if scaled:
        return scaled, True
    return f"{size[0]}x{size[1]}", False


def _adb_start_screenrecord(device: Device, remote_path: str, recording_res: str, scaled: bool,
                            time_limit: int = 0) -> subprocess.Popen:
    cmd = ["adb", "-s", device.id, "shell", "screenrecord", "--bit-rate", "8000000",
           "--time-limit", str(time_limit)]
    if scaled:
        cmd.extend(["--size", recording_res])
    cmd.append(remote_path)

    warn_file = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
    warn_path = Path(warn_file.name)
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stderr=warn_file,
            stdout=subprocess.DEVNULL,
        )
        time.sleep(0.2)

        warn_file.close()
        if warn_path.stat().st_size > 0:
            for line in warn_path.read_text().splitlines():
                console.print(f"  [dim]{line}[/dim]")
    finally:
        warn_path.unlink(missing_ok=True)

    return proc


def _adb_stop_screenrecord(device: Device, proc: subprocess.Popen) -> None:
    _adb("-s", device.id, "shell", "pkill", "-INT", "screenrecord", quiet=True)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(1)


def _prompt_brew_install(package: str, cask: bool = False) -> bool:
    if not shutil.which("brew"):
        return False
    reply = console.input(f"[bold cyan]Install {package} via Homebrew? [Y/n]:[/bold cyan] ").strip()
    if reply.lower() == "n":
        sys.exit(1)
    cmd = ["brew", "install"]
    if cask:
        cmd.append("--cask")
    cmd.append(package)
    subprocess.run(cmd)
    console.print()
    reply = console.input(f"[bold cyan]{package} installed. Continue? [Y/n]:[/bold cyan] ").strip()
    if reply.lower() == "n":
        sys.exit(0)
    console.print()
    return True


def _require_adb() -> None:
    if shutil.which("adb"):
        return
    console.print("[yellow]adb (Android Debug Bridge) is required but not installed.[/yellow]")
    console.print()
    if not _prompt_brew_install("android-platform-tools", cask=True):
        console.print("[bright_red]\u2717 Install adb and try again.[/bright_red]")
        console.print("  [dim]Download: https://developer.android.com/tools/releases/platform-tools[/dim]")
        sys.exit(1)


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg"):
        return
    console.print("[yellow]ffmpeg is required for gif conversion but is not installed.[/yellow]")
    console.print()
    if not _prompt_brew_install("ffmpeg"):
        console.print("[bright_red]\u2717 Install ffmpeg and try again.[/bright_red]")
        sys.exit(1)


def _convert_to_gif(input_path: Path, hd: bool = False) -> bool:
    output_path = input_path.with_suffix(".gif")
    filt = "fps=30" if hd else "fps=15,scale=480:-1"

    console.print("[bold]Converting to gif...[/bold]")
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as lf:
        log_path = Path(lf.name)

    try:
        with log_path.open("w") as log_file:
            result = subprocess.run(
                [
                    "ffmpeg", "-i", str(input_path),
                    "-filter_complex", f"{filt},split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                    "-y", str(output_path),
                ],
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )

        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            return False

        input_path.unlink(missing_ok=True)
        console.print(f"[green]\u2713[/green] {_clickable(output_path)}")
        return True
    finally:
        log_path.unlink(missing_ok=True)


def _open_directory(path: Path) -> None:
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["open", str(path)], capture_output=True)
    elif shutil.which("explorer.exe"):  # WSL (reports as Linux)
        wsl_path = subprocess.run(
            ["wslpath", "-w", str(path)], capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(["explorer.exe", wsl_path], capture_output=True)
    elif system == "Linux" and shutil.which("xdg-open"):
        subprocess.run(["xdg-open", str(path)], capture_output=True)
    else:
        console.print(f"  [dim]Output directory: {path}[/dim]")


def _is_single_frame(path: Path) -> bool | None:
    if not shutil.which("ffprobe"):
        return None
    result = subprocess.run(
        ["ffprobe", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    count = result.stdout.strip()
    if count.isdigit():
        return int(count) <= 1
    return count == ""


def _extract_frame(video_path: Path, png_path: Path) -> None:
    if not shutil.which("ffmpeg"):
        return
    subprocess.run(
        ["ffmpeg", "-i", str(video_path), "-frames:v", "1", "-y", str(png_path)],
        capture_output=True,
    )


def _short(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def _clickable(path: Path) -> str:
    return f"[link=file://{path}]{_short(path)}[/link]"


def _timestamp() -> str:
    now = datetime.now()
    return f"{now:%y%m%d_%H%M%S}{now.microsecond // 10000:02d}"


def _require_devices() -> list[Device]:
    devices = _adb_devices()
    if not devices:
        raise CLIError("No ADB devices connected.")
    return devices


def _select_devices(
    devices: list[Device], allow_all: bool = False, selection: str = "",
) -> list[Device]:
    if selection:
        sel = selection.lower()
        if sel.isdigit():
            idx = int(sel)
            if 1 <= idx <= len(devices):
                return [devices[idx - 1]]
        if allow_all and sel in ("a", "all"):
            return list(devices)
        matches = [d for d in devices if sel in d.name.lower()]
        if len(matches) == 1:
            return [matches[0]]
        if len(matches) > 1:
            raise CLIError(
                f"Multiple devices match [bold]{selection}[/bold]: "
                + ", ".join(d.name for d in matches)
                + ". Be more specific or use a device number."
            )
        console.print(f"[bright_red]\u2717 No device matching [bold]{selection}[/bold].[/bright_red]")
        console.print()
        console.print("[bold]Connected devices:[/bold]")
        for i, d in enumerate(devices, 1):
            console.print(f"  [bold]{i})[/bold] {d.name} [dim]{d.id}[/dim]")
        sys.exit(1)

    if len(devices) == 1:
        return [devices[0]]

    console.print("[bold]Multiple devices connected:[/bold]")
    console.print()
    for i, d in enumerate(devices, 1):
        console.print(f"  [bold]{i})[/bold] [bold]{d.name:<20s}[/bold] [dim]{d.id}[/dim]")
    if allow_all:
        console.print(f"  [bold]A)[/bold] All devices")
    console.print()

    max_idx = len(devices)
    suffix = ", A" if allow_all else ""

    while True:
        choice = console.input(f"[bold cyan]Select [1-{max_idx}{suffix}]:[/bold cyan] ").strip()

        if allow_all and choice.lower() == "a":
            return list(devices)
        if choice.isdigit() and 1 <= int(choice) <= max_idx:
            return [devices[int(choice) - 1]]

        console.print("[bright_red]Invalid selection, try again.[/bright_red]")


# --- commands ---


def _is_screen_on(device: Device) -> bool:
    output = _adb_capture("-s", device.id, "shell", "dumpsys", "power")
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("mWakefulness="):
            return stripped.split("=", 1)[1] in ("Awake", "Dreaming")
        if "Display Power" in stripped and "state=" in stripped:
            return "ON" in stripped
    return True  # assume on if we can't determine


def cmd_screenshot(args: argparse.Namespace) -> None:
    devices = _require_devices()
    selected = _select_devices(devices, allow_all=True, selection=args.device or "")

    screen_off = [d for d in selected if not _is_screen_on(d)]
    if screen_off:
        names = ", ".join(d.name for d in screen_off)
        raise CLIError(f"Screen is off on {names}. Turn the screen on before capturing.")

    for device in selected:
        filename = f"{_timestamp()}_{device.safe_name}_screenshot.png"
        remote = f"/sdcard/{filename}"
        local = args.dir / filename

        console.print(f"[bold]Capturing {device.name}...[/bold]")
        _adb_screencap(device, remote)
        _adb_pull(device, remote, local)
        _adb_rm(device, remote)
        console.print(f"[green]\u2713[/green] {_clickable(local)}")

    if args.open:
        _open_directory(args.dir)


def cmd_record(args: argparse.Namespace) -> None:
    if args.gif:
        _require_ffmpeg()

    devices = _require_devices()
    selected = _select_devices(devices, selection=args.device or "")
    device = selected[0]

    if not _is_screen_on(device):
        raise CLIError(f"Screen is off on {device.name}. Turn the screen on before recording.")

    ts = _timestamp()
    remote_path = f"/sdcard/{ts}_screenrec.mp4"
    local_filename = f"{ts}_{device.safe_name}_screenrec.mp4"
    local_path = args.dir / local_filename

    show_touches = args.touches_shown
    original_touches = None
    if show_touches:
        original_touches = _adb_capture("-s", device.id, "shell", "settings", "get", "system", "show_touches").strip()
        _adb("-s", device.id, "shell", "settings", "put", "system", "show_touches", "1", quiet=True)

    def _restore_touches() -> None:
        if show_touches:
            subprocess.run(
                ["adb", "-s", device.id, "shell", "settings", "put", "system", "show_touches", original_touches or "0"],
                capture_output=True,
            )

    recording_res, needs_scaling = _get_recording_res(device)
    proc = None

    def on_cancel(_sig, _frame):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        console.print()
        console.print("[bold]Cancelling recording...[/bold]")
        if proc is not None:
            _adb_stop_screenrecord(device, proc)
            _adb_rm(device, remote_path)
        _restore_touches()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_cancel)

    gif_label = " gif" if args.gif else ""
    countdown = args.countdown
    if countdown > 0:
        console.print(f"[bold]\u25cf Recording{gif_label} {device.name} at [dim]{recording_res}[/dim] in [/bold]", end="")
        for i in range(countdown, 0, -1):
            console.print(f"[bold]{i}[/bold]", end="")
            for _ in range(3):
                time.sleep(1 / 3)
                console.print("[bold].[/bold]", end="")
        console.print()
    else:
        console.print(f"[bold]\u25cf Recording{gif_label} {device.name} at [dim]{recording_res}[/dim]...[/bold]")

    proc = _adb_start_screenrecord(device, remote_path, recording_res, needs_scaling,
                                   time_limit=args.time_limit)

    if proc.poll() is not None:
        _restore_touches()
        raise CLIError("Recording failed to start. See device output above.")

    time_limit_hit = False
    console.print("[bold cyan]Recording started, press Enter to stop.[/bold cyan] ", end="")
    try:
        while True:
            if select.select([sys.stdin], [], [], 0.5)[0]:
                sys.stdin.readline()
                break
            if proc.poll() is not None:
                time_limit_hit = True
                break
    except EOFError:
        pass

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    if time_limit_hit:
        console.print()
        console.print(f"[yellow]Time limit ({args.time_limit}s) reached.[/yellow]")
        console.print("[bold]Saving...[/bold]")
    else:
        console.print("[bold]Stopping recording and saving...[/bold]")
    if not time_limit_hit:
        _adb_stop_screenrecord(device, proc)
    _restore_touches()
    _adb_pull(device, remote_path, local_path)
    _adb_rm(device, remote_path)

    single_frame = _is_single_frame(local_path)
    if single_frame:
        png_path = local_path.with_suffix(".png")
        _extract_frame(local_path, png_path)
        if png_path.exists():
            local_path.unlink(missing_ok=True)
            console.print("[yellow]Recording had no screen changes — saved as screenshot instead.[/yellow]")
            console.print(f"[green]\u2713[/green] {_clickable(png_path)}")
        else:
            console.print(f"[green]\u2713[/green] {_clickable(local_path)}")
    elif args.gif:
        if not _convert_to_gif(local_path, hd=(args.gif == "hi")):
            console.print(f"[bright_red]\u2717 Gif conversion failed. Video saved as mp4 instead.[/bright_red]")
            console.print(f"[green]\u2713[/green] {_clickable(local_path)}")
    else:
        console.print(f"[green]\u2713[/green] {_clickable(local_path)}")

    if args.open:
        _open_directory(args.dir)


def cmd_list(_args: argparse.Namespace) -> None:
    devices = _require_devices()

    console.print()
    console.print("[bold]Connected devices:[/bold]")
    console.print()
    for i, d in enumerate(devices, 1):
        size = _get_screen_size(d)
        res = f"{size[0]}x{size[1]}" if size else "?"
        console.print(f"  [bold]{i})[/bold] [bold]{d.name:<20s}[/bold] [dim]{res:<12s} {d.id}[/dim]")
    console.print()


def cmd_open(args: argparse.Namespace) -> None:
    _open_directory(args.dir)


def cmd_config(args: argparse.Namespace) -> None:
    if args.reset:
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
            console.print("[green]\u2713[/green] Config reset to defaults.")
        else:
            console.print("  [dim]Already using defaults.[/dim]")
        return

    if args.key:
        key = args.key
        if key not in DEFAULTS:
            raise CLIError(f"Unknown config key: [bold]{key}[/bold]. Valid keys: {', '.join(DEFAULTS)}")

        if args.value is None:
            config = _load_config()
            is_default = config[key] == DEFAULTS[key]
            suffix = " [dim](default)[/dim]" if is_default else ""
            console.print(f"  [bold]{key}[/bold] = {config[key]}{suffix}")
            return

        value = args.value
        if key == "dir":
            path = Path(value).expanduser().resolve()
            if not path.parent.exists():
                raise CLIError(f"Parent directory does not exist: {path.parent}")
            value = str(path)
        elif key in ("open", "touches_shown"):
            if value.lower() not in ("true", "false", "1", "0", "yes", "no"):
                raise CLIError(f"Invalid value for {key}: must be true or false.")
        elif key == "countdown":
            if not value.isdigit():
                raise CLIError(f"Invalid value for {key}: must be a number.")
        elif key == "gif":
            if value not in ("", "lo", "hi"):
                raise CLIError(f"Invalid value for {key}: must be lo, hi, or empty.")

        config = _load_config()
        config[key] = value
        _save_config(config)
        console.print(f"[green]\u2713[/green] {key} = {value}")
    else:
        config = _load_config()
        for key, value in config.items():
            is_default = value == DEFAULTS[key]
            suffix = " [dim](default)[/dim]" if is_default else ""
            console.print(f"  [bold]{key}[/bold] = {value}{suffix}")
        if CONFIG_FILE.exists():
            console.print(f"\n  [dim]Config: {CONFIG_FILE}[/dim]")


def cmd_clean(args: argparse.Namespace) -> None:
    output_dir = args.dir
    if not output_dir.exists():
        console.print("  [dim]Nothing to clean.[/dim]")
        return

    days = args.older_than
    extensions = {".png", ".mp4", ".gif"}
    now = time.time()
    cutoff = now - (days * 86400)

    files = [f for f in output_dir.iterdir() if f.suffix in extensions and f.stat().st_mtime < cutoff]

    if not files:
        console.print(f"  [dim]No files older than {days} days.[/dim]")
        return

    console.print(f"[bold]Found {len(files)} file(s) older than {days} days:[/bold]")
    for f in sorted(files):
        console.print(f"  [dim]{f.name}[/dim]")
    console.print()

    reply = console.input("[bold cyan]Delete these files? [y/N]:[/bold cyan] ").strip()
    if reply.lower() != "y":
        return

    for f in files:
        f.unlink()
    console.print(f"[green]\u2713[/green] Deleted {len(files)} file(s).")


# --- entry point ---


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adbcap",
        description="ADB Capture — Screenshot and screen recording tool for ADB-connected Android devices.",
    )
    parser.add_argument("-v", "--version", action="version", version=f"ADB Capture v{VERSION}")

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    cfg = _load_config()
    cfg_dir = Path(cfg["dir"]).expanduser()
    cfg_open = _parse_bool(cfg["open"])
    cfg_countdown = int(cfg["countdown"])
    cfg_touches = _parse_bool(cfg["touches_shown"])
    cfg_gif = cfg["gif"] or None

    _dir_arg = dict(default=cfg_dir, type=Path, metavar="DIR", help=f"Output directory (default: {_short(cfg_dir)})")
    _open_arg = dict(type=_parse_bool, default=cfg_open, metavar="BOOL", help=f"Open output directory after capture (default: {cfg_open})")

    ss = subparsers.add_parser(
        "shot", aliases=["ss"],
        help="Take a screenshot",
        description="Take a screenshot from a connected Android device.",
    )
    ss.add_argument("-d", "--device", default="", metavar="N", help="Select device by number, name, or 'all'")
    ss.add_argument("-o", "--open", **_open_arg)
    ss.add_argument("--dir", **_dir_arg)
    ss.set_defaults(func=cmd_screenshot)

    rec = subparsers.add_parser(
        "rec", aliases=["sr"],
        help="Record the screen",
        description="Record the screen of a connected Android device. Press Enter to stop, Ctrl+C to cancel.",
    )
    rec.add_argument("-d", "--device", default="", metavar="N", help="Select device by number or name")
    rec.add_argument("-o", "--open", **_open_arg)
    rec.add_argument("-t", "--touches-shown", type=_parse_bool, default=cfg_touches, metavar="BOOL", help=f"Show touch indicators during recording (default: {cfg_touches})")
    rec.add_argument("-c", "--countdown", type=int, default=cfg_countdown, metavar="SEC", help=f"Countdown before recording starts (default: {cfg_countdown}, 0 to skip)")
    rec.add_argument("-g", "--gif", nargs="?", const=cfg_gif or "lo", choices=["lo", "hi"], default=cfg_gif, metavar="QUALITY", help="Convert to gif: lo (480p, 15fps, default) or hi (full resolution, 30fps)")
    rec.add_argument("-l", "--time-limit", type=int, default=0, metavar="SEC", help="Max recording time in seconds (default: unlimited)")

    rec.add_argument("--dir", **_dir_arg)
    rec.set_defaults(func=cmd_record)

    ls = subparsers.add_parser(
        "list", aliases=["ls"],
        help="List connected devices",
        description="List all ADB-connected devices.",
    )
    ls.set_defaults(func=cmd_list)

    op = subparsers.add_parser(
        "open", aliases=["o"],
        help="Open the output directory",
        description="Open the output directory in the file manager.",
    )
    op.add_argument("--dir", **_dir_arg)
    op.set_defaults(func=cmd_open)

    cl = subparsers.add_parser(
        "clean",
        help="Delete old captures",
        description="Delete captures older than a given number of days.",
    )
    cl.add_argument("--older-than", type=int, default=30, metavar="DAYS", help="Delete files older than this many days (default: 30)")
    cl.add_argument("--dir", **_dir_arg)
    cl.set_defaults(func=cmd_clean)

    cfg = subparsers.add_parser(
        "config",
        help="View or set configuration",
        description=f"View or set default values. Keys: {', '.join(DEFAULTS)}",
    )
    cfg.add_argument("key", nargs="?", default=None, help="Config key to view or set")
    cfg.add_argument("value", nargs="?", default=None, help="Value to set")
    cfg.add_argument("--reset", action="store_true", help="Reset all config to defaults")
    cfg.set_defaults(func=cmd_config)

    return parser


_NO_ADB_COMMANDS = {cmd_open, cmd_clean, cmd_config}
_NO_MKDIR_COMMANDS = {cmd_clean, cmd_config}


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.func not in _NO_ADB_COMMANDS:
        _require_adb()

    if hasattr(args, "dir"):
        args.dir = args.dir.expanduser()
        if args.func not in _NO_MKDIR_COMMANDS:
            args.dir.mkdir(parents=True, exist_ok=True)

    try:
        args.func(args)
    except CLIError as e:
        console.print(f"[bright_red]\u2717 {e}[/bright_red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
