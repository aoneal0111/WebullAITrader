"""Run the config authentication body comparison under Python 3.13 and 3.11."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .diagnostic import BodyMode, TimestampMode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--timestamp",
        choices=[item.value for item in TimestampMode],
        default=TimestampMode.ISO_8601_UTC.value,
        help=(
            "Diagnostic only. epoch-milliseconds is contrary to Webull's "
            "currently published ISO 8601 UTC requirement and is never used by Atlas."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    interpreters = {minor: _find_python(minor) for minor in ("3.13", "3.11")}
    for minor, body_mode in (
        ("3.13", BodyMode.SDK_DEFAULT),
        ("3.13", BodyMode.EXPLICIT_EMPTY),
        ("3.11", BodyMode.SDK_DEFAULT),
        ("3.11", BodyMode.EXPLICIT_EMPTY),
    ):
        output = _run_worker(interpreters[minor], body_mode, args.timestamp)
        # Re-serialize an object with the exact allow-listed result keys. This
        # prevents unexpected subprocess output from reaching diagnostic stdout.
        payload = json.loads(output)
        allowed = {
            key: payload[key]
            for key in (
                "python_version",
                "sdk_version",
                "timestamp_format_classification",
                "body_type",
                "body_length",
                "http_status",
                "sanitized_error_code",
                "request_id",
            )
        }
        print(json.dumps(allowed, separators=(",", ":")))
    return 0


def _run_worker(python: Path, body_mode: BodyMode, timestamp: str) -> str:
    completed = subprocess.run(
        [
            str(python),
            "-m",
            "app.webull_auth_diagnostic.worker",
            "--body",
            body_mode.value,
            "--timestamp",
            timestamp,
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=True,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("diagnostic worker produced unexpected output")
    return lines[0]


def _find_python(minor: str) -> Path:
    override = os.environ.get(f"ATLAS_DIAGNOSTIC_PYTHON{minor.replace('.', '')}")
    candidates = [Path(override)] if override else []
    root = Path(__file__).resolve().parents[2]
    if minor == "3.13":
        candidates.extend((root / ".venv" / "Scripts" / "python.exe", root / ".venv313" / "Scripts" / "python.exe"))
    else:
        candidates.extend((root / ".venv311-auth-diagnostic" / "Scripts" / "python.exe",))
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "Python" / "Python311" / "python.exe")
    if sys.version_info[:2] == tuple(map(int, minor.split("."))):
        candidates.insert(0, Path(sys.executable))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"Python {minor} interpreter was not found")


if __name__ == "__main__":
    raise SystemExit(main())
