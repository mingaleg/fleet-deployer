"""The single place that spawns processes, so tests can substitute a fake."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner:
    def run(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Result:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, **(env or {})},
            )
        except subprocess.TimeoutExpired:
            return Result(124, "", f"timed out after {timeout}s: {cmd[0]}")
        except FileNotFoundError as e:
            return Result(127, "", str(e))
        return Result(proc.returncode, proc.stdout, proc.stderr)

    def run_logged(self, cmd: list[str], log_path: Path, *, env: dict[str, str] | None = None) -> int:
        """Run with stdout+stderr appended to `log_path` as they're produced."""
        with open(log_path, "a", buffering=1) as log:
            log.write("$ " + " ".join(cmd) + "\n")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, **(env or {})},
                )
            except FileNotFoundError as e:
                log.write(f"{e}\n")
                return 127
            return proc.wait()
