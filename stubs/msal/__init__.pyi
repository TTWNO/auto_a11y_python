from collections.abc import Mapping

class ClientApplication:
    client_id: str
    client_credential: str | dict[str, str] | None
    authority: object

    def __init__(
        self,
        client_id: str,
        client_credential: str | dict[str, str] | None = ...,
        authority: str | None = ...,
        validate_authority: bool = ...,
        token_cache: object | None = ...,
        http_client: object | None = ...,
        verify: bool = ...,
        proxies: dict[str, str] | None = ...,
        timeout: float | tuple[float, float] | None = ...,
        client_claims: dict[str, str] | None = ...,
        app_name: str | None = ...,
        app_version: str | None = ...,
        client_capabilities: list[str] | None = ...,
        azure_region: str | bool | None = ...,
        exclude_scopes: list[str] | None = ...,
        http_cache: dict[str, object] | None = ...,
        instance_discovery: bool | None = ...,
        allow_broker: bool | None = ...,
        enable_pii_log: bool | None = ...,
        oidc_authority: str | None = ...,
    ) -> None: ...

    def get_authorization_request_url(
        self,
        scopes: list[str],
        login_hint: str | None = ...,
        state: str | None = ...,
        redirect_uri: str | None = ...,
        response_type: str = ...,
        prompt: str | None = ...,
        nonce: str | None = ...,
        domain_hint: str | None = ...,
        claims_challenge: str | None = ...,
        **kwargs: object,
    ) -> str: ...

    def acquire_token_by_authorization_code(
        self,
        code: str,
        scopes: list[str],
        redirect_uri: str | None = ...,
        nonce: str | None = ...,
        claims_challenge: str | None = ...,
        **kwargs: object,
    ) -> dict[str, str]: ...

class ConfidentialClientApplication(ClientApplication):
    def acquire_token_for_client(
        self,
        scopes: list[str],
        claims_challenge: str | None = ...,
        **kwargs: object,
    ) -> dict[str, str]: ...

class PublicClientApplication(ClientApplication): ...
