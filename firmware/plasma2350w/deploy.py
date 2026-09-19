"""Export or upload the complete MicroPython firmware bundle."""

import argparse
import shutil
import subprocess
from pathlib import Path

FILES = [
    "main.py", "hardware.py", "scheduler.py", "environment.py",
    "pumpkinpi_core/__init__.py", "pumpkinpi_core/accounting.py",
    "pumpkinpi_core/candle.py", "pumpkinpi_core/power.py",
    "pumpkinpi_core/runtime.py",
    "pumpkinpi_core/timebase.py",
    "pumpkinpi_core/billing.py",
    "pumpkinpi_core/demo.py",
    "providers/__init__.py",
    "providers/github.py", "providers/direct_network.py",
    "certs/github-root.pem",
]


def export_bundle(destination):
    source = Path(__file__).resolve().parent
    destination = Path(destination)
    for relative in FILES:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    return list(FILES)


def deploy(destination):
    """Backward-compatible bundle export; this does not access a USB device."""
    return export_bundle(destination)


def mpremote_commands(device=None, config=None, executable="mpremote", env=None):
    prefix = [executable]
    if device:
        prefix += ["connect", device]
    commands = [prefix + ["exec",
        "import os\nfor p in ('pumpkinpi_core','providers','certs'):\n"
        " try: os.mkdir(p)\n"
        " except OSError:\n"
        "  if not (os.stat(p)[0] & 0x4000): raise"]]
    source = Path(__file__).resolve().parent
    for relative in FILES:
        commands.append(prefix + ["fs", "cp", str(source / relative),
                                  ":" + relative])
    if config is not None:
        commands.append(prefix + ["fs", "cp", str(Path(config).resolve()),
                                  ":config.json"])
    if env is not None:
        commands.append(prefix + ["fs", "cp", str(Path(env).resolve()), ":.env"])
    return commands


def install(device=None, config=None, executable="mpremote", runner=subprocess.run,
            env=None):
    for command in mpremote_commands(device, config, executable, env):
        runner(command, check=True)
    return list(FILES)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    bundle = commands.add_parser("bundle", help="export files to a local folder")
    bundle.add_argument("destination")
    upload = commands.add_parser(
        "install", help="upload to a running MicroPython board with mpremote")
    upload.add_argument("--device", help="serial device, such as /dev/cu.usbmodem...")
    upload.add_argument("--config", help="private board config.json to upload")
    upload.add_argument("--env", help="explicitly upload private Wi-Fi/GitHub .env")
    upload.add_argument("--mpremote", default="mpremote")
    args = parser.parse_args()
    if args.command == "bundle":
        copied = export_bundle(args.destination)
        print("Exported %d firmware files; no device was accessed." % len(copied))
    else:
        copied = install(args.device, args.config, args.mpremote, env=args.env)
        print("Uploaded %d firmware files to MicroPython storage." % len(copied))
