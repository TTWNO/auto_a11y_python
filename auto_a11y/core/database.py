"""
Database connection and repository management
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from collections.abc import Generator
from pymongo import MongoClient
from pymongo.database import Database as MongoDatabase
from pymongo.collection import Collection
from bson import ObjectId
from datetime import datetime
import logging
import re
import certifi

from auto_a11y.models import (
    Project, Website, Page, TestResult,
    ProjectStatus, PageStatus,
    Recording, RecordingIssue, RecordingType,
    DocumentReference, DiscoveryRun,
    PageSetupScript, ScriptExecutionSession,
    WebsiteUser, ProjectUser, DiscoveredPage,
    TestStateMatrix, AppUser, UserRole,
    TestSchedule, ScheduleRunStatus,
    ShareToken, TokenScope
)
from auto_a11y.models.api_token import ApiToken
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.models.permission_group import PermissionGroup

if TYPE_CHECKING:
    from auto_a11y.pdf.storage import PdfStorage

logger = logging.getLogger(__name__)


class Database:
    """MongoDB database connection and operations"""
    
    def __init__(self, connection_uri: str, database_name: str):
        """
        Initialize database connection
        
        Args:
            connection_uri: MongoDB connection URI
            database_name: Name of database to use
        """
        # Use TLS for remote MongoDB connections (e.g. Atlas), skip for local/Docker
        if 'mongodb+srv' in connection_uri or 'tls=true' in connection_uri or 'ssl=true' in connection_uri:
            self.client: MongoClient[dict[str, Any]] = MongoClient(connection_uri, tlsCAFile=certifi.where())
        else:
            self.client = MongoClient(connection_uri)
        self.db: MongoDatabase[dict[str, Any]] = self.client[database_name]
        
        # Collections
        self.projects: Collection[dict[str, Any]] = self.db.projects
        self.websites: Collection[dict[str, Any]] = self.db.websites
        self.pages: Collection[dict[str, Any]] = self.db.pages
        self.test_results: Collection[dict[str, Any]] = self.db.test_results
        self.test_result_items: Collection[dict[str, Any]] = self.db.test_result_items  # NEW: Detailed violations/warnings
        self.document_references: Collection[dict[str, Any]] = self.db.document_references
        self.discovery_runs: Collection[dict[str, Any]] = self.db.discovery_runs
        self.issue_documentation_status: Collection[dict[str, Any]] = self.db.issue_documentation_status
        self.page_setup_scripts: Collection[dict[str, Any]] = self.db.page_setup_scripts  # Page training scripts
        self.script_execution_sessions: Collection[dict[str, Any]] = self.db.script_execution_sessions  # Session tracking
        self.website_users: Collection[dict[str, Any]] = self.db.website_users  # Test users for authenticated testing (deprecated)
        self.project_users: Collection[dict[str, Any]] = self.db.project_users  # Test users at project level
        self.recordings: Collection[dict[str, Any]] = self.db.recordings  # Manual audit recordings from Dictaphone
        self.recording_issues: Collection[dict[str, Any]] = self.db.recording_issues  # Issues from manual audits
        self.discovered_pages: Collection[dict[str, Any]] = self.db.discovered_pages  # Discovered pages for Drupal export
        self.drupal_issues: Collection[dict[str, Any]] = self.db.drupal_issues  # Track Drupal issue uploads by violation ID
        self.test_state_matrices: Collection[dict[str, Any]] = self.db.test_state_matrices  # Multi-state test configuration matrices
        self.app_users: Collection[dict[str, Any]] = self.db.app_users  # Application users for authentication
        self.test_schedules: Collection[dict[str, Any]] = self.db.test_schedules  # Scheduled test configurations
        self.share_tokens: Collection[dict[str, Any]] = self.db.share_tokens  # Public share tokens
        self.groups: Collection[dict[str, Any]] = self.db['groups']  # Permission groups
        self.issues: Collection[dict[str, Any]] = self.db.issues  # Issues for Drupal sync
        self.pdf_documents: Collection[dict[str, Any]] = self.db.pdf_documents  # Downloaded auditable PDFs
        self.system_settings: Collection[dict[str, Any]] = self.db.system_settings  # Singleton doc with admin-managed config (Drupal, SMTP, SSO, etc.)
        self.idempotency_keys: Collection[dict[str, Any]] = self.db.idempotency_keys  # REST API Idempotency-Key store (TTL index, see auto_a11y/web/api/idempotency.py)
        self.api_tokens: Collection[dict[str, Any]] = self.db.api_tokens  # Bearer-auth tokens for /api/v1 (see auto_a11y/models/api_token.py)

        # Create indexes
        self._create_indexes()
        
        logger.info(f"Connected to MongoDB database: {database_name}")
    
    def _create_indexes(self) -> None:
        """Create database indexes for performance"""
        # Projects
        self.projects.create_index("name")
        self.projects.create_index("status")
        self.projects.create_index("project_type")
        self.projects.create_index([("project_type", 1), ("status", 1)])
        self.projects.create_index([("members.user_id", 1)])

        # Websites
        self.websites.create_index("project_id")
        self.websites.create_index([("members.user_id", 1)])
        self.websites.create_index("url")
        
        # Pages
        self.pages.create_index("website_id")
        self.pages.create_index("url")
        self.pages.create_index("status")
        self.pages.create_index([("website_id", 1), ("url", 1)], unique=True)
        
        # Test results
        self.test_results.create_index("page_id")
        self.test_results.create_index("test_date")
        # Multi-state testing indexes
        self.test_results.create_index("session_id")
        self.test_results.create_index([("page_id", 1), ("session_id", 1), ("state_sequence", 1)])
        self.test_results.create_index([("session_id", 1), ("state_sequence", 1)])

        # Test result items (NEW: detailed violations/warnings)
        self.test_result_items.create_index("test_result_id")
        self.test_result_items.create_index([("page_id", 1), ("test_date", -1)])
        self.test_result_items.create_index([("item_type", 1), ("test_result_id", 1)])
        self.test_result_items.create_index([("issue_id", 1), ("test_result_id", 1)])
        self.test_result_items.create_index([("touchpoint", 1), ("test_result_id", 1)])
        # Compound index for common queries
        self.test_result_items.create_index([
            ("test_result_id", 1),
            ("item_type", 1),
            ("issue_id", 1)
        ])

        # Document references
        self.document_references.create_index("website_id")
        self.document_references.create_index("document_url")
        self.document_references.create_index([("website_id", 1), ("document_url", 1)])
        
        # Discovery runs
        self.discovery_runs.create_index("website_id")
        self.discovery_runs.create_index("started_at")
        self.discovery_runs.create_index("is_latest")

        # Issue documentation status
        self.issue_documentation_status.create_index("issue_code", unique=True)
        self.discovery_runs.create_index([("website_id", 1), ("is_latest", 1)])

        # Update pages index for discovery run
        self.pages.create_index("discovery_run_id")
        self.pages.create_index("is_in_latest_discovery")

        # Page setup scripts
        self.page_setup_scripts.create_index("page_id")
        self.page_setup_scripts.create_index("website_id")  # NEW: For website-level scripts
        self.page_setup_scripts.create_index([("website_id", 1), ("scope", 1), ("enabled", 1)])
        self.page_setup_scripts.create_index([("page_id", 1), ("enabled", 1)])
        self.page_setup_scripts.create_index("created_date")

        # Script execution sessions
        self.script_execution_sessions.create_index("session_id", unique=True)
        self.script_execution_sessions.create_index("website_id")
        self.script_execution_sessions.create_index([("website_id", 1), ("started_at", -1)])

        # Test state matrices
        self.test_state_matrices.create_index("page_id", unique=True)
        self.test_state_matrices.create_index("website_id")
        self.test_state_matrices.create_index([("website_id", 1), ("created_date", -1)])

        # Website users (test users for authenticated testing)
        self.website_users.create_index("website_id")
        self.website_users.create_index([("website_id", 1), ("username", 1)], unique=True)
        self.website_users.create_index([("website_id", 1), ("enabled", 1)])
        self.website_users.create_index("roles")

        # Project users (test users at project level)
        self.project_users.create_index("project_id")
        self.project_users.create_index([("project_id", 1), ("username", 1)], unique=True)
        self.project_users.create_index([("project_id", 1), ("enabled", 1)])
        self.project_users.create_index("roles")

        # Recordings (manual audit recordings)
        self.recordings.create_index("recording_id", unique=True)
        self.recordings.create_index("project_id")
        self.recordings.create_index("recorded_date")
        self.recordings.create_index("recording_type")
        self.recordings.create_index("component_names")  # For component-specific queries
        self.recordings.create_index([("project_id", 1), ("recorded_date", -1)])
        self.recordings.create_index("drupal_video_uuid")  # For Drupal sync
        self.recordings.create_index("drupal_sync_status")
        # Phase 6 of audioA11y: looking up Recordings by pipeline status
        # (e.g. ``processing`` documents on server restart) is a routine
        # query path for the runner / recovery code.
        self.recordings.create_index("status")

        # Discovered pages (for Drupal export)
        self.discovered_pages.create_index("project_id")
        self.discovered_pages.create_index("url")
        self.discovered_pages.create_index([("project_id", 1), ("url", 1)], unique=True)
        self.discovered_pages.create_index("source_type")
        self.discovered_pages.create_index("drupal_uuid")  # For Drupal sync
        self.discovered_pages.create_index("drupal_sync_status")

        # Recording issues
        self.recording_issues.create_index("recording_id")
        self.recording_issues.create_index("project_id")
        self.recording_issues.create_index("impact")
        self.recording_issues.create_index("status")
        self.recording_issues.create_index("component_names")  # For component-specific queries
        self.recording_issues.create_index([("recording_id", 1), ("impact", 1)])
        self.recording_issues.create_index([("project_id", 1), ("status", 1)])

        # Drupal issues (track issue uploads by unique_id)
        self.drupal_issues.create_index("unique_id")
        self.drupal_issues.create_index("violation_id")  # Keep for reference
        self.drupal_issues.create_index("project_id")
        self.drupal_issues.create_index([("unique_id", 1), ("project_id", 1)], unique=True)
        self.drupal_issues.create_index("drupal_uuid")
        self.drupal_issues.create_index("discovered_page_id")

        # App users (application authentication)
        self.app_users.create_index("email", unique=True)
        self.app_users.create_index("role")
        self.app_users.create_index("is_active")

        # Test schedules (scheduled testing configuration)
        self.test_schedules.create_index("website_id")
        self.test_schedules.create_index("enabled")
        self.test_schedules.create_index([("website_id", 1), ("enabled", 1)])
        self.test_schedules.create_index("next_run_at")
        # Unique on apscheduler_job_id, but only for documents that have a
        # non-null string value. ``sparse=True`` is not sufficient: MongoDB
        # treats an explicit ``null`` as a value and indexes it, so two
        # schedules created before a scheduler assigns them job IDs would
        # collide. Drop the legacy sparse index if it lingers from an older
        # deploy before installing the partial replacement.
        existing_index_names = {ix["name"] for ix in self.test_schedules.list_indexes()}
        if "apscheduler_job_id_1" in existing_index_names:
            self.test_schedules.drop_index("apscheduler_job_id_1")
        self.test_schedules.create_index(
            "apscheduler_job_id",
            unique=True,
            partialFilterExpression={"apscheduler_job_id": {"$type": "string"}},
            name="apscheduler_job_id_unique_partial",
        )

        # Share tokens (public share links)
        self.share_tokens.create_index("token_hash", unique=True)
        self.share_tokens.create_index([("scope", 1), ("scope_id", 1)])

        # PDF documents (downloaded auditable PDF artefacts)
        self.pdf_documents.create_index(
            [("website_id", 1), ("sha256", 1)],
            unique=True,
            name="pdf_documents_website_sha_unique",
        )
        self.pdf_documents.create_index([("project_id", 1), ("discovered_at", -1)])
        self.pdf_documents.create_index([("website_id", 1), ("discovered_at", -1)])
        self.pdf_documents.create_index("status")

        # Idempotency keys for REST API. The TTL index lives next to the
        # rest of the API scaffolding so the retention duration is owned in
        # one place (auto_a11y/web/api/idempotency.py).
        from auto_a11y.web.api.idempotency import ensure_ttl_index
        ensure_ttl_index(self.idempotency_keys)

    def test_connection(self) -> bool:
        """Test database connection"""
        try:
            # Ping the database
            self.client.admin.command('ping')
            return True
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False
    
    def create_indexes(self) -> None:
        """Public method to create indexes"""
        self._create_indexes()
    
    def close(self) -> None:
        """Close database connection"""
        self.client.close()
        logger.info("Database connection closed")
    
    # Project operations
    
    def create_project(self, project: Project) -> str:
        """Create new project"""
        result = self.projects.insert_one(project.to_dict())
        project.mongo_id = result.inserted_id
        project_id = str(result.inserted_id)
        logger.info(f"Created project: {project.name} ({project_id})")
        return project_id
    
    def get_project(self, project_id: str) -> Project | None:
        """Get project by ID"""
        doc = self.projects.find_one({"_id": ObjectId(project_id)})
        return Project.from_dict(doc) if doc else None
    
    def get_projects(
        self,
        status: ProjectStatus | None = None,
        limit: int = 0,
        skip: int = 0
    ) -> list[Project]:
        """Get projects with optional filtering"""
        query: dict[str, Any] = {}
        if status:
            query["status"] = status.value
        
        docs = self.projects.find(query).limit(limit).skip(skip)
        return [Project.from_dict(doc) for doc in docs]
    
    def get_all_projects(self) -> list[Project]:
        """Get all projects"""
        docs = self.projects.find()
        return [Project.from_dict(doc) for doc in docs]

    def get_projects_for_user(self, user_id: str) -> list[Project]:
        """Return projects where user_id is a member."""
        cursor = self.projects.find({"members.user_id": user_id})
        return [Project.from_dict(doc) for doc in cursor]

    # Project member operations

    def add_project_member(self, project_id: str, user_id: str, group_ids: list[str]) -> bool:
        """Add or update a member on a project with given group IDs."""
        self.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$pull": {"members": {"user_id": user_id}}}
        )
        result = self.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$push": {"members": {"user_id": user_id, "group_ids": [str(g) for g in group_ids]}}}
        )
        return result.modified_count > 0

    def remove_project_member(self, project_id: str, user_id: str) -> bool:
        """Remove a member from a project."""
        result = self.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$pull": {"members": {"user_id": user_id}}}
        )
        return result.modified_count > 0

    def update_project_member_groups(self, project_id: str, user_id: str, group_ids: list[str]) -> bool:
        """Update a member's group assignments on a project."""
        result = self.projects.update_one(
            {"_id": ObjectId(project_id), "members.user_id": user_id},
            {"$set": {"members.$.group_ids": [str(g) for g in group_ids]}}
        )
        return result.modified_count > 0

    def update_project(self, project: Project) -> bool:
        """Update existing project"""
        project.update_timestamp()
        result = self.projects.replace_one(
            {"_id": project.mongo_id},
            project.to_dict()
        )
        return result.modified_count > 0
    
    def delete_project(self, project_id: str) -> bool:
        """Delete project and related data"""
        # Delete related data
        websites = self.get_websites(project_id)
        for website in websites:
            if website.id:
                self.delete_website(website.id)
        
        # Delete project
        result = self.projects.delete_one({"_id": ObjectId(project_id)})
        logger.info(f"Deleted project: {project_id}")
        return result.deleted_count > 0
    
    # Website operations
    
    def create_website(self, website: Website) -> str:
        """Create new website"""
        result = self.websites.insert_one(website.to_dict())
        website.mongo_id = result.inserted_id
        
        # Add to project's website list
        self.projects.update_one(
            {"_id": ObjectId(website.project_id)},
            {"$push": {"website_ids": website.id}}
        )
        
        website_id = str(result.inserted_id)
        logger.info(f"Created website: {website.url} ({website_id})")
        return website_id
    
    def get_website(self, website_id: str) -> Website | None:
        """Get website by ID"""
        doc = self.websites.find_one({"_id": ObjectId(website_id)})
        return Website.from_dict(doc) if doc else None
    
    def get_all_websites(self) -> list[Website]:
        """Get all websites across all projects"""
        docs = self.websites.find()
        return [Website.from_dict(doc) for doc in docs]

    def get_websites(self, project_id: str) -> list[Website]:
        """Get all websites for a project"""
        docs = self.websites.find({"project_id": project_id})
        return [Website.from_dict(doc) for doc in docs]

    def yield_websites(self, project_id: str) -> Generator[Website, None, None]:
        """Yield Website objects one at a time from cursor."""
        cursor = self.websites.find({"project_id": project_id}, no_cursor_timeout=True)
        try:
            for doc in cursor:
                yield Website.from_dict(doc)
        finally:
            cursor.close()

    def update_website(self, website: Website) -> bool:
        """Update existing website"""
        result = self.websites.replace_one(
            {"_id": website.mongo_id},
            website.to_dict()
        )
        return result.modified_count > 0
    
    def delete_website(self, website_id: str) -> bool:
        """Delete website and related data"""
        website = self.get_website(website_id)
        if not website:
            return False

        # Delete related pages and test results
        pages = self.get_pages(website_id)
        for page in pages:
            if page.id:
                self.delete_page(page.id)

        # Cascade: delete pdf_documents (and their test_results) for this website
        for pdf in self.get_pdf_documents(website_id=website_id, limit=10000):
            if pdf.id:
                self.delete_pdf_document(pdf.id)

        # Remove from project's website list
        self.projects.update_one(
            {"_id": ObjectId(website.project_id)},
            {"$pull": {"website_ids": website_id}}
        )
        
        # Delete website
        result = self.websites.delete_one({"_id": ObjectId(website_id)})
        logger.info(f"Deleted website: {website_id}")
        return result.deleted_count > 0

    def clear_website_test_results(self, website_id: str) -> dict[str, int]:
        """
        Clear all test data for every page and PDF in a website.

        Deletes all test_results documents for pages and PDFs belonging to
        this website, and resets each page's cached test state (violation/
        warning/info/discovery/pass counts, last_tested, test_duration_ms)
        so the pages show as untested. Pages whose status was TESTED,
        TESTING, or ERROR are moved back to DISCOVERED so they can be
        re-tested. PDFs whose status was AUDITED, AUDITING, or AUDIT_FAILED
        are moved back to PENDING so they can be re-audited; their
        last_audit_result_id and last_audited_at are cleared. Resetting
        PDFs is required for the website/project rollup totals — which
        sum HTML page counts plus issue_map.json tallies for AUDITED
        PDFs (auto_a11y/pdf/issue_map_counts.py) — to fall to zero.

        Pages themselves, their screenshots, discovery runs, and document
        references are preserved. PDFs themselves and their downloaded
        bytes are preserved.

        Args:
            website_id: Website ID

        Returns:
            Dict with counts: {'test_results_deleted', 'pages_reset', 'pdf_documents_reset'}
        """
        # Collect page IDs for this website so we can delete their test results
        page_id_strings = [
            str(doc['_id'])
            for doc in self.pages.find({"website_id": website_id}, {"_id": 1})
        ]

        # Collect PDF IDs for this website so we can delete their PDF audit
        # test_results (target_type='pdf_document', page_id=None).
        pdf_id_strings = [
            str(doc['_id'])
            for doc in self.pdf_documents.find({"website_id": website_id}, {"_id": 1})
        ]

        test_results_deleted = 0
        if page_id_strings:
            del_result = self.test_results.delete_many(
                {"page_id": {"$in": page_id_strings}}
            )
            test_results_deleted += del_result.deleted_count

        if pdf_id_strings:
            pdf_results_del = self.test_results.delete_many(
                {"target_type": "pdf_document", "target_id": {"$in": pdf_id_strings}}
            )
            test_results_deleted += pdf_results_del.deleted_count

        # Reset per-page cached test state
        reset_result = self.pages.update_many(
            {"website_id": website_id},
            {"$set": {
                "violation_count": 0,
                "warning_count": 0,
                "info_count": 0,
                "discovery_count": 0,
                "pass_count": 0,
                "last_tested": None,
                "test_duration_ms": None,
            }}
        )

        # Reset status for pages that were tested/testing/errored back to DISCOVERED.
        # Leave DISCOVERED, QUEUED, SKIPPED, and DISCOVERY_FAILED alone.
        self.pages.update_many(
            {
                "website_id": website_id,
                "status": {"$in": [
                    PageStatus.TESTED.value,
                    PageStatus.TESTING.value,
                    PageStatus.ERROR.value,
                ]},
            },
            {"$set": {"status": PageStatus.DISCOVERED.value}}
        )

        # Reset PDF audit state. Only AUDITED / AUDITING / AUDIT_FAILED move
        # back to PENDING — PENDING / FETCHING / FETCH_FAILED describe fetch
        # state that's unrelated to audit results.
        pdf_reset_result = self.pdf_documents.update_many(
            {
                "website_id": website_id,
                "status": {"$in": [
                    PdfDocumentStatus.AUDITED.value,
                    PdfDocumentStatus.AUDITING.value,
                    PdfDocumentStatus.AUDIT_FAILED.value,
                ]},
            },
            {"$set": {
                "status": PdfDocumentStatus.PENDING.value,
                "last_audit_result_id": None,
                "last_audited_at": None,
            }}
        )

        pages_reset = reset_result.modified_count
        pdf_documents_reset = pdf_reset_result.modified_count
        logger.info(f"Cleared test results for website {website_id}: {test_results_deleted} test_results deleted, {pages_reset} pages reset, {pdf_documents_reset} PDFs reset")
        return {
            'test_results_deleted': test_results_deleted,
            'pages_reset': pages_reset,
            'pdf_documents_reset': pdf_documents_reset,
        }

    # Page operations
    
    def create_page(self, page: Page) -> str:
        """Create new page"""
        # Check if page already exists
        existing = self.pages.find_one({
            "website_id": page.website_id,
            "url": page.url
        })
        
        if existing:
            page.mongo_id = existing["_id"]
            return str(existing["_id"])
        
        result = self.pages.insert_one(page.to_dict())
        page.mongo_id = result.inserted_id
        
        # Update website page count
        self.websites.update_one(
            {"_id": ObjectId(page.website_id)},
            {"$inc": {"page_count": 1}}
        )
        
        return str(result.inserted_id)
    
    def get_page(self, page_id: str) -> Page | None:
        """Get page by ID"""
        doc = self.pages.find_one({"_id": ObjectId(page_id)})
        return Page.from_dict(doc) if doc else None
    
    def get_page_by_url(self, website_id: str, url: str) -> Page | None:
        """Get page by URL"""
        doc = self.pages.find_one({
            "website_id": website_id,
            "url": url
        })
        return Page.from_dict(doc) if doc else None
    
    def get_pages(
        self,
        website_id: str,
        status: PageStatus | None = None,
        limit: int = 0,
        skip: int = 0,
        latest_only: bool = True
    ) -> list[Page]:
        """Get pages for a website"""
        query: dict[str, Any] = {"website_id": website_id}
        if status:
            query["status"] = status.value
        
        # By default, only return pages from latest discovery for testing
        if latest_only:
            query["is_in_latest_discovery"] = True
        
        docs = self.pages.find(query).limit(limit).skip(skip)
        return [Page.from_dict(doc) for doc in docs]

    def yield_pages(self, website_id: str, sort_field: str = 'url', sort_order: int = 1, latest_only: bool = True) -> Generator[Page, None, None]:
        """Yield Page objects one at a time from cursor.
        Memory-efficient alternative to get_pages() for report generation.
        Uses no_cursor_timeout to prevent timeout during long operations."""
        query: dict[str, Any] = {"website_id": website_id}
        if latest_only:
            query["is_in_latest_discovery"] = True
        cursor = self.pages.find(query, no_cursor_timeout=True).sort(sort_field, sort_order)
        try:
            for doc in cursor:
                yield Page.from_dict(doc)
        finally:
            cursor.close()

    def update_page(self, page: Page) -> bool:
        """Update existing page"""
        result = self.pages.replace_one(
            {"_id": page.mongo_id},
            page.to_dict()
        )
        return result.modified_count > 0
    
    def delete_page(self, page_id: str) -> bool:
        """Delete page and related test results"""
        # Delete test results
        self.test_results.delete_many({"page_id": page_id})
        
        # Update website page count
        page = self.get_page(page_id)
        if page:
            self.websites.update_one(
                {"_id": ObjectId(page.website_id)},
                {"$inc": {"page_count": -1}}
            )
        
        # Delete page
        result = self.pages.delete_one({"_id": ObjectId(page_id)})
        return result.deleted_count > 0
    
    def bulk_create_pages(self, pages: list[Page]) -> int:
        """Create multiple pages efficiently"""
        if not pages:
            return 0
        
        # Filter out existing pages
        new_pages: list[dict[str, Any]] = []
        for page in pages:
            existing = self.pages.find_one({
                "website_id": page.website_id,
                "url": page.url
            })
            if not existing:
                new_pages.append(page.to_dict())
        
        if not new_pages:
            return 0
        
        insert_result = self.pages.insert_many(new_pages)
        
        # Update website page count
        if new_pages:
            website_id = new_pages[0]["website_id"]
            # website_id is already a string, need to find by string ID
            update_result = self.websites.update_one(
                {"_id": ObjectId(website_id)},
                {"$inc": {"page_count": len(new_pages)}}
            )
            logger.info(f"Updated website {website_id} page count by {len(new_pages)}, modified={update_result.modified_count}")
        
        return len(insert_result.inserted_ids)
    
    # Test result operations

    def _create_test_result_items(self, test_result_id: ObjectId, test_result: TestResult) -> int:
        """
        Create individual test result item documents (violations, warnings, etc.)

        Args:
            test_result_id: ObjectId of the test result summary document
            test_result: TestResult object containing all items

        Returns:
            Number of items inserted
        """
        items: list[dict[str, Any]] = []

        # Convert violations to items
        for violation in test_result.violations:
            items.append({
                'test_result_id': test_result_id,
                'page_id': test_result.page_id,
                'test_date': test_result.test_date,
                'item_type': 'violation',
                'issue_id': violation.id,
                'impact': violation.impact.value if hasattr(violation.impact, 'value') else str(violation.impact),
                'touchpoint': violation.touchpoint,
                'xpath': violation.xpath,
                'element': violation.element,
                'html': violation.html,
                'description': violation.description,
                'failure_summary': violation.failure_summary,
                'wcag_criteria': violation.wcag_criteria if violation.wcag_criteria else [],
                'help_url': violation.help_url,
                'metadata': violation.metadata if violation.metadata else {}
            })

        # Convert warnings to items
        for warning in test_result.warnings:
            items.append({
                'test_result_id': test_result_id,
                'page_id': test_result.page_id,
                'test_date': test_result.test_date,
                'item_type': 'warning',
                'issue_id': warning.id,
                'impact': warning.impact.value if hasattr(warning.impact, 'value') else str(warning.impact),
                'touchpoint': warning.touchpoint,
                'xpath': warning.xpath,
                'element': warning.element,
                'html': warning.html,
                'description': warning.description,
                'failure_summary': warning.failure_summary,
                'wcag_criteria': warning.wcag_criteria if warning.wcag_criteria else [],
                'help_url': warning.help_url,
                'metadata': warning.metadata if warning.metadata else {}
            })

        # Convert info items
        for info in test_result.info:
            items.append({
                'test_result_id': test_result_id,
                'page_id': test_result.page_id,
                'test_date': test_result.test_date,
                'item_type': 'info',
                'issue_id': info.id,
                'impact': info.impact.value if hasattr(info.impact, 'value') else str(info.impact),
                'touchpoint': info.touchpoint,
                'xpath': info.xpath,
                'element': info.element,
                'html': info.html,
                'description': info.description,
                'metadata': info.metadata if info.metadata else {}
            })

        # Convert discovery items
        for discovery in test_result.discovery:
            items.append({
                'test_result_id': test_result_id,
                'page_id': test_result.page_id,
                'test_date': test_result.test_date,
                'item_type': 'discovery',
                'issue_id': discovery.id,
                'impact': discovery.impact.value if hasattr(discovery.impact, 'value') else str(discovery.impact),
                'touchpoint': discovery.touchpoint,
                'xpath': discovery.xpath,
                'element': discovery.element,
                'html': discovery.html,
                'description': discovery.description,
                'metadata': discovery.metadata if discovery.metadata else {}
            })

        # Convert passes
        for passed in test_result.passes:
            items.append({
                'test_result_id': test_result_id,
                'page_id': test_result.page_id,
                'test_date': test_result.test_date,
                'item_type': 'pass',
                'issue_id': passed.get('id'),
                'touchpoint': passed.get('touchpoint'),
                'xpath': passed.get('xpath'),
                'element': passed.get('element'),
                'html': passed.get('html'),
                'description': passed.get('description'),
                'metadata': passed.get('metadata', {})
            })

        # Batch insert all items
        if items:
            result = self.test_result_items.insert_many(items, ordered=False)
            logger.info(f"Created {len(result.inserted_ids)} test result items for test_result {test_result_id}")
            return len(result.inserted_ids)

        return 0

    def create_test_result(self, test_result: TestResult) -> str:
        """
        Create new test result using split schema (summary + items)

        NEW APPROACH:
        - Stores summary (counts, metadata) in test_results collection
        - Stores individual items in test_result_items collection
        - No size limit issues since each item is a separate document
        - All raw data preserved

        POLICY: Never truncates violations/warnings/passes data.
        """
        # Create summary document (counts only, no arrays)
        summary = {
            'page_id': test_result.page_id,
            # Polymorphic target. Persisting both fields lets the cascade
            # delete in :meth:`delete_pdf_document` find PDF-targeted
            # results, and lets :meth:`TestResult.from_dict` reconstruct
            # the polymorphism when reading the record back.
            'target_type': test_result.target_type.value,
            'target_id': test_result.target_id,
            'website_id': test_result.website_id,
            'test_date': test_result.test_date,
            'duration_ms': test_result.duration_ms,

            # Counts only (not arrays)
            'violation_count': test_result.violation_count,
            'warning_count': test_result.warning_count,
            'info_count': test_result.info_count,
            'discovery_count': test_result.discovery_count,
            'pass_count': test_result.pass_count,

            # AI findings (usually small, keep in summary)
            'ai_findings': test_result.ai_findings,

            # Screenshot info
            'screenshot_path': test_result.screenshot_path,

            # Test metadata (includes applicable_checks, passed_checks, etc.)
            'metadata': test_result.metadata if hasattr(test_result, 'metadata') and test_result.metadata else {},

            # Multi-state testing fields
            'session_id': test_result.session_id if hasattr(test_result, 'session_id') else None,
            'state_sequence': test_result.state_sequence if hasattr(test_result, 'state_sequence') else 0,
            'page_state': test_result.page_state if hasattr(test_result, 'page_state') else None,
            'related_result_ids': test_result.related_result_ids if hasattr(test_result, 'related_result_ids') else [],

            # Error message (if test failed)
            'error': test_result.error if hasattr(test_result, 'error') else None,

            # Metadata
            '_has_detailed_items': True,
            '_items_collection': 'test_result_items'
        }

        try:
            # Insert summary document
            result = self.test_results.insert_one(summary)
            test_result.mongo_id = result.inserted_id
            test_result_id = result.inserted_id

            logger.info(f"Created test result summary for page: {test_result.page_id}")

            # Create detailed item documents
            items_count = self._create_test_result_items(test_result_id, test_result)
            logger.info(f"Created {items_count} detailed items for test result {test_result_id}")

        except Exception as e:
            # If anything fails, log error and create error result
            logger.error(f"Error creating test result for page {test_result.page_id}: {e}")

            # Create minimal error result
            error_result: dict[str, Any] = {
                'page_id': test_result.page_id,
                'target_type': test_result.target_type.value,
                'target_id': test_result.target_id,
                'website_id': test_result.website_id,
                'test_date': test_result.test_date,
                'duration_ms': test_result.duration_ms,
                'violation_count': 1,
                'warning_count': 0,
                'info_count': 0,
                'discovery_count': 0,
                'pass_count': 0,
                'violations': [{
                    'id': 'ErrTestResultCreationFailed',
                    'impact': 'high',
                    'touchpoint': 'system',
                    'description': f'Failed to create test result: {str(e)}',
                    'xpath': '/',
                    'element': 'DOCUMENT'
                }],
                'warnings': [],
                'info': [],
                'discovery': [],
                'passes': [],
                'ai_findings': [],
                'screenshot_path': test_result.screenshot_path,
                'error': str(e)
            }

            result = self.test_results.insert_one(error_result)
            test_result.mongo_id = result.inserted_id

        # Update page with latest test info (only for page-targeted results)
        page = self.get_page(test_result.page_id) if test_result.page_id else None
        if page:
            page.last_tested = test_result.test_date
            page.status = PageStatus.TESTED
            page.violation_count = test_result.violation_count
            page.warning_count = test_result.warning_count
            page.info_count = test_result.info_count
            page.discovery_count = test_result.discovery_count
            page.pass_count = test_result.pass_count
            page.test_duration_ms = test_result.duration_ms
            self.update_page(page)

        logger.info(f"Created test result for page: {test_result.page_id}")
        return str(test_result.mongo_id) if test_result.mongo_id else ""

    def _get_test_result_items(self, test_result_id: ObjectId, item_type: str | None = None) -> list[dict[str, Any]]:
        """
        Get test result items from the test_result_items collection

        Args:
            test_result_id: ObjectId of the test result
            item_type: Optional filter by item type (violation, warning, info, discovery, pass)

        Returns:
            List of item dictionaries
        """
        query: dict[str, Any] = {'test_result_id': test_result_id}
        if item_type:
            query['item_type'] = item_type

        items = list(self.test_result_items.find(query))
        return items

    def yield_test_result_items(self, test_result_id: ObjectId, item_type: str | None = None) -> Generator[dict[str, Any], None, None]:
        """Yield individual test result items from cursor.
        Each item is a raw dict from MongoDB."""
        query: dict[str, Any] = {'test_result_id': test_result_id}
        if item_type:
            query['item_type'] = item_type
        cursor = self.test_result_items.find(query, no_cursor_timeout=True)
        try:
            for doc in cursor:
                yield doc
        finally:
            cursor.close()

    def get_test_result(self, result_id: str) -> TestResult | None:
        """
        Get test result by ID

        Supports both old schema (with arrays) and new schema (with separate items)
        """
        doc = self.test_results.find_one({"_id": ObjectId(result_id)})
        if not doc:
            return None

        # Check if this uses the new schema (split items)
        if doc.get('_has_detailed_items'):
            # Load items from test_result_items collection
            test_result_id = doc['_id']
            items = self._get_test_result_items(test_result_id)

            # Group items by type
            violations: list[dict[str, Any]] = []
            warnings: list[dict[str, Any]] = []
            info: list[dict[str, Any]] = []
            discovery: list[dict[str, Any]] = []
            passes: list[dict[str, Any]] = []

            for item in items:
                item_data: dict[str, Any] = {
                    'id': item.get('issue_id'),
                    'impact': item.get('impact'),
                    'touchpoint': item.get('touchpoint'),
                    'xpath': item.get('xpath'),
                    'element': item.get('element'),
                    'html': item.get('html'),
                    'description': item.get('description'),
                    'metadata': item.get('metadata', {})
                }

                item_type = item.get('item_type')
                if item_type == 'violation':
                    item_data['failure_summary'] = item.get('failure_summary')
                    item_data['wcag_criteria'] = item.get('wcag_criteria', [])
                    item_data['help_url'] = item.get('help_url')
                    violations.append(item_data)
                elif item_type == 'warning':
                    item_data['failure_summary'] = item.get('failure_summary')
                    item_data['wcag_criteria'] = item.get('wcag_criteria', [])
                    item_data['help_url'] = item.get('help_url')
                    warnings.append(item_data)
                elif item_type == 'info':
                    info.append(item_data)
                elif item_type == 'discovery':
                    discovery.append(item_data)
                elif item_type == 'pass':
                    passes.append(item_data)

            # Add arrays back to doc for TestResult.from_dict()
            doc['violations'] = violations
            doc['warnings'] = warnings
            doc['info'] = info
            doc['discovery'] = discovery
            doc['passes'] = passes

        # Old schema already has arrays, just use as-is
        return TestResult.from_dict(doc)
    
    def get_latest_test_result(self, page_id: str) -> TestResult | None:
        """
        Get most recent test result for a page

        Supports both old schema (with arrays) and new schema (with separate items)
        """
        doc = self.test_results.find_one(
            {"page_id": page_id},
            sort=[("test_date", -1)]
        )
        if not doc:
            return None

        # Check if this uses the new schema (split items)
        if doc.get('_has_detailed_items'):
            # Load items from test_result_items collection
            test_result_id = doc['_id']
            items = self._get_test_result_items(test_result_id)

            # Group items by type
            violations: list[dict[str, Any]] = []
            warnings: list[dict[str, Any]] = []
            info: list[dict[str, Any]] = []
            discovery: list[dict[str, Any]] = []
            passes: list[dict[str, Any]] = []

            for item in items:
                item_data: dict[str, Any] = {
                    'id': item.get('issue_id'),
                    'impact': item.get('impact'),
                    'touchpoint': item.get('touchpoint'),
                    'xpath': item.get('xpath'),
                    'element': item.get('element'),
                    'html': item.get('html'),
                    'description': item.get('description'),
                    'metadata': item.get('metadata', {})
                }

                item_type = item.get('item_type')
                if item_type == 'violation':
                    item_data['failure_summary'] = item.get('failure_summary')
                    item_data['wcag_criteria'] = item.get('wcag_criteria', [])
                    item_data['help_url'] = item.get('help_url')
                    violations.append(item_data)
                elif item_type == 'warning':
                    item_data['failure_summary'] = item.get('failure_summary')
                    item_data['wcag_criteria'] = item.get('wcag_criteria', [])
                    item_data['help_url'] = item.get('help_url')
                    warnings.append(item_data)
                elif item_type == 'info':
                    info.append(item_data)
                elif item_type == 'discovery':
                    discovery.append(item_data)
                elif item_type == 'pass':
                    passes.append(item_data)

            # Add arrays back to doc for TestResult.from_dict()
            doc['violations'] = violations
            doc['warnings'] = warnings
            doc['info'] = info
            doc['discovery'] = discovery
            doc['passes'] = passes

        # Old schema already has arrays, just use as-is
        return TestResult.from_dict(doc)

    def get_latest_test_result_summary(self, page_id: str) -> dict[str, Any] | None:
        """Get summary counts for the latest test result without loading items.
        Returns a lightweight dict, NOT a full TestResult object."""
        doc = self.test_results.find_one(
            {"page_id": page_id},
            sort=[("test_date", -1)]
        )
        if not doc:
            return None
        return {
            'id': str(doc['_id']),
            'page_id': page_id,
            'violation_count': doc.get('violation_count', 0),
            'warning_count': doc.get('warning_count', 0),
            'info_count': doc.get('info_count', 0),
            'discovery_count': doc.get('discovery_count', 0),
            'pass_count': doc.get('pass_count', 0),
            'test_date': doc.get('test_date'),
            'score': doc.get('score'),
        }

    def get_test_results(
        self,
        page_id: str | None = None,
        page_ids: set[str] | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 0,
        skip: int = 0,
        summary_only: bool = False
    ) -> list[TestResult]:
        """Get test results with optional filtering

        Args:
            page_id: Single page ID to filter by
            page_ids: Set of page IDs to filter by (filters at database level for efficiency)
            start_date: Filter results on or after this date
            end_date: Filter results on or before this date
            limit: Maximum results to return
            skip: Number of results to skip (for pagination)
            summary_only: If True, skip loading detailed items (much faster for trend analysis)
        """
        query: dict[str, Any] = {}
        if page_id:
            query["page_id"] = page_id
        elif page_ids:
            # Filter by multiple page IDs at the database level - much more efficient
            query["page_id"] = {"$in": list(page_ids)}
        if start_date or end_date:
            query["test_date"] = {}
            if start_date:
                query["test_date"]["$gte"] = start_date
            if end_date:
                query["test_date"]["$lte"] = end_date

        docs = self.test_results.find(query).limit(limit).skip(skip).sort("test_date", -1)
        results: list[TestResult] = []
        for doc in docs:
            # Check if this uses the new schema (split items)
            # Skip loading detailed items if summary_only=True (for trend analysis)
            if doc.get('_has_detailed_items') and not summary_only:
                # Load items from test_result_items collection
                test_result_id = doc['_id']
                items = self._get_test_result_items(test_result_id)

                # Group items by type
                violations: list[dict[str, Any]] = []
                warnings: list[dict[str, Any]] = []
                info: list[dict[str, Any]] = []
                discovery: list[dict[str, Any]] = []
                passes: list[dict[str, Any]] = []

                for item in items:
                    item_data: dict[str, Any] = {
                        'id': item.get('issue_id'),
                        'impact': item.get('impact'),
                        'touchpoint': item.get('touchpoint'),
                        'xpath': item.get('xpath'),
                        'element': item.get('element'),
                        'html': item.get('html'),
                        'description': item.get('description'),
                        'metadata': item.get('metadata', {})
                    }

                    item_type = item.get('item_type')
                    if item_type == 'violation':
                        item_data['failure_summary'] = item.get('failure_summary')
                        item_data['wcag_criteria'] = item.get('wcag_criteria', [])
                        item_data['help_url'] = item.get('help_url')
                        violations.append(item_data)
                    elif item_type == 'warning':
                        item_data['failure_summary'] = item.get('failure_summary')
                        item_data['wcag_criteria'] = item.get('wcag_criteria', [])
                        item_data['help_url'] = item.get('help_url')
                        warnings.append(item_data)
                    elif item_type == 'info':
                        info.append(item_data)
                    elif item_type == 'discovery':
                        discovery.append(item_data)
                    elif item_type == 'pass':
                        passes.append(item_data)

                # Add arrays back to doc
                doc['violations'] = violations
                doc['warnings'] = warnings
                doc['info'] = info
                doc['discovery'] = discovery
                doc['passes'] = passes

            # Old schema already has arrays, or we just loaded them
            results.append(TestResult.from_dict(doc))

        return results
    
    def delete_test_result(self, result_id: str) -> bool:
        """Delete test result"""
        result = self.test_results.delete_one({"_id": ObjectId(result_id)})
        return result.deleted_count > 0

    # Multi-state test result methods

    def get_test_results_by_session(self, session_id: str) -> list[TestResult]:
        """
        Get all test results for a script execution session

        Args:
            session_id: Script execution session ID

        Returns:
            List of test results ordered by state_sequence
        """
        docs = self.test_results.find({"session_id": session_id}).sort("state_sequence", 1)
        results: list[TestResult] = []
        for doc in docs:
            # Check if this uses the new schema (split items)
            if doc.get('_has_detailed_items'):
                # Load items from test_result_items collection
                test_result_id = doc['_id']
                items = self._get_test_result_items(test_result_id)

                # Group items by type
                violations_l: list[dict[str, Any]] = []
                warnings_l: list[dict[str, Any]] = []
                info_l: list[dict[str, Any]] = []
                discovery_l: list[dict[str, Any]] = []
                passes_l: list[dict[str, Any]] = []

                for item in items:
                    item_data: dict[str, Any] = {
                        'id': item.get('issue_id'),
                        'impact': item.get('impact'),
                        'touchpoint': item.get('touchpoint'),
                        'xpath': item.get('xpath'),
                        'element': item.get('element'),
                        'html': item.get('html'),
                        'description': item.get('description'),
                        'metadata': item.get('metadata', {})
                    }

                    item_type = item.get('item_type')
                    if item_type == 'violation':
                        item_data['failure_summary'] = item.get('failure_summary')
                        item_data['wcag_criteria'] = item.get('wcag_criteria', [])
                        item_data['help_url'] = item.get('help_url')
                        violations_l.append(item_data)
                    elif item_type == 'warning':
                        item_data['failure_summary'] = item.get('failure_summary')
                        item_data['wcag_criteria'] = item.get('wcag_criteria', [])
                        item_data['help_url'] = item.get('help_url')
                        warnings_l.append(item_data)
                    elif item_type == 'info':
                        info_l.append(item_data)
                    elif item_type == 'discovery':
                        discovery_l.append(item_data)
                    elif item_type == 'pass':
                        passes_l.append(item_data)

                # Add arrays back to doc for TestResult.from_dict()
                doc['violations'] = violations_l
                doc['warnings'] = warnings_l
                doc['info'] = info_l
                doc['discovery'] = discovery_l
                doc['passes'] = passes_l

            # Old schema already has arrays, or we just added them
            result = TestResult.from_dict(doc)
            results.append(result)
        return results

    def get_test_results_by_page_and_session(
        self,
        page_id: str,
        session_id: str
    ) -> list[TestResult]:
        """
        Get all test results for a specific page in a session

        Args:
            page_id: Page ID
            session_id: Script execution session ID

        Returns:
            List of test results ordered by state_sequence
        """
        docs = self.test_results.find({
            "page_id": page_id,
            "session_id": session_id
        }).sort("state_sequence", 1)
        return [TestResult.from_dict(doc) for doc in docs]

    def get_related_test_results(self, result_id: str) -> list[TestResult]:
        """
        Get all test results related to a specific result

        Args:
            result_id: Test result ID

        Returns:
            List of related test results
        """
        # First get the result to find its related IDs
        result = self.get_test_result(result_id)
        if not result or not result.related_result_ids:
            return []

        # Get all related results
        object_ids = [ObjectId(rid) for rid in result.related_result_ids]
        docs = self.test_results.find({"_id": {"$in": object_ids}}).sort("state_sequence", 1)
        return [TestResult.from_dict(doc) for doc in docs]

    def get_latest_test_results_per_state(
        self,
        page_id: str,
        limit: int = 1
    ) -> dict[int, TestResult]:
        """
        Get the most recent test result for each state sequence

        Args:
            page_id: Page ID
            limit: Number of test runs to consider (default: most recent)

        Returns:
            Dictionary of {state_sequence: TestResult}
        """
        # Get all test results for page, grouped by session
        pipeline: list[dict[str, Any]] = [
            {"$match": {"page_id": page_id}},
            {"$sort": {"test_date": -1}},
            {"$group": {
                "_id": {"session_id": "$session_id", "state_sequence": "$state_sequence"},
                "latest": {"$first": "$$ROOT"}
            }},
            {"$limit": limit * 10}  # Get more than we need to handle multiple sequences
        ]

        results = self.test_results.aggregate(pipeline)
        state_results: dict[int, TestResult] = {}

        for doc in results:
            result = TestResult.from_dict(doc['latest'])
            state_seq = result.state_sequence
            # Only keep the most recent for each state
            if state_seq not in state_results or result.test_date > state_results[state_seq].test_date:
                state_results[state_seq] = result

        return state_results

    # Query methods for reporting

    def get_violations_by_issue(self, test_result_id: str, item_type: str = 'violation') -> dict[str, list[dict[str, Any]]]:
        """
        Get violations grouped by issue_id for deduplication in reports

        Args:
            test_result_id: Test result ID
            item_type: Type of items to get (violation, warning, info, discovery, pass)

        Returns:
            Dictionary of {issue_id: [list of items]}
        """
        items = self._get_test_result_items(ObjectId(test_result_id), item_type=item_type)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            issue_id: str = item.get('issue_id', '')
            if issue_id not in grouped:
                grouped[issue_id] = []
            grouped[issue_id].append(item)

        return grouped

    def count_violations_by_type(self, test_result_id: str) -> dict[str, int]:
        """
        Get counts of violations grouped by issue_id using aggregation

        Args:
            test_result_id: Test result ID

        Returns:
            Dictionary of {issue_id: count}
        """
        pipeline: list[dict[str, Any]] = [
            {'$match': {
                'test_result_id': ObjectId(test_result_id),
                'item_type': 'violation'
            }},
            {'$group': {
                '_id': '$issue_id',
                'count': {'$sum': 1}
            }},
            {'$sort': {'count': -1}}
        ]

        results = self.test_result_items.aggregate(pipeline)
        return {item['_id']: item['count'] for item in results}

    def get_violations_by_touchpoint(self, test_result_id: str) -> dict[str, int]:
        """
        Get violation counts grouped by touchpoint

        Args:
            test_result_id: Test result ID

        Returns:
            Dictionary of {touchpoint: count}
        """
        pipeline: list[dict[str, Any]] = [
            {'$match': {
                'test_result_id': ObjectId(test_result_id),
                'item_type': 'violation'
            }},
            {'$group': {
                '_id': '$touchpoint',
                'count': {'$sum': 1}
            }},
            {'$sort': {'count': -1}}
        ]

        results = self.test_result_items.aggregate(pipeline)
        return {item['_id']: item['count'] for item in results}

    def get_sample_violations(self, test_result_id: str, issue_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Get sample violations for a specific issue (for displaying in reports)

        Args:
            test_result_id: Test result ID
            issue_id: Issue ID to get samples for
            limit: Maximum number of samples to return

        Returns:
            List of sample violation items
        """
        items = list(self.test_result_items.find({
            'test_result_id': ObjectId(test_result_id),
            'item_type': 'violation',
            'issue_id': issue_id
        }).limit(limit))

        return items

    # Statistics
    
    def get_project_stats(
        self,
        project_id: str,
        pdf_storage: "PdfStorage | None" = None,
    ) -> dict[str, Any]:
        """Get statistics for a project.

        ``pdf_storage`` is optional only because non-web callers (CLI
        tooling, fixture scripts) may not have web app config loaded.
        Web routes that render the project overview MUST pass it so
        the FAIL/WARN totals include audited PDFs and stay consistent
        with the per-website badges shown directly below the totals
        on the same page (issues-counts branch fix).
        """
        from auto_a11y.core.issue_aggregator import (
            ZERO_ISSUE_COUNTS,
            count_html_page_issues,
            count_pdf_issues,
        )

        websites = self.get_websites(project_id)

        html_total = 0
        html_tested = 0
        tested_page_ids: list[str | None] = []

        for website in websites:
            if not website.id:
                continue
            pages = self.get_pages(website.id)
            html_total += len(pages)

            for page in pages:
                if page.status == PageStatus.TESTED:
                    html_tested += 1
                    tested_page_ids.append(page.id)

        # PDFs are testable documents too. They count toward both the
        # "documents in the project" denominator and the "tested" numerator
        # so the project overview's coverage percentage matches what the
        # user actually sees: a project of one audited PDF reads 1/1 (100%)
        # rather than 0/0. AUDITING is not "tested yet" — it's in flight —
        # so we only credit AUDITED. FETCH_FAILED is excluded from the
        # denominator because the file never made it to a state where
        # auditing is possible (mirrors the website-detail "documents"
        # math in auto_a11y/web/routes/websites.py).
        pdf_total = 0
        pdf_tested = 0
        project_pdfs: list[PdfDocument] = []
        if pdf_storage is not None:
            project_pdfs = self.get_pdf_documents(project_id=project_id, limit=10000)
            for pdf in project_pdfs:
                if pdf.status is PdfDocumentStatus.FETCH_FAILED:
                    continue
                pdf_total += 1
                if pdf.status is PdfDocumentStatus.AUDITED:
                    pdf_tested += 1

        total_documents = html_total + pdf_total
        tested_documents = html_tested + pdf_tested

        html_counts = count_html_page_issues(self, tested_page_ids)
        pdf_counts = ZERO_ISSUE_COUNTS
        if pdf_storage is not None:
            pdf_counts = count_pdf_issues(project_pdfs, pdf_storage)
        totals = html_counts + pdf_counts

        return {
            "website_count": len(websites),
            # HTML-only counts retained for callers/templates that still
            # distinguish pages from PDFs.
            "html_page_count": html_total,
            "tested_html_pages": html_tested,
            "pdf_count": pdf_total,
            "tested_pdfs": pdf_tested,
            # Combined "documents" — what the project overview now displays.
            "total_pages": total_documents,
            "tested_pages": tested_documents,
            "untested_pages": total_documents - tested_documents,
            "total_violations": totals.violations,
            "total_warnings": totals.warnings,
            "test_coverage": (
                (tested_documents / total_documents * 100)
                if total_documents > 0 else 0
            ),
        }
    
    # Document Reference methods
    def add_document_reference(self, doc_ref: DocumentReference) -> str:
        """Add or update a document reference"""
        # Check if this document already exists for this website
        existing = self.document_references.find_one({
            'website_id': doc_ref.website_id,
            'document_url': doc_ref.document_url
        })
        
        if existing:
            # Update existing reference
            self.document_references.update_one(
                {'_id': existing['_id']},
                {
                    '$set': {
                        'last_seen': doc_ref.last_seen,
                        'via_redirect': doc_ref.via_redirect or existing.get('via_redirect', False)
                    },
                    '$inc': {'seen_count': 1},
                    '$addToSet': {'referring_pages': doc_ref.referring_page_url}
                }
            )
            return str(existing['_id'])
        else:
            # Add new reference
            doc_data = doc_ref.to_dict()
            doc_data['referring_pages'] = [doc_ref.referring_page_url]
            result = self.document_references.insert_one(doc_data)
            return str(result.inserted_id)
    
    def get_document_references(self, website_id: str, internal_only: bool | None = None) -> list[DocumentReference]:
        """Get document references for a website"""

        
        query: dict[str, Any] = {'website_id': website_id}
        if internal_only is not None:
            query['is_internal'] = internal_only
        
        docs = self.document_references.find(query)
        return [DocumentReference.from_dict(doc) for doc in docs]
    
    def get_all_document_references(self, project_id: str | None = None) -> list[DocumentReference]:
        """Get all document references, optionally filtered by project"""

        
        if project_id:
            # Get all websites for the project first
            websites = self.get_websites(project_id)
            website_ids = [w.id for w in websites]
            docs = self.document_references.find({'website_id': {'$in': website_ids}})
        else:
            docs = self.document_references.find({})
        
        return [DocumentReference.from_dict(doc) for doc in docs]
    
    def delete_document_references(self, website_id: str) -> bool:
        """Delete all document references for a website"""
        result = self.document_references.delete_many({'website_id': website_id})
        return result.deleted_count > 0

    # PDF document methods

    def create_pdf_document(self, doc: PdfDocument) -> str:
        """Insert a new PdfDocument and return its string id.

        Honors a pre-set ``doc._id`` (via :attr:`PdfDocument.mongo_id`) when
        the caller has reserved an ``ObjectId`` ahead of insertion. This is
        the path the :class:`~auto_a11y.testing.pdf_runner.PdfRunner` uses
        so the on-disk storage layout (which embeds the document id in its
        path) and the Mongo ``_id`` agree from the very first insert. When
        no id is reserved, Mongo assigns one and we back-fill it onto the
        passed-in ``PdfDocument`` so downstream code can read ``doc.id``.
        """
        data = doc.to_dict()
        if doc.mongo_id is None:
            # ``to_dict`` only adds '_id' when set, so it's already absent.
            result = self.pdf_documents.insert_one(data)
            doc.mongo_id = result.inserted_id
            return str(result.inserted_id)
        # Pre-allocated id: ``to_dict`` has already embedded it.
        result = self.pdf_documents.insert_one(data)
        return str(result.inserted_id)

    def get_pdf_document(self, pdf_document_id: str) -> PdfDocument | None:
        """Get a PdfDocument by id, or None if not found / id invalid."""
        try:
            obj_id = ObjectId(pdf_document_id)
        except Exception:
            return None
        doc = self.pdf_documents.find_one({"_id": obj_id})
        return PdfDocument.from_dict(doc) if doc else None

    def update_pdf_document(self, doc: PdfDocument) -> bool:
        """Update an existing PdfDocument; raises ValueError if it has no _id."""
        mongo_id = doc.mongo_id
        if mongo_id is None:
            raise ValueError("Cannot update a PdfDocument without _id")
        data = doc.to_dict()
        data.pop('_id', None)
        result = self.pdf_documents.update_one({"_id": mongo_id}, {"$set": data})
        return result.modified_count > 0

    def delete_pdf_document(self, pdf_document_id: str) -> bool:
        """Delete a PdfDocument and its associated test_results."""
        try:
            obj_id = ObjectId(pdf_document_id)
        except Exception:
            return False
        # Cascade: delete associated test results first
        self.test_results.delete_many(
            {"target_type": "pdf_document", "target_id": pdf_document_id}
        )
        result = self.pdf_documents.delete_one({"_id": obj_id})
        return result.deleted_count > 0

    def find_pdf_document_by_sha256(
        self, website_id: str, sha256: str
    ) -> PdfDocument | None:
        """Find a PdfDocument by (website_id, sha256), the dedup key."""
        doc = self.pdf_documents.find_one(
            {"website_id": website_id, "sha256": sha256}
        )
        return PdfDocument.from_dict(doc) if doc else None

    def find_pdf_document_by_url(
        self, website_id: str, source_url: str
    ) -> PdfDocument | None:
        """Find any PdfDocument fetched from ``source_url`` for a website.

        Used by discovery's auto-fetch step to skip re-downloading URLs
        that have already been ingested. SHA-256 dedup still applies for
        documents that arrive via a different URL.
        """
        doc = self.pdf_documents.find_one(
            {"website_id": website_id, "source_url": source_url}
        )
        return PdfDocument.from_dict(doc) if doc else None

    def get_pdf_documents(
        self,
        *,
        website_id: str | None = None,
        project_id: str | None = None,
        status: PdfDocumentStatus | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[PdfDocument]:
        """List PdfDocuments with optional filters, newest discovery first."""
        query: dict[str, object] = {}
        if website_id is not None:
            query['website_id'] = website_id
        if project_id is not None:
            query['project_id'] = project_id
        if status is not None:
            query['status'] = status.value
        docs = (
            self.pdf_documents.find(query)
            .sort("discovered_at", -1)
            .skip(skip)
            .limit(limit)
        )
        return [PdfDocument.from_dict(d) for d in docs]

    # Discovery Run methods
    def create_discovery_run(self, discovery_run: DiscoveryRun) -> str:
        """Create a new discovery run"""

        
        # Mark all previous runs for this website as not latest
        self.discovery_runs.update_many(
            {'website_id': discovery_run.website_id},
            {'$set': {'is_latest': False}}
        )
        
        # Insert new discovery run
        result = self.discovery_runs.insert_one(discovery_run.to_dict())
        discovery_run.mongo_id = result.inserted_id
        discovery_run_id = str(result.inserted_id)
        logger.info(f"Created discovery run {discovery_run_id} for website {discovery_run.website_id}")
        return discovery_run_id
    
    def get_discovery_run(self, discovery_run_id: str) -> DiscoveryRun | None:
        """Get a discovery run by ID"""

        
        doc = self.discovery_runs.find_one({'_id': ObjectId(discovery_run_id)})
        return DiscoveryRun.from_dict(doc) if doc else None
    
    def get_latest_discovery_run(self, website_id: str) -> DiscoveryRun | None:
        """Get the latest discovery run for a website"""

        
        doc = self.discovery_runs.find_one({
            'website_id': website_id,
            'is_latest': True
        })
        return DiscoveryRun.from_dict(doc) if doc else None
    
    def get_discovery_runs(self, website_id: str) -> list[DiscoveryRun]:
        """Get all discovery runs for a website, ordered by date descending"""

        
        docs = self.discovery_runs.find({'website_id': website_id}).sort('started_at', -1)
        return [DiscoveryRun.from_dict(doc) for doc in docs]
    
    def update_discovery_run(self, discovery_run: DiscoveryRun) -> bool:
        """Update a discovery run"""
        result = self.discovery_runs.update_one(
            {'_id': discovery_run.mongo_id},
            {'$set': discovery_run.to_dict()}
        )
        return result.modified_count > 0
    
    def bulk_create_pages_with_discovery(self, pages: list[Page], discovery_run_id: str) -> int:
        """Create multiple pages with discovery run tracking"""
        if not pages:
            return 0

        # Get existing pages for this website
        website_id = pages[0].website_id
        existing_urls: set[str] = set()
        for doc in self.pages.find({'website_id': website_id}, {'url': 1}):
            url_val: str = doc['url']
            existing_urls.add(url_val)

        # Mark all existing pages as not in latest discovery
        self.pages.update_many(
            {'website_id': website_id},
            {'$set': {'is_in_latest_discovery': False}}
        )

        # Process new and existing pages
        new_pages: list[dict[str, Any]] = []
        updated_count = 0

        for page in pages:
            page.discovery_run_id = discovery_run_id
            page.is_in_latest_discovery = True

            if page.url in existing_urls:
                # Update existing page
                self.pages.update_one(
                    {'website_id': website_id, 'url': page.url},
                    {'$set': {
                        'discovery_run_id': discovery_run_id,
                        'is_in_latest_discovery': True,
                        'discovered_at': page.discovered_at,
                        'title': page.title,
                        'depth': page.depth,
                        'status': page.status.value if hasattr(page.status, 'value') else page.status,
                        'error_reason': page.error_reason
                    }}
                )
                updated_count += 1
            else:
                # New page
                new_pages.append(page.to_dict())

        # Insert new pages
        if new_pages:
            insert_result = self.pages.insert_many(new_pages)

            # Update website page count
            self.websites.update_one(
                {"_id": ObjectId(website_id)},
                {"$inc": {"page_count": len(new_pages)}}
            )
            logger.info(f"Added {len(new_pages)} new pages, updated {updated_count} existing pages")

            return len(insert_result.inserted_ids) + updated_count

        return updated_count

    def mark_pages_not_in_latest_discovery(self, website_id: str) -> int:
        """Mark all existing pages for a website as not in the latest discovery.

        Call this once at the start of a discovery run so that pages
        can be individually marked back as is_in_latest_discovery=True
        as they are found.

        Returns:
            Number of pages updated
        """
        result = self.pages.update_many(
            {'website_id': website_id},
            {'$set': {'is_in_latest_discovery': False}}
        )
        return result.modified_count

    def save_discovered_page(self, page: Page, discovery_run_id: str) -> str:
        """Save a single discovered page immediately (upsert).

        If the page URL already exists for the website, update it.
        Otherwise insert a new page.

        Args:
            page: The discovered Page object
            discovery_run_id: The current discovery run ID

        Returns:
            The page ID (str)
        """
        page.discovery_run_id = discovery_run_id
        page.is_in_latest_discovery = True

        existing = self.pages.find_one(
            {'website_id': page.website_id, 'url': page.url},
            {'_id': 1}
        )

        if existing:
            # Update existing page
            self.pages.update_one(
                {'_id': existing['_id']},
                {'$set': {
                    'discovery_run_id': discovery_run_id,
                    'is_in_latest_discovery': True,
                    'discovered_at': page.discovered_at,
                    'title': page.title,
                    'depth': page.depth,
                    'status': page.status.value if hasattr(page.status, 'value') else page.status,
                    'error_reason': page.error_reason
                }}
            )
            page.mongo_id = existing['_id']
            return str(existing['_id'])
        else:
            # Insert new page
            result = self.pages.insert_one(page.to_dict())
            page.mongo_id = result.inserted_id
            # Update website page count
            self.websites.update_one(
                {"_id": ObjectId(page.website_id)},
                {"$inc": {"page_count": 1}}
            )
            return str(result.inserted_id)
    
    def get_pages_for_testing(self, website_id: str, status: PageStatus | None = None) -> list[Page]:
        """Get pages for testing (only from latest discovery)"""
        query: dict[str, Any] = {
            'website_id': website_id,
            'is_in_latest_discovery': True
        }
        
        if status:
            query['status'] = status.value
        
        docs = self.pages.find(query)
        return [Page.from_dict(doc) for doc in docs]
    
    def compare_discoveries(self, website_id: str, old_run_id: str, new_run_id: str) -> dict[str, Any]:
        """Compare two discovery runs to find added/removed pages"""
        
        # Get pages from old discovery
        old_pages: set[str] = set()
        for doc in self.pages.find({'website_id': website_id, 'discovery_run_id': old_run_id}, {'url': 1}):
            old_url: str = doc['url']
            old_pages.add(old_url)

        # Get pages from new discovery
        new_pages_set: set[str] = set()
        for doc in self.pages.find({'website_id': website_id, 'discovery_run_id': new_run_id}, {'url': 1}):
            new_url: str = doc['url']
            new_pages_set.add(new_url)
        
        # Calculate differences
        pages_added = new_pages_set - old_pages
        pages_removed = old_pages - new_pages_set
        pages_unchanged = old_pages & new_pages_set

        return {
            'pages_added': list(pages_added),
            'pages_removed': list(pages_removed),
            'pages_unchanged': list(pages_unchanged),
            'added_count': len(pages_added),
            'removed_count': len(pages_removed),
            'unchanged_count': len(pages_unchanged)
        }

    # Issue Documentation Status Methods

    def get_issue_documentation_status(self, issue_code: str) -> dict[str, Any] | None:
        """Get documentation status for an issue code"""
        return self.issue_documentation_status.find_one({'issue_code': issue_code})

    def set_issue_production_ready(self, issue_code: str, production_ready: bool, updated_by: str = "system") -> bool:
        """Set the production_ready flag for an issue code"""
        from datetime import datetime

        result = self.issue_documentation_status.update_one(
            {'issue_code': issue_code},
            {
                '$set': {
                    'production_ready': production_ready,
                    'updated_by': updated_by,
                    'updated_at': datetime.now()
                },
                '$setOnInsert': {
                    'issue_code': issue_code,
                    'created_at': datetime.now()
                }
            },
            upsert=True
        )

        return result.modified_count > 0 or result.upserted_id is not None

    def get_all_issue_documentation_statuses(self) -> dict[str, bool]:
        """Get all issue documentation statuses as a dict of issue_code -> production_ready"""
        statuses: dict[str, bool] = {}
        for doc in self.issue_documentation_status.find():
            code: str = doc['issue_code']
            statuses[code] = bool(doc.get('production_ready', False))
        return statuses

    def get_production_ready_issues(self) -> list[str]:
        """Get list of issue codes marked as production ready"""
        docs = self.issue_documentation_status.find({'production_ready': True})
        return [doc['issue_code'] for doc in docs]

    def get_not_production_ready_issues(self) -> list[str]:
        """Get list of issue codes not marked as production ready"""
        docs = self.issue_documentation_status.find({'production_ready': {'$ne': True}})
        return [doc['issue_code'] for doc in docs]

    # Page Setup Script Methods

    def create_page_setup_script(self, script: PageSetupScript) -> str:
        """
        Create a new page setup script

        Args:
            script: PageSetupScript object

        Returns:
            Script ID as string
        """


        result = self.page_setup_scripts.insert_one(script.to_dict())
        script.mongo_id = result.inserted_id
        script_id = str(result.inserted_id)
        logger.info(f"Created page setup script: {script.name} ({script_id}) for page {script.page_id}")
        return script_id

    def get_page_setup_script(self, script_id: str) -> PageSetupScript | None:
        """
        Get a page setup script by ID

        Args:
            script_id: Script ID

        Returns:
            PageSetupScript object or None
        """


        doc = self.page_setup_scripts.find_one({"_id": ObjectId(script_id)})
        return PageSetupScript.from_dict(doc) if doc else None

    def get_page_setup_scripts_for_page(self, page_id: str, enabled_only: bool = False) -> list[PageSetupScript]:
        """
        Get all setup scripts for a page

        Args:
            page_id: Page ID
            enabled_only: If True, only return enabled scripts

        Returns:
            List of PageSetupScript objects
        """


        query: dict[str, Any] = {"page_id": page_id}
        if enabled_only:
            query["enabled"] = True

        docs = self.page_setup_scripts.find(query).sort("created_date", -1)
        return [PageSetupScript.from_dict(doc) for doc in docs]

    def get_enabled_script_for_page(self, page_id: str) -> PageSetupScript | None:
        """
        Get the enabled setup script for a page (returns first enabled script)

        Args:
            page_id: Page ID

        Returns:
            PageSetupScript object or None
        """


        doc = self.page_setup_scripts.find_one(
            {"page_id": page_id, "enabled": True},
            sort=[("created_date", -1)]
        )
        return PageSetupScript.from_dict(doc) if doc else None

    def update_page_setup_script(self, script: PageSetupScript) -> bool:
        """
        Update an existing page setup script

        Args:
            script: PageSetupScript object with updates

        Returns:
            True if updated successfully
        """
        if not script.mongo_id:
            logger.error("Cannot update script without _id")
            return False

        script.update_timestamp()

        # Get dict and remove _id (can't update _id in MongoDB)
        update_data = script.to_dict()
        if '_id' in update_data:
            del update_data['_id']

        logger.debug(f"Updating script {script.mongo_id} with data keys: {list(update_data.keys())}")

        try:
            result = self.page_setup_scripts.update_one(
                {"_id": script.mongo_id},
                {"$set": update_data}
            )
        except Exception as e:
            logger.error(f"MongoDB update error: {e}")
            logger.error(f"Script ID: {script.mongo_id}, type: {type(script.mongo_id)}")
            import traceback
            logger.error(traceback.format_exc())
            raise

        if result.modified_count > 0:
            logger.info(f"Updated page setup script: {script.name} ({script.id})")
            return True
        return False

    def delete_page_setup_script(self, script_id: str) -> bool:
        """
        Delete a page setup script

        Args:
            script_id: Script ID

        Returns:
            True if deleted successfully
        """
        result = self.page_setup_scripts.delete_one({"_id": ObjectId(script_id)})

        if result.deleted_count > 0:
            logger.info(f"Deleted page setup script: {script_id}")
            return True
        return False

    def enable_page_setup_script(self, script_id: str, enabled: bool = True) -> bool:
        """
        Enable or disable a page setup script

        Args:
            script_id: Script ID
            enabled: True to enable, False to disable

        Returns:
            True if updated successfully
        """
        result = self.page_setup_scripts.update_one(
            {"_id": ObjectId(script_id)},
            {
                "$set": {
                    "enabled": enabled,
                    "last_modified": datetime.now()
                }
            }
        )

        if result.modified_count > 0:
            logger.info(f"{'Enabled' if enabled else 'Disabled'} page setup script: {script_id}")
            return True
        return False

    def update_script_execution_stats(
        self,
        script_id: str,
        success: bool,
        duration_ms: int
    ) -> bool:
        """
        Update execution statistics for a script

        Args:
            script_id: Script ID
            success: Whether execution was successful
            duration_ms: Execution duration in milliseconds

        Returns:
            True if updated successfully
        """
        # Get current stats
        script = self.get_page_setup_script(script_id)
        if not script:
            return False

        stats = script.execution_stats

        # Update stats
        stats.last_executed = datetime.now()
        if success:
            stats.success_count += 1
        else:
            stats.failure_count += 1

        # Update average duration (rolling average)
        total_executions = stats.success_count + stats.failure_count
        if total_executions > 0:
            stats.average_duration_ms = int(
                (stats.average_duration_ms * (total_executions - 1) + duration_ms) / total_executions
            )

        # Save updated stats
        result = self.page_setup_scripts.update_one(
            {"_id": ObjectId(script_id)},
            {"$set": {"execution_stats": stats.to_dict()}}
        )

        return result.modified_count > 0

    def get_scripts_for_website(
        self,
        website_id: str,
        scope: str | None = None,
        enabled_only: bool = True
    ) -> list[PageSetupScript]:
        """
        Get scripts for a website (any scope or specific scope)

        Args:
            website_id: Website ID
            scope: Optional scope filter ("website", "page", "test_run")
            enabled_only: If True, only return enabled scripts

        Returns:
            List of PageSetupScript objects
        """


        query: dict[str, Any] = {"website_id": website_id}
        if scope:
            query["scope"] = scope
        if enabled_only:
            query["enabled"] = True

        docs = self.page_setup_scripts.find(query).sort("created_date", -1)
        return [PageSetupScript.from_dict(doc) for doc in docs]

    def get_scripts_for_page_v2(
        self,
        page_id: str,
        website_id: str,
        enabled_only: bool = True
    ) -> list[PageSetupScript]:
        """
        Get all applicable scripts for a page (both page-level and website-level)

        Args:
            page_id: Page ID
            website_id: Website ID
            enabled_only: If True, only return enabled scripts

        Returns:
            List of PageSetupScript objects (website-level + page-level)
        """
        from auto_a11y.models import ScriptScope

        query_filter = {"enabled": True} if enabled_only else {}

        # Get website-level scripts
        website_query = {
            "website_id": website_id,
            "scope": ScriptScope.WEBSITE.value,
            **query_filter
        }
        website_scripts = list(self.page_setup_scripts.find(website_query))

        # Get page-level scripts
        page_query = {
            "page_id": page_id,
            "scope": ScriptScope.PAGE.value,
            **query_filter
        }
        page_scripts = list(self.page_setup_scripts.find(page_query))

        # Combine and return
        all_scripts = website_scripts + page_scripts
        return [PageSetupScript.from_dict(doc) for doc in all_scripts]

    # Script Execution Session Methods

    def create_script_session(self, session: ScriptExecutionSession) -> str:
        """
        Create a new script execution session

        Args:
            session: ScriptExecutionSession object

        Returns:
            Session ID as string
        """


        result = self.script_execution_sessions.insert_one(session.to_dict())
        session.mongo_id = result.inserted_id
        logger.info(f"Created script execution session: {session.session_id} for website {session.website_id}")
        return session.session_id

    def get_script_session(self, session_id: str) -> ScriptExecutionSession | None:
        """
        Get a script execution session by session ID

        Args:
            session_id: Session ID (UUID string)

        Returns:
            ScriptExecutionSession object or None
        """


        doc = self.script_execution_sessions.find_one({"session_id": session_id})
        return ScriptExecutionSession.from_dict(doc) if doc else None

    def update_script_session(self, session: ScriptExecutionSession) -> bool:
        """
        Update an existing script execution session

        Args:
            session: ScriptExecutionSession object with updates

        Returns:
            True if updated successfully
        """
        if not session.mongo_id:
            logger.error("Cannot update session without _id")
            return False

        result = self.script_execution_sessions.update_one(
            {"_id": session.mongo_id},
            {"$set": session.to_dict()}
        )

        if result.modified_count > 0:
            logger.info(f"Updated script execution session: {session.session_id}")
            return True
        return False

    def get_latest_session_for_website(self, website_id: str) -> ScriptExecutionSession | None:
        """
        Get the most recent session for a website

        Args:
            website_id: Website ID

        Returns:
            ScriptExecutionSession object or None
        """


        doc = self.script_execution_sessions.find_one(
            {"website_id": website_id},
            sort=[("started_at", -1)]
        )
        return ScriptExecutionSession.from_dict(doc) if doc else None

    # ==================== Website Users ====================

    def create_website_user(self, user: WebsiteUser) -> str:
        """
        Create a new website user for authenticated testing

        Args:
            user: WebsiteUser object

        Returns:
            User ID as string
        """


        result = self.website_users.insert_one(user.to_dict())
        user.mongo_id = result.inserted_id
        logger.info(f"Created website user: {user.username} for website {user.website_id}")
        return str(result.inserted_id)

    def get_website_user(self, user_id: str) -> WebsiteUser | None:
        """
        Get a website user by ID

        Args:
            user_id: User ID

        Returns:
            WebsiteUser object or None
        """


        doc = self.website_users.find_one({"_id": ObjectId(user_id)})
        return WebsiteUser.from_dict(doc) if doc else None

    def get_website_users(self, website_id: str, enabled_only: bool = False, role: str | None = None) -> list[WebsiteUser]:
        """
        Get all users for a website

        Args:
            website_id: Website ID
            enabled_only: If True, only return enabled users
            role: If specified, only return users with this role

        Returns:
            List of WebsiteUser objects
        """


        query: dict[str, Any] = {"website_id": website_id}
        if enabled_only:
            query["enabled"] = True
        if role:
            query["roles"] = role

        docs = self.website_users.find(query).sort("display_name", 1)
        return [WebsiteUser.from_dict(doc) for doc in docs]

    def get_website_user_by_username(self, website_id: str, username: str) -> WebsiteUser | None:
        """
        Get a website user by username

        Args:
            website_id: Website ID
            username: Username

        Returns:
            WebsiteUser object or None
        """


        doc = self.website_users.find_one({
            "website_id": website_id,
            "username": username
        })
        return WebsiteUser.from_dict(doc) if doc else None

    def update_website_user(self, user: WebsiteUser) -> bool:
        """
        Update a website user

        Args:
            user: WebsiteUser object

        Returns:
            True if updated successfully
        """
        if not user.mongo_id:
            logger.error("Cannot update user without _id")
            return False

        user.update_timestamp()

        # Get dict and remove _id (can't update _id in MongoDB)
        update_data = user.to_dict()
        if '_id' in update_data:
            del update_data['_id']

        result = self.website_users.update_one(
            {"_id": user.mongo_id},
            {"$set": update_data}
        )

        if result.modified_count > 0:
            logger.info(f"Updated website user: {user.username} ({user.id})")
            return True
        return False

    def delete_website_user(self, user_id: str) -> bool:
        """
        Delete a website user

        Args:
            user_id: User ID

        Returns:
            True if deleted successfully
        """
        result = self.website_users.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count > 0:
            logger.info(f"Deleted website user: {user_id}")
            return True
        return False

    def get_user_roles_for_website(self, website_id: str) -> list[str]:
        """
        Get all unique roles used by users in a website

        Args:
            website_id: Website ID

        Returns:
            List of unique role names
        """
        pipeline: list[dict[str, Any]] = [
            {"$match": {"website_id": website_id}},
            {"$unwind": "$roles"},
            {"$group": {"_id": "$roles"}},
            {"$sort": {"_id": 1}}
        ]

        results = self.website_users.aggregate(pipeline)
        return [doc["_id"] for doc in results]

    # Project user operations (test users at project level)

    def create_project_user(self, user: ProjectUser) -> str:
        """Create a new project user for authenticated testing"""
        result = self.project_users.insert_one(user.to_dict())
        user.mongo_id = result.inserted_id
        logger.info(f"Created project user: {user.username} for project {user.project_id}")
        return str(result.inserted_id)

    def get_project_user(self, user_id: str) -> ProjectUser | None:
        """Get a project user by ID"""
        from auto_a11y.models import ProjectUser
        doc = self.project_users.find_one({"_id": ObjectId(user_id)})
        return ProjectUser.from_dict(doc) if doc else None

    def get_project_users(self, project_id: str, enabled_only: bool = False, role: str | None = None) -> list[ProjectUser]:
        """Get all users for a project"""
        from auto_a11y.models import ProjectUser
        query: dict[str, Any] = {"project_id": project_id}
        if enabled_only:
            query["enabled"] = True
        if role:
            query["roles"] = role
        docs = self.project_users.find(query).sort("display_name", 1)
        return [ProjectUser.from_dict(doc) for doc in docs]

    def get_project_user_by_username(self, project_id: str, username: str) -> ProjectUser | None:
        """Get a project user by username"""
        from auto_a11y.models import ProjectUser
        doc = self.project_users.find_one({"project_id": project_id, "username": username})
        return ProjectUser.from_dict(doc) if doc else None

    def update_project_user(self, user: ProjectUser) -> bool:
        """Update a project user"""
        if not user.mongo_id:
            logger.error("Cannot update user without _id")
            return False
        user.update_timestamp()
        update_data = user.to_dict()
        if '_id' in update_data:
            del update_data['_id']
        result = self.project_users.update_one({"_id": user.mongo_id}, {"$set": update_data})
        if result.modified_count > 0:
            logger.info(f"Updated project user: {user.username}")
            return True
        return False

    def delete_project_user(self, user_id: str) -> bool:
        """Delete a project user"""
        result = self.project_users.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count > 0:
            logger.info(f"Deleted project user: {user_id}")
            return True
        return False

    def get_user_roles_for_project(self, project_id: str) -> list[str]:
        """Get all unique roles used by users in a project"""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"project_id": project_id}},
            {"$unwind": "$roles"},
            {"$group": {"_id": "$roles"}},
            {"$sort": {"_id": 1}}
        ]
        results = self.project_users.aggregate(pipeline)
        return [doc["_id"] for doc in results]

    # Recording operations (Manual audits from Dictaphone)

    def create_recording(self, recording: Recording) -> str:
        """Create new recording"""
        result = self.recordings.insert_one(recording.to_dict())
        recording.mongo_id = result.inserted_id
        rec_id = str(result.inserted_id)
        logger.info(f"Created recording: {recording.recording_id} ({rec_id})")
        return rec_id

    def get_recording(self, recording_id: str) -> Recording | None:
        """Get recording by MongoDB ID"""
        doc = self.recordings.find_one({"_id": ObjectId(recording_id)})
        return Recording.from_dict(doc) if doc else None

    def get_recording_by_recording_id(self, recording_id: str) -> Recording | None:
        """Get recording by recording_id field (e.g., 'NED-A')"""
        doc = self.recordings.find_one({"recording_id": recording_id})
        return Recording.from_dict(doc) if doc else None

    def get_recordings(
        self,
        project_id: str | None = None,
        recording_type: RecordingType | None = None,
        limit: int = 0,
        skip: int = 0
    ) -> list[Recording]:
        """Get recordings with optional filtering"""
        query: dict[str, Any] = {}
        if project_id:
            query["project_id"] = project_id
        if recording_type:
            query["recording_type"] = recording_type.value

        docs = self.recordings.find(query).sort("recorded_date", -1).limit(limit).skip(skip)
        return [Recording.from_dict(doc) for doc in docs]

    def get_recordings_for_project(self, project_id: str) -> list[Recording]:
        """Get all recordings for a project"""
        docs = self.recordings.find({"project_id": project_id}).sort("recorded_date", -1)
        return [Recording.from_dict(doc) for doc in docs]

    def update_recording(self, recording: Recording) -> bool:
        """Update existing recording"""
        recording.updated_at = datetime.now()
        result = self.recordings.replace_one(
            {"_id": recording.mongo_id},
            recording.to_dict()
        )
        return result.modified_count > 0

    def delete_recording(self, recording_id: str) -> bool:
        """Delete recording and related issues"""
        # Get recording to find recording_id field
        recording = self.get_recording(recording_id)
        if recording:
            # Delete related issues
            self.recording_issues.delete_many({"recording_id": recording.recording_id})

        # Delete recording
        result = self.recordings.delete_one({"_id": ObjectId(recording_id)})
        logger.info(f"Deleted recording: {recording_id}")
        return result.deleted_count > 0

    def get_recording_count(self, project_id: str | None = None) -> int:
        """Get total count of recordings"""
        query: dict[str, str] = {}
        if project_id:
            query["project_id"] = project_id
        return self.recordings.count_documents(query)

    # Recording Issue operations

    def create_recording_issue(self, issue: RecordingIssue) -> str:
        """Create new recording issue"""
        result = self.recording_issues.insert_one(issue.to_dict())
        issue.mongo_id = result.inserted_id
        return str(result.inserted_id)

    def create_recording_issues_bulk(self, issues: list[RecordingIssue]) -> list[str]:
        """Create multiple recording issues at once"""
        if not issues:
            return []

        docs = [issue.to_dict() for issue in issues]
        result = self.recording_issues.insert_many(docs)

        # Update issue objects with their new IDs
        for issue, inserted_id in zip(issues, result.inserted_ids):
            issue.mongo_id = inserted_id

        logger.info(f"Created {len(issues)} recording issues")
        return [str(id) for id in result.inserted_ids]

    def get_recording_issue(self, issue_id: str) -> RecordingIssue | None:
        """Get recording issue by ID"""
        doc = self.recording_issues.find_one({"_id": ObjectId(issue_id)})
        return RecordingIssue.from_dict(doc) if doc else None

    def get_recording_issues(
        self,
        recording_id: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 0,
        skip: int = 0
    ) -> list[RecordingIssue]:
        """Get recording issues with optional filtering"""
        query: dict[str, Any] = {}
        if recording_id:
            query["recording_id"] = recording_id
        if project_id:
            query["project_id"] = project_id
        if status:
            query["status"] = status

        docs = self.recording_issues.find(query).limit(limit).skip(skip)
        return [RecordingIssue.from_dict(doc) for doc in docs]

    def get_recording_issues_for_recording(self, recording_id: str) -> list[RecordingIssue]:
        """Get all issues for a specific recording"""
        docs = self.recording_issues.find({"recording_id": recording_id})
        return [RecordingIssue.from_dict(doc) for doc in docs]

    def update_recording_issue(self, issue: RecordingIssue) -> bool:
        """Update existing recording issue"""
        issue.updated_at = datetime.now()
        result = self.recording_issues.replace_one(
            {"_id": issue.mongo_id},
            issue.to_dict()
        )
        return result.modified_count > 0

    def delete_recording_issue(self, issue_id: str) -> bool:
        """Delete recording issue"""
        result = self.recording_issues.delete_one({"_id": ObjectId(issue_id)})
        return result.deleted_count > 0

    def get_recording_issue_count(
        self,
        recording_id: str | None = None,
        project_id: str | None = None
    ) -> int:
        """Get total count of recording issues"""
        query: dict[str, Any] = {}
        if recording_id:
            query["recording_id"] = recording_id
        if project_id:
            query["project_id"] = project_id
        return self.recording_issues.count_documents(query)

    def update_recording_issue_status(self, issue_id: str, status: str) -> bool:
        """Update status of a recording issue"""
        result = self.recording_issues.update_one(
            {"_id": ObjectId(issue_id)},
            {
                "$set": {
                    "status": status,
                    "updated_at": datetime.now()
                }
            }
        )
        return result.modified_count > 0

    # ===== Discovered Pages =====

    def create_discovered_page(self, discovered_page: DiscoveredPage) -> str:
        """
        Create a new discovered page or return existing if duplicate.
        Uses unique index on (project_id, url) to prevent duplicates.

        Args:
            discovered_page: DiscoveredPage instance to create

        Returns:
            Discovered page ID (str)
        """
        discovered_page.created_at = datetime.now()
        discovered_page.updated_at = datetime.now()

        try:
            result = self.discovered_pages.insert_one(discovered_page.to_dict())
            discovered_page.mongo_id = result.inserted_id
            return str(result.inserted_id)
        except Exception as e:
            # If duplicate key error, find and return existing
            if "duplicate key" in str(e).lower() or "E11000" in str(e):
                existing = self.discovered_pages.find_one({
                    "project_id": discovered_page.project_id,
                    "url": discovered_page.url
                })
                if existing:
                    logger.info(f"Discovered page already exists for URL: {discovered_page.url}")
                    return str(existing["_id"])
            logger.error(f"Failed to create discovered page: {e}")
            raise

    def get_discovered_page_by_id(self, page_id: str) -> DiscoveredPage | None:
        """Get discovered page by ID"""
        doc = self.discovered_pages.find_one({"_id": ObjectId(page_id)})
        return DiscoveredPage.from_dict(doc) if doc else None

    def get_discovered_page_by_url(self, project_id: str, url: str) -> DiscoveredPage | None:
        """
        Get discovered page by project ID and URL.
        Used for deduplication check before creating new pages.

        Args:
            project_id: Project ID
            url: Page URL or component identifier

        Returns:
            DiscoveredPage if found, None otherwise
        """
        doc = self.discovered_pages.find_one({
            "project_id": project_id,
            "url": url
        })
        return DiscoveredPage.from_dict(doc) if doc else None

    def get_discovered_pages_for_project(
        self,
        project_id: str,
        source_type: str | None = None,
        include_in_report: bool | None = None
    ) -> list[DiscoveredPage]:
        """
        Get all discovered pages for a project with optional filtering.

        Args:
            project_id: Project ID
            source_type: Optional filter by source type
            include_in_report: Optional filter by include_in_report flag

        Returns:
            List of DiscoveredPage instances
        """
        query: dict[str, Any] = {"project_id": project_id}
        if source_type:
            query["source_type"] = source_type
        if include_in_report is not None:
            query["include_in_report"] = include_in_report

        docs = self.discovered_pages.find(query)
        return [DiscoveredPage.from_dict(doc) for doc in docs]

    def update_discovered_page(self, discovered_page: DiscoveredPage) -> bool:
        """Update existing discovered page"""
        discovered_page.updated_at = datetime.now()
        result = self.discovered_pages.replace_one(
            {"_id": discovered_page.mongo_id},
            discovered_page.to_dict()
        )
        return result.modified_count > 0

    def delete_discovered_page(self, page_id: str) -> bool:
        """Delete discovered page"""
        result = self.discovered_pages.delete_one({"_id": ObjectId(page_id)})
        return result.deleted_count > 0

    def get_discovered_pages_needing_sync(self, project_id: str | None = None) -> list[DiscoveredPage]:
        """
        Get discovered pages that need to be synced to Drupal.

        Args:
            project_id: Optional project ID filter

        Returns:
            List of DiscoveredPage instances that need sync
        """
        query: dict[str, Any] = {
            "drupal_sync_status": {"$in": ["not_synced", "sync_failed"]}
        }
        if project_id:
            query["project_id"] = project_id

        docs = self.discovered_pages.find(query)
        return [DiscoveredPage.from_dict(doc) for doc in docs]

    # ==========================================
    # Test State Matrix Methods
    # ==========================================

    def create_test_state_matrix(self, matrix: TestStateMatrix) -> str:
        """
        Create a new test state matrix

        Args:
            matrix: TestStateMatrix instance

        Returns:
            Matrix ID (string)
        """
        matrix_dict = matrix.to_dict()
        result = self.test_state_matrices.insert_one(matrix_dict)
        return str(result.inserted_id)

    def get_test_state_matrix(self, matrix_id: str) -> TestStateMatrix | None:
        """
        Get test state matrix by ID

        Args:
            matrix_id: Matrix ID

        Returns:
            TestStateMatrix instance or None
        """
        doc = self.test_state_matrices.find_one({"_id": ObjectId(matrix_id)})
        return TestStateMatrix.from_dict(doc) if doc else None

    def get_test_state_matrix_by_page(self, page_id: str) -> TestStateMatrix | None:
        """
        Get test state matrix for a specific page

        Args:
            page_id: Page ID

        Returns:
            TestStateMatrix instance or None
        """
        doc = self.test_state_matrices.find_one({"page_id": page_id})
        return TestStateMatrix.from_dict(doc) if doc else None

    def update_test_state_matrix(self, matrix: TestStateMatrix) -> None:
        """
        Update existing test state matrix

        Args:
            matrix: TestStateMatrix instance with updated data
        """
        if not matrix.mongo_id:
            raise ValueError("Matrix must have an ID to update")

        matrix.update_timestamp()
        matrix_dict = matrix.to_dict()

        self.test_state_matrices.update_one(
            {"_id": matrix.mongo_id},
            {"$set": matrix_dict}
        )
        logger.info(f"Updated test state matrix {matrix.id}")

    def delete_test_state_matrix(self, matrix_id: str) -> None:
        """
        Delete test state matrix

        Args:
            matrix_id: Matrix ID
        """
        self.test_state_matrices.delete_one({"_id": ObjectId(matrix_id)})
        logger.info(f"Deleted test state matrix {matrix_id}")

    def list_test_state_matrices(self, website_id: str | None = None) -> list[TestStateMatrix]:
        """
        List test state matrices

        Args:
            website_id: Optional website ID filter

        Returns:
            List of TestStateMatrix instances
        """
        query: dict[str, Any] = {}
        if website_id:
            query["website_id"] = website_id

        docs = self.test_state_matrices.find(query).sort("created_date", -1)
        return [TestStateMatrix.from_dict(doc) for doc in docs]

    # ==========================================
    # App User Methods (Authentication)
    # ==========================================

    def create_app_user(self, user: AppUser) -> str:
        """
        Create a new application user

        Args:
            user: AppUser instance

        Returns:
            User ID (string)
        """
        try:
            result = self.app_users.insert_one(user.to_dict())
            user.mongo_id = result.inserted_id
            logger.info(f"Created app user: {user.email}")
            return str(result.inserted_id)
        except Exception as e:
            if "duplicate key" in str(e).lower() or "E11000" in str(e):
                logger.warning(f"App user already exists: {user.email}")
                raise ValueError(f"User with email {user.email} already exists")
            raise

    def get_app_user(self, user_id: str) -> AppUser | None:
        """Get app user by ID"""
        doc = self.app_users.find_one({"_id": ObjectId(user_id)})
        return AppUser.from_dict(doc) if doc else None

    def get_app_user_by_email(self, email: str) -> AppUser | None:
        """Get app user by email"""
        doc = self.app_users.find_one({"email": email.lower()})
        return AppUser.from_dict(doc) if doc else None

    def update_app_user(self, user: AppUser) -> bool:
        """Update existing app user"""
        if not user.mongo_id:
            logger.error("Cannot update user without _id")
            return False

        user.update_timestamp()
        update_data = user.to_dict()
        if '_id' in update_data:
            del update_data['_id']

        result = self.app_users.update_one(
            {"_id": user.mongo_id},
            {"$set": update_data}
        )

        if result.modified_count > 0:
            logger.info(f"Updated app user: {user.email}")
            return True
        return False

    # --- API tokens (§5.13) --------------------------------------------------

    def create_api_token(self, token: ApiToken) -> str:
        """Persist a new :class:`ApiToken` and return the inserted id."""
        result = self.api_tokens.insert_one(token.to_dict())
        token.mongo_id = result.inserted_id
        return str(result.inserted_id)

    def get_api_token_by_hash(self, token_hash: str) -> ApiToken | None:
        """Look up a token by its SHA-256 hash.

        Returned regardless of expiry / revocation — callers should
        consult :attr:`ApiToken.is_valid` before trusting it.
        """
        doc = self.api_tokens.find_one({"token_hash": token_hash})
        return ApiToken.from_dict(doc) if doc else None

    def update_api_token(self, token: ApiToken) -> bool:
        """Persist mutations to ``last_used_at`` / ``revoked_at``."""
        if not token.mongo_id:
            return False
        data = token.to_dict()
        data.pop("_id", None)
        result = self.api_tokens.update_one(
            {"_id": token.mongo_id}, {"$set": data}
        )
        return result.matched_count > 0

    def list_api_tokens_for_user(
        self, user_id: str, *, include_revoked: bool = False,
    ) -> list[ApiToken]:
        """Return every token for ``user_id``, newest first.

        ``include_revoked=False`` filters out revoked tokens for the
        "active sessions" admin view; pass ``True`` for full audit.
        """
        query: dict[str, Any] = {"user_id": user_id}
        if not include_revoked:
            query["revoked_at"] = None
        cursor = self.api_tokens.find(query).sort("created_at", -1)
        return [ApiToken.from_dict(doc) for doc in cursor]

    def delete_app_user(self, user_id: str) -> bool:
        """Delete app user"""
        result = self.app_users.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count > 0:
            logger.info(f"Deleted app user: {user_id}")
            return True
        return False

    def get_app_users(
        self,
        role: UserRole | None = None,
        is_active: bool | None = None,
        limit: int = 0,
        skip: int = 0
    ) -> list[AppUser]:
        """Get app users with optional filtering"""
        query: dict[str, Any] = {}
        if role:
            query["role"] = role.value
        if is_active is not None:
            query["is_active"] = is_active

        docs = self.app_users.find(query).sort("email", 1).limit(limit).skip(skip)
        return [AppUser.from_dict(doc) for doc in docs]

    def search_app_users(
        self,
        query: str,
        exclude_user_ids: list[str] | None = None,
        limit: int = 10
    ) -> list[AppUser]:
        """Search active app users by email or display_name.

        Args:
            query: Search string, split into terms. Each term is matched
                   as a case-insensitive substring against email and display_name.
                   All terms must match (AND logic).
            exclude_user_ids: User IDs to exclude from results.
            limit: Maximum results to return (capped at 20).
        """
        limit = min(limit, 20)
        terms = query.strip().split()
        if not terms:
            return []

        mongo_query: dict[str, Any] = {"is_active": True}

        # Each term must match email OR display_name
        and_conditions: list[dict[str, Any]] = []
        for term in terms:
            escaped = re.escape(term)
            pattern = {"$regex": escaped, "$options": "i"}
            and_conditions.append({
                "$or": [
                    {"email": pattern},
                    {"display_name": pattern},
                ]
            })
        mongo_query["$and"] = and_conditions

        if exclude_user_ids:
            mongo_query["_id"] = {
                "$nin": [ObjectId(uid) for uid in exclude_user_ids]
            }

        docs = self.app_users.find(mongo_query).sort("email", 1).limit(limit)
        return [AppUser.from_dict(doc) for doc in docs]

    def count_app_users(self, role: UserRole | None = None) -> int:
        """Count app users"""
        query: dict[str, str] = {}
        if role:
            query["role"] = role.value
        return self.app_users.count_documents(query)

    def app_user_exists(self, email: str) -> bool:
        """Check if an app user exists by email"""
        return self.app_users.count_documents({"email": email.lower()}) > 0

    # ==========================================
    # Permission Group operations
    # ==========================================

    def create_group(self, group: PermissionGroup) -> str:
        """Create a permission group. Returns inserted ID as string."""
        result = self.groups.insert_one(group.to_dict())
        return str(result.inserted_id)

    def get_group(self, group_id: str) -> PermissionGroup | None:
        """Get a single group by ID."""
        from auto_a11y.models.permission_group import PermissionGroup
        doc = self.groups.find_one({"_id": ObjectId(group_id)})
        return PermissionGroup.from_dict(doc) if doc else None

    def get_group_by_name(self, name: str) -> PermissionGroup | None:
        """Get a group by its name."""
        from auto_a11y.models.permission_group import PermissionGroup
        doc = self.groups.find_one({"name": name})
        return PermissionGroup.from_dict(doc) if doc else None

    def get_all_groups(self) -> list[PermissionGroup]:
        """Get all permission groups."""
        from auto_a11y.models.permission_group import PermissionGroup
        return [PermissionGroup.from_dict(doc) for doc in self.groups.find()]

    def get_groups_by_ids(self, group_ids: list[str]) -> list[PermissionGroup]:
        """Get multiple groups by their IDs. Silently skips missing IDs."""
        from auto_a11y.models.permission_group import PermissionGroup
        if not group_ids:
            return []
        oids: list[ObjectId] = []
        for gid in group_ids:
            try:
                oids.append(ObjectId(gid))
            except Exception:
                continue
        docs = self.groups.find({"_id": {"$in": oids}})
        return [PermissionGroup.from_dict(doc) for doc in docs]

    def update_group(self, group: PermissionGroup) -> bool:
        """Update an existing group."""
        from datetime import datetime
        group.updated_at = datetime.now()
        result = self.groups.replace_one(
            {"_id": group.mongo_id},
            group.to_dict()
        )
        return result.modified_count > 0

    def delete_group(self, group_id: str) -> bool:
        """Delete a non-system group."""
        result = self.groups.delete_one({
            "_id": ObjectId(group_id),
            "is_system": {"$ne": True}
        })
        return result.deleted_count > 0

    def count_group_members(self, group_id: str) -> int:
        """Count how many project memberships reference this group."""
        return self.projects.count_documents({
            "members.group_ids": str(group_id)
        })

    # ==========================================
    # Test Schedule Methods (Scheduled Testing)
    # ==========================================

    def create_test_schedule(self, schedule: TestSchedule) -> str:
        """
        Create a new test schedule

        Args:
            schedule: TestSchedule instance

        Returns:
            Schedule ID (string)
        """
        result = self.test_schedules.insert_one(schedule.to_dict())
        schedule.mongo_id = result.inserted_id
        logger.info(f"Created test schedule: {schedule.name} for website {schedule.website_id}")
        return str(result.inserted_id)

    def get_test_schedule(self, schedule_id: str) -> TestSchedule | None:
        """
        Get test schedule by ID

        Args:
            schedule_id: Schedule ID

        Returns:
            TestSchedule instance or None
        """
        doc = self.test_schedules.find_one({"_id": ObjectId(schedule_id)})
        return TestSchedule.from_dict(doc) if doc else None

    def get_test_schedule_by_apscheduler_id(self, apscheduler_job_id: str) -> TestSchedule | None:
        """
        Get test schedule by APScheduler job ID

        Args:
            apscheduler_job_id: APScheduler job ID

        Returns:
            TestSchedule instance or None
        """
        doc = self.test_schedules.find_one({"apscheduler_job_id": apscheduler_job_id})
        return TestSchedule.from_dict(doc) if doc else None

    def get_test_schedules_for_website(
        self,
        website_id: str,
        enabled_only: bool = False
    ) -> list[TestSchedule]:
        """
        Get all test schedules for a website

        Args:
            website_id: Website ID
            enabled_only: If True, only return enabled schedules

        Returns:
            List of TestSchedule instances
        """
        query: dict[str, Any] = {"website_id": website_id}
        if enabled_only:
            query["enabled"] = True

        docs = self.test_schedules.find(query).sort("created_at", -1)
        return [TestSchedule.from_dict(doc) for doc in docs]

    def get_enabled_test_schedules(self) -> list[TestSchedule]:
        """
        Get all enabled test schedules across all websites

        Returns:
            List of enabled TestSchedule instances
        """
        docs = self.test_schedules.find({"enabled": True})
        return [TestSchedule.from_dict(doc) for doc in docs]

    def get_all_test_schedules(
        self,
        project_id: str | None = None,
        enabled_only: bool = False
    ) -> list[TestSchedule]:
        """
        Get all test schedules across all websites, optionally filtered by project

        Args:
            project_id: Optional project ID to filter by
            enabled_only: If True, only return enabled schedules

        Returns:
            List of TestSchedule instances
        """
        query: dict[str, Any] = {}
        if enabled_only:
            query["enabled"] = True

        # If project_id provided, get website IDs for that project first
        if project_id:
            websites = self.get_websites(project_id)
            website_ids = [w.id for w in websites]
            query["website_id"] = {"$in": website_ids}

        docs = self.test_schedules.find(query).sort("created_at", -1)
        return [TestSchedule.from_dict(doc) for doc in docs]

    def update_test_schedule(self, schedule: TestSchedule) -> bool:
        """
        Update existing test schedule

        Args:
            schedule: TestSchedule instance with updated data

        Returns:
            True if updated successfully
        """
        if not schedule.mongo_id:
            logger.error("Cannot update schedule without _id")
            return False

        schedule.update_timestamp()
        update_data = schedule.to_dict()
        if '_id' in update_data:
            del update_data['_id']

        result = self.test_schedules.update_one(
            {"_id": schedule.mongo_id},
            {"$set": update_data}
        )

        if result.modified_count > 0:
            logger.info(f"Updated test schedule: {schedule.name}")
            return True
        return False

    def delete_test_schedule(self, schedule_id: str) -> bool:
        """
        Delete test schedule

        Args:
            schedule_id: Schedule ID

        Returns:
            True if deleted successfully
        """
        result = self.test_schedules.delete_one({"_id": ObjectId(schedule_id)})
        if result.deleted_count > 0:
            logger.info(f"Deleted test schedule: {schedule_id}")
            return True
        return False

    def toggle_test_schedule(self, schedule_id: str, enabled: bool) -> bool:
        """
        Enable or disable a test schedule

        Args:
            schedule_id: Schedule ID
            enabled: True to enable, False to disable

        Returns:
            True if updated successfully
        """
        result = self.test_schedules.update_one(
            {"_id": ObjectId(schedule_id)},
            {
                "$set": {
                    "enabled": enabled,
                    "updated_at": datetime.now()
                }
            }
        )

        if result.modified_count > 0:
            logger.info(f"{'Enabled' if enabled else 'Disabled'} test schedule: {schedule_id}")
            return True
        return False

    def update_test_schedule_run_status(
        self,
        schedule_id: str,
        job_id: str,
        status: ScheduleRunStatus,
        next_run_at: datetime | None = None
    ) -> bool:
        """
        Update execution status of a test schedule

        Args:
            schedule_id: Schedule ID
            job_id: Testing job ID
            status: Run status
            next_run_at: Next scheduled run time

        Returns:
            True if updated successfully
        """
        update_fields = {
            "last_run_job_id": job_id,
            "last_run_status": status.value,
            "updated_at": datetime.now()
        }

        if status == ScheduleRunStatus.RUNNING:
            update_fields["last_run_at"] = datetime.now()

        if next_run_at:
            update_fields["next_run_at"] = next_run_at

        result = self.test_schedules.update_one(
            {"_id": ObjectId(schedule_id)},
            {
                "$set": update_fields,
                "$inc": {"run_count": 1} if status == ScheduleRunStatus.RUNNING else {}
            }
        )

        return result.modified_count > 0

    def set_test_schedule_apscheduler_id(
        self,
        schedule_id: str,
        apscheduler_job_id: str
    ) -> bool:
        """
        Set the APScheduler job ID for a schedule

        Args:
            schedule_id: Schedule ID
            apscheduler_job_id: APScheduler job ID

        Returns:
            True if updated successfully
        """
        result = self.test_schedules.update_one(
            {"_id": ObjectId(schedule_id)},
            {
                "$set": {
                    "apscheduler_job_id": apscheduler_job_id,
                    "updated_at": datetime.now()
                }
            }
        )

        return result.modified_count > 0

    def count_test_schedules(
        self,
        website_id: str | None = None,
        enabled_only: bool = False
    ) -> int:
        """
        Count test schedules

        Args:
            website_id: Optional website ID filter
            enabled_only: If True, only count enabled schedules

        Returns:
            Number of schedules
        """
        query: dict[str, Any] = {}
        if website_id:
            query["website_id"] = website_id
        if enabled_only:
            query["enabled"] = True

        return self.test_schedules.count_documents(query)

    # ==========================================
    # Share Token Methods (Public Share Links)
    # ==========================================

    def create_share_token(self, token: ShareToken) -> str:
        """Create a new share token"""
        result = self.share_tokens.insert_one(token.to_dict())
        token.mongo_id = result.inserted_id
        logger.info(f"Created share token: {token.label} ({token.scope.value}:{token.scope_id})")
        return str(result.inserted_id)

    def get_share_token(self, token_id: str) -> ShareToken | None:
        """Get share token by its ID"""
        doc = self.share_tokens.find_one({"_id": ObjectId(token_id)})
        return ShareToken.from_dict(doc) if doc else None

    def get_share_token_by_hash(self, token_hash: str) -> ShareToken | None:
        """Get share token by its hash"""
        doc = self.share_tokens.find_one({"token_hash": token_hash})
        return ShareToken.from_dict(doc) if doc else None

    def get_share_tokens_for_scope(
        self,
        scope: TokenScope,
        scope_id: str
    ) -> list[ShareToken]:
        """Get all share tokens for a given scope (project or website)"""
        docs = self.share_tokens.find({
            "scope": scope.value,
            "scope_id": scope_id
        }).sort("created_at", -1)
        return [ShareToken.from_dict(doc) for doc in docs]

    def revoke_share_token(self, token_id: str) -> bool:
        """Revoke a share token"""
        result = self.share_tokens.update_one(
            {"_id": ObjectId(token_id)},
            {"$set": {"revoked": True, "revoked_at": datetime.now()}}
        )
        if result.modified_count > 0:
            logger.info(f"Revoked share token: {token_id}")
            return True
        return False

    def record_token_use(self, token_hash: str) -> None:
        """Record that a token was used"""
        self.share_tokens.update_one(
            {"token_hash": token_hash},
            {"$set": {"last_used": datetime.now()}, "$inc": {"use_count": 1}}
        )
