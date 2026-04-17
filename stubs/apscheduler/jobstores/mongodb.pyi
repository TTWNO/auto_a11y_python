class MongoDBJobStore:
    def __init__(
        self,
        database: str = ...,
        collection: str = ...,
        client: object | None = ...,
        pickle_protocol: int = ...,
        **connect_args: object,
    ) -> None: ...
    def shutdown(self) -> None: ...
