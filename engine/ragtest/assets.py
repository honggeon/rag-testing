"""测试资产加载：dataset.yaml / golden suite yaml（架构 §7）。

fail fast 原则：schema 错误、checksum 不匹配、未知 evaluator 名 → 加载期报错（exit 3）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import ValidationError

from ragtest.models.suite import Dataset, DatasetDocument, GoldenSuite


class AssetError(Exception):
    """资产配置/校验错误（CLI exit 3）。"""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_dataset(dataset_yaml: Path) -> tuple[Dataset, Path]:
    """加载 dataset.yaml，校验文档存在性与 sha256（声明了才校验，未声明则回填）。"""
    dataset_yaml = Path(dataset_yaml)
    if not dataset_yaml.exists():
        raise AssetError(f"dataset 不存在: {dataset_yaml}")
    try:
        raw = yaml.safe_load(dataset_yaml.read_text(encoding="utf-8"))
        dataset = Dataset.model_validate(raw)
    except ValidationError as e:
        raise AssetError(f"dataset schema 校验失败 {dataset_yaml}:\n{e}") from e

    base = dataset_yaml.parent
    for doc in dataset.documents:
        path = base / doc.path
        if not path.exists():
            raise AssetError(f"文档不存在: {path}（logical_id={doc.logical_id}）")
        actual = _sha256_file(path)
        if doc.sha256 and doc.sha256 != actual:
            raise AssetError(
                f"文档 checksum 不匹配: {path}\n  声明 {doc.sha256}\n  实际 {actual}"
            )
        doc.sha256 = actual
    return dataset, base


def load_suite(suite_yaml: Path, *, known_evaluators: set[str]) -> tuple[GoldenSuite, Dataset, Path]:
    """加载 golden suite：schema 校验 → dataset 解析 → evaluator 名校验（fail fast）。"""
    suite_yaml = Path(suite_yaml)
    if not suite_yaml.exists():
        raise AssetError(f"suite 不存在: {suite_yaml}")
    try:
        raw = yaml.safe_load(suite_yaml.read_text(encoding="utf-8"))
        suite = GoldenSuite.model_validate(raw)
    except ValidationError as e:
        raise AssetError(f"suite schema 校验失败 {suite_yaml}:\n{e}") from e

    dataset_ref = (suite_yaml.parent / suite.dataset.ref).resolve()
    dataset, dataset_base = load_dataset(dataset_ref)

    # 未知 evaluator → 加载期报错（fail fast）
    for case in suite.cases:
        for spec in case.evaluators:
            if spec.name not in known_evaluators:
                raise AssetError(
                    f"case {case.id}: 未知 evaluator '{spec.name}'"
                    f"（可用: {sorted(known_evaluators)}）"
                )

    # dataset 引用的 logical_id 必须存在
    doc_ids = {d.logical_id for d in dataset.documents}
    for case in suite.cases:
        if not case.expected:
            continue
        for field_name, refs in (
            ("documents", case.expected.documents),
            ("forbidden_documents", case.expected.forbidden_documents),
        ):
            for ref in refs:
                if ref not in doc_ids:
                    raise AssetError(
                        f"case {case.id}: expected.{field_name} 引用了数据集中不存在的 logical_id '{ref}'"
                    )
    return suite, dataset, dataset_base
