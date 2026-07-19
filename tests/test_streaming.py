"""Тесты EventBus — фундамент стриминга."""
import asyncio

import pytest

from deep_research.streaming import EventBus


@pytest.mark.asyncio
async def test_publish_and_stream():
    bus = EventBus()
    received = []

    async def consumer():
        async for ev in bus.stream():
            received.append(ev)
            if ev.type == "done":
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)  # дать consumer подписаться

    await bus.emit_plan("hello")
    await bus.emit_search_start(["q1"])
    await bus.emit_done("answer", [{"title": "t", "url": "u", "content": "c"}])
    await bus.close()

    await asyncio.wait_for(task, timeout=1.0)
    types = [e.type for e in received]
    assert types == ["plan", "search_start", "done"]


@pytest.mark.asyncio
async def test_close_unblocks_subscribers():
    bus = EventBus()
    received = []

    async def consumer():
        async for ev in bus.stream():
            received.append(ev)
        # После break сюда приходим после close()

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)
    await bus.close()
    await asyncio.wait_for(task, timeout=1.0)
    assert received == []
