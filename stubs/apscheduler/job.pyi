from collections.abc import Callable
from datetime import datetime

class Job:
    id: str
    name: str
    next_run_time: datetime | None
    func: Callable[..., object]
    func_ref: str | None
    args: tuple[object, ...]
    kwargs: dict[str, object]
    coalesce: bool
    trigger: object
    executor: str
    misfire_grace_time: int | None
    max_instances: int

    @property
    def pending(self) -> bool: ...
    def modify(self, **changes: object) -> Job: ...
    def reschedule(self, trigger: object, **trigger_args: object) -> Job: ...
    def pause(self) -> Job: ...
    def resume(self) -> Job: ...
    def remove(self) -> None: ...
