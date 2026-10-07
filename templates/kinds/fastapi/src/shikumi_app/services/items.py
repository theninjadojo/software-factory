import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from shikumi_app.domain.items import Item, NewItem
from shikumi_app.services.ports import ItemRepository, JobQueue

log = logging.getLogger(__name__)

PROCESS_ITEM = "process_item"


class ItemService:
    def __init__(
        self,
        items: ItemRepository,
        jobs: JobQueue,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.items, self.jobs, self.clock = items, jobs, clock

    def list(self, limit: int = 100) -> Sequence[Item]:
        return self.items.list(limit)

    def create(self, name: str) -> Item:
        item = self.items.add(NewItem(name=name.strip(), created_at=self.clock()))
        # Enqueued in the same transaction as the item: both are saved or neither is.
        self.jobs.enqueue(PROCESS_ITEM, {"item_id": item.id}, dedupe_key=f"{PROCESS_ITEM}:{item.id}")
        log.info("item created", extra={"item_id": item.id})
        return item

    def process(self, item_id: int) -> None:
        """The example job. Idempotent: running it twice for an item does nothing the second time."""
        item = self.items.get(item_id)
        if item is None or item.processed_at is not None:
            return
        self.items.mark_processed(item_id, self.clock())
        log.info("item processed", extra={"item_id": item_id})
