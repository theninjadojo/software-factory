"""What the API accepts and returns. These shape the OpenAPI schema that clients are generated from."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from shikumi_app.domain.items import MAX_NAME


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME, examples=["First item"])


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    processed_at: datetime | None


class Status(BaseModel):
    status: str
