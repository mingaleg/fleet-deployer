"""The fleet: tracks main, evaluates it per host, polls hosts, runs deploy jobs."""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import nixops, status
from .config import Config, HostConfig
from .db import (
    Commit,
    Expected,
    HostStatus,
    Job,
    commit_for_path,
    get_meta,
    iso,
    make_sessionmaker,
    set_meta,
    utcnow,
)
from .gitmirror import GitMirror
from .runner import Runner

log = logging.getLogger(__name__)

RECENT_COMMITS = 20


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


class Fleet:
    def __init__(self, config: Config, runner: Runner | None = None, db_path: Path | str | None = None):
        self.config = config
        self.runner = runner or Runner()
        self.Session = make_sessionmaker(db_path or config.db_path)
        self.mirror = GitMirror(config.repo_url, config.mirror_dir, config.branch, self.runner)
        self._jobs: queue.Queue[int] = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._eval_lock = threading.Lock()

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        self.recover_jobs()
        for target in (self._poll_loop, self._job_loop):
            t = threading.Thread(target=target, daemon=True, name=target.__name__)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._jobs.put(-1)

    def wake(self) -> None:
        """Ask the poller to refresh main and all hosts now."""
        self._wake.set()

    # ---- main branch -----------------------------------------------------

    def refresh_main(self) -> str:
        self.mirror.fetch()
        commits = self.mirror.log(RECENT_COMMITS)
        with self.Session.begin() as s:
            for c in commits:
                if not s.get(Commit, c.sha):
                    s.add(Commit(sha=c.sha, subject=c.subject, author=c.author, committed_at=c.committed_at))
            set_meta(s, "main_sha", commits[0].sha)
            set_meta(s, "main_fetched_at", iso(utcnow()))
        for host in self.config.hosts:
            self.expected_path(commits[0].sha, host)
        return commits[0].sha

    def main_sha(self) -> str | None:
        with self.Session() as s:
            return get_meta(s, "main_sha")

    def expected_path(self, sha: str, host: str) -> str | None:
        """Evaluate (once) what `host` would run at `sha`.

        Failures are cached too: a commit that doesn't evaluate never will.
        """
        with self.Session() as s:
            row = s.get(Expected, (sha, host))
            if row:
                return row.out_path
        with self._eval_lock:
            res = nixops.eval_toplevel(self.runner, self.mirror.flake_ref(sha), host)
        out, err = (res.stdout.strip(), None) if res.ok else (None, res.stderr.strip()[-2000:])
        with self.Session.begin() as s:
            row = s.get(Expected, (sha, host)) or Expected(sha=sha, host=host)
            row.out_path, row.error, row.evaluated_at = out, err, utcnow()
            s.merge(row)
        if err:
            log.warning("eval of %s at %s failed: %s", host, sha[:8], err.splitlines()[-1:])
        return out

    # ---- host status -----------------------------------------------------

    def poll_host(self, host: HostConfig) -> None:
        now = utcnow()
        snap, err = None, None
        if host.target:
            try:
                snap = status.poll(self.runner, self.config.ssh_args(), self.config.ssh_user, host.target)
            except Exception as e:  # noqa: BLE001 - any failure means "unreachable"
                err = str(e)
        else:
            err = "no target configured"
        with self.Session.begin() as s:
            row = s.get(HostStatus, host.name) or HostStatus(host=host.name)
            row.last_attempt_at, row.error = now, err
            if snap:
                row.last_success_at = now
                row.current_path, row.boot_path, row.booted_path = snap.current, snap.boot, snap.booted
                row.current_kernel, row.booted_kernel = snap.current_kernel, snap.booted_kernel
                row.uptime_s = snap.uptime
            s.merge(row)

    def poll_all(self) -> None:
        for host in self.config.hosts.values():
            self.poll_host(host)
        self.resolve_interrupted()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh_main()
            except Exception:  # noqa: BLE001
                log.exception("refreshing main failed")
            try:
                self.poll_all()
            except Exception:  # noqa: BLE001
                log.exception("polling hosts failed")
            self._wake.wait(self.config.poll_interval)
            self._wake.clear()

    # ---- jobs ------------------------------------------------------------

    def submit(self, host: str, mode: str, rev: str | None = None) -> int:
        h = self.config.hosts.get(host)
        if not h:
            raise NotFound(f"unknown host {host!r}")
        if not h.deploy or not h.target:
            raise Invalid(f"{host} is status-only")
        if mode not in nixops.MODES:
            raise Invalid(f"mode must be one of {', '.join(nixops.MODES)}")
        sha = rev or self.main_sha()
        if not sha:
            raise Invalid("main has not been fetched yet")
        with self.Session.begin() as s:
            if not s.get(Commit, sha):
                raise Invalid(f"unknown revision {sha!r}; only recent commits of {self.config.branch} can be deployed")
            job = Job(host=host, mode=mode, sha=sha)
            s.add(job)
            s.flush()
            job_id = job.id
        self._jobs.put(job_id)
        return job_id

    def _job_loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._jobs.get()
            if job_id < 0:
                return
            try:
                self.run_job(job_id)
            except Exception as e:  # noqa: BLE001
                log.exception("job %s crashed", job_id)
                with self.Session.begin() as s:
                    job = s.get(Job, job_id)
                    job.state, job.error, job.finished_at = "failed", str(e), utcnow()

    def run_job(self, job_id: int) -> None:
        with self.Session.begin() as s:
            job = s.get(Job, job_id)
            if job.state != "queued":
                return
            job.state, job.started_at = "running", utcnow()
            job.log_path = str(self.config.log_dir / f"job-{job.id}.log")
            host, mode, sha, log_path = job.host, job.mode, job.sha, Path(job.log_path)
        target = self.config.hosts[host]
        expected = self.expected_path(sha, host)
        with self.Session.begin() as s:
            s.get(Job, job_id).expected_path = expected
        if expected is None:
            log_path.write_text("evaluation failed; see the dashboard for the error\n")
            self._finish(job_id, 1, "evaluation failed")
            return
        cmd = nixops.deploy_command(self.mirror.flake_ref(sha), host, mode, self.config.ssh_user, target.target)
        env = {"NIX_SSHOPTS": nixops.nix_sshopts(self.config.ssh_args())}
        code = self.runner.run_logged(cmd, log_path, env=env)
        self._finish(job_id, code, None if code == 0 else f"nixos-rebuild exited {code}")
        self.poll_host(target)

    def _finish(self, job_id: int, code: int, error: str | None) -> None:
        with self.Session.begin() as s:
            job = s.get(Job, job_id)
            job.exit_code, job.error, job.finished_at = code, error, utcnow()
            job.state = "succeeded" if code == 0 else "failed"

    def recover_jobs(self) -> None:
        """After a restart: requeue queued jobs; running ones were cut off.

        Deploying ronove to itself restarts this service mid-job. The switch
        itself runs detached (nixos-rebuild uses systemd-run), so whether it
        worked is decided by the host's state on the next poll.
        """
        with self.Session.begin() as s:
            for job in s.scalars(select(Job).where(Job.state == "running")):
                job.state = "interrupted"
            queued = [j.id for j in s.scalars(select(Job).where(Job.state == "queued").order_by(Job.id))]
        for job_id in queued:
            self._jobs.put(job_id)

    def resolve_interrupted(self) -> None:
        with self.Session.begin() as s:
            for job in s.scalars(select(Job).where(Job.state == "interrupted")):
                st = s.get(HostStatus, job.host)
                if not st or not st.last_success_at or not job.started_at or st.last_success_at < job.started_at:
                    continue  # no fresh status yet
                landed = st.boot_path if job.mode == "boot" else st.current_path
                ok = job.expected_path is not None and landed == job.expected_path
                job.state = "succeeded" if ok else "failed"
                job.finished_at = utcnow()
                job.error = None if ok else "interrupted, and the host does not run the expected system"

    # ---- views -----------------------------------------------------------

    def host_views(self) -> list[dict]:
        with self.Session() as s:
            main = get_meta(s, "main_sha")
            return [self._host_view(s, h, main) for h in self.config.hosts.values()]

    def host_view(self, name: str) -> dict:
        h = self.config.hosts.get(name)
        if not h:
            raise NotFound(f"unknown host {name!r}")
        with self.Session() as s:
            return self._host_view(s, h, get_meta(s, "main_sha"))

    def _host_view(self, s: Session, h: HostConfig, main: str | None) -> dict:
        st = s.get(HostStatus, h.name)
        exp = s.get(Expected, (main, h.name)) if main else None
        expected = exp.out_path if exp else None
        reachable = bool(st and st.last_attempt_at and st.error is None)
        state = status.compute_state(
            reachable=reachable,
            ever_succeeded=bool(st and st.last_success_at),
            current=st.current_path if st else None,
            boot=st.boot_path if st else None,
            current_kernel=st.current_kernel if st else None,
            booted_kernel=st.booted_kernel if st else None,
            expected=expected,
        )
        active = s.scalars(
            select(Job).where(Job.host == h.name, Job.state.in_(("queued", "running", "interrupted"))).order_by(Job.id)
        ).first()
        current = st.current_path if st else None
        return {
            "name": h.name,
            "target": h.target,
            "deploy": h.deploy,
            "state": state.state,
            "flags": state.flags,
            "current_path": current,
            "current_commit": commit_for_path(s, h.name, current),
            "boot_path": st.boot_path if st else None,
            "booted_path": st.booted_path if st else None,
            "expected_path": expected,
            "expected_error": exp.error if exp else None,
            "main_sha": main,
            "uptime_s": st.uptime_s if st else None,
            "last_attempt_at": iso(st.last_attempt_at) if st else None,
            "last_success_at": iso(st.last_success_at) if st else None,
            "error": st.error if st else None,
            "active_job": active.id if active else None,
        }

    def main_view(self) -> dict:
        with self.Session() as s:
            sha = get_meta(s, "main_sha")
            c = s.get(Commit, sha) if sha else None
            recent = s.scalars(select(Commit).order_by(Commit.committed_at.desc()).limit(RECENT_COMMITS)).all()
            return {
                "branch": self.config.branch,
                "repo_url": self.config.repo_url,
                "sha": sha,
                "subject": c.subject if c else None,
                "author": c.author if c else None,
                "committed_at": iso(c.committed_at) if c else None,
                "fetched_at": get_meta(s, "main_fetched_at"),
                "recent": [
                    {"sha": r.sha, "subject": r.subject, "committed_at": iso(r.committed_at)} for r in recent
                ],
            }

    def job_views(self, limit: int = 30) -> list[dict]:
        with self.Session() as s:
            jobs = s.scalars(select(Job).order_by(Job.id.desc()).limit(limit)).all()
            return [self._job_view(s, j) for j in jobs]

    def job_view(self, job_id: int) -> dict:
        with self.Session() as s:
            job = s.get(Job, job_id)
            if not job:
                raise NotFound(f"no job {job_id}")
            return self._job_view(s, job)

    def _job_view(self, s: Session, j: Job) -> dict:
        c = s.get(Commit, j.sha)
        return {
            "id": j.id,
            "host": j.host,
            "mode": j.mode,
            "sha": j.sha,
            "subject": c.subject if c else None,
            "state": j.state,
            "exit_code": j.exit_code,
            "error": j.error,
            "expected_path": j.expected_path,
            "created_at": iso(j.created_at),
            "started_at": iso(j.started_at),
            "finished_at": iso(j.finished_at),
        }

    def job_log(self, job_id: int, offset: int = 0) -> tuple[str, int]:
        with self.Session() as s:
            job = s.get(Job, job_id)
            if not job:
                raise NotFound(f"no job {job_id}")
            path = Path(job.log_path) if job.log_path else None
        if not path or not path.exists():
            return "", offset
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        return data.decode(errors="replace"), offset + len(data)
