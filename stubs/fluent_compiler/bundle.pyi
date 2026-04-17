from collections.abc import Callable, Sequence

class FluentBundle:
    locale: str

    def __init__(
        self,
        locale: str,
        resources: Sequence[object],
        functions: dict[str, Callable[..., object]] | None = ...,
        use_isolating: bool = ...,
        escapers: object | None = ...,
    ) -> None: ...

    @classmethod
    def from_string(
        cls,
        locale: str,
        text: str,
        functions: dict[str, Callable[..., object]] | None = ...,
        use_isolating: bool = ...,
        escapers: object | None = ...,
    ) -> FluentBundle: ...

    @classmethod
    def from_files(
        cls,
        locale: str,
        filenames: Sequence[str],
        functions: dict[str, Callable[..., object]] | None = ...,
        use_isolating: bool = ...,
        escapers: object | None = ...,
    ) -> FluentBundle: ...

    def has_message(self, message_id: str) -> bool: ...
    def format(
        self, message_id: str, args: dict[str, object] | None = ...
    ) -> tuple[str, list[Exception]]: ...
    def check_messages(self) -> list[object]: ...
