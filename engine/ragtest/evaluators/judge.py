"""LLM Judge（架构 v0.3 §9 可插拔 evaluator）：faithfulness / answer_relevancy /
semantic_similarity。

设计：
- JudgeClient 走 OpenAI 兼容 /chat/completions（被测环境的 vLLM :7500 或任意兼容端点）
- provider 可插拔：默认 openai_compat；扩展点 = JudgeClient protocol + 注册表
- evaluator 支持 async（Executor 探测 awaitable）
- 未配置 judge → 全部 skipped（不影响 pass/fail，detail 提示配置）
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Protocol

import httpx

from ragtest.adapters.base import AdapterError, ErrorKind
from ragtest.evaluators.base import register
from ragtest.models.result import CaseResult, MetricResult
from ragtest.models.suite import GoldenCase

# ── JudgeClient（可插拔 provider）───────────────────────────────────────────


class JudgeClient(Protocol):
    async def score(self, prompt: str) -> float: ...


class OpenAiCompatJudge:
    """OpenAI 兼容 /v1/chat/completions，temperature=0，解析响应中的首个 0-1 数字。"""

    name = "openai_compat"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout_s: float = 30.0):
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout_s, trust_env=False)

    async def score(self, prompt: str) -> float:
        try:
            resp = await self._client.post(
                self._url,
                headers=self._headers,
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 16,
                },
            )
        except httpx.TimeoutException as e:
            raise AdapterError(ErrorKind.TIMEOUT, f"judge 请求超时: {e}") from e
        except httpx.TransportError as e:
            raise AdapterError(ErrorKind.SERVER, f"judge 连接失败: {e}") from e
        if resp.status_code >= 400:
            raise AdapterError(ErrorKind.SERVER,
                               f"judge HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError):
            raise AdapterError(ErrorKind.SERVER, f"judge 响应结构异常: {resp.text[:200]}")
        m = re.search(r"0\.\d+|1\.0|1|0", content or "")
        if not m:
            raise AdapterError(ErrorKind.SERVER, f"judge 未输出分数: {content!r}")
        return max(0.0, min(1.0, float(m.group(0))))

    async def aclose(self) -> None:
        await self._client.aclose()


# 全局 judge 实例（由 runner 在 run 生命周期内 set/clear）
_judge: JudgeClient | None = None


def set_judge(client: JudgeClient | None) -> None:
    global _judge
    _judge = client


def get_judge() -> JudgeClient | None:
    return _judge


def _need_judge(result: CaseResult, name: str) -> MetricResult | None:
    """judge 缺失 → skipped 指标（不阻断 case）。"""
    if _judge is None:
        return MetricResult(
            name=name, skipped=True, category="generation",
            detail="未配置 LLM Judge（RAGTEST_JUDGE_BASE_URL/MODEL）",
        )
    if not result.generation:
        return MetricResult(name=name, skipped=True, category="generation",
                            detail="无生成结果")
    return None


async def _score_async(prompt: str) -> float:
    return await _judge.score(prompt)  # type: ignore[union-attr]


# ── evaluators ──────────────────────────────────────────────────────────────


@register("faithfulness")
async def faithfulness(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    skip = _need_judge(result, "faithfulness")
    if skip is not None:
        return skip
    answer = result.generation.answer
    context = result.generation.context or "（无检索上下文）"
    prompt = (
        "你是 RAG 忠实度评审员。判断「回答」中的每个事实是否都能由「检索上下文」支持"
        "（无法由上下文支持的内容即编造/幻觉）。\n"
        f"检索上下文：\n{context[:6000]}\n\n"
        f"回答：\n{answer[:3000]}\n\n"
        "输出一个 0 到 1 之间的分数（1=回答完全由上下文支持，无编造），只输出数字。"
    )
    try:
        value = await _score_async(prompt)
    except AdapterError as e:
        return MetricResult(name="faithfulness", passed=False, category="error",
                            detail=f"judge 调用失败: {e.message}")
    threshold = float(params.get("threshold", 0.85))
    return MetricResult(
        name="faithfulness", value=value, threshold=threshold, passed=value >= threshold,
        category="generation", detail=f"LLM Judge 忠实度 {value:.3f}（阈值 {threshold}）",
    )


@register("answer_relevancy")
async def answer_relevancy(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    skip = _need_judge(result, "answer_relevancy")
    if skip is not None:
        return skip
    question = case.input.question or case.input.query or ""
    answer = result.generation.answer
    prompt = (
        "你是回答相关性评审员。判断「回答」是否切题地回应了「问题」，"
        "而非答非所问或泛泛而谈。\n"
        f"问题：{question}\n回答：{answer[:3000]}\n\n"
        "输出一个 0 到 1 之间的分数，只输出数字。"
    )
    try:
        value = await _score_async(prompt)
    except AdapterError as e:
        return MetricResult(name="answer_relevancy", passed=False, category="error",
                            detail=f"judge 调用失败: {e.message}")
    threshold = float(params.get("threshold", 0.8))
    return MetricResult(
        name="answer_relevancy", value=value, threshold=threshold, passed=value >= threshold,
        category="generation", detail=f"LLM Judge 相关性 {value:.3f}（阈值 {threshold}）",
    )


@register("semantic_similarity")
async def semantic_similarity(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    """回答 vs 参考答案的语义相似度（期望答案经 expected.answer.reference 提供）。"""
    skip = _need_judge(result, "semantic_similarity")
    if skip is not None:
        return skip
    reference = ""
    if case.expected and case.expected.answer:
        reference = case.expected.answer.get("reference") or ""
    if not reference:
        facts = case.expected.golden_facts if case.expected else []
        reference = "；".join(facts)
    if not reference:
        return MetricResult(name="semantic_similarity", skipped=True, category="generation",
                            detail="未提供参考答案（expected.answer.reference 或 golden_facts）")
    answer = result.generation.answer
    prompt = (
        "比较以下两个文本的语义相似度（内容一致性，不要求字面相同），输出 0 到 1 之间的分数，只输出数字。\n"
        f"文本A（参考答案）：{reference[:3000]}\n文本B（模型回答）：{answer[:3000]}"
    )
    try:
        value = await _score_async(prompt)
    except AdapterError as e:
        return MetricResult(name="semantic_similarity", passed=False, category="error",
                            detail=f"judge 调用失败: {e.message}")
    threshold = float(params.get("threshold", 0.7))
    return MetricResult(
        name="semantic_similarity", value=value, threshold=threshold, passed=value >= threshold,
        category="generation", detail=f"LLM Judge 语义相似度 {value:.3f}（阈值 {threshold}）",
    )
