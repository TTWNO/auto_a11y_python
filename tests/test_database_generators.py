"""Tests for database generator methods (streaming report support)."""
from __future__ import annotations

import os
os.environ.setdefault('RUN_AI_ANALYSIS', 'false')

from typing import Any

import pytest
from unittest.mock import MagicMock, patch
from bson import ObjectId
from datetime import datetime

from auto_a11y.models.page import Page
from auto_a11y.models.website import Website


def _make_db() -> Any:
    """Create a Database instance with mocked MongoDB connection."""
    with patch('auto_a11y.core.database.MongoClient'):
        from auto_a11y.core.database import Database
        db = Database('mongodb://localhost:27017', 'test_db')
    return db


def _page_doc(website_id: str = 'ws1', url: str = 'https://example.com',
              **overrides: Any) -> dict[str, Any]:
    """Create a minimal page document dict."""
    doc: dict[str, Any] = {
        '_id': ObjectId(),
        'website_id': website_id,
        'url': url,
        'status': 'discovered',
        'is_in_latest_discovery': True,
    }
    doc.update(overrides)
    return doc


def _website_doc(project_id: str = 'proj1', url: str = 'https://example.com',
                 **overrides: Any) -> dict[str, Any]:
    """Create a minimal website document dict."""
    doc: dict[str, Any] = {
        '_id': ObjectId(),
        'project_id': project_id,
        'url': url,
    }
    doc.update(overrides)
    return doc


# --- yield_pages ---

class TestYieldPages:
    def test_yields_page_objects(self) -> None:
        db = _make_db()
        docs = [_page_doc(), _page_doc(url='https://example.com/about')]
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter(docs))
        db.pages.find.return_value = cursor
        cursor.sort.return_value = cursor

        pages = list(db.yield_pages('ws1'))

        assert len(pages) == 2
        assert all(isinstance(p, Page) for p in pages)
        assert pages[0].url == 'https://example.com'
        assert pages[1].url == 'https://example.com/about'

    def test_no_cursor_timeout(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.pages.find.return_value = cursor
        cursor.sort.return_value = cursor

        list(db.yield_pages('ws1'))

        db.pages.find.assert_called_once()
        call_kwargs = db.pages.find.call_args
        assert call_kwargs[1].get('no_cursor_timeout') is True

    def test_cursor_closed_in_finally(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.pages.find.return_value = cursor
        cursor.sort.return_value = cursor

        list(db.yield_pages('ws1'))

        cursor.close.assert_called_once()

    def test_cursor_closed_on_exception(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(side_effect=RuntimeError("boom"))
        db.pages.find.return_value = cursor
        cursor.sort.return_value = cursor

        with pytest.raises(RuntimeError):
            list(db.yield_pages('ws1'))

        cursor.close.assert_called_once()

    def test_latest_only_default_filter(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.pages.find.return_value = cursor
        cursor.sort.return_value = cursor

        list(db.yield_pages('ws1'))

        query = db.pages.find.call_args[0][0]
        assert query['is_in_latest_discovery'] is True

    def test_latest_only_false_omits_filter(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.pages.find.return_value = cursor
        cursor.sort.return_value = cursor

        list(db.yield_pages('ws1', latest_only=False))

        query = db.pages.find.call_args[0][0]
        assert 'is_in_latest_discovery' not in query

    def test_sort_parameters(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.pages.find.return_value = cursor
        cursor.sort.return_value = cursor

        list(db.yield_pages('ws1', sort_field='title', sort_order=-1))

        cursor.sort.assert_called_once_with('title', -1)

    def test_empty_results(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.pages.find.return_value = cursor
        cursor.sort.return_value = cursor

        pages = list(db.yield_pages('ws1'))

        assert pages == []


# --- yield_websites ---

class TestYieldWebsites:
    def test_yields_website_objects(self) -> None:
        db = _make_db()
        docs = [_website_doc(), _website_doc(url='https://other.com')]
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter(docs))
        db.websites.find.return_value = cursor

        websites = list(db.yield_websites('proj1'))

        assert len(websites) == 2
        assert all(isinstance(w, Website) for w in websites)
        assert websites[0].url == 'https://example.com'
        assert websites[1].url == 'https://other.com'

    def test_no_cursor_timeout(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.websites.find.return_value = cursor

        list(db.yield_websites('proj1'))

        call_kwargs = db.websites.find.call_args
        assert call_kwargs[1].get('no_cursor_timeout') is True

    def test_cursor_closed_in_finally(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.websites.find.return_value = cursor

        list(db.yield_websites('proj1'))

        cursor.close.assert_called_once()

    def test_cursor_closed_on_exception(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(side_effect=RuntimeError("boom"))
        db.websites.find.return_value = cursor

        with pytest.raises(RuntimeError):
            list(db.yield_websites('proj1'))

        cursor.close.assert_called_once()

    def test_filters_by_project_id(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.websites.find.return_value = cursor

        list(db.yield_websites('proj42'))

        query = db.websites.find.call_args[0][0]
        assert query == {"project_id": "proj42"}

    def test_empty_results(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.websites.find.return_value = cursor

        websites = list(db.yield_websites('proj1'))

        assert websites == []


# --- yield_test_result_items ---

class TestYieldTestResultItems:
    def test_yields_raw_dicts(self) -> None:
        db = _make_db()
        items: list[dict[str, Any]] = [
            {'_id': ObjectId(), 'test_result_id': 'tr1', 'item_type': 'violation', 'description': 'No alt'},
            {'_id': ObjectId(), 'test_result_id': 'tr1', 'item_type': 'warning', 'description': 'Low contrast'},
        ]
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter(items))
        db.test_result_items.find.return_value = cursor

        results = list(db.yield_test_result_items('tr1'))

        assert len(results) == 2
        assert results[0]['description'] == 'No alt'
        assert results[1]['item_type'] == 'warning'

    def test_no_cursor_timeout(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.test_result_items.find.return_value = cursor

        list(db.yield_test_result_items('tr1'))

        call_kwargs = db.test_result_items.find.call_args
        assert call_kwargs[1].get('no_cursor_timeout') is True

    def test_cursor_closed_in_finally(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.test_result_items.find.return_value = cursor

        list(db.yield_test_result_items('tr1'))

        cursor.close.assert_called_once()

    def test_cursor_closed_on_exception(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(side_effect=RuntimeError("boom"))
        db.test_result_items.find.return_value = cursor

        with pytest.raises(RuntimeError):
            list(db.yield_test_result_items('tr1'))

        cursor.close.assert_called_once()

    def test_item_type_filter_applied(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.test_result_items.find.return_value = cursor

        list(db.yield_test_result_items('tr1', item_type='violation'))

        query = db.test_result_items.find.call_args[0][0]
        assert query == {'test_result_id': 'tr1', 'item_type': 'violation'}

    def test_no_item_type_filter_when_none(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.test_result_items.find.return_value = cursor

        list(db.yield_test_result_items('tr1'))

        query = db.test_result_items.find.call_args[0][0]
        assert query == {'test_result_id': 'tr1'}
        assert 'item_type' not in query

    def test_empty_results(self) -> None:
        db = _make_db()
        cursor = MagicMock()
        cursor.__iter__ = MagicMock(return_value=iter([]))
        db.test_result_items.find.return_value = cursor

        results = list(db.yield_test_result_items('tr1'))

        assert results == []


# --- get_latest_test_result_summary ---

class TestGetLatestTestResultSummary:
    def test_returns_summary_dict(self) -> None:
        db = _make_db()
        doc_id = ObjectId()
        test_date = datetime(2025, 6, 15)
        db.test_results.find_one.return_value = {
            '_id': doc_id,
            'page_id': 'page1',
            'violation_count': 5,
            'warning_count': 3,
            'info_count': 1,
            'discovery_count': 2,
            'pass_count': 10,
            'test_date': test_date,
            'score': 85.0,
        }

        summary = db.get_latest_test_result_summary('page1')

        assert summary is not None
        assert summary['id'] == str(doc_id)
        assert summary['page_id'] == 'page1'
        assert summary['violation_count'] == 5
        assert summary['warning_count'] == 3
        assert summary['info_count'] == 1
        assert summary['discovery_count'] == 2
        assert summary['pass_count'] == 10
        assert summary['test_date'] == test_date
        assert summary['score'] == 85.0

    def test_returns_none_when_no_result(self) -> None:
        db = _make_db()
        db.test_results.find_one.return_value = None

        summary = db.get_latest_test_result_summary('page1')

        assert summary is None

    def test_defaults_missing_counts_to_zero(self) -> None:
        db = _make_db()
        doc_id = ObjectId()
        db.test_results.find_one.return_value = {
            '_id': doc_id,
            'page_id': 'page1',
            # No count fields present
        }

        summary = db.get_latest_test_result_summary('page1')

        assert summary['violation_count'] == 0
        assert summary['warning_count'] == 0
        assert summary['info_count'] == 0
        assert summary['discovery_count'] == 0
        assert summary['pass_count'] == 0
        assert summary['test_date'] is None
        assert summary['score'] is None

    def test_queries_with_sort_by_test_date(self) -> None:
        db = _make_db()
        db.test_results.find_one.return_value = None

        db.get_latest_test_result_summary('page1')

        db.test_results.find_one.assert_called_once_with(
            {"page_id": "page1"},
            sort=[("test_date", -1)]
        )
