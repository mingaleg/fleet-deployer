"""Evaluating and deploying nixosConfigurations."""

from __future__ import annotations

import shlex

from .runner import Result, Runner

NIX_FLAGS = ["--extra-experimental-features", "nix-command flakes"]
MODES = ("test", "switch", "boot")


def toplevel_attr(host: str) -> str:
    return f'nixosConfigurations."{host}".config.system.build.toplevel.outPath'


def eval_toplevel(runner: Runner, flake_ref: str, host: str) -> Result:
    return runner.run(
        ["nix", *NIX_FLAGS, "eval", "--raw", f"{flake_ref}#{toplevel_attr(host)}"],
        timeout=1800,
    )


def deploy_command(flake_ref: str, host: str, mode: str, ssh_user: str, target: str) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    return [
        "nixos-rebuild", mode,
        "--flake", f"{flake_ref}#{host}",
        "--target-host", f"{ssh_user}@{target}",
        "--sudo",
    ]


def nix_sshopts(ssh_args: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in ssh_args)
