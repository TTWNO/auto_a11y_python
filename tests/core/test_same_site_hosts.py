"""Tests for how the crawler decides two hosts are the same site.

The bug these pin down: ``www.example.com`` and ``example.com`` were compared
as unrelated hosts. A site registered under the host it redirects *away* from
therefore failed on its very first page -- the redirect check saw the apex
domain as external, recorded "Redirected to external domain", and since that
reason is classified as an expected skip, discovery ended with zero pages and
no error. Every link on such a site points at the other host too, so even a
page that survived would contribute nothing to the queue.

Apex/www redirects are near-universal, so this made whole sites look
unscrapable while being perfectly crawlable in a browser.
"""
from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol, cast

import pytest
from bson import ObjectId

from auto_a11y.core.database import Database
from auto_a11y.core.scraper import ScrapingEngine, is_internal_host
from auto_a11y.models.website import ScrapingConfig, Website


class TestIsInternalHost:
    """``is_internal_host`` backs every same-site decision in the crawler."""

    def test_www_and_apex_are_the_same_site(self) -> None:
        assert is_internal_host("workplacenl.ca", "www.workplacenl.ca", False) is True
        assert is_internal_host("www.workplacenl.ca", "workplacenl.ca", False) is True

    def test_identical_hosts_match(self) -> None:
        assert is_internal_host("example.com", "example.com", False) is True

    def test_host_comparison_ignores_case(self) -> None:
        assert is_internal_host("Example.COM", "example.com", False) is True

    def test_unrelated_host_is_external(self) -> None:
        assert is_internal_host("facebook.com", "workplacenl.ca", False) is False

    def test_suffix_lookalike_is_not_the_same_site(self) -> None:
        """``notworkplacenl.ca`` must not pass as ``workplacenl.ca``."""
        assert is_internal_host("notworkplacenl.ca", "workplacenl.ca", True) is False

    def test_sibling_domain_is_external(self) -> None:
        """A separate site is external even when its name is a near-miss."""
        assert is_internal_host("myworkplacenl.ca", "workplacenl.ca", True) is False

    def test_subdomain_requires_the_option(self) -> None:
        assert is_internal_host("secure.example.com", "example.com", False) is False
        assert is_internal_host("secure.example.com", "example.com", True) is True

    def test_subdomain_matches_across_www_base(self) -> None:
        """A base entered with www still recognises the site's subdomains."""
        assert is_internal_host("secure.example.com", "www.example.com", True) is True

    def test_port_is_part_of_the_host(self) -> None:
        assert is_internal_host("localhost:8080", "localhost:8080", False) is True
        assert is_internal_host("localhost:9090", "localhost:8080", False) is False


class _ExtractLinks(Protocol):
    """Typed signature of ``ScrapingEngine._extract_links``."""

    def __call__(
        self,
        page: object,
        current_url: str,
        website: Website,
        base_domain: str,
        base_path: str = ...,
    ) -> Awaitable[set[str]]: ...


class _FakePage:
    """A page whose anchors are supplied directly."""

    def __init__(self, hrefs: list[str]) -> None:
        self._hrefs = hrefs

    async def evaluate(self, _expression: str, *_a: object, **_k: object) -> object:
        return [{"href": h, "text": ""} for h in self._hrefs]


def _website(url: str, *, include_subdomains: bool) -> Website:
    site = Website(
        project_id="p",
        url=url,
        scraping_config=ScrapingConfig(include_subdomains=include_subdomains),
    )
    site.mongo_id = ObjectId()
    return site


async def _links(page: _FakePage, website: Website, base_domain: str) -> set[str]:
    engine = ScrapingEngine(cast(Database, object()), {})
    return await cast(_ExtractLinks, getattr(engine, "_extract_links"))(
        page, website.url, website, base_domain, ""
    )


@pytest.mark.asyncio
async def test_links_to_the_apex_are_followed_from_a_www_base() -> None:
    """The links on such a site all point at the other host; they must be queued."""
    website = _website("https://www.workplacenl.ca", include_subdomains=False)
    page = _FakePage([
        "https://workplacenl.ca/about/",
        "https://workplacenl.ca/contact/",
        "https://www.facebook.com/WorkplaceNL",
    ])

    found = await _links(page, website, "www.workplacenl.ca")

    assert found == {
        "https://workplacenl.ca/about",
        "https://workplacenl.ca/contact",
    }


@pytest.mark.asyncio
async def test_links_to_www_are_followed_from_an_apex_base() -> None:
    website = _website("https://workplacenl.ca", include_subdomains=False)
    page = _FakePage(["https://www.workplacenl.ca/about/"])

    found = await _links(page, website, "workplacenl.ca")

    assert found == {"https://www.workplacenl.ca/about"}


@pytest.mark.asyncio
async def test_genuinely_external_links_are_still_dropped() -> None:
    website = _website("https://workplacenl.ca", include_subdomains=True)
    page = _FakePage([
        "https://myworkplacenl.ca/services",
        "https://twitter.com/WorkplaceNL",
        "https://workplacenl.ca/workers/",
    ])

    found = await _links(page, website, "workplacenl.ca")

    assert found == {"https://workplacenl.ca/workers"}
