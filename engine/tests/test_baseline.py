"""baseline 存取 / diff 分类 / 可比性校验测试（架构 §11）。"""

from pathlib import Path

import pytest

from ragtest.baseline import compare, load_baseline, save_baseline
from ragtest.models.result import RunSummary, TestRun


def make_run(pass_rate=1.0, metrics=None, latency=None, fp=None, ds_version="v1") -> TestRun:
    run = TestRun(run_id="r1")
    run.suite = {"id": "s1", "dataset_version": ds_version, "golden_checksum": "sha256:x"}
    run.environment = {"fingerprint": {"retrieval": fp or {"embedding_model": "m1", "reranker_enabled": False}}}
    run.summary = RunSummary(
        total=3, passed=3, pass_rate=pass_rate,
        metrics_avg=metrics or {"recall_at_5": 0.92, "mrr": 0.85},
        latency=latency or {"search_p95_ms": 850.0},
    )
    return run


def test_save_and_load_roundtrip(tmp_path: Path):
    run = make_run()
    path = save_baseline(run, tmp_path, "main")
    loaded = load_baseline(tmp_path, "s1", "main")
    assert loaded is not None and loaded.suite_id == "s1"
    assert loaded.summary["metrics_avg"]["recall_at_5"] == 0.92
    assert path.name == "main.json"
    assert load_baseline(tmp_path, "s1", "nonexist") is None


def test_diff_regression_and_improvement(tmp_path: Path):
    base_run = make_run(metrics={"recall_at_5": 0.92, "mrr": 0.85}, latency={"search_p95_ms": 850.0})
    save_baseline(base_run, tmp_path, "main")
    baseline = load_baseline(tmp_path, "s1", "main")

    current = make_run(metrics={"recall_at_5": 0.84, "mrr": 0.87}, latency={"search_p95_ms": 1310.0})
    diff = compare(current, baseline)

    assert diff.comparable
    by_name = {m.name: m for m in diff.metrics}
    # 比率类：下降 0.08 → regression
    assert by_name["recall_at_5"].classification == "regression"
    assert by_name["recall_at_5"].delta == pytest.approx(-0.08)
    # 比率类：上升 0.02 → improvement
    assert by_name["mrr"].classification == "improvement"
    # 时延类：+54% → regression
    assert by_name["search_p95_ms"].classification == "regression"
    assert by_name["search_p95_ms"].delta_pct > 0.5


def test_diff_stable_within_threshold(tmp_path: Path):
    save_baseline(make_run(metrics={"recall_at_5": 0.92}), tmp_path, "main")
    baseline = load_baseline(tmp_path, "s1", "main")
    current = make_run(metrics={"recall_at_5": 0.915}, latency={"search_p95_ms": 900.0})
    diff = compare(current, baseline)
    by_name = {m.name: m for m in diff.metrics}
    assert by_name["recall_at_5"].classification == "stable"      # |Δ|=0.005 ≤ 0.01
    assert by_name["search_p95_ms"].classification == "stable"    # +5.9% ≤ 10%


def test_incomparable_on_fingerprint_mismatch(tmp_path: Path):
    save_baseline(make_run(fp={"embedding_model": "m1"}), tmp_path, "main")
    baseline = load_baseline(tmp_path, "s1", "main")

    diff = compare(make_run(fp={"embedding_model": "m2"}), baseline)
    assert not diff.comparable
    assert any("embedding_model" in r for r in diff.incomparable_reasons)
    assert not diff.metrics  # 不可比 → 不做指标对比

    diff2 = compare(make_run(ds_version="v2"), baseline)
    assert not diff2.comparable
    assert any("dataset_version" in r for r in diff2.incomparable_reasons)
