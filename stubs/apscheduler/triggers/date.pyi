from datetime import datetime, tzinfo

class DateTrigger:
    run_date: datetime

    def __init__(
        self,
        run_date: datetime | str | None = ...,
        timezone: tzinfo | str | None = ...,
    ) -> None: ...

    def get_next_fire_time(
        self, previous_fire_time: datetime | None, now: datetime
    ) -> datetime | None: ...
