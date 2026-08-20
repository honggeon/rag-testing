"""资产加载测试：checksum、schema、未知 evaluator、逻辑引用校验（fail fast）。"""

from pathlib import Path

import pytest
import yaml

from ragtest.assets import AssetError, load_dataset, load_suite
from ragtest.evaluators import known_evaluators


def write_dataset(base: Path, sha: str | None = None) -> Path:
    (base / "documents").mkdir(parents=True, exist_ok=True)
    doc = base / "documents" / "a.md"
    doc.write_text("# 测试文档", encoding="utf-8")
    data = {
        "schema_version": "1",
        "id": "ds",
        "version": "v1",
        "documents": [{"logical_id": "doc_a", "path": "documents/a.md", **({"sha256": sha} if sha else {})}],
    }
    p = base / "dataset.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def write_suite(base: Path, dataset_rel: str, cases: list[dict]) -> Path:
    data = {
        "schema_version": "1",
        "kind": "GoldenSuite",
        "id": "s1",
        "dataset": {"ref": dataset_rel},
        "cases": cases,
    }
    p = base / "suite.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


CASE_OK = {
    "id": "c1",
    "input": {"query": "q"},
    "expected": {"documents": ["doc_a"]},
    "evaluators": [{"name": "recall_at_k", "k": 5, "threshold": 1.0}],
}


def test_dataset_checksum_backfill(tmp_path: Path):
    ds, base = load_dataset(write_dataset(tmp_path))
    assert ds.documents[0].sha256 and len(ds.documents[0].sha256) == 64


def test_dataset_checksum_mismatch(tmp_path: Path):
    with pytest.raises(AssetError, match="checksum 不匹配"):
        load_dataset(write_dataset(tmp_path, sha="0" * 64))


def test_dataset_missing_document(tmp_path: Path):
    p = write_dataset(tmp_path)
    (tmp_path / "documents" / "a.md").unlink()
    with pytest.raises(AssetError, match="文档不存在"):
        load_dataset(p)


def test_suite_ok(tmp_path: Path):
    ds_dir = tmp_path / "datasets" / "basic"
    write_dataset(ds_dir)
    sp = write_suite(tmp_path, "datasets/basic/dataset.yaml", [CASE_OK])
    suite, dataset, _ = load_suite(sp, known_evaluators=known_evaluators())
    assert suite.id == "s1"
    # EvaluatorSpec.flatten_params：name 之外的键进 params
    assert suite.cases[0].evaluators[0].params == {"k": 5, "threshold": 1.0}


def test_suite_unknown_evaluator_fails_fast(tmp_path: Path):
    write_dataset(tmp_path / "datasets" / "basic")
    bad = dict(CASE_OK, evaluators=[{"name": "no_such_metric"}])
    sp = write_suite(tmp_path, "datasets/basic/dataset.yaml", [bad])
    with pytest.raises(AssetError, match="未知 evaluator"):
        load_suite(sp, known_evaluators=known_evaluators())


def test_suite_unknown_logical_id(tmp_path: Path):
    write_dataset(tmp_path / "datasets" / "basic")
    bad = dict(CASE_OK, expected={"documents": ["doc_ghost"]})
    sp = write_suite(tmp_path, "datasets/basic/dataset.yaml", [bad])
    with pytest.raises(AssetError, match="不存在的 logical_id"):
        load_suite(sp, known_evaluators=known_evaluators())


def test_case_expected_and_expect_error_mutex(tmp_path: Path):
    write_dataset(tmp_path / "datasets" / "basic")
    bad = dict(CASE_OK, expect_error={"kind": "validation"})
    sp = write_suite(tmp_path, "datasets/basic/dataset.yaml", [bad])
    with pytest.raises(AssetError, match="互斥"):
        load_suite(sp, known_evaluators=known_evaluators())
