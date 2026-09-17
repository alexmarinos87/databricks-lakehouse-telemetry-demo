#!/usr/bin/env python3
"""Check the installed CLI release without authentication or workspace commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED_VERSION = "1.14.1"
COMMAND_TIMEOUT_SECONDS = 10
MAX_VERSION_OUTPUT_BYTES = 16_384


class CLIInstallationError(RuntimeError):
    """A stable failure category without CLI output or local paths."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CLIInstallationError("cli_version_invalid")
        result[key] = value
    return result


def check_installation() -> dict[str, object]:
    """Execute only the local version command with an empty configuration home."""
    with tempfile.TemporaryDirectory(prefix="cli-version-") as temporary:
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": temporary,
            "XDG_CONFIG_HOME": temporary,
            "DATABRICKS_CONFIG_FILE": str(Path(temporary) / "absent-config"),
        }
        try:
            result = subprocess.run(
                ["databricks", "version", "--output", "json"],
                cwd=temporary, env=environment, stdin=subprocess.DEVNULL,
                capture_output=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CLIInstallationError("cli_version_timeout") from exc
        except OSError as exc:
            raise CLIInstallationError("cli_unavailable") from exc
    if result.returncode != 0:
        raise CLIInstallationError("cli_version_command_failed")
    if len(result.stdout) > MAX_VERSION_OUTPUT_BYTES:
        raise CLIInstallationError("cli_version_invalid")
    try:
        info = json.loads(result.stdout, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CLIInstallationError("cli_version_invalid") from exc
    if not isinstance(info, dict):
        raise CLIInstallationError("cli_version_invalid")
    if (info.get("Version") != EXPECTED_VERSION
            or info.get("IsSnapshot") is not False
            or info.get("Prerelease") != ""):
        raise CLIInstallationError("cli_version_mismatch")
    return {"status": "verified", "version": EXPECTED_VERSION,
            "evidence_boundary": "local_cli_installation_only"}


def main() -> int:
    try:
        result = check_installation()
    except CLIInstallationError as exc:
        print(json.dumps({"status": "failed", "category": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({"status": "failed", "category": "cli_check_environment_failed"}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
