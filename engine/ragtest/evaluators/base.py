"""Evaluator 基类与注册表（架构 v0.3 §9）。

Evaluator 是纯函数式插件：golden case + case 执行结果 → MetricResult。
不 import runner / adapter（依赖规则 §4.4）。
"""

from __future__ import annotations

from collections.abc import Callable

from ragtest.models.result import CaseResult, MetricResult
from ragtest.models.suite import GoldenCase

# evaluate(case, result, params) -> MetricResult
EvaluatorFn = Callable[[GoldenCase, CaseResult, dict], MetricResult]

_REGISTRY: dict[str, EvaluatorFn] = {}


def register(name: str) -> Callable[[EvaluatorFn], EvaluatorFn]:
    """注册 evaluator：`@register("recall_at_k")`，suite YAML 按名引用。"""

    def deco(fn: EvaluatorFn) -> EvaluatorFn:
        if name in _REGISTRY:
            raise ValueError(f"evaluator 重复注册: {name}")
        _REGISTRY[name] = fn
        return fn

    return deco


def get_evaluator(name: str) -> EvaluatorFn:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"未知 evaluator: {name}（可用: {sorted(_REGISTRY)}）") from None


def known_evaluators() -> set[str]:
    return set(_REGISTRY)


# ── 公共工具 ────────────────────────────────────────────────────────────────


def retrieved_logical_docs(result: CaseResult, k: int | None = None) -> list[str]:
    """取检索命中的 logical_doc 列表（按 rank 序，去重保序）。k=None 表示全部。"""
    if not result.retrieval:
        return []
    chunks = result.retrieval.chunks if k is None else result.retrieval.chunks[:k]
    seen: list[str] = []
    for c in chunks:
        if c.logical_doc and c.logical_doc not in seen:
            seen.append(c.logical_doc)
    return seen


def expected_docs(case: GoldenCase) -> list[str]:
    return list(case.expected.documents) if case.expected else []


def forbidden_docs(case: GoldenCase) -> list[str]:
    return list(case.expected.forbidden_documents) if case.expected else []
