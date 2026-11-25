from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    status: Literal["ok"]
    service: Literal["vibeos"]
    version: ShortText


class Component(StrictModel):
    id: ShortText
    type: Literal["heading", "text", "input", "button", "counter"]
    label: ShortText


class Definition(StrictModel):
    name: ShortText
    components: list[Component] = Field(min_length=1, max_length=50)


class Credentials(StrictModel):
    username: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_]{3,40}$")]
    password: Annotated[str, StringConstraints(min_length=10, max_length=200)]


class ShareRequest(StrictModel):
    recipient: ShortText


class StateRequest(StrictModel):
    values: dict[str, str | int] = Field(max_length=50)
