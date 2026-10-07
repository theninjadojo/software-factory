from dataclasses import dataclass
from datetime import datetime

MAX_NAME = 200


class InvalidItemError(ValueError):
    pass


@dataclass(frozen=True)
class NewItem:
    name: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvalidItemError("an item needs a name")
        if len(self.name) > MAX_NAME:
            raise InvalidItemError(f"an item's name is at most {MAX_NAME} characters")


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    created_at: datetime
    processed_at: datetime | None = None
