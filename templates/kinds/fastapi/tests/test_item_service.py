"""The use case on its own, with fakes for its ports: no database, no HTTP."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from shikumi_app.domain.items import InvalidItemError, Item, NewItem
from shikumi_app.services.items import PROCESS_ITEM, ItemService

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeItems:
    def __init__(self) -> None:
        self.rows: dict[int, Item] = {}

    def add(self, item: NewItem) -> Item:
        new = Item(id=len(self.rows) + 1, name=item.name, created_at=item.created_at)
        self.rows[new.id] = new
        return new

    def list(self, limit: int) -> Sequence[Item]:
        return sorted(self.rows.values(), key=lambda i: -i.id)[:limit]

    def get(self, item_id: int) -> Item | None:
        return self.rows.get(item_id)

    def mark_processed(self, item_id: int, at: datetime) -> None:
        self.rows[item_id] = replace(self.rows[item_id], processed_at=at)


class FakeJobs:
    def __init__(self) -> None:
        self.queued: list[tuple[str, dict[str, Any], str | None]] = []

    def enqueue(self, kind: str, payload: dict[str, Any], dedupe_key: str | None = None) -> None:
        self.queued.append((kind, payload, dedupe_key))


def test_create_saves_the_item_and_queues_its_job() -> None:
    items, jobs = FakeItems(), FakeJobs()
    item = ItemService(items, jobs, clock=lambda: NOW).create(" Tea ")
    assert item == Item(id=1, name="Tea", created_at=NOW)
    assert jobs.queued == [(PROCESS_ITEM, {"item_id": 1}, "process_item:1")]


def test_create_refuses_a_blank_name() -> None:
    with pytest.raises(InvalidItemError):
        ItemService(FakeItems(), FakeJobs()).create("  ")


def test_process_is_idempotent() -> None:
    items = FakeItems()
    service = ItemService(items, FakeJobs(), clock=lambda: NOW)
    service.create("Tea")
    service.process(1)
    service.clock = lambda: datetime(2027, 1, 1, tzinfo=UTC)
    service.process(1)
    service.process(99)  # a missing item is not an error
    assert items.rows[1].processed_at == NOW
