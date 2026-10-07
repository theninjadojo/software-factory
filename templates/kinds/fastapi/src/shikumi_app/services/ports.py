"""What the use cases need from the outside world. The adapters in repositories/ implement these."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from shikumi_app.domain.items import Item, NewItem


class ItemRepository(Protocol):
    def add(self, item: NewItem) -> Item: ...

    def list(self, limit: int) -> Sequence[Item]: ...

    def get(self, item_id: int) -> Item | None: ...

    def mark_processed(self, item_id: int, at: datetime) -> None: ...


class JobQueue(Protocol):
    def enqueue(self, kind: str, payload: dict[str, Any], dedupe_key: str | None = None) -> None: ...
