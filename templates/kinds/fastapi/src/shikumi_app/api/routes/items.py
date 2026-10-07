from typing import Annotated

from fastapi import APIRouter, Query, status

from shikumi_app.api.deps import ItemServiceDep, SessionDep
from shikumi_app.api.schemas import ItemCreate, ItemOut

router = APIRouter(prefix="/items", tags=["items"])


@router.get("")
def list_items(service: ItemServiceDep, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[ItemOut]:
    return [ItemOut.model_validate(i) for i in service.list(limit)]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_item(body: ItemCreate, service: ItemServiceDep, session: SessionDep) -> ItemOut:
    item = service.create(body.name)
    session.commit()
    return ItemOut.model_validate(item)
