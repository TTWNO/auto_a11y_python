"""
Tests for the Drupal misc code-audit fixes.

Covers:
  A. client.get() RequestException handler must not raise AttributeError when
     the exception has no usable `response` attribute.
  B. Pagination loops in taxonomy.py / discovered_page_importer.py must
     terminate at a hard cap even if the server ignores the offset.
  C. _convert_drupal_page must handle field_public_note_on_page as either a
     dict or a list of value objects.
  D. DiscoveredPageExporter.batch_export must route by isinstance, not hasattr.
"""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import MagicMock, patch

from typing_extensions import override

import pytest
import requests

from auto_a11y.drupal.client import DrupalJSONAPIClient, DrupalJSONAPIError
from auto_a11y.drupal.taxonomy import TaxonomyCache
from auto_a11y.drupal.discovered_page_importer import DiscoveredPageImporter
from auto_a11y.drupal.discovered_page_exporter import DiscoveredPageExporter
from auto_a11y.models.page import Page
from auto_a11y.models.discovered_page import DiscoveredPage


# ---------------------------------------------------------------------------
# Bug A: client.get error handler must not access e.response unguarded
# ---------------------------------------------------------------------------

class _ResponselessError(requests.exceptions.RequestException):
    """A RequestException whose ``response`` access raises, mimicking a bare
    connection-level error where touching ``e.response`` masks the original.

    ``hasattr(e, 'response')`` returns False for such an exception (hasattr
    swallows the AttributeError), so the guarded handler must short-circuit.
    """

    @override
    def __getattribute__(self, name: str) -> Any:
        if name == "response":
            raise AttributeError("response is unavailable on a bare connection error")
        return super().__getattribute__(name)


def test_get_handles_request_exception_without_usable_response() -> None:
    """A bare connection-level RequestException (where touching ``response``
    raises) must be converted to DrupalJSONAPIError, not AttributeError."""
    client = DrupalJSONAPIClient("https://example.com", "user", "pass")

    with patch.object(client.session, "get", side_effect=_ResponselessError("boom")):
        with pytest.raises(DrupalJSONAPIError):
            client.get("node/discovered_page")


def test_get_handles_request_exception_with_none_response() -> None:
    """RequestException with response=None must be handled gracefully."""
    client = DrupalJSONAPIClient("https://example.com", "user", "pass")
    exc = requests.exceptions.RequestException("boom")

    with patch.object(client.session, "get", side_effect=exc):
        with pytest.raises(DrupalJSONAPIError):
            client.get("node/discovered_page")


# ---------------------------------------------------------------------------
# Bug B: pagination loops must terminate at a hard cap
# ---------------------------------------------------------------------------

def test_taxonomy_refresh_terminates_on_runaway_server() -> None:
    """_refresh_vocabulary must stop even if every page is full (server
    ignoring page[offset])."""
    client = MagicMock()

    def _get(endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        data = [
            {"id": f"uuid-{i}", "attributes": {"name": f"term-{i}"}}
            for i in range(50)
        ]
        return {"data": data}

    client.get.side_effect = _get

    cache = TaxonomyCache(client)
    cache.get_terms("interested_in_because")

    assert 1 < client.get.call_count < 10_000


def test_discovered_page_importer_terminates_on_runaway_server() -> None:
    """fetch_discovered_pages_for_audit must stop even if every page is full."""
    client = MagicMock()

    def _get(endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        data = [
            {"id": f"uuid-{i}", "attributes": {"title": f"t{i}"}, "relationships": {}}
            for i in range(50)
        ]
        return {"data": data, "included": []}

    client.get.side_effect = _get
    taxonomies = MagicMock()
    taxonomies.lookup_interested_because_names.return_value = []
    taxonomies.lookup_page_elements_names.return_value = []

    importer = DiscoveredPageImporter(client, taxonomies)
    importer.fetch_discovered_pages_for_audit("audit-uuid")

    assert 1 < client.get.call_count < 10_000


# ---------------------------------------------------------------------------
# Bug C: field_public_note_on_page may be a dict OR a list
# ---------------------------------------------------------------------------

def _make_importer() -> DiscoveredPageImporter:
    taxonomies = MagicMock()
    taxonomies.lookup_interested_because_names.return_value = []
    taxonomies.lookup_page_elements_names.return_value = []
    return DiscoveredPageImporter(MagicMock(), taxonomies)


def _convert(importer: DiscoveredPageImporter, page_data: dict[str, Any]) -> dict[str, Any]:
    """Call the protected ``_convert_drupal_page`` without tripping reportPrivateUsage."""
    method: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]] = getattr(
        importer, "_convert_drupal_page"
    )
    return method(page_data, [])


def test_convert_page_public_note_as_list() -> None:
    importer = _make_importer()
    page_data: dict[str, Any] = {
        "id": "uuid-1",
        "attributes": {
            "title": "Test",
            "field_public_note_on_page": [{"value": "hello note"}],
        },
        "relationships": {},
    }
    result = _convert(importer, page_data)
    assert result["public_notes"] == "hello note"


def test_convert_page_public_note_as_dict() -> None:
    importer = _make_importer()
    page_data: dict[str, Any] = {
        "id": "uuid-1",
        "attributes": {
            "title": "Test",
            "field_public_note_on_page": {"value": "hello note"},
        },
        "relationships": {},
    }
    result = _convert(importer, page_data)
    assert result["public_notes"] == "hello note"


def test_convert_page_public_note_missing() -> None:
    importer = _make_importer()
    page_data: dict[str, Any] = {
        "id": "uuid-1",
        "attributes": {"title": "Test"},
        "relationships": {},
    }
    result = _convert(importer, page_data)
    assert result["public_notes"] is None


# ---------------------------------------------------------------------------
# Bug D: batch_export must route by isinstance, not hasattr
# ---------------------------------------------------------------------------

def test_batch_export_routes_by_isinstance() -> None:
    exporter = DiscoveredPageExporter(MagicMock(), MagicMock())

    page = Page(website_id="w1", url="https://example.com/p")
    discovered = DiscoveredPage(title="D", url="https://example.com/d", project_id="p1")

    with patch.object(
        exporter, "export_from_page_model", return_value={"success": True}
    ) as page_mock, patch.object(
        exporter, "export_from_discovered_page_model", return_value={"success": True}
    ) as discovered_mock:
        exporter.batch_export([page, discovered], "audit-uuid")

    page_mock.assert_called_once()
    assert page_mock.call_args.args[0] is page
    discovered_mock.assert_called_once()
    assert discovered_mock.call_args.args[0] is discovered
