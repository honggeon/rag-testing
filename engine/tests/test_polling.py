"""backoff 轮询测试（架构 §6.1：禁止固定 sleep，指数退避 + 超时）。"""

import asyncio

import pytest

from ragtest.adapters.base import IngestFailed, PollPolicy, WaitTimeout
from ragtest.runner.polling import poll_until

FAST = PollPolicy(initial_s=0.01, factor=2.0, max_interval_s=0.02, timeout_s=5.0)


async def test_success_after_retries():
    calls = 0

    async def probe():
        nonlocal calls
        calls += 1
        return "ready" if calls >= 3 else None

    assert await poll_until(probe, policy=FAST) == "ready"
    assert calls == 3


async def test_timeout_raises_with_observation():
    async def probe():
        return None

    with pytest.raises(WaitTimeout):
        await poll_until(probe, policy=PollPolicy(
            initial_s=0.01, factor=2.0, max_interval_s=0.02, timeout_s=0.1))


async def test_ingest_failed_propagates_immediately():
    calls = 0

    async def probe():
        nonlocal calls
        calls += 1
        raise IngestFailed("文档摄入失败: 解析错误")

    with pytest.raises(IngestFailed):
        await poll_until(probe, policy=FAST)
    assert calls == 1  # 终态失败不重试


async def test_backoff_intervals_grow(monkeypatch):
    sleeps: list[float] = []
    calls = 0

    async def spy_sleep(s: float):
        sleeps.append(s)  # 不真实睡眠：probe 第 5 次即成功，时间压力不参与

    async def probe():
        nonlocal calls
        calls += 1
        return "ok" if calls >= 5 else None

    monkeypatch.setattr(asyncio, "sleep", spy_sleep)
    result = await poll_until(
        probe,
        policy=PollPolicy(initial_s=1.0, factor=2.0, max_interval_s=4.0, timeout_s=100.0),
    )

    assert result == "ok"
    # 间隔序列应为 1, 2, 4, 4（封顶 max_interval_s）
    assert len(sleeps) == 4
    assert sleeps[0] == pytest.approx(1.0)
    assert sleeps[1] == pytest.approx(2.0)
    assert sleeps[2] == pytest.approx(4.0)
    assert sleeps[3] == pytest.approx(4.0)
