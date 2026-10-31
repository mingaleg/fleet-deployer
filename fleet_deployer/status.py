"""Reading what a host runs, and turning it into a dashboard state."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources

from .runner import Runner

# Shared with nixos-config (the flake's `lib.statusScript`), which installs it
# as the forced command on status-only hosts.
STATUS_SCRIPT = (resources.files(__package__) / "status.sh").read_text()


@dataclass
class Snapshot:
    current: str | None = None
    boot: str | None = None
    booted: str | None = None
    current_kernel: str | None = None
    booted_kernel: str | None = None
    uptime: float | None = None


def parse_snapshot(text: str) -> Snapshot:
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip() or None
    if not values.get("current"):
        raise ValueError("no current system in status output")
    uptime = values.get("uptime")
    return Snapshot(
        current=values.get("current"),
        boot=values.get("boot"),
        booted=values.get("booted"),
        current_kernel=values.get("current_kernel"),
        booted_kernel=values.get("booted_kernel"),
        uptime=float(uptime) if uptime else None,
    )


def poll(runner: Runner, ssh_args: list[str], ssh_user: str, target: str) -> Snapshot:
    res = runner.run(["ssh", *ssh_args, f"{ssh_user}@{target}", STATUS_SCRIPT], timeout=30)
    if not res.ok:
        msg = (res.stderr.strip().splitlines() or [f"ssh exited {res.returncode}"])[-1]
        raise RuntimeError(msg)
    return parse_snapshot(res.stdout)


@dataclass
class HostState:
    # in_sync | behind | unknown | unreachable | never_checked
    state: str
    flags: list[str] = field(default_factory=list)


def compute_state(
    *,
    reachable: bool,
    ever_succeeded: bool,
    current: str | None,
    boot: str | None,
    current_kernel: str | None,
    booted_kernel: str | None,
    expected: str | None,
) -> HostState:
    if not ever_succeeded:
        return HostState("never_checked" if reachable else "unreachable")
    flags = []
    if boot and current and boot != current:
        # A `test` deploy is live, or a `boot` deploy is waiting for a reboot.
        flags.append("boot_differs")
    if current_kernel and booted_kernel and current_kernel != booted_kernel:
        flags.append("reboot_pending")
    if not reachable:
        return HostState("unreachable", flags)
    if expected is None:
        return HostState("unknown", flags)
    return HostState("in_sync" if current == expected else "behind", flags)
