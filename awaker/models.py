from typing import NotRequired, TypedDict

type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)


class Job(TypedDict):
    handle: str
    runs_on: NotRequired[list[str]]
    status: NotRequired[str]
    id: NotRequired[int]
    repo_id: NotRequired[int]
    repository: NotRequired[str]


class RunnerRegistration(TypedDict):
    id: int
    uuid: str
    token: str


class RegisteredRunner(TypedDict):
    id: int
    name: str


class StateEntry(TypedDict):
    name: str
    url: str
    repo: str
    handle: str
    runner_id: int | None
    container_name: str
    pending: int
    released: int
    runner_scope: str
