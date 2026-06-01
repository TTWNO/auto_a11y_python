"""Round-trip tests for the Website model."""
from __future__ import annotations

from datetime import datetime

from auto_a11y.models.website import Website


def test_discovery_history_survives_round_trip() -> None:
    """discovery_history must persist through to_dict() -> from_dict()."""
    history: list[dict[str, object]] = [
        {
            'timestamp': datetime(2026, 4, 24, 12, 0, 0),
            'pages_found': 42,
            'method': 'crawl',
        },
        {
            'timestamp': datetime(2026, 4, 25, 9, 30, 0),
            'pages_found': 7,
            'method': 'sitemap',
        },
    ]
    website = Website(
        project_id='p1',
        url='https://example.com',
        name='Example',
        page_count=49,
        discovery_history=history,
    )

    round_tripped = Website.from_dict(website.to_dict())

    assert round_tripped.discovery_history == history
