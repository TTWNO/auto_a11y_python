from datetime import datetime, tzinfo

class CronTrigger:
    timezone: tzinfo
    start_date: datetime | None
    end_date: datetime | None
    jitter: int | None

    def __init__(
        self,
        year: int | str | None = ...,
        month: int | str | None = ...,
        day: int | str | None = ...,
        week: int | str | None = ...,
        day_of_week: int | str | None = ...,
        hour: int | str | None = ...,
        minute: int | str | None = ...,
        second: int | str | None = ...,
        start_date: datetime | str | None = ...,
        end_date: datetime | str | None = ...,
        timezone: tzinfo | str | None = ...,
        jitter: int | None = ...,
    ) -> None: ...

    @classmethod
    def from_crontab(
        cls, expr: str, timezone: tzinfo | str | None = ...
    ) -> CronTrigger: ...

    def get_next_fire_time(
        self, previous_fire_time: datetime | None, now: datetime
    ) -> datetime | None: ...
