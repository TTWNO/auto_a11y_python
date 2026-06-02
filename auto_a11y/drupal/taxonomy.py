"""
Drupal Taxonomy Management

Handles caching and lookup of Drupal taxonomy terms, particularly:
- "interested_in_because" - Why pages are interesting (75 terms)
- "page_elements" - Areas of display (16 terms)
"""

from __future__ import annotations

import logging
from typing import Any
from datetime import datetime, timedelta

from auto_a11y.drupal.client import DrupalJSONAPIClient

logger = logging.getLogger(__name__)


class TaxonomyCache:
    """
    Cache for Drupal taxonomy terms with automatic refresh.

    Caches taxonomy terms by vocabulary and provides efficient lookup by name or UUID.
    """

    def __init__(self, client: DrupalJSONAPIClient, cache_duration_hours: int = 24) -> None:
        """
        Initialize taxonomy cache.

        Args:
            client: DrupalJSONAPIClient instance
            cache_duration_hours: How long to cache terms before refresh (default: 24 hours)
        """
        self.client: DrupalJSONAPIClient = client
        self.cache_duration: timedelta = timedelta(hours=cache_duration_hours)

        # Cache structure: {vocabulary_name: {'terms': [...], 'last_updated': datetime, 'by_name': {}, 'by_uuid': {}}}
        self._cache: dict[str, dict[str, Any]] = {}

    def get_terms(self, vocabulary: str, force_refresh: bool = False) -> list[dict[str, Any]]:
        """
        Get all terms for a vocabulary.

        Args:
            vocabulary: Vocabulary machine name (e.g., "interested_in_because")
            force_refresh: Force refresh from Drupal even if cached

        Returns:
            List of term dictionaries with 'uuid', 'name', 'tid', 'weight', etc.
        """
        # Check if we need to refresh
        if force_refresh or vocabulary not in self._cache or self._is_cache_expired(vocabulary):
            self._refresh_vocabulary(vocabulary)

        cache_entry = self._cache.get(vocabulary, {})
        terms: list[dict[str, Any]] = cache_entry.get('terms', [])
        return terms

    def get_uuid_by_name(self, vocabulary: str, term_name: str) -> str | None:
        """
        Look up a term UUID by its name.

        Args:
            vocabulary: Vocabulary machine name
            term_name: Term name to look up (case-insensitive)

        Returns:
            Term UUID or None if not found
        """
        # Ensure vocabulary is cached
        if vocabulary not in self._cache:
            self._refresh_vocabulary(vocabulary)

        by_name: dict[str, str] = self._cache.get(vocabulary, {}).get('by_name', {})
        return by_name.get(term_name.lower())

    def get_name_by_uuid(self, vocabulary: str, term_uuid: str) -> str | None:
        """
        Look up a term name by its UUID.

        Args:
            vocabulary: Vocabulary machine name
            term_uuid: Term UUID to look up

        Returns:
            Term name or None if not found
        """
        # Ensure vocabulary is cached
        if vocabulary not in self._cache:
            self._refresh_vocabulary(vocabulary)

        by_uuid: dict[str, str] = self._cache.get(vocabulary, {}).get('by_uuid', {})
        return by_uuid.get(term_uuid)

    def get_term_details(self, vocabulary: str, term_name: str) -> dict[str, Any] | None:
        """
        Get full term details by name.

        Args:
            vocabulary: Vocabulary machine name
            term_name: Term name to look up

        Returns:
            Full term dictionary or None if not found
        """
        # Ensure vocabulary is cached
        if vocabulary not in self._cache:
            self._refresh_vocabulary(vocabulary)

        terms: list[dict[str, Any]] = self._cache.get(vocabulary, {}).get('terms', [])
        term_name_lower = term_name.lower()

        for term in terms:
            if term.get('name', '').lower() == term_name_lower:
                return term

        return None

    def lookup_uuids(self, vocabulary: str, term_names: list[str]) -> list[str]:
        """
        Look up UUIDs for multiple term names.

        Args:
            vocabulary: Vocabulary machine name
            term_names: List of term names to look up

        Returns:
            List of UUIDs (skips terms not found, logs warning)
        """
        uuids: list[str] = []

        for name in term_names:
            uuid = self.get_uuid_by_name(vocabulary, name)
            if uuid:
                uuids.append(uuid)
            else:
                logger.warning(f"Term '{name}' not found in vocabulary '{vocabulary}'")

        return uuids

    def lookup_names(self, vocabulary: str, term_uuids: list[str]) -> list[str]:
        """
        Look up names for multiple term UUIDs.

        Args:
            vocabulary: Vocabulary machine name
            term_uuids: List of term UUIDs to look up

        Returns:
            List of term names (skips UUIDs not found, logs warning)
        """
        names: list[str] = []

        for uuid in term_uuids:
            name = self.get_name_by_uuid(vocabulary, uuid)
            if name:
                names.append(name)
            else:
                logger.warning(f"UUID '{uuid}' not found in vocabulary '{vocabulary}'")

        return names

    def refresh_all(self) -> None:
        """Refresh all cached vocabularies."""
        vocabularies = list(self._cache.keys())
        for vocab in vocabularies:
            self._refresh_vocabulary(vocab)

    def clear_cache(self) -> None:
        """Clear all cached taxonomy data."""
        self._cache.clear()
        logger.info("Taxonomy cache cleared")

    def _is_cache_expired(self, vocabulary: str) -> bool:
        """Check if cached vocabulary has expired."""
        if vocabulary not in self._cache:
            return True

        last_updated: datetime | None = self._cache[vocabulary].get('last_updated')
        if not last_updated:
            return True

        return datetime.now() - last_updated > self.cache_duration

    def _refresh_vocabulary(self, vocabulary: str) -> None:
        """
        Refresh a vocabulary from Drupal.

        Args:
            vocabulary: Vocabulary machine name
        """
        logger.info(f"Refreshing taxonomy vocabulary: {vocabulary}")

        try:
            # Fetch all terms for this vocabulary
            # Use pagination to get all terms (some vocabularies have 75+ terms)
            all_terms: list[dict[str, Any]] = []
            page_limit = 50
            offset = 0
            # Hard backstop: a server that ignores page[offset] would otherwise
            # loop forever returning full pages. Cap the total pages fetched.
            max_pages = 1000

            for _ in range(max_pages):
                # Use client.get() which now properly handles bracket encoding
                response = self.client.get(
                    f'taxonomy_term/{vocabulary}',
                    params={
                        'page[limit]': page_limit,
                        'page[offset]': offset,
                        'sort': 'weight,name'
                    }
                )

                terms: list[dict[str, Any]] = response.get('data', [])
                if not terms:
                    break

                all_terms.extend(terms)
                offset += page_limit

                # If we got fewer than page_limit, we're done
                if len(terms) < page_limit:
                    break
            else:
                logger.warning(
                    f"Pagination cap ({max_pages} pages) reached refreshing"
                    + f" vocabulary '{vocabulary}'; results may be truncated."
                )

            # Process terms into cache structure
            by_name: dict[str, str] = {}
            by_uuid: dict[str, str] = {}
            processed_terms: list[dict[str, Any]] = []

            for term in all_terms:
                uuid: str | None = term.get('id')
                attributes: dict[str, Any] = term.get('attributes', {})
                name: str | None = attributes.get('name')
                tid: int | None = attributes.get('drupal_internal__tid')
                weight: int = attributes.get('weight', 0)
                desc_field: dict[str, Any] | None = attributes.get('description')
                description: str = desc_field.get('value', '') if desc_field else ''

                if not uuid or not name:
                    logger.warning(f"Skipping invalid term in {vocabulary}: {term}")
                    continue

                # Store in lookup maps (case-insensitive for names)
                by_name[name.lower()] = uuid
                by_uuid[uuid] = name

                # Store full term details
                processed_terms.append({
                    'uuid': uuid,
                    'name': name,
                    'tid': tid,
                    'weight': weight,
                    'description': description
                })

            # Update cache
            self._cache[vocabulary] = {
                'terms': processed_terms,
                'by_name': by_name,
                'by_uuid': by_uuid,
                'last_updated': datetime.now()
            }

            logger.info(f"Cached {len(processed_terms)} terms for vocabulary '{vocabulary}'")

        except Exception as e:
            logger.error(f"Failed to refresh vocabulary '{vocabulary}': {e}")
            raise


class DiscoveredPageTaxonomies:
    """
    High-level interface for discovered page taxonomies.

    Provides convenient access to the two main taxonomies used for discovered pages:
    - interested_in_because (75 terms)
    - page_elements (16 terms)
    """

    # Vocabulary machine names
    INTERESTED_BECAUSE: str = "interested_in_because"
    PAGE_ELEMENTS: str = "page_elements"

    def __init__(self, client: DrupalJSONAPIClient) -> None:
        """
        Initialize taxonomy manager.

        Args:
            client: DrupalJSONAPIClient instance
        """
        self.cache: TaxonomyCache = TaxonomyCache(client)

    def get_interested_because_terms(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """
        Get all "interested because" terms.

        Returns:
            List of term dicts with 'uuid', 'name', 'tid', 'weight', 'description'
        """
        return self.cache.get_terms(self.INTERESTED_BECAUSE, force_refresh)

    def get_page_elements_terms(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """
        Get all "page elements" (area of display) terms.

        Returns:
            List of term dicts with 'uuid', 'name', 'tid', 'weight', 'description'
        """
        return self.cache.get_terms(self.PAGE_ELEMENTS, force_refresh)

    def lookup_interested_because_uuids(self, term_names: list[str]) -> list[str]:
        """
        Convert "interested because" term names to UUIDs.

        Args:
            term_names: List of term names (e.g., ["Form", "Date Picker or Calendar"])

        Returns:
            List of UUIDs
        """
        return self.cache.lookup_uuids(self.INTERESTED_BECAUSE, term_names)

    def lookup_page_elements_uuids(self, term_names: list[str]) -> list[str]:
        """
        Convert "page elements" term names to UUIDs.

        Args:
            term_names: List of term names (e.g., ["Header", "Main body"])

        Returns:
            List of UUIDs
        """
        return self.cache.lookup_uuids(self.PAGE_ELEMENTS, term_names)

    def lookup_interested_because_names(self, term_uuids: list[str]) -> list[str]:
        """
        Convert "interested because" UUIDs to term names.

        Args:
            term_uuids: List of term UUIDs

        Returns:
            List of term names
        """
        return self.cache.lookup_names(self.INTERESTED_BECAUSE, term_uuids)

    def lookup_page_elements_names(self, term_uuids: list[str]) -> list[str]:
        """
        Convert "page elements" UUIDs to term names.

        Args:
            term_uuids: List of term UUIDs

        Returns:
            List of term names
        """
        return self.cache.lookup_names(self.PAGE_ELEMENTS, term_uuids)

    def validate_term_names(self, vocabulary: str, term_names: list[str]) -> tuple[list[str], list[str]]:
        """
        Validate term names against a vocabulary.

        Args:
            vocabulary: Vocabulary machine name
            term_names: List of term names to validate

        Returns:
            Tuple of (valid_names, invalid_names)
        """
        valid: list[str] = []
        invalid: list[str] = []

        for name in term_names:
            if self.cache.get_uuid_by_name(vocabulary, name):
                valid.append(name)
            else:
                invalid.append(name)

        return valid, invalid

    def get_term_suggestions(self, vocabulary: str, partial_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Get term suggestions based on partial name match.

        Args:
            vocabulary: Vocabulary machine name
            partial_name: Partial term name to search for
            limit: Maximum number of suggestions to return

        Returns:
            List of matching term dicts
        """
        terms = self.cache.get_terms(vocabulary)
        partial_lower = partial_name.lower()

        # Find terms that contain the partial name
        matches = [
            term for term in terms
            if partial_lower in term.get('name', '').lower()
        ]

        # Sort by how early the match appears in the term name
        matches.sort(key=lambda t: t.get('name', '').lower().index(partial_lower))

        return matches[:limit]

    def refresh_all(self) -> None:
        """Refresh all taxonomy caches."""
        self.cache.refresh_all()

    def clear_cache(self) -> None:
        """Clear all taxonomy caches."""
        self.cache.clear_cache()


class WCAGChapterCache:
    """
    Cache for WCAG chapter nodes.

    WCAG chapters are stored as nodes (node--wcag_chapter) with field_chapter_number
    that matches WCAG success criteria format (e.g., "1.3.1", "2.4.6").
    """

    def __init__(self, client: DrupalJSONAPIClient, cache_duration_hours: int = 24) -> None:
        """
        Initialize WCAG chapter cache.

        Args:
            client: DrupalJSONAPIClient instance
            cache_duration_hours: How long to cache chapters before refresh (default: 24 hours)
        """
        self.client: DrupalJSONAPIClient = client
        self.cache_duration: timedelta = timedelta(hours=cache_duration_hours)

        # Cache structure: {'chapters': [...], 'by_number': {number: uuid}, 'by_uuid': {uuid: number}, 'last_updated': datetime}
        self._cache: dict[str, Any] | None = None

    def get_chapters(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """
        Get all WCAG chapters.

        Args:
            force_refresh: Force refresh from Drupal even if cached

        Returns:
            List of chapter dictionaries with 'uuid', 'number', 'title', etc.
        """
        if force_refresh or not self._cache or self._is_cache_expired():
            self._refresh_chapters()

        assert self._cache is not None
        chapters: list[dict[str, Any]] = self._cache.get('chapters', [])
        return chapters

    def get_uuid_by_number(self, chapter_number: str) -> str | None:
        """
        Look up a WCAG chapter UUID by its number.

        Args:
            chapter_number: WCAG criteria number (e.g., "1.3.1", "2.4.6")

        Returns:
            Chapter UUID or None if not found
        """
        if not self._cache:
            self._refresh_chapters()

        assert self._cache is not None
        by_number: dict[str, str] = self._cache.get('by_number', {})
        return by_number.get(chapter_number)

    def get_number_by_uuid(self, chapter_uuid: str) -> str | None:
        """
        Look up a WCAG chapter number by its UUID.

        Args:
            chapter_uuid: Chapter UUID

        Returns:
            Chapter number or None if not found
        """
        if not self._cache:
            self._refresh_chapters()

        assert self._cache is not None
        by_uuid: dict[str, str] = self._cache.get('by_uuid', {})
        return by_uuid.get(chapter_uuid)

    def lookup_uuids(self, chapter_numbers: list[str]) -> list[str]:
        """
        Look up UUIDs for multiple WCAG chapter numbers.

        Args:
            chapter_numbers: List of WCAG criteria numbers

        Returns:
            List of UUIDs (skips chapters not found, logs warning)
        """
        uuids: list[str] = []

        for number in chapter_numbers:
            uuid = self.get_uuid_by_number(number)
            if uuid:
                uuids.append(uuid)
            else:
                logger.warning(f"WCAG chapter '{number}' not found")

        return uuids

    def refresh(self) -> None:
        """Refresh the WCAG chapter cache."""
        self._refresh_chapters()

    def clear_cache(self) -> None:
        """Clear cached WCAG chapter data."""
        self._cache = None
        logger.info("WCAG chapter cache cleared")

    def _is_cache_expired(self) -> bool:
        """Check if cached chapters have expired."""
        if not self._cache:
            return True

        last_updated: datetime | None = self._cache.get('last_updated')
        if not last_updated:
            return True

        return datetime.now() - last_updated > self.cache_duration

    def _refresh_chapters(self) -> None:
        """Refresh WCAG chapters from Drupal."""
        logger.info("Refreshing WCAG chapters from Drupal")

        try:
            # Fetch all WCAG chapter nodes with pagination
            all_chapters: list[dict[str, Any]] = []
            page_limit = 50
            offset = 0
            # Hard backstop against a server that ignores page[offset].
            max_pages = 1000

            for _ in range(max_pages):
                # Use client.get() which now properly handles bracket encoding
                response = self.client.get(
                    'node/wcag_chapter',
                    params={
                        'page[limit]': page_limit,
                        'page[offset]': offset
                    }
                )

                chapters: list[dict[str, Any]] = response.get('data', [])
                if not chapters:
                    break

                all_chapters.extend(chapters)
                offset += page_limit

                # If we got fewer than page_limit, we're done
                if len(chapters) < page_limit:
                    break
            else:
                logger.warning(
                    f"Pagination cap ({max_pages} pages) reached refreshing"
                    + " WCAG chapters; results may be truncated."
                )

            # Process chapters into cache structure
            by_number: dict[str, str] = {}
            by_uuid: dict[str, str] = {}
            processed_chapters: list[dict[str, Any]] = []

            for chapter in all_chapters:
                uuid: str | None = chapter.get('id')
                attributes: dict[str, Any] = chapter.get('attributes', {})
                chapter_number: str | None = attributes.get('field_chapter_number')
                title: str | None = attributes.get('title')
                nid: int | None = attributes.get('drupal_internal__nid')

                if not uuid or not chapter_number:
                    logger.warning(f"Skipping invalid WCAG chapter: {chapter}")
                    continue

                # Store in lookup maps
                by_number[chapter_number] = uuid
                by_uuid[uuid] = chapter_number

                # Store full chapter details
                processed_chapters.append({
                    'uuid': uuid,
                    'number': chapter_number,
                    'title': title,
                    'nid': nid
                })

            # Update cache
            self._cache = {
                'chapters': processed_chapters,
                'by_number': by_number,
                'by_uuid': by_uuid,
                'last_updated': datetime.now()
            }

            logger.info(f"Cached {len(processed_chapters)} WCAG chapters")

        except Exception as e:
            logger.error(f"Failed to refresh WCAG chapters: {e}")
            raise
