import pytest

from fleet_deployer.status import compute_state, parse_snapshot


def state(**kw):
    base = dict(
        attempted=True, reachable=True, ever_succeeded=True, current="/s/a", boot="/s/a",
        current_kernel="k1", booted_kernel="k1", expected="/s/a",
    )
    return compute_state(**{**base, **kw})


def test_in_sync():
    assert state().state == "in_sync"
    assert state().flags == []


def test_behind():
    assert state(expected="/s/b").state == "behind"


def test_test_deploy_live_flags_boot_differs():
    s = state(boot="/s/old")
    assert s.state == "in_sync"
    assert s.flags == ["boot_differs"]


def test_reboot_pending():
    assert "reboot_pending" in state(booted_kernel="k0").flags


def test_unknown_when_eval_missing():
    assert state(expected=None).state == "unknown"


def test_unreachable_keeps_flags_from_last_success():
    s = state(reachable=False, booted_kernel="k0")
    assert s.state == "unreachable"
    assert s.flags == ["reboot_pending"]


def test_never_reached():
    assert state(ever_succeeded=False, reachable=False).state == "unreachable"


def test_not_polled_yet():
    assert state(attempted=False, ever_succeeded=False, reachable=False).state == "never_checked"


def test_parse_snapshot():
    snap = parse_snapshot("current=/s/a\nboot=/s/b\nbooted=/s/a\ncurrent_kernel=k1\nbooted_kernel=k1\nuptime=42.5\n")
    assert (snap.current, snap.boot, snap.uptime) == ("/s/a", "/s/b", 42.5)


def test_parse_snapshot_requires_current():
    with pytest.raises(ValueError):
        parse_snapshot("boot=/s/b\n")
