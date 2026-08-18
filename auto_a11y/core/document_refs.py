"""Recording document references from a page's links, outside a crawl.

The crawler classifies documents as it walks a site. Two other moments produce
the same information and used to discard it: a page added by hand, which is
never crawled, and a page being tested, whose links may include documents that
did not exist at discovery time.

Both reuse the crawler's classification — same extension table, same host test —
so a document found here is indistinguishable from one found by the crawler, and
the project's PDF counts stay true regardless of how a page entered the project.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from auto_a11y.core.scraper import (
    DOCUMENT_EXTENSIONS,
    document_extension,
    is_internal_host,
)
from auto_a11y.models import DocumentReference

if TYPE_CHECKING:
    from auto_a11y.core.database import Database
    from auto_a11y.models import Website

logger = logging.getLogger(__name__)


def record_document_references(
    database: "Database",
    website: "Website",
    referring_page_url: str,
    hrefs: list[tuple[str, str | None]],
) -> int:
    """Record any document links found on a page.

    Args:
        database: Database instance (``add_document_reference``).
        website: Website whose base URL decides hosted-vs-external.
        referring_page_url: The page the links were found on.
        hrefs: ``(absolute_url, link_text)`` pairs from that page.

    Returns:
        Number of document references recorded or refreshed.
    """
    website_id = website.id
    base_url = website.url
    if not website_id or not base_url:
        return 0

    base_domain = urlparse(base_url).netloc
    include_subdomains = bool(
        getattr(website.scraping_config, 'include_subdomains', False)
    )

    seen: set[str] = set()
    recorded = 0
    for url, link_text in hrefs:
        if not url or url in seen:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            continue
        ext = document_extension(parsed.path)
        if ext is None:
            continue
        seen.add(url)
        try:
            # add_document_reference dedupes on (website_id, document_url) and
            # appends to referring_pages, so re-testing a page is idempotent.
            database.add_document_reference(
                DocumentReference(
                    website_id=str(website_id),
                    document_url=url,
                    referring_page_url=referring_page_url,
                    mime_type=DOCUMENT_EXTENSIONS[ext],
                    is_internal=is_internal_host(
                        parsed.netloc, base_domain, include_subdomains
                    ),
                    link_text=link_text,
                    file_extension=ext,
                )
            )
            recorded += 1
        except Exception as exc:  # noqa: BLE001 — one bad link must not fail the caller
            logger.debug("Could not record document reference %s: %s", url, exc)
    return recorded
