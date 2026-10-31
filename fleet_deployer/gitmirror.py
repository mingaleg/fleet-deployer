"""A bare mirror of the config repo; nix evaluates straight from it, pinned by rev."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .runner import Runner


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    subject: str
    author: str
    committed_at: datetime  # naive UTC


class GitMirror:
    def __init__(self, url: str, path: Path, branch: str, runner: Runner):
        self.url, self.path, self.branch, self.runner = url, path, branch, runner

    def _git(self, *args: str, timeout: float = 120) -> str:
        res = self.runner.run(["git", "-C", str(self.path), *args], timeout=timeout)
        if not res.ok:
            raise RuntimeError(f"git {args[0]} failed: {res.stderr.strip()}")
        return res.stdout

    def fetch(self) -> None:
        if not (self.path / "HEAD").exists():
            res = self.runner.run(["git", "clone", "--bare", self.url, str(self.path)], timeout=600)
            if not res.ok:
                raise RuntimeError(f"git clone failed: {res.stderr.strip()}")
        # A bare repo can force-update its local branch directly, which is the
        # ref nix resolves `?ref=<branch>&rev=<sha>` against.
        self._git("fetch", "--prune", "origin", f"+refs/heads/{self.branch}:refs/heads/{self.branch}")

    def head(self) -> CommitInfo:
        return self.log(1)[0]

    def log(self, count: int) -> list[CommitInfo]:
        out = self._git("log", f"-{count}", "--format=%H%x00%s%x00%an%x00%ct", self.branch)
        commits = []
        for line in out.splitlines():
            sha, subject, author, ts = line.split("\x00")
            when = datetime.fromtimestamp(int(ts), timezone.utc).replace(tzinfo=None)
            commits.append(CommitInfo(sha, subject, author, when))
        return commits

    def flake_ref(self, sha: str) -> str:
        return f"git+file://{self.path}?ref={self.branch}&rev={sha}"
