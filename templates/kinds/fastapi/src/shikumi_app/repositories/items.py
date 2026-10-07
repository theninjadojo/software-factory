from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shikumi_app.domain.items import Item, NewItem
from shikumi_app.repositories.tables import ItemRow


def _item(row: ItemRow) -> Item:
    return Item(id=row.id, name=row.name, created_at=row.created_at, processed_at=row.processed_at)


class SqlItemRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, item: NewItem) -> Item:
        row = ItemRow(name=item.name, created_at=item.created_at)
        self.session.add(row)
        self.session.flush()  # assigns the id
        return _item(row)

    def list(self, limit: int) -> Sequence[Item]:
        rows = self.session.scalars(select(ItemRow).order_by(ItemRow.id.desc()).limit(limit))
        return [_item(r) for r in rows]

    def get(self, item_id: int) -> Item | None:
        row = self.session.get(ItemRow, item_id)
        return _item(row) if row else None

    def mark_processed(self, item_id: int, at: datetime) -> None:
        self.session.execute(update(ItemRow).where(ItemRow.id == item_id).values(processed_at=at))
