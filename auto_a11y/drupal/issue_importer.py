"""
Drupal Issue Importer

Handles importing issues from Drupal to Auto A11y via JSON:API.
"""

from __future__ import annotations

import logging
from typing import Any
from datetime import datetime

from auto_a11y.models import Issue, ImpactLevel
from auto_a11y.models.page import DrupalSyncStatus

logger = logging.getLogger(__name__)


def _parse_drupal_datetime(value: str | None) -> datetime | None:
    """
    Parse a Drupal JSON:API timestamp into a datetime.

    Drupal JSON:API returns ``created`` / ``changed`` as ISO-8601 strings
    (e.g. ``"2024-01-15T10:30:00+00:00"``), not numeric POSIX timestamps.

    Args:
        value: ISO-8601 datetime string, or None.

    Returns:
        Parsed datetime, or None if the value is empty/unparseable.
    """
    if not value:
        return None

    # Normalize a trailing 'Z' (UTC) to an explicit offset. Python 3.11+
    # accepts 'Z' in fromisoformat, but older versions do not — so be safe.
    normalized = value[:-1] + '+00:00' if value.endswith('Z') else value

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        logger.warning(f"Could not parse Drupal timestamp: {value!r}")
        return None


class IssueImporter:
    """
    Import issues from Drupal.

    Handles fetching issue nodes from Drupal and converting them
    to Auto A11y Issue objects.
    """

    # Safety backstop for pagination: a non-conforming endpoint that ignores
    # page[offset] would otherwise loop forever, re-fetching the first page.
    # 50 pages * 50 items = 2500 issues, well above any realistic audit.
    MAX_PAGES = 50

    def __init__(self, client: Any) -> None:
        """
        Initialize issue importer.

        Args:
            client: DrupalJSONAPIClient instance
        """
        self.client: Any = client

    def fetch_issues_for_audit(self, audit_uuid: str) -> list[dict[str, Any]]:
        """
        Fetch all issues for a given audit from Drupal.

        Args:
            audit_uuid: Drupal audit UUID

        Returns:
            List of issue dictionaries with parsed data
        """
        logger.info(f"Fetching issues for audit {audit_uuid}")

        all_issues: list[dict[str, Any]] = []
        page_limit = 50
        offset = 0

        # Hard cap on the number of fetches as a backstop: if the server
        # ignores page[offset] and keeps returning a full page, the
        # empty/short-page termination conditions below never fire, so without
        # this bound the loop would run forever (and accumulate duplicates).
        for page_index in range(self.MAX_PAGES):
            # Fetch issues with parent_audit filter
            response = self.client.get(
                'node/issue',
                params={
                    'filter[field_parent_audit.id]': audit_uuid,
                    'page[limit]': page_limit,
                    'page[offset]': offset,
                    'include': 'field_issue_type,field_location_on_page,field_wcag_chapter'
                }
            )

            issues: list[dict[str, Any]] = response.get('data', [])
            if not issues:
                break

            # Parse each issue
            for issue_node in issues:
                try:
                    parsed_issue = self._parse_issue_node(issue_node, response.get('included', []))
                    all_issues.append(parsed_issue)
                except Exception as e:
                    logger.error(f"Error parsing issue node: {e}")
                    continue

            offset += page_limit

            # If we got fewer than page_limit, we're done
            if len(issues) < page_limit:
                break

            if page_index == self.MAX_PAGES - 1:
                logger.warning(
                    f"Pagination hit safety cap of {self.MAX_PAGES} pages for audit {audit_uuid}; the endpoint may be ignoring page[offset]. Stopping to avoid an unbounded loop."
                )

        logger.info(f"Fetched {len(all_issues)} issues for audit {audit_uuid}")
        return all_issues

    def _parse_issue_node(self, node: dict[str, Any], included: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Parse a Drupal issue node into a dictionary.

        Args:
            node: JSON:API node data
            included: Included relationship data

        Returns:
            Dictionary with parsed issue data
        """
        uuid: str | None = node.get('id')
        attributes: dict[str, Any] = node.get('attributes', {})
        relationships: dict[str, Any] = node.get('relationships', {})

        # Extract basic fields
        title: str = attributes.get('title', 'Untitled Issue')

        # Body/description
        body_field: dict[str, Any] | list[dict[str, Any]] | None = attributes.get('body')
        description: str = ''
        if body_field and isinstance(body_field, dict):
            description = body_field.get('value', '')
        elif body_field and isinstance(body_field, list) and len(body_field) > 0:
            description = body_field[0].get('value', '')

        # Impact
        impact_value: str = attributes.get('field_impact', 'med').lower()
        impact_mapping: dict[str, str] = {
            'low': 'low',
            'med': 'medium',
            'medium': 'medium',
            'high': 'high'
        }
        impact: str = impact_mapping.get(impact_value, 'medium')

        # Issue type (taxonomy term)
        issue_type: str | None = self._extract_taxonomy_term(
            relationships.get('field_issue_type'),
            included
        )

        # Location on page (taxonomy term)
        location_on_page: str | None = self._extract_taxonomy_term(
            relationships.get('field_location_on_page'),
            included
        )

        # WCAG chapters (may be multiple)
        wcag_criteria: list[str] = self._extract_wcag_references(
            relationships.get('field_wcag_chapter'),
            included
        )

        # Technical fields
        xpath: str | None = attributes.get('field_xpath')
        url_field: dict[str, str] | list[dict[str, str]] | None = attributes.get('field_url')
        url: str | None = None
        if url_field and isinstance(url_field, dict):
            url = url_field.get('uri')
        elif url_field and isinstance(url_field, list) and len(url_field) > 0:
            url = url_field[0].get('uri')

        video_timecode: str | None = attributes.get('field_video_timecode')

        # Drupal IDs
        drupal_issue_id: int | None = attributes.get('field_id')
        drupal_nid: int | None = attributes.get('drupal_internal__nid')

        # Timestamps
        created: str | None = attributes.get('created')
        changed: str | None = attributes.get('changed')

        return {
            'uuid': uuid,
            'title': title,
            'description': description,
            'impact': impact,
            'issue_type': issue_type,
            'location_on_page': location_on_page,
            'wcag_criteria': wcag_criteria,
            'xpath': xpath,
            'url': url,
            'video_timecode': video_timecode,
            'drupal_issue_id': drupal_issue_id,
            'drupal_nid': drupal_nid,
            'created_timestamp': created,
            'changed_timestamp': changed
        }

    def _extract_taxonomy_term(
        self,
        relationship: dict[str, Any] | None,
        included: list[dict[str, Any]]
    ) -> str | None:
        """
        Extract taxonomy term name from relationship.

        Args:
            relationship: Relationship object
            included: Included resources

        Returns:
            Term name or None
        """
        if not relationship:
            return None

        rel_data: dict[str, Any] | list[dict[str, Any]] | None = relationship.get('data')
        if not rel_data:
            return None

        # Handle single relationship
        term_id: str | None = None
        if isinstance(rel_data, dict):
            term_id = rel_data.get('id')
        # Handle multiple (take first)
        elif len(rel_data) > 0:
            term_id = rel_data[0].get('id')

        if not term_id:
            return None

        # Find term in included
        for resource in included:
            if resource.get('id') == term_id:
                name: str | None = resource.get('attributes', {}).get('name')
                return name

        return None

    def _extract_wcag_references(
        self,
        relationship: dict[str, Any] | None,
        included: list[dict[str, Any]]
    ) -> list[str]:
        """
        Extract WCAG criteria from relationship.

        Args:
            relationship: Relationship object
            included: Included resources

        Returns:
            List of WCAG criteria strings (e.g., ["1.3.1", "2.4.6"])
        """
        if not relationship:
            return []

        rel_data: dict[str, Any] | list[dict[str, Any]] | None = relationship.get('data')
        if not rel_data:
            return []

        # Ensure it's a list
        rel_items: list[dict[str, Any]]
        if isinstance(rel_data, dict):
            rel_items = [rel_data]
        else:
            rel_items = rel_data

        criteria: list[str] = []
        for item in rel_items:
            wcag_id: str | None = item.get('id')

            # Find WCAG node in included
            for resource in included:
                if resource.get('id') == wcag_id:
                    # Extract WCAG number from title
                    title: str = resource.get('attributes', {}).get('title', '')
                    # Title might be like "1.3.1 Info and Relationships"
                    # Extract just the number
                    parts = title.split()
                    if parts and parts[0].replace('.', '').isdigit():
                        criteria.append(parts[0])
                    break

        return criteria

    def convert_to_issue_model(
        self,
        drupal_issue: dict[str, Any],
        project_id: str
    ) -> Issue:
        """
        Convert Drupal issue dict to Issue model instance.

        Args:
            drupal_issue: Parsed Drupal issue dict
            project_id: Project ID to associate with

        Returns:
            Issue model instance
        """
        # Convert timestamp to datetime. Drupal JSON:API returns these as
        # ISO-8601 strings (see _parse_issue_node), not POSIX timestamps.
        created_at = _parse_drupal_datetime(drupal_issue.get('created_timestamp')) or datetime.now()
        updated_at = _parse_drupal_datetime(drupal_issue.get('changed_timestamp')) or datetime.now()

        # Map impact
        impact_str: str = drupal_issue['impact']
        impact: ImpactLevel = ImpactLevel.MEDIUM
        if impact_str == 'low':
            impact = ImpactLevel.LOW
        elif impact_str == 'high':
            impact = ImpactLevel.HIGH

        return Issue(
            title=drupal_issue['title'],
            description=drupal_issue['description'],
            impact=impact,
            issue_type=drupal_issue.get('issue_type'),
            location_on_page=drupal_issue.get('location_on_page'),
            wcag_criteria=drupal_issue.get('wcag_criteria', []),
            xpath=drupal_issue.get('xpath'),
            url=drupal_issue.get('url'),
            video_timecode=drupal_issue.get('video_timecode'),
            project_id=project_id,
            source_type="manual",
            detection_method="drupal_import",
            created_at=created_at,
            updated_at=updated_at,
            drupal_issue_id=drupal_issue.get('drupal_issue_id'),
            drupal_uuid=drupal_issue['uuid'],
            drupal_nid=drupal_issue.get('drupal_nid'),
            drupal_sync_status=DrupalSyncStatus.SYNCED,
            drupal_last_synced=datetime.now()
        )

    def to_database_dict(self, drupal_issue: dict[str, Any], project_id: str) -> dict[str, Any]:
        """
        Convert Drupal issue to database-ready dictionary.

        Args:
            drupal_issue: Parsed Drupal issue dict
            project_id: Project ID to associate with

        Returns:
            Dictionary ready for MongoDB insertion
        """
        issue = self.convert_to_issue_model(drupal_issue, project_id)
        return issue.to_dict()
