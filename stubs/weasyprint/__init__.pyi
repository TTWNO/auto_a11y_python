from collections.abc import Callable
from pathlib import Path
from typing import IO

class HTML:
    base_url: str | None
    media_type: str

    def __init__(
        self,
        guess: str | Path | None = ...,
        filename: str | Path | None = ...,
        url: str | None = ...,
        file_obj: IO[bytes] | None = ...,
        string: str | None = ...,
        encoding: str | None = ...,
        base_url: str | Path | None = ...,
        url_fetcher: object | None = ...,
        media_type: str = ...,
    ) -> None: ...

    def write_pdf(
        self,
        target: str | Path | IO[bytes] | None = ...,
        zoom: float = ...,
        finisher: Callable[..., object] | None = ...,
        font_config: object | None = ...,
        counter_style: object | None = ...,
        color_profiles: object | None = ...,
        **options: object,
    ) -> bytes | None: ...

    def render(
        self,
        font_config: object | None = ...,
        counter_style: object | None = ...,
        color_profiles: object | None = ...,
        **options: object,
    ) -> object: ...

class CSS:
    base_url: str | None

    def __init__(
        self,
        guess: str | Path | None = ...,
        filename: str | Path | None = ...,
        url: str | None = ...,
        file_obj: IO[bytes] | None = ...,
        string: str | None = ...,
        encoding: str | None = ...,
        base_url: str | Path | None = ...,
        url_fetcher: object | None = ...,
        _check_mime_type: bool = ...,
        media_type: str = ...,
        font_config: object | None = ...,
        counter_style: object | None = ...,
        color_profiles: object | None = ...,
        matcher: object | None = ...,
        page_rules: list[object] | None = ...,
        layers: list[object] | None = ...,
        layer: object | None = ...,
    ) -> None: ...
