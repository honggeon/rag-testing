"""Quality Gate 测试：阈值表达式 / 各 section 违规路径 / 回归项 / defect 排除 / exit code。"""

from ragtest.baseline import compare
from ragtest.cli import decide_exit_code
from ragtest.gate import evaluate_gate
from ragtest.models.result import RunSummary, TestRun
from tests.test_baseline import make_run, save_baseline, load_baseline


def gate_cfg(**overrides):
    cfg = {
        "overall": {"pass_rate": ">=0.95"},
        "retrieval": {"recall_at_5": ">=0.90"},
        "security": {"permission_leak": "==0"},
        "performance": {"search_p95_ms": "<=2000"},
    }
    cfg.update(overrides)
    return cfg


def test_gate_pass(tmp_path):
    run = make_run(metrics={"recall_at_5": 0.92, "permission_leak": 0.0})
    verdict = evaluate_gate(run, gate_cfg())
    assert verdict.passed and not verdict.violations


def test_gate_violations_all_sections():
    run = make_run(
        pass_rate=0.90,                                   # overall 违规
        metrics={"recall_at_5": 0.80, "permission_leak": 1.0},  # retrieval + security 违规
        latency={"search_p95_ms": 2500.0},                # performance 违规
    )
    verdict = evaluate_gate(run, gate_cfg())
    assert not verdict.passed
    sections = {v.section for v in verdict.violations}
    assert sections == {"overall", "retrieval", "security", "performance"}


def test_gate_missing_metric_fails_closed():
    run = make_run(metrics={})  # 无 recall_at_5 / permission_leak
    run.summary.metrics_avg = {}
    verdict = evaluate_gate(run, {"retrieval": {"recall_at_5": ">=0.9"}})
    assert not verdict.passed
    assert "缺失" in verdict.violations[0].detail


def test_gate_regression_drop(tmp_path):
    save_baseline(make_run(metrics={"recall_at_5": 0.92}), tmp_path, "main")
    baseline = load_baseline(tmp_path, "s1", "main")

    # 下降 0.04 > 允许 0.03 → 违规
    run = make_run(metrics={"recall_at_5": 0.88})
    diff = compare(run, baseline)
    verdict = evaluate_gate(run, {"regression": {"recall_at_5_drop": "<=0.03"}}, diff)
    assert not verdict.passed
    assert verdict.violations[0].section == "regression"

    # 下降 0.01 ≤ 0.03 → 通过
    run2 = make_run(metrics={"recall_at_5": 0.91})
    verdict2 = evaluate_gate(run2, {"regression": {"recall_at_5_drop": "<=0.03"}},
                             compare(run2, baseline))
    assert verdict2.passed


def test_gate_regression_without_baseline_fails_closed():
    run = make_run()
    verdict = evaluate_gate(run, {"regression": {"recall_at_5_drop": "<=0.03"}}, None)
    assert not verdict.passed
    assert "baseline" in verdict.violations[0].detail


def test_defect_suite_skipped():
    run = make_run(pass_rate=0.0, metrics={})
    verdict = evaluate_gate(run, gate_cfg(), is_defect_suite=True)
    assert verdict.skipped and verdict.passed


def test_exit_code_matrix():
    s = RunSummary(total=3, passed=3, failed=0, error=0)
    assert decide_exit_code("DONE", True, s) == 0
    assert decide_exit_code("DONE", False, s) == 1
    assert decide_exit_code("ERROR", None, s) == 2
    assert decide_exit_code("TIMEOUT", None, s) == 2
    assert decide_exit_code("CANCELLED", None, s) == 2
    # 无 gate 时按 case 结果
    assert decide_exit_code("DONE", None, RunSummary(total=3, passed=2, failed=1)) == 1
    assert decide_exit_code("PARTIAL", None, RunSummary(total=3, passed=2, error=1)) == 1
    assert decide_exit_code("DONE", None, s) == 0
