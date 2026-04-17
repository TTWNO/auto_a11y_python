from collections.abc import Mapping, Sequence

import google.oauth2.credentials

class Flow:
    client_type: str
    client_config: Mapping[str, object]
    redirect_uri: str | None
    code_verifier: str | None
    autogenerate_code_verifier: bool

    def __init__(
        self,
        oauth2session: object,
        client_type: str,
        client_config: Mapping[str, object],
        redirect_uri: str | None = ...,
        code_verifier: str | None = ...,
        autogenerate_code_verifier: bool = ...,
    ) -> None: ...

    @classmethod
    def from_client_config(
        cls,
        client_config: Mapping[str, object],
        scopes: Sequence[str],
        **kwargs: object,
    ) -> Flow: ...

    @classmethod
    def from_client_secrets_file(
        cls,
        client_secrets_file: str,
        scopes: Sequence[str],
        **kwargs: object,
    ) -> Flow: ...

    def authorization_url(self, **kwargs: object) -> tuple[str, str]: ...

    def fetch_token(self, **kwargs: object) -> Mapping[str, str]: ...

    @property
    def credentials(self) -> google.oauth2.credentials.Credentials: ...
