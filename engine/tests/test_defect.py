"""M3：A/B 缺陷探针 + expected_fail 语义反转 + 生命周期幂等/预期失败。"""

from pathlib import Path

import pytest

from ragtest.adapters.base import AdapterError, ErrorKind, RetrievalResult, RetrievedChunk
from ragtest.evaluators import get_evaluator
from ragtest.models import CaseStatus
from ragtest.models.result import CaseResult, TraceInfo
from ragtest.models.suite import EvaluatorSpec, ExpectedSpec, GoldenCase, InputSpec
from ragtest.runner.executor import execute_case
from tests.test_lifecycle import FakeAdapter, make_runner, CASES


# ── ab_results_differ evaluator ──


def make_ab_case(expected_fail: bool) -> GoldenCase:
    return GoldenCase(
        id="d1",
        input=InputSpec(query="q", extra_body={"vector_similarity_weight": 0.95}),
        input_b=InputSpec(query="q", extra_body={"vector_similarity_weight": 0.05}),
        expected=ExpectedSpec(documents=[]),
        expected_fail={"reason": "权重被忽略"} if expected_fail else None,
        evaluators=[EvaluatorSpec(name="ab_results_differ")],
    )


class ABAdapter:
    """返回可控 A/B 结果的 adapter（identical 参数控制）。"""

    def __init__(self, identical: bool):
        self.identical = identical

    async def retrieve(self, session, kb, query) -> RetrievalResult:
        weight = query.extra_body.get("vector_similarity_weight", 0)
        if self.identical:
            chunks = [RetrievedChunk(chunk_id="c1", document_id="d1", score=0.9, rank=1)]
        else:
            cid = "c1" if weight > 0.5 else "c2"
            chunks = [RetrievedChunk(chunk_id=cid, document_id="d1", score=0.9, rank=1)]
        return RetrievalResult(chunks=chunks, latency_ms=1)


async def test_ab_identical_means_param_ignored():
    """A/B 结果一致 → evaluator fail（参数被忽略）；expected_fail → 翻转为证据有效。"""
    case = make_ab_case(expected_fail=True)
    result = await execute_case(
        case, adapter=ABAdapter(identical=True), session=None, kb=None,
        doc_id_to_logical={}, raw_dir=None,
    )
    # 缺陷复现：expected_fail 翻转为 PASSED
    assert result.status is CaseStatus.PASSED
    flip = [a for a in result.assertions if a.kind == "expected_fail"]
    assert flip and "已复现" in flip[0].detail
    # 底层 metric 仍为 fail（结果被忽略）
    metric = result.metrics[0]
    assert not metric.passed and "未生效" in metric.detail


async def test_ab_different_means_param_honored():
    """A/B 结果不同 → evaluator pass（参数生效）；expected_fail → 意外通过提示。"""
    case = make_ab_case(expected_fail=True)
    result = await execute_case(
        case, adapter=ABAdapter(identical=False), session=None, kb=None,
        doc_id_to_logical={},
    )
    assert result.status is CaseStatus.PASSED
    assert result.metrics[0].passed
    flip = [a for a in result.assertions if a.kind == "expected_fail"]
    assert flip and "疑似已修复" in flip[0].detail


def test_ab_evaluator_without_ab_data_skipped():
    m = get_evaluator("ab_results_differ")(
        make_ab_case(False), CaseResult(case_id="x", status="passed", trace=TraceInfo()), {})
    assert m.skipped


# ── 生命周期：upload_twice / expect_ingest_failure ──


class RobustFakeAdapter(FakeAdapter):
    async def wait_until_ready(self, session, kb, doc, *, poll):
        from ragtest.adapters.base import IngestFailed
        if doc.logical_id == "doc_empty":
            self.calls.append("wait_ready:empty")
            raise IngestFailed("文档摄入失败: 空文件无法解析")
        return await super().wait_until_ready(session, kb, doc, poll=poll)


ROBUST_CASES = [
    {"id": "c1", "identity": "reader", "input": {"query": "共聚物是什么"},
     "expected": {"documents": ["doc_copolymer"]},
     "evaluators": [{"name": "hit_rate_at_k", "k": 5, "threshold": 1.0}]},
]


async def test_upload_twice_and_expect_ingest_failure(tmp_path):
    import yaml

    from ragtest.artifacts import RunStatusWriter
    from ragtest.adapters.base import Identity
    from ragtest.assets import load_suite
    from ragtest.evaluators import known_evaluators
    from ragtest.runner.lifecycle import SuiteRunner

    # 数据集：正常文档 + 空文件（预期失败）+ 重复上传探针
    doc_dir = tmp_path / "ds" / "documents"
    doc_dir.mkdir(parents=True)
    (doc_dir / "a.md").write_text("# 共聚物", encoding="utf-8")
    (doc_dir / "empty.md").write_text("", encoding="utf-8")
    (tmp_path / "ds" / "dataset.yaml").write_text(yaml.safe_dump({
        "schema_version": "1", "id": "ds", "version": "v1",
        "documents": [
            {"logical_id": "doc_copolymer", "path": "documents/a.md"},
            {"logical_id": "doc_empty", "path": "documents/empty.md",
             "metadata": {"expect_ingest_failure": True}},
            {"logical_id": "doc_dup", "path": "documents/a.md",
             "metadata": {"upload_twice": True}},
        ],
    }), encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(yaml.safe_dump({
        "schema_version": "1", "kind": "GoldenSuite", "id": "s1",
        "dataset": {"ref": "ds/dataset.yaml"},
        "identities": {
            "owner": {"role": "admin"},
            "reader": {"role": "user", "create": True, "grant_level": "read"},
        },
        "cases": ROBUST_CASES,
    }, allow_unicode=True), encoding="utf-8")
    suite, dataset, base = load_suite(suite_path, known_evaluators=known_evaluators())

    adapter = RobustFakeAdapter()
    writer = RunStatusWriter(tmp_path / "run", "test-run", total=1)
    runner = SuiteRunner(
        suite=suite, dataset=dataset, dataset_base=base, adapter=adapter,
        admin_identity=Identity(logical_name="owner", role="admin", email="a", password="p"),
        run_id="test-run", writer=writer, repo_root=tmp_path,
    )
    run = await runner.run_suite()

    docs = {d["logical_id"]: d for d in run.kb["documents"]}
    # 空文件：按预期失败，不拖垮 suite
    assert docs["doc_empty"]["final_status"] == "failed_expected"
    # 正常文档与 dup 文档：就绪
    assert docs["doc_copolymer"]["final_status"] == "ready"
    assert docs["doc_dup"]["final_status"] == "ready"
    # 重复上传探针记录
    assert any("重复上传幂等探针" in (e.detail or "") for e in run.lifecycle)
    # suite 终态 DONE（预期失败不算错误）
    assert runner.writer.status.state == "DONE"
