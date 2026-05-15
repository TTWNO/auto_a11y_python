"""OpenAPI 3.1 generation infrastructure for the /api/v1/* surface."""
from auto_a11y.web.api.openapi.registry import (
    EndpointDoc,
    REGISTRY,
    register,
    registry_snapshot,
)

__all__ = ["EndpointDoc", "REGISTRY", "register", "registry_snapshot"]
