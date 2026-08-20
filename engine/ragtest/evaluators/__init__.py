"""Evaluator 注册表：import 本包即完成全部内建 evaluator 注册。"""

from ragtest.evaluators import (
    defect,
    generation,
    judge,
    performance,
    ranking,
    retrieval,
    security,
)  # noqa: F401
from ragtest.evaluators.base import get_evaluator, known_evaluators, register

__all__ = ["register", "get_evaluator", "known_evaluators"]
