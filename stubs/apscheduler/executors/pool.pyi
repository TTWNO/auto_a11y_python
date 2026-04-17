class ThreadPoolExecutor:
    def __init__(
        self,
        max_workers: int = ...,
        pool_kwargs: dict[str, object] | None = ...,
    ) -> None: ...
    def shutdown(self, wait: bool = ...) -> None: ...
