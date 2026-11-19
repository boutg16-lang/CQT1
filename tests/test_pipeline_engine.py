# -*- coding: utf-8 -*-
"""Direct tests for scripts/pipeline_engine.py.

Dependency-free module: stage registration, dependency ordering, retry
policy, cancellation, atomic state persistence and output validation.
No network, no models, no media.
"""
import json

import pytest

from scripts.pipeline_engine import (
    CancellationToken,
    PipelineEngine,
    _json_safe,
    fingerprint_files,
)


def _engine(tmp_path, **kwargs):
    return PipelineEngine(tmp_path / "state.json", **kwargs)


# ---------------------------------------------------------------------------
# CancellationToken
# ---------------------------------------------------------------------------

def test_token_starts_uncancelled():
    token = CancellationToken()
    assert token.cancelled is False
    token.raise_if_cancelled()  # must not raise


def test_token_cancel_sets_flag_and_raises():
    token = CancellationToken()
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(RuntimeError, match="cancelled"):
        token.raise_if_cancelled()


# ---------------------------------------------------------------------------
# fingerprint_files
# ---------------------------------------------------------------------------

def test_fingerprint_is_deterministic_and_order_independent(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("hello")
    b.write_text("world")
    assert fingerprint_files([a, b]) == fingerprint_files([b, a])
    assert fingerprint_files([a, b]) == fingerprint_files([a, b])
    assert len(fingerprint_files([a, b])) == 64  # sha256 hex


def test_fingerprint_changes_with_content_size(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("one")
    first = fingerprint_files([a])
    a.write_text("a much longer content")
    assert fingerprint_files([a]) != first


def test_fingerprint_missing_file_still_hashes_path(tmp_path):
    missing = tmp_path / "ghost.txt"
    digest = fingerprint_files([missing])
    assert digest == fingerprint_files([missing])
    assert digest != fingerprint_files([tmp_path / "other.txt"])


def test_fingerprint_accepts_strings_and_paths(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("x")
    assert fingerprint_files([str(a)]) == fingerprint_files([a])


# ---------------------------------------------------------------------------
# _json_safe
# ---------------------------------------------------------------------------

def test_json_safe_passthrough_for_jsonable_values():
    value = {"a": [1, 2.5, "x", None, True]}
    assert _json_safe(value) is value


def test_json_safe_drops_exotic_keys():
    # tuple keys are not JSON-serialisable; skipkeys drops them.
    result = _json_safe({1: "x", (2, 3): "y"})
    assert result == {"1": "x"}


def test_json_safe_falls_back_to_string_for_custom_objects():
    result = _json_safe(object())
    assert isinstance(result, str)
    assert "object" in result


def test_json_safe_handles_nested_custom_object():
    class Point:
        def __str__(self):
            return "Point(1, 2)"

    result = _json_safe({"point": Point()})
    assert result == {"point": "Point(1, 2)"}


# ---------------------------------------------------------------------------
# Registration and ordering
# ---------------------------------------------------------------------------

def test_register_duplicate_stage_raises(tmp_path):
    engine = _engine(tmp_path)
    engine.register("a", lambda ctx, res: 1)
    with pytest.raises(ValueError, match="duplicate stage"):
        engine.register("a", lambda ctx, res: 2)


def test_register_clamps_negative_retries(tmp_path):
    engine = _engine(tmp_path)
    engine.register("a", lambda ctx, res: 1, retries=-5)
    assert engine.stages["a"].retries == 0


def test_order_respects_dependencies(tmp_path):
    engine = _engine(tmp_path)
    engine.register("c", lambda ctx, res: 3, deps=["a", "b"])
    engine.register("a", lambda ctx, res: 1)
    engine.register("b", lambda ctx, res: 2, deps=["a"])
    assert engine.order() == ["a", "b", "c"]


def test_order_detects_cycle(tmp_path):
    engine = _engine(tmp_path)
    engine.register("a", lambda ctx, res: 1, deps=["b"])
    engine.register("b", lambda ctx, res: 2, deps=["a"])
    with pytest.raises(ValueError, match="dependency cycle"):
        engine.order()


def test_order_unknown_dependency_raises_key_error(tmp_path):
    engine = _engine(tmp_path)
    engine.register("a", lambda ctx, res: 1, deps=["ghost"])
    with pytest.raises(KeyError, match="unknown dependency"):
        engine.order()


# ---------------------------------------------------------------------------
# State loading
# ---------------------------------------------------------------------------

def test_load_missing_state_file_starts_empty(tmp_path):
    engine = _engine(tmp_path)
    assert engine.state == {"stages": {}, "updated": None}


def test_load_malformed_json_starts_empty(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("{not json", encoding="utf-8")
    assert _engine(tmp_path).state == {"stages": {}, "updated": None}


def test_load_non_object_json_starts_empty(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert _engine(tmp_path).state == {"stages": {}, "updated": None}


def test_load_valid_state_is_kept(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"stages": {"a": {"status": "success", "result": 7}},
                                      "updated": 123.0}), encoding="utf-8")
    engine = _engine(tmp_path)
    assert engine.state["stages"]["a"]["result"] == 7


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def test_run_single_stage_and_persists_result(tmp_path):
    engine = _engine(tmp_path)
    engine.register("a", lambda ctx, res: 42)
    results = engine.run()
    assert results == {"a": 42}
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["stages"]["a"]["status"] == "success"
    assert saved["stages"]["a"]["result"] == 42


def test_run_passes_context_and_prior_results(tmp_path):
    seen = {}

    def first(ctx, res):
        seen["ctx"] = ctx
        return "one"

    def second(ctx, res):
        seen["res"] = dict(res)
        return res["first"] + "-two"

    engine = _engine(tmp_path)
    engine.register("first", first)
    engine.register("second", second, deps=["first"])
    results = engine.run(context={"k": "v"})
    assert results["second"] == "one-two"
    assert seen["ctx"] == {"k": "v"}
    assert seen["res"] == {"first": "one"}


def test_run_resumes_from_persisted_success_without_rerunning(tmp_path):
    calls = {"n": 0}

    def action(ctx, res):
        calls["n"] += 1
        return "done"

    engine = _engine(tmp_path)
    engine.register("a", action)
    engine.run()
    assert calls["n"] == 1
    engine2 = _engine(tmp_path)
    engine2.register("a", action)
    results = engine2.run()
    assert results == {"a": "done"}
    assert calls["n"] == 1  # not re-executed


def test_run_target_runs_only_needed_stages(tmp_path):
    ran = []

    def make(name):
        def action(ctx, res):
            ran.append(name)
            return name
        return action

    engine = _engine(tmp_path)
    engine.register("a", make("a"))
    engine.register("b", make("b"), deps=["a"])
    engine.register("c", make("c"))
    results = engine.run(targets=["b"])
    assert results == {"a": "a", "b": "b"}
    assert ran == ["a", "b"]


def test_run_failed_dependency_is_retried_not_skipped(tmp_path):
    # A stage marked "failed" in persisted state is re-run (not trusted),
    # so the pipeline recovers instead of failing on the dependency check.
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(
        {"stages": {"a": {"status": "failed"}}, "updated": None}), encoding="utf-8")
    engine = _engine(tmp_path)
    engine.register("a", lambda ctx, res: 1)
    engine.register("b", lambda ctx, res: 2, deps=["a"])
    assert engine.run() == {"a": 1, "b": 2}


def test_run_validator_failure_marks_stage_failed(tmp_path):
    engine = _engine(tmp_path)
    engine.register("a", lambda ctx, res: -1, validator=lambda r: r > 0)
    with pytest.raises(ValueError, match="output validation failed"):
        engine.run()
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["stages"]["a"]["status"] == "failed"
    assert saved["stages"]["a"]["error_type"] == "ValueError"


def test_run_retries_then_succeeds(tmp_path):
    sleeps = []
    attempts = {"n": 0}

    def flaky(ctx, res):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    engine = _engine(tmp_path, sleep=sleeps.append)
    engine.register("a", flaky, retries=3)
    assert engine.run() == {"a": "ok"}
    assert attempts["n"] == 3
    assert sleeps == [1, 2]  # 2**0, 2**1


def test_run_retry_exhausted_raises_and_records_error(tmp_path):
    sleeps = []
    engine = _engine(tmp_path, sleep=sleeps.append)
    engine.register("a", lambda ctx, res: (_ for _ in ()).throw(RuntimeError("nope")),
                    retries=1)
    with pytest.raises(RuntimeError, match="nope"):
        engine.run()
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["stages"]["a"]["status"] == "failed"
    assert saved["stages"]["a"]["attempt"] == 2
    assert sleeps == [1]


def test_run_sleep_backoff_capped_at_30(tmp_path):
    sleeps = []
    engine = _engine(tmp_path, sleep=sleeps.append)
    engine.register("a", lambda ctx, res: (_ for _ in ()).throw(RuntimeError("x")),
                    retries=6)
    with pytest.raises(RuntimeError):
        engine.run()
    assert sleeps == [1, 2, 4, 8, 16, 30]


def test_run_cancelled_token_raises_before_any_stage(tmp_path):
    token = CancellationToken()
    token.cancel()
    engine = _engine(tmp_path, token=token)
    ran = []
    engine.register("a", lambda ctx, res: ran.append("a"))
    with pytest.raises(RuntimeError, match="cancelled"):
        engine.run()
    assert ran == []


def test_run_cancellation_mid_run_stops_next_stage(tmp_path):
    token = CancellationToken()

    def first(ctx, res):
        token.cancel()
        return 1

    engine = _engine(tmp_path, token=token)
    engine.register("a", first)
    engine.register("b", lambda ctx, res: 2, deps=["a"])
    with pytest.raises(RuntimeError, match="cancelled"):
        engine.run()


# ---------------------------------------------------------------------------
# invalidate_downstream and atomic save
# ---------------------------------------------------------------------------

def test_invalidate_downstream_clears_dependents_only(tmp_path):
    engine = _engine(tmp_path)
    engine.register("a", lambda ctx, res: 1)
    engine.register("b", lambda ctx, res: 2, deps=["a"])
    engine.register("c", lambda ctx, res: 3)
    engine.run()
    engine.invalidate_downstream(["a"])
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "a" in saved["stages"]  # the changed stage itself is kept
    assert "b" not in saved["stages"]  # downstream is invalidated
    assert "c" in saved["stages"]  # unrelated stage is kept


def test_atomic_save_writes_valid_json_and_no_temp_leftovers(tmp_path):
    engine = _engine(tmp_path)
    engine.register("a", lambda ctx, res: 1)
    engine.run()
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["stages"]["a"]["status"] == "success"


def test_state_file_created_in_missing_directory(tmp_path):
    nested = tmp_path / "deep" / "nested" / "state.json"
    engine = PipelineEngine(nested)
    engine.register("a", lambda ctx, res: 1)
    engine.run()
    assert nested.is_file()
