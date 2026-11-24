# -*- coding: utf-8 -*-
"""Direct tests for webui.render_queue (RenderQueue + RenderJob).

Covers the durable state machine, the worker pool, pause/resume, retry,
cancel, prune and the crash-recovery paths of the persistent render queue.

Deliberately excluded (already covered elsewhere, not duplicated here):
- corrupt-state preservation, persist/recover of a running job, the
  retry/cancel/progress worker flow, prune of one old job, terminal
  cancellation, pause persistence, enqueue dedup, priority ordering and
  retry_failed on selected jobs — see tests/test_pro_components.py.
- malformed ``jobs`` payloads, restart-after-stop and ``retrying`` recovery
  — see tests/test_reliability_hardening.py.
- No real media, no network, no Gradio server; runners are in-process fakes.
"""

import json
import threading
import time

import pytest

from webui.render_queue import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    RenderJob,
    RenderQueue,
)


def _wait_status(queue, job_id, wanted, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = queue.snapshot(job_id)
        if snapshot is not None and snapshot["status"] in wanted:
            return snapshot["status"]
        time.sleep(0.01)
    return queue.snapshot(job_id)["status"]


@pytest.fixture
def queue(tmp_path):
    """A fresh queue that is always stopped on teardown."""
    q = RenderQueue(tmp_path / "queue.json")
    yield q
    q.stop()


# ---------------------------------------------------------------------------
# RenderJob dataclass
# ---------------------------------------------------------------------------


def test_render_job_defaults():
    job = RenderJob(id="j1", plan={"source": "a.mp4"})
    assert job.status == "queued"
    assert job.created == 0.0
    assert job.started is None
    assert job.finished is None
    assert job.output is None
    assert job.error is None
    assert job.attempts == 0
    assert job.max_retries == 0
    assert job.priority == 0
    assert job.progress == 0
    assert job.message == ""
    assert job.cancel_requested is False
    assert job.metadata == {}


def test_render_job_metadata_default_is_per_instance():
    first = RenderJob(id="j1", plan={})
    second = RenderJob(id="j2", plan={})
    first.metadata["key"] = "value"
    assert second.metadata == {}


def test_terminal_and_active_state_constants():
    assert TERMINAL_STATES == {"succeeded", "failed", "cancelled"}
    assert ACTIVE_STATES == {"queued", "running", "retrying", "cancelling"}


# ---------------------------------------------------------------------------
# Construction and durable state loading
# ---------------------------------------------------------------------------


def test_init_missing_state_file_starts_empty_with_first_run_warning(tmp_path):
    # Quirk (documented, not fixed): a missing state file raises
    # FileNotFoundError inside _load, which the OSError handler turns into a
    # load warning even though a first run with no state file is normal.
    q = RenderQueue(tmp_path / "missing.json")
    assert q.snapshot() == {}
    assert q.state_warning
    assert "missing.json" in q.state_warning
    assert q.paused is False
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_init_clamps_max_workers(tmp_path):
    assert RenderQueue(tmp_path / "q.json", max_workers=0).max_workers == 1
    assert RenderQueue(tmp_path / "q.json", max_workers=-3).max_workers == 1
    assert RenderQueue(tmp_path / "q.json", max_workers=None).max_workers == 1
    assert RenderQueue(tmp_path / "q.json", max_workers=3).max_workers == 3


def test_load_non_object_json_state_warns_and_preserves_corrupt(tmp_path):
    path = tmp_path / "queue.json"
    path.write_text("[1, 2]", encoding="utf-8")
    q = RenderQueue(path)
    assert q.snapshot() == {}
    assert q.state_warning
    assert not path.exists()
    assert list(tmp_path.glob("queue.json.corrupt-*"))


def test_load_drops_unknown_fields_and_defaults_missing_plan(tmp_path):
    path = tmp_path / "queue.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "paused": False,
                "jobs": {
                    "j1": {
                        "id": "j1",
                        "plan": {"source": "a.mp4"},
                        "status": "queued",
                        "bogus_field": "ignored",
                    },
                    "j2": {"id": "j2", "status": "succeeded"},
                },
            }
        ),
        encoding="utf-8",
    )
    q = RenderQueue(path)
    assert "bogus_field" not in q.snapshot("j1")
    assert q.snapshot("j1")["plan"] == {"source": "a.mp4"}
    assert q.snapshot("j2")["plan"] == {}
    assert q.snapshot("j2")["status"] == "succeeded"


def test_load_requeues_cancelling_job_with_recovery_message(tmp_path):
    path = tmp_path / "queue.json"
    q = RenderQueue(path)
    jid = q.add({"source": "a.mp4"})
    q.jobs[jid].status = "cancelling"
    q.jobs[jid].cancel_requested = True
    q._save()
    reloaded = RenderQueue(path)
    snapshot = reloaded.snapshot(jid)
    assert snapshot["status"] == "queued"
    assert snapshot["cancel_requested"] is False
    assert snapshot["message"] == "Recovered after previous process stopped"


def test_load_persists_paused_flag(tmp_path):
    path = tmp_path / "queue.json"
    q = RenderQueue(path)
    q.pause_all()
    assert RenderQueue(path).paused is True


def test_save_creates_nested_directories_and_writes_version_2(tmp_path):
    path = tmp_path / "deep" / "nested" / "queue.json"
    q = RenderQueue(path)
    jid = q.add({"source": "a.mp4"})
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2
    assert raw["paused"] is False
    assert "updated" in raw
    assert raw["jobs"][jid]["plan"] == {"source": "a.mp4"}
    assert not list(tmp_path.glob("**/*.tmp"))


def test_save_roundtrip_preserves_job_fields(tmp_path):
    path = tmp_path / "queue.json"
    q = RenderQueue(path)
    jid = q.add({"source": "a.mp4"}, max_retries=2, priority=4, metadata={"k": "v"})
    q.mark_started(jid)
    q.update_progress(jid, 40, "working")
    reloaded = RenderQueue(path)
    snapshot = reloaded.snapshot(jid)
    assert snapshot["status"] == "queued"  # running jobs re-queue on load
    assert snapshot["max_retries"] == 2
    assert snapshot["priority"] == 4
    assert snapshot["metadata"] == {"k": "v"}
    assert snapshot["progress"] == 40


# ---------------------------------------------------------------------------
# add / get / snapshot
# ---------------------------------------------------------------------------


def test_add_defaults_and_returns_hex_id(queue):
    jid = queue.add({"source": "a.mp4"})
    assert len(jid) == 32
    assert all(ch in "0123456789abcdef" for ch in jid)
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "queued"
    assert snapshot["created"] > 0
    assert snapshot["max_retries"] == 0
    assert snapshot["priority"] == 0


def test_add_explicit_id_and_copies_plan_and_metadata(queue):
    plan = {"source": "a.mp4"}
    metadata = {"tag": "x"}
    jid = queue.add(plan, job_id="job-1", metadata=metadata)
    assert jid == "job-1"
    plan["source"] = "changed.mp4"
    metadata["tag"] = "changed"
    snapshot = queue.snapshot(jid)
    assert snapshot["plan"] == {"source": "a.mp4"}
    assert snapshot["metadata"] == {"tag": "x"}


def test_add_none_plan_becomes_empty_dict(queue):
    jid = queue.add(None)
    assert queue.snapshot(jid)["plan"] == {}


def test_add_duplicate_job_id_raises(queue):
    queue.add({"source": "a.mp4"}, job_id="dup")
    with pytest.raises(ValueError, match="duplicate render job: dup"):
        queue.add({"source": "b.mp4"}, job_id="dup")


def test_add_clamps_negative_max_retries_and_coerces_priority(queue):
    jid = queue.add({"source": "a.mp4"}, max_retries=-5, priority="3")
    snapshot = queue.snapshot(jid)
    assert snapshot["max_retries"] == 0
    assert snapshot["priority"] == 3


def test_add_without_workers_does_not_enqueue(queue):
    jid = queue.add({"source": "a.mp4"})
    assert queue._work.qsize() == 0
    assert jid not in queue._queued_ids


def test_add_while_paused_does_not_enqueue_then_resume_runs_it(tmp_path):
    q = RenderQueue(
        tmp_path / "queue.json",
        runner=lambda job, cancel_event, progress: "done.mp4",
    )
    q.start()
    try:
        q.pause_all()
        jid = q.add({"source": "a.mp4"})
        time.sleep(0.2)
        assert q.snapshot(jid)["status"] == "queued"
        assert jid not in q._queued_ids
        q.resume_all()
        assert q.wait(jid, timeout=5) == "succeeded"
    finally:
        q.stop()


def test_get_missing_returns_none_and_snapshot_missing_returns_none(queue):
    assert queue.get("no-such-job") is None
    assert queue.snapshot("no-such-job") is None


def test_snapshot_all_returns_copies(queue):
    jid = queue.add({"source": "a.mp4"})
    snapshot = queue.snapshot()
    snapshot[jid]["plan"]["source"] = "tampered.mp4"
    assert queue.get(jid).plan == {"source": "a.mp4"}


# ---------------------------------------------------------------------------
# mark_started / update_progress / update_metadata
# ---------------------------------------------------------------------------


def test_mark_started_sets_started_attempts_and_message(queue):
    jid = queue.add({"source": "a.mp4"})
    assert queue.mark_started(jid) == "running"
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "running"
    assert snapshot["started"] is not None
    assert snapshot["attempts"] == 1
    assert snapshot["message"] == "Processing"


def test_mark_started_preserves_existing_started_and_attempts(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.jobs[jid].started = 1234.5
    queue.jobs[jid].attempts = 3
    queue.jobs[jid].message = "custom"
    queue.mark_started(jid)
    snapshot = queue.snapshot(jid)
    assert snapshot["started"] == 1234.5
    assert snapshot["attempts"] == 3
    assert snapshot["message"] == "custom"


def test_mark_started_on_terminal_job_is_noop(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.mark_finished(jid, output="done.mp4")
    assert queue.mark_started(jid) == "succeeded"
    assert queue.snapshot(jid)["status"] == "succeeded"


def test_mark_started_unknown_job_raises_keyerror(queue):
    with pytest.raises(KeyError):
        queue.mark_started("missing")


def test_update_progress_clamps_and_truncates_message(queue):
    jid = queue.add({"source": "a.mp4"})
    assert queue.update_progress(jid, 150, "x" * 600) == "queued"
    snapshot = queue.snapshot(jid)
    assert snapshot["progress"] == 100
    assert len(snapshot["message"]) == 500
    queue.update_progress(jid, -5)
    assert queue.snapshot(jid)["progress"] == 0
    queue.update_progress(jid, 33.7)
    assert queue.snapshot(jid)["progress"] == 33


def test_update_progress_on_terminal_job_is_noop(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.mark_finished(jid, output="done.mp4")
    assert queue.update_progress(jid, 50, "late") == "succeeded"
    snapshot = queue.snapshot(jid)
    assert snapshot["progress"] == 100
    assert snapshot["message"] == "Completed"


def test_update_progress_unknown_job_raises_keyerror(queue):
    with pytest.raises(KeyError):
        queue.update_progress("missing", 10)


def test_update_metadata_merges_and_returns_copy(queue):
    jid = queue.add({"source": "a.mp4"}, metadata={"a": 1})
    merged = queue.update_metadata(jid, b=2, a=9)
    assert merged == {"a": 9, "b": 2}
    merged["a"] = 0
    assert queue.snapshot(jid)["metadata"] == {"a": 9, "b": 2}


def test_update_metadata_unknown_job_raises_keyerror(queue):
    with pytest.raises(KeyError):
        queue.update_metadata("missing", a=1)


# ---------------------------------------------------------------------------
# mark_finished / mark_failed
# ---------------------------------------------------------------------------


def test_mark_finished_output_wins_over_result(queue):
    jid = queue.add({"source": "a.mp4"})
    assert (
        queue.mark_finished(jid, output="final.mp4", result={"clips": 2}) == "succeeded"
    )
    snapshot = queue.snapshot(jid)
    assert snapshot["output"] == "final.mp4"
    assert snapshot["progress"] == 100
    assert snapshot["finished"] is not None
    assert snapshot["message"] == "Completed"


def test_mark_finished_stores_non_string_result_as_output(queue):
    # Quirk (documented, not fixed): RenderJob.output is annotated str | None
    # but mark_finished stores any JSON-serializable result there when no
    # explicit output is given; _save serialises it via json default=str.
    jid = queue.add({"source": "a.mp4"})
    queue.mark_finished(jid, result={"clips": 2})
    assert queue.snapshot(jid)["output"] == {"clips": 2}


def test_mark_finished_with_cancel_requested_becomes_cancelled(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.jobs[jid].cancel_requested = True
    assert queue.mark_finished(jid, output="final.mp4") == "cancelled"
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "cancelled"
    assert snapshot["output"] is None
    assert snapshot["finished"] is not None


def test_mark_finished_does_not_guard_terminal_jobs(queue):
    # Unlike mark_started/update_progress, mark_finished rewrites a terminal
    # job (status stays succeeded but finished is refreshed).
    jid = queue.add({"source": "a.mp4"})
    queue.mark_finished(jid, output="first.mp4")
    first_finished = queue.snapshot(jid)["finished"]
    time.sleep(0.01)
    queue.mark_finished(jid, output="second.mp4")
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "succeeded"
    assert snapshot["output"] == "second.mp4"
    assert snapshot["finished"] >= first_finished


def test_mark_finished_unknown_job_raises_keyerror(queue):
    with pytest.raises(KeyError):
        queue.mark_finished("missing", output="x.mp4")


def test_mark_failed_sets_error_truncated_to_4000_chars(queue):
    jid = queue.add({"source": "a.mp4"})
    assert queue.mark_failed(jid, "e" * 5000) == "failed"
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "failed"
    assert len(snapshot["error"]) == 4000
    assert snapshot["finished"] is not None
    assert snapshot["message"] == "Failed"


def test_mark_failed_with_cancel_requested_becomes_cancelled(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.jobs[jid].cancel_requested = True
    assert queue.mark_failed(jid, "boom") == "cancelled"
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "cancelled"
    assert snapshot["error"] is None
    assert snapshot["message"] == "Cancelled"


def test_mark_failed_unknown_job_raises_keyerror(queue):
    with pytest.raises(KeyError):
        queue.mark_failed("missing", "boom")


# ---------------------------------------------------------------------------
# cancel / retry
# ---------------------------------------------------------------------------


def test_cancel_queued_job_is_terminal_immediately(queue):
    jid = queue.add({"source": "a.mp4"})
    assert queue.cancel(jid) == "cancelled"
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "cancelled"
    assert snapshot["cancel_requested"] is True
    assert snapshot["finished"] is not None
    assert snapshot["message"] == "Cancelled before start"


def test_cancel_running_job_sets_cancelling_and_signals_event(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.mark_started(jid)
    assert queue.cancel(jid) == "cancelling"
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "cancelling"
    assert snapshot["cancel_requested"] is True
    assert snapshot["message"] == "Cancellation requested"
    assert queue._events[jid].is_set()


def test_cancel_terminal_job_is_noop(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.mark_finished(jid, output="done.mp4")
    finished = queue.snapshot(jid)["finished"]
    assert queue.cancel(jid) == "succeeded"
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "succeeded"
    assert snapshot["finished"] == finished


def test_cancel_unknown_job_raises_keyerror(queue):
    with pytest.raises(KeyError):
        queue.cancel("missing")


def test_retry_resets_all_run_fields(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.update_progress(jid, 50, "halfway")
    queue.mark_failed(jid, "boom")
    assert queue.retry(jid) == jid
    snapshot = queue.snapshot(jid)
    assert snapshot["status"] == "queued"
    assert snapshot["started"] is None
    assert snapshot["finished"] is None
    assert snapshot["output"] is None
    assert snapshot["error"] is None
    assert snapshot["progress"] == 0
    assert snapshot["cancel_requested"] is False
    assert snapshot["message"] == "Queued for retry"


def test_retry_non_retryable_status_raises(queue):
    jid = queue.add({"source": "a.mp4"})
    with pytest.raises(ValueError, match="only failed or cancelled"):
        queue.retry(jid)
    queue.mark_started(jid)
    with pytest.raises(ValueError, match="only failed or cancelled"):
        queue.retry(jid)
    queue.mark_finished(jid, output="done.mp4")
    with pytest.raises(ValueError, match="only failed or cancelled"):
        queue.retry(jid)


def test_retry_reenqueues_when_workers_running(tmp_path):
    q = RenderQueue(
        tmp_path / "queue.json",
        runner=lambda job, cancel_event, progress: "done.mp4",
    )
    q.start()
    try:
        jid = q.add({"source": "a.mp4"})
        assert q.wait(jid, timeout=5) == "succeeded"
        q.mark_failed(jid, "late failure")
        q.retry(jid)
        assert q.wait(jid, timeout=5) == "succeeded"
    finally:
        q.stop()


def test_retry_unknown_job_raises_keyerror(queue):
    with pytest.raises(KeyError):
        queue.retry("missing")


# ---------------------------------------------------------------------------
# pause / resume / retry_failed / pending / active
# ---------------------------------------------------------------------------


def test_pause_blocks_worker_until_resume(tmp_path):
    q = RenderQueue(
        tmp_path / "queue.json",
        runner=lambda job, cancel_event, progress: "done.mp4",
    )
    q.pause_all()
    q.start()
    try:
        jid = q.add({"source": "a.mp4"})
        time.sleep(0.3)
        assert q.snapshot(jid)["status"] == "queued"
        assert q.resume_all() is True
        assert q.wait(jid, timeout=5) == "succeeded"
    finally:
        q.stop()


def test_resume_all_reenqueues_pending_jobs_with_workers_running(tmp_path):
    q = RenderQueue(
        tmp_path / "queue.json",
        runner=lambda job, cancel_event, progress: "done.mp4",
    )
    q.start()
    try:
        q.pause_all()
        first = q.add({"source": "a.mp4"})
        second = q.add({"source": "b.mp4"})
        time.sleep(0.2)
        assert q.snapshot(first)["status"] == "queued"
        q.resume_all()
        assert q.wait(first, timeout=5) == "succeeded"
        assert q.wait(second, timeout=5) == "succeeded"
    finally:
        q.stop()


def test_pause_and_resume_return_true_and_toggle_state(queue):
    assert queue.pause_all() is True
    assert queue.paused is True
    assert queue.resume_all() is True
    assert queue.paused is False


def test_retry_failed_without_args_retries_all_failed_and_cancelled(queue):
    failed = queue.add({"source": "f.mp4"})
    cancelled = queue.add({"source": "c.mp4"})
    done = queue.add({"source": "d.mp4"})
    queue.mark_failed(failed, "boom")
    queue.cancel(cancelled)
    queue.mark_finished(done, output="done.mp4")
    assert queue.retry_failed() == [failed, cancelled]
    assert queue.snapshot(failed)["status"] == "queued"
    assert queue.snapshot(cancelled)["status"] == "queued"
    assert queue.snapshot(done)["status"] == "succeeded"


def test_retry_failed_ignores_unknown_ids(queue):
    failed = queue.add({"source": "f.mp4"})
    queue.mark_failed(failed, "boom")
    assert queue.retry_failed(["missing", failed, "also-missing"]) == [failed]


def test_pending_and_active_lists(queue):
    queued = queue.add({"source": "q.mp4"})
    running = queue.add({"source": "r.mp4"})
    done = queue.add({"source": "d.mp4"})
    queue.mark_started(running)
    queue.mark_finished(done, output="done.mp4")
    assert [job.id for job in queue.pending()] == [queued]
    assert {job.id for job in queue.active()} == {queued, running}
    queue.jobs[queued].status = "retrying"
    assert {job.id for job in queue.active()} == {queued, running}


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


def test_prune_keeps_recent_terminal_and_non_terminal_jobs(queue):
    old = queue.add({"source": "old.mp4"})
    recent = queue.add({"source": "recent.mp4"})
    live = queue.add({"source": "live.mp4"})
    queue.mark_finished(old, output="old.mp4")
    queue.mark_finished(recent, output="recent.mp4")
    queue.jobs[old].finished = time.time() - 20 * 86400
    queue.jobs[recent].finished = time.time() - 2 * 86400
    queue._save()
    assert queue.prune(max_age_days=14, keep_terminal=0) == 1
    assert queue.get(old) is None
    assert queue.get(recent) is not None
    assert queue.get(live) is not None


def test_prune_keep_terminal_bound_keeps_newest(queue):
    older = queue.add({"source": "older.mp4"})
    newer = queue.add({"source": "newer.mp4"})
    queue.mark_finished(older, output="older.mp4")
    queue.mark_finished(newer, output="newer.mp4")
    queue.jobs[older].finished = time.time() - 20 * 86400
    queue.jobs[newer].finished = time.time() - 19 * 86400
    queue._save()
    assert queue.prune(max_age_days=14, keep_terminal=1) == 1
    assert queue.get(older) is None
    assert queue.get(newer) is not None


def test_prune_with_nothing_old_returns_zero(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.mark_finished(jid, output="done.mp4")
    assert queue.prune(max_age_days=14, keep_terminal=200) == 0
    assert queue.get(jid) is not None


def test_prune_clamps_max_age_days_to_at_least_one(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.mark_finished(jid, output="done.mp4")
    queue.jobs[jid].finished = time.time() - 2 * 86400
    queue._save()
    # max_age_days=0 is clamped to 1 day, so a 2-day-old job is pruned.
    assert queue.prune(max_age_days=0, keep_terminal=0) == 1
    assert queue.get(jid) is None


# ---------------------------------------------------------------------------
# start / stop / wait
# ---------------------------------------------------------------------------


def test_start_without_runner_raises(tmp_path):
    q = RenderQueue(tmp_path / "queue.json")
    with pytest.raises(ValueError, match="a runner callable is required"):
        q.start()
    with pytest.raises(ValueError, match="a runner callable is required"):
        q.start(runner=None)


def test_start_twice_replaces_runner_but_keeps_threads(tmp_path):
    # A second start() is a no-op for the worker threads, but the runner
    # assignment happens before that early return, so the new runner serves
    # subsequent jobs.
    calls = {"first": 0, "second": 0}

    def first(job, cancel_event, progress):
        calls["first"] += 1
        return "first.mp4"

    def second(job, cancel_event, progress):
        calls["second"] += 1
        return "second.mp4"

    q = RenderQueue(tmp_path / "queue.json", runner=first)
    q.start()
    threads_before = list(q._threads)
    q.start(runner=second)
    try:
        assert q._threads == threads_before
        jid = q.add({"source": "a.mp4"})
        assert q.wait(jid, timeout=5) == "succeeded"
        assert calls == {"first": 0, "second": 1}
        assert q.snapshot(jid)["output"] == "second.mp4"
    finally:
        q.stop()


def test_start_with_two_workers_runs_jobs_concurrently(tmp_path):
    barrier = threading.Barrier(2, timeout=5)

    def runner(job, cancel_event, progress):
        barrier.wait()
        return "done.mp4"

    q = RenderQueue(tmp_path / "queue.json", runner=runner, max_workers=2)
    q.start()
    try:
        first = q.add({"source": "a.mp4"})
        second = q.add({"source": "b.mp4"})
        assert q.wait(first, timeout=5) == "succeeded"
        assert q.wait(second, timeout=5) == "succeeded"
    finally:
        q.stop()


def test_stop_without_start_is_safe(tmp_path):
    q = RenderQueue(tmp_path / "queue.json")
    q.stop()
    assert q._threads == []


def test_wait_unknown_job_returns_none(queue):
    assert queue.wait("missing", timeout=1) is None


def test_wait_timeout_returns_current_status(queue):
    jid = queue.add({"source": "a.mp4"})
    assert queue.wait(jid, timeout=0.2) == "queued"


def test_wait_returns_terminal_status_immediately(queue):
    jid = queue.add({"source": "a.mp4"})
    queue.mark_finished(jid, output="done.mp4")
    assert queue.wait(jid, timeout=5) == "succeeded"


# ---------------------------------------------------------------------------
# Worker behaviour
# ---------------------------------------------------------------------------


def test_worker_non_string_result_stored_as_output(tmp_path):
    q = RenderQueue(
        tmp_path / "queue.json",
        runner=lambda job, cancel_event, progress: {"clips": 2},
    )
    q.start()
    try:
        jid = q.add({"source": "a.mp4"})
        assert q.wait(jid, timeout=5) == "succeeded"
        # Same quirk as mark_finished: a dict result lands in `output`.
        assert q.snapshot(jid)["output"] == {"clips": 2}
    finally:
        q.stop()


def test_worker_runner_returning_none_succeeds_with_none_output(tmp_path):
    q = RenderQueue(
        tmp_path / "queue.json",
        runner=lambda job, cancel_event, progress: None,
    )
    q.start()
    try:
        jid = q.add({"source": "a.mp4"})
        assert q.wait(jid, timeout=5) == "succeeded"
        snapshot = q.snapshot(jid)
        assert snapshot["output"] is None
        assert snapshot["progress"] == 100
    finally:
        q.stop()


def test_worker_failure_without_retries_marks_failed_with_traceback(tmp_path):
    def runner(job, cancel_event, progress):
        raise RuntimeError("boom")

    q = RenderQueue(tmp_path / "queue.json", runner=runner)
    q.start()
    try:
        jid = q.add({"source": "a.mp4"}, max_retries=0)
        assert q.wait(jid, timeout=5) == "failed"
        snapshot = q.snapshot(jid)
        assert snapshot["attempts"] == 1
        assert "boom" in snapshot["error"]
        assert "Traceback" in snapshot["error"]
    finally:
        q.stop()


def test_worker_retries_exhausted_marks_failed(tmp_path):
    attempts = []

    def runner(job, cancel_event, progress):
        attempts.append(job.attempts)
        raise RuntimeError("always fails")

    q = RenderQueue(tmp_path / "queue.json", runner=runner)
    q.start()
    try:
        jid = q.add({"source": "a.mp4"}, max_retries=1)
        assert q.wait(jid, timeout=5) == "failed"
        assert attempts == [1, 2]
        assert q.snapshot(jid)["attempts"] == 2
    finally:
        q.stop()


def test_worker_progress_callback_updates_job_mid_run(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def runner(job, cancel_event, progress):
        progress(42, "halfway")
        entered.set()
        release.wait(5)
        return "done.mp4"

    q = RenderQueue(tmp_path / "queue.json", runner=runner)
    q.start()
    try:
        jid = q.add({"source": "a.mp4"})
        assert entered.wait(5)
        snapshot = q.snapshot(jid)
        assert snapshot["progress"] == 42
        assert snapshot["message"] == "halfway"
        assert snapshot["status"] == "running"
        release.set()
        assert q.wait(jid, timeout=5) == "succeeded"
    finally:
        release.set()
        q.stop()


def test_worker_runner_setting_cancel_event_cancels_job(tmp_path):
    def runner(job, cancel_event, progress):
        cancel_event.set()
        return "should-not-be-output.mp4"

    q = RenderQueue(tmp_path / "queue.json", runner=runner)
    q.start()
    try:
        jid = q.add({"source": "a.mp4"})
        assert q.wait(jid, timeout=5) == "cancelled"
        assert q.snapshot(jid)["output"] is None
    finally:
        q.stop()


def test_worker_skips_job_cancelled_before_pickup(tmp_path):
    calls = []

    def runner(job, cancel_event, progress):
        calls.append(job.id)
        return "done.mp4"

    q = RenderQueue(tmp_path / "queue.json", runner=runner)
    jid = q.add({"source": "a.mp4"})
    queue_status = q.cancel(jid)
    assert queue_status == "cancelled"
    q.start()
    try:
        time.sleep(0.3)
        assert calls == []
        assert q.snapshot(jid)["status"] == "cancelled"
    finally:
        q.stop()
