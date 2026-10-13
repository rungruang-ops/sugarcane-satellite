"""Scheduler: what is due (pure), blocking rules, one pass over the jobs, running a job."""

import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from canesat import scheduler as sch
from canesat.scheduler import BANGKOK, JOBS, Job, Run, blocked_reason, is_due, run_job, tick


def at(hour, minute=0, day=13):
    return datetime(2026, 10, day, hour, minute, tzinfo=BANGKOK)


def ran(job, when, status="ok", took=timedelta(minutes=1)):
    return Run(job, when, status, finished_at=when + took)


def job_named(name):
    return next(j for j in JOBS if j.name == name)


S2 = job_named("s2_fetch")  # every 12 h
RULES = job_named("ndvi_rules")  # after s2_fetch
RAIN = job_named("rain_check")  # daily from 08:00
NOTIFY = job_named("notify")  # every 30 min


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "ENABLE_PUSH_ALERTS",
        "EARTHDATA_TOKEN",
        "PUSH_WINDOW_START_HOUR",
        "PUSH_WINDOW_END_HOUR",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------- the job table
def test_job_table_is_consistent():
    names = [j.name for j in JOBS]
    assert names == ["s2_fetch", "ndvi_rules", "rain_check", "notify"]
    for i, job in enumerate(JOBS):
        schedules = [job.every, job.daily_at_hour, job.after]
        assert sum(s is not None for s in schedules) == 1, job.name
        if job.after:
            assert job.after in names[:i], "upstream job must run earlier in the pass"


def test_s2_fetch_looks_back_14_days():
    assert S2.args(at(9)) == ["ingest", "--all", "--start", "2026-09-29", "--end", "2026-10-13"]


# ---------------------------------------------------------------- interval jobs
@pytest.mark.parametrize(
    ("minutes_ago", "due"), [(11 * 60 + 59, False), (12 * 60, True), (30 * 60, True)]
)
def test_interval_after_success(minutes_ago, due):
    now = at(20)
    runs = {"s2_fetch": [ran("s2_fetch", now - timedelta(minutes=minutes_ago))]}
    assert is_due(S2, runs, now) is due


def test_interval_never_ran_is_due():
    assert is_due(S2, {}, at(9))


@pytest.mark.parametrize(("minutes_ago", "due"), [(10, False), (29, False), (30, True)])
def test_interval_failed_run_is_retried_after_retry_after(minutes_ago, due):
    now = at(20)
    runs = {"s2_fetch": [ran("s2_fetch", now - timedelta(minutes=minutes_ago), "failed")]}
    assert is_due(S2, runs, now) is due


def test_interval_gives_up_retrying_after_max_attempts_until_next_cycle():
    now = at(20)
    fails = [ran("s2_fetch", now - timedelta(minutes=40 * i), "failed") for i in range(1, 5)]
    assert len(fails) == S2.max_attempts
    runs = {"s2_fetch": fails}  # latest failure 40 min ago
    assert is_due(S2, runs, now) is False
    assert is_due(S2, runs, now + timedelta(hours=12)) is True


def test_running_or_abandoned_rows_do_not_count_as_a_run():
    now = at(20)
    assert not is_due(S2, {"s2_fetch": [Run("s2_fetch", now, "running")]}, now)
    # a crashed run (even one from minutes ago) must neither block the job nor count as a failure
    abandoned = Run("s2_fetch", now - timedelta(minutes=5), "abandoned")
    assert is_due(S2, {"s2_fetch": [abandoned]}, now)


# ---------------------------------------------------------------- daily job
def test_daily_not_before_its_hour():
    assert not is_due(RAIN, {}, at(7, 59))
    assert is_due(RAIN, {}, at(8, 0))


def test_daily_runs_once_per_bangkok_day():
    done_today = {"rain_check": [ran("rain_check", at(8, 5))]}
    assert not is_due(RAIN, done_today, at(15))
    done_yesterday = {"rain_check": [ran("rain_check", at(8, 5, day=12))]}
    assert is_due(RAIN, done_yesterday, at(8, 0))


def test_daily_uses_the_bangkok_calendar_day_whatever_tz_the_db_returns():
    # the DB returns UTC timestamps; 01:05 UTC on the 13th is 08:05 Bangkok on the 13th
    utc_today = datetime(2026, 10, 13, 1, 5, tzinfo=UTC)
    assert not is_due(RAIN, {"rain_check": [ran("rain_check", utc_today)]}, at(15))
    # 16:30 UTC on the 12th is 23:30 Bangkok on the 12th -> yesterday for Bangkok
    utc_yesterday = datetime(2026, 10, 12, 16, 30, tzinfo=UTC)
    assert is_due(RAIN, {"rain_check": [ran("rain_check", utc_yesterday)]}, at(8, 0))


def test_daily_run_before_the_daily_hour_does_not_count():
    early = at(0, 30)  # e.g. a manual run in the small hours
    assert is_due(RAIN, {"rain_check": [ran("rain_check", early)]}, at(9))


def test_daily_retries_failures_then_stops_for_the_day():
    runs = {"rain_check": [ran("rain_check", at(8, 0), "failed")]}
    assert not is_due(RAIN, runs, at(8, 29))
    assert is_due(RAIN, runs, at(8, 30))
    four = {"rain_check": [ran("rain_check", at(8 + i, 0), "failed") for i in range(4)]}
    assert not is_due(RAIN, four, at(18))
    assert is_due(RAIN, four, at(8, 0, day=14))  # fresh day, fresh attempts


# ---------------------------------------------------------------- dependent job
def test_after_job_waits_for_a_successful_upstream():
    now = at(9)
    assert not is_due(RULES, {}, now)
    assert not is_due(RULES, {"s2_fetch": [ran("s2_fetch", at(8), "failed")]}, now)


def test_after_job_runs_once_per_upstream_success():
    up = ran("s2_fetch", at(8, 0))  # finished 08:01
    assert is_due(RULES, {"s2_fetch": [up]}, at(8, 2))
    done = {"s2_fetch": [up], "ndvi_rules": [ran("ndvi_rules", at(8, 2))]}
    assert not is_due(RULES, done, at(9))
    newer = {"s2_fetch": [ran("s2_fetch", at(20, 0)), up], "ndvi_rules": done["ndvi_rules"]}
    assert is_due(RULES, newer, at(20, 5))  # next s2_fetch result -> evaluate again


def test_after_job_retries_failure_then_gives_up():
    up = {"s2_fetch": [ran("s2_fetch", at(8, 0))]}
    one = {**up, "ndvi_rules": [ran("ndvi_rules", at(8, 2), "failed")]}
    assert not is_due(RULES, one, at(8, 20))
    assert is_due(RULES, one, at(8, 32))
    four = {**up, "ndvi_rules": [ran("ndvi_rules", at(8, 2 + i), "failed") for i in range(4)]}
    assert not is_due(RULES, four, at(11))


# ---------------------------------------------------------------- blocked (not failed)
def test_rain_check_blocked_without_earthdata_token(monkeypatch):
    assert blocked_reason(RAIN, at(9)) == "EARTHDATA_TOKEN is not set"
    monkeypatch.setenv("EARTHDATA_TOKEN", "x" * 20)
    assert blocked_reason(RAIN, at(9)) is None


def test_notify_blocked_while_push_is_off(monkeypatch):
    assert blocked_reason(NOTIFY, at(10)) == "ENABLE_PUSH_ALERTS is off"
    monkeypatch.setenv("ENABLE_PUSH_ALERTS", "false")
    assert blocked_reason(NOTIFY, at(10)) == "ENABLE_PUSH_ALERTS is off"


def test_notify_blocked_outside_send_window(monkeypatch):
    monkeypatch.setenv("ENABLE_PUSH_ALERTS", "true")
    assert blocked_reason(NOTIFY, at(10)) is None
    assert blocked_reason(NOTIFY, at(5, 59)) == "outside the send window"
    assert blocked_reason(NOTIFY, at(19, 0)) == "outside the send window"


def test_s2_fetch_and_rules_are_never_blocked():
    assert blocked_reason(S2, at(3)) is None
    assert blocked_reason(RULES, at(3)) is None


# ---------------------------------------------------------------- one pass
class MemoryStore:
    """In-memory `Store` (same contract as PgStore)."""

    def __init__(self):
        self.runs: dict[int, Run] = {}
        self.next_id = 1
        self.abandon_calls = 0

    def abandon_stale(self):
        self.abandon_calls += 1
        stale = [i for i, r in self.runs.items() if r.status == "running"]
        for i in stale:
            self.runs[i] = replace(self.runs[i], status="abandoned")
        return len(stale)

    def load_runs(self, now):
        out = {}
        for r in sorted(self.runs.values(), key=lambda r: r.started_at, reverse=True):
            out.setdefault(r.job, []).append(r)
        return out

    def start_run(self, job, now):
        run_id, self.next_id = self.next_id, self.next_id + 1
        self.runs[run_id] = Run(job, now, "running")
        return run_id

    def finish_run(self, run_id, status, exit_code, detail, finished_at):
        self.runs[run_id] = replace(
            self.runs[run_id], status=status, exit_code=exit_code, finished_at=finished_at
        )


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make_runner(outcomes=None):
    calls = []

    def runner(job, now):
        calls.append(job.name)
        status = (outcomes or {}).get(job.name, "ok")
        return status, 0 if status == "ok" else 1, "output"

    runner.calls = calls
    return runner


def actions(report):
    return {r["job"]: r["action"] for r in report}


def test_tick_runs_due_jobs_in_order_and_skips_blocked():
    clock = Clock(at(9))
    store, runner = MemoryStore(), make_runner()
    report = tick(store, clock=clock, runner=runner)
    # no EARTHDATA_TOKEN and push off in the test env -> both blocked, never run
    assert actions(report) == {
        "s2_fetch": "ran",
        "ndvi_rules": "ran",
        "rain_check": "blocked",
        "notify": "blocked",
    }
    assert runner.calls == ["s2_fetch", "ndvi_rules"]
    assert store.abandon_calls == 1


def test_second_pass_does_nothing_until_the_next_cycle():
    clock = Clock(at(9))
    store, runner = MemoryStore(), make_runner()
    tick(store, clock=clock, runner=runner)
    runner.calls.clear()
    clock.now = at(9, 1)
    report = tick(store, clock=clock, runner=runner)
    assert runner.calls == []
    assert actions(report)["s2_fetch"] == "not_due"
    assert actions(report)["ndvi_rules"] == "not_due"
    clock.now = at(21, 1)  # 12 h later
    tick(store, clock=clock, runner=runner)
    assert runner.calls == ["s2_fetch", "ndvi_rules"]


def test_failed_upstream_blocks_dependent_and_is_retried_later():
    clock = Clock(at(9))
    store = MemoryStore()
    runner = make_runner({"s2_fetch": "failed"})
    report = tick(store, clock=clock, runner=runner)
    assert runner.calls == ["s2_fetch"]  # ndvi_rules not run on stale data
    assert actions(report)["ndvi_rules"] == "not_due"
    clock.now = at(9, 31)
    runner2 = make_runner()
    tick(store, clock=clock, runner=runner2)
    assert runner2.calls == ["s2_fetch", "ndvi_rules"]


def test_notify_and_rain_run_when_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_PUSH_ALERTS", "true")
    monkeypatch.setenv("EARTHDATA_TOKEN", "t" * 20)
    clock = Clock(at(9))
    store, runner = MemoryStore(), make_runner()
    tick(store, clock=clock, runner=runner)
    assert runner.calls == ["s2_fetch", "ndvi_rules", "rain_check", "notify"]
    runner.calls.clear()
    clock.now = at(9, 31)  # notify is every 30 min, the others are not due yet
    tick(store, clock=clock, runner=runner)
    assert runner.calls == ["notify"]


def test_clock_is_read_per_job_so_a_slow_job_cannot_push_notify_past_the_window(monkeypatch):
    monkeypatch.setenv("ENABLE_PUSH_ALERTS", "true")
    clock = Clock(at(18, 40))

    def slow_runner(job, now):
        clock.now = at(19, 5)  # the first job runs past 19:00
        return "ok", 0, ""

    store = MemoryStore()
    report = tick(store, clock=clock, runner=slow_runner)
    assert actions(report)["notify"] == "blocked"


def test_leftover_running_rows_are_abandoned_and_rerun():
    clock = Clock(at(9))
    store = MemoryStore()
    store.start_run("s2_fetch", at(8, 58))  # a crashed previous scheduler left this behind
    runner = make_runner()
    tick(store, clock=clock, runner=runner)
    assert runner.calls[0] == "s2_fetch"
    assert sorted(r.status for r in store.runs.values() if r.job == "s2_fetch") == [
        "abandoned",
        "ok",
    ]


# ---------------------------------------------------------------- running one job
def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_run_job_builds_the_cli_command_and_reports_success():
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return completed(0, '{"stored": 3}')

    assert run_job(S2, at(9), run=fake_run) == ("ok", 0, '{"stored": 3}')
    assert seen["cmd"][1:4] == ["-m", "canesat.cli", "ingest"]
    assert seen["cmd"][-4:] == ["--start", "2026-09-29", "--end", "2026-10-13"]
    assert seen["kw"]["timeout"] == S2.timeout_s
    assert seen["kw"]["stdin"] == subprocess.DEVNULL


def test_run_job_nonzero_exit_is_failed_with_output_tail():
    status, code, detail = run_job(
        RULES, at(9), run=lambda cmd, **kw: completed(2, "", "Traceback: boom")
    )
    assert (status, code) == ("failed", 2)
    assert "boom" in detail


def test_run_job_timeout_and_spawn_errors_are_failed():
    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"], output=b"partial")

    status, code, detail = run_job(RULES, at(9), run=timeout)
    assert (status, code) == ("failed", None) and "timed out" in detail

    def oserror(cmd, **kw):
        raise OSError("no such file")

    assert run_job(RULES, at(9), run=oserror)[0] == "failed"


def test_run_job_keeps_only_a_tail_and_redacts_secrets(monkeypatch):
    monkeypatch.setenv("EARTHDATA_TOKEN", "super-secret-token-123")
    out = "x" * 5000 + " token=super-secret-token-123"
    _, _, detail = run_job(RULES, at(9), run=lambda cmd, **kw: completed(1, out))
    assert "super-secret-token-123" not in detail
    assert "<EARTHDATA_TOKEN>" in detail
    assert len(detail) <= sch.DETAIL_MAX + 1


def test_schedule_less_job_is_rejected():
    broken = Job("broken", lambda now: [])
    with pytest.raises(ValueError):
        is_due(broken, {}, at(9))
