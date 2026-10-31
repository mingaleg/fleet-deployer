import pytest

from fleet_deployer.db import Job
from fleet_deployer.service import Invalid, NotFound

from .conftest import SHA1, SHA2, snapshot


@pytest.fixture
def ready(fleet, runner):
    runner.toplevels = {(SHA2, "ronove"): "/s/r2", (SHA2, "vps"): "/s/v2", (SHA2, "mingamini"): "/s/m2",
                        (SHA1, "vps"): "/s/v1"}
    fleet.refresh_main()
    return fleet


def views(fleet):
    return {h["name"]: h for h in fleet.host_views()}


def test_refresh_evaluates_main_for_every_host(ready, runner):
    assert ready.main_sha() == SHA2
    evals = [c for c in runner.calls if c[0] == "nix"]
    assert len(evals) == 3


def test_eval_is_cached_including_failures(ready, runner):
    ready.expected_path(SHA1, "ronove")  # fails: not in toplevels
    ready.expected_path(SHA1, "ronove")
    ready.refresh_main()
    evals = [c for c in runner.calls if c[0] == "nix"]
    assert len(evals) == 4


def test_poll_states(ready, runner):
    ready.expected_path(SHA1, "vps")
    runner.snapshots = {"localhost": snapshot("/s/r2"), "home-gw": snapshot("/s/v1")}
    ready.poll_all()
    v = views(ready)
    assert v["ronove"]["state"] == "in_sync"
    assert v["vps"]["state"] == "behind"
    assert v["vps"]["current_commit"] == SHA1
    assert v["mingamini"]["state"] == "unreachable"
    assert "No route to host" in v["mingamini"]["error"]
    assert v["ronove"]["last_success_at"].endswith("+00:00")


def test_unreachable_after_success_keeps_last_seen(ready, runner):
    runner.snapshots = {"home-gw": snapshot("/s/v2")}
    ready.poll_all()
    seen = views(ready)["vps"]["last_success_at"]
    runner.snapshots = {}
    ready.poll_all()
    v = views(ready)["vps"]
    assert v["state"] == "unreachable"
    assert v["last_success_at"] == seen
    assert v["current_path"] == "/s/v2"


def test_submit_validation(ready):
    with pytest.raises(NotFound):
        ready.submit("nope", "switch")
    with pytest.raises(Invalid):
        ready.submit("mingamini", "switch")
    with pytest.raises(Invalid):
        ready.submit("vps", "dance")
    with pytest.raises(Invalid):
        ready.submit("vps", "switch", rev="c" * 40)


def test_job_runs_nixos_rebuild_pinned_to_rev(ready, runner):
    job_id = ready.submit("vps", "test")
    runner.snapshots = {"home-gw": snapshot("/s/v2", boot="/s/v1")}
    ready.run_job(job_id)
    job = ready.job_view(job_id)
    assert job["state"] == "succeeded"
    assert job["expected_path"] == "/s/v2"
    cmd = runner.deployed[0]
    assert cmd[:2] == ["nixos-rebuild", "test"]
    assert cmd[cmd.index("--flake") + 1].endswith(f"rev={SHA2}#vps")
    assert cmd[cmd.index("--target-host") + 1] == "mingaleg@home-gw"
    text, offset = ready.job_log(job_id)
    assert "building..." in text and offset == len(text)
    assert views(ready)["vps"]["flags"] == ["boot_differs"]


def test_failed_deploy(ready, runner):
    runner.deploy_exit = 1
    job_id = ready.submit("vps", "switch")
    ready.run_job(job_id)
    assert ready.job_view(job_id)["state"] == "failed"
    assert ready.job_view(job_id)["exit_code"] == 1


def test_eval_failure_fails_job_without_deploying(ready, runner):
    job_id = ready.submit("ronove", "switch", rev=SHA1)
    ready.run_job(job_id)
    assert ready.job_view(job_id)["state"] == "failed"
    assert runner.deployed == []


def interrupt(fleet, job_id, expected):
    with fleet.Session.begin() as s:
        job = s.get(Job, job_id)
        job.state, job.expected_path = "running", expected
        from fleet_deployer.db import utcnow
        job.started_at = utcnow()
    fleet.recover_jobs()
    assert fleet.job_view(job_id)["state"] == "interrupted"


def test_interrupted_self_deploy_resolves_from_host_state(ready, runner):
    job_id = ready.submit("ronove", "switch")
    ready._jobs.get_nowait()
    interrupt(ready, job_id, "/s/r2")
    runner.snapshots = {"localhost": snapshot("/s/r2")}
    ready.poll_all()
    assert ready.job_view(job_id)["state"] == "succeeded"


def test_interrupted_deploy_that_did_not_land_fails(ready, runner):
    job_id = ready.submit("ronove", "switch")
    ready._jobs.get_nowait()
    interrupt(ready, job_id, "/s/r2")
    runner.snapshots = {"localhost": snapshot("/s/r-old")}
    ready.poll_all()
    assert ready.job_view(job_id)["state"] == "failed"


def test_recover_requeues_queued_jobs(ready):
    job_id = ready.submit("vps", "switch")
    ready._jobs.get_nowait()
    ready.recover_jobs()
    assert ready._jobs.get_nowait() == job_id


def test_hosts_show_not_checked_before_first_poll(ready):
    assert {h["state"] for h in ready.host_views()} == {"never_checked"}
