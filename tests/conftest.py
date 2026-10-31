from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from fleet_deployer.config import Config, HostConfig
from fleet_deployer.gitmirror import CommitInfo
from fleet_deployer.runner import Result
from fleet_deployer.service import Fleet

SHA1 = "a" * 40
SHA2 = "b" * 40


class FakeRunner:
    """Answers git/nix/ssh/nixos-rebuild calls from canned data."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.toplevels: dict[tuple[str, str], str] = {}  # (sha, host) -> path
        self.snapshots: dict[str, str | None] = {}  # target -> status output (None = unreachable)
        self.deploy_exit = 0
        self.deployed: list[list[str]] = []

    def run(self, cmd, *, env=None, timeout=None):
        self.calls.append(cmd)
        if cmd[0] == "nix" and "eval" in cmd:
            ref = cmd[-1]
            sha = ref.split("rev=")[1].split("#")[0]
            host = ref.split('nixosConfigurations."')[1].split('"')[0]
            path = self.toplevels.get((sha, host))
            return Result(0, path, "") if path else Result(1, "", "error: attribute missing")
        if cmd[0] == "ssh":
            target = cmd[-2].split("@", 1)[1]
            out = self.snapshots.get(target)
            return Result(0, out, "") if out else Result(255, "", "ssh: connect to host: No route to host")
        raise AssertionError(f"unexpected command {cmd}")

    def run_logged(self, cmd, log_path: Path, *, env=None):
        self.deployed.append(cmd)
        log_path.write_text("$ " + " ".join(cmd) + "\nbuilding...\n")
        return self.deploy_exit


def snapshot(current, boot=None, kernel="k1", booted_kernel=None):
    return (
        f"current={current}\nboot={boot or current}\nbooted={current}\n"
        f"current_kernel={kernel}\nbooted_kernel={booted_kernel or kernel}\nuptime=123.4\n"
    )


class FakeMirror:
    def __init__(self, commits):
        self.commits = commits
        self.path = Path("/mirror.git")

    def fetch(self):
        pass

    def log(self, count):
        return self.commits[:count]

    def head(self):
        return self.commits[0]

    def flake_ref(self, sha):
        return f"git+file:///mirror.git?ref=main&rev={sha}"


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def config(tmp_path):
    token = tmp_path / "token"
    token.write_text("s3cret\n")
    return Config(
        repo_url="https://example.invalid/repo.git",
        state_dir=tmp_path,
        hosts={
            "ronove": HostConfig("ronove", "localhost", True),
            "vps": HostConfig("vps", "home-gw", True),
            "mingamini": HostConfig("mingamini", "mingamini", False),
        },
        token_file=token,
    )


@pytest.fixture
def fleet(config, runner):
    f = Fleet(config, runner=runner, db_path=":memory:")
    f.mirror = FakeMirror(
        [
            CommitInfo(SHA2, "second", "me", datetime(2026, 10, 31, 12, 0)),
            CommitInfo(SHA1, "first", "me", datetime(2026, 10, 30, 12, 0)),
        ]
    )
    config.log_dir.mkdir(parents=True, exist_ok=True)
    return f
