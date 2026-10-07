"""Wiring: how a request gets its database session and services."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from shikumi_app.repositories.items import SqlItemRepository
from shikumi_app.repositories.jobs import SqlJobQueue
from shikumi_app.services.items import ItemService


def get_session(request: Request) -> Iterator[Session]:
    """A session per request. Routes commit what they change; anything else is rolled back."""
    with request.app.state.sessions() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_item_service(session: SessionDep) -> ItemService:
    return ItemService(SqlItemRepository(session), SqlJobQueue(session))


ItemServiceDep = Annotated[ItemService, Depends(get_item_service)]
