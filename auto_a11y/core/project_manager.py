"""
Project management business logic
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bson import ObjectId

from auto_a11y.models import Project, ProjectStatus
from auto_a11y.core.database import Database

if TYPE_CHECKING:
    from auto_a11y.pdf.storage import PdfStorage

logger = logging.getLogger(__name__)


class ProjectManager:
    """Manages project operations"""
    
    def __init__(self, database: Database):
        """
        Initialize project manager
        
        Args:
            database: Database connection
        """
        self.db = database
    
    def create_project(
        self,
        name: str,
        description: str = "",
        config: dict[str, Any] | None = None
    ) -> Project:
        """
        Create a new project
        
        Args:
            name: Project name
            description: Project description
            config: Project configuration
            
        Returns:
            Created project
        """
        # Check if name exists
        _db: Any = self.db
        existing: dict[str, Any] | None = _db.projects.find_one({'name': name})
        if existing:
            raise ValueError(f"Project '{name}' already exists")

        # Create project
        project = Project(
            name=name,
            description=description,
            status=ProjectStatus.ACTIVE,
            config=config or {}
        )

        project_id_str = self.db.create_project(project)
        object.__setattr__(project, '_id', ObjectId(project_id_str))
        
        logger.info(f"Created project: {name} ({project_id_str})")
        return project
    
    def get_project(self, project_id: str) -> Project | None:
        """
        Get project by ID
        
        Args:
            project_id: Project ID
            
        Returns:
            Project or None
        """
        return self.db.get_project(project_id)
    
    def list_projects(
        self,
        status: ProjectStatus | None = None,
        limit: int = 100
    ) -> list[Project]:
        """
        List projects with optional filtering
        
        Args:
            status: Filter by status
            limit: Maximum number of projects
            
        Returns:
            List of projects
        """
        return self.db.get_projects(status=status, limit=limit)
    
    def update_project(
        self,
        project_id: str,
        name: str | None = None,
        description: str | None = None,
        status: ProjectStatus | None = None,
        config: dict[str, Any] | None = None
    ) -> bool:
        """
        Update project details
        
        Args:
            project_id: Project ID
            name: New name
            description: New description
            status: New status
            config: New configuration
            
        Returns:
            True if updated successfully
        """
        project = self.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        # Update fields
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        if status is not None:
            project.status = status
        if config is not None:
            project.config.update(config)
        
        return self.db.update_project(project)
    
    def delete_project(self, project_id: str) -> bool:
        """
        Delete project and all related data
        
        Args:
            project_id: Project ID
            
        Returns:
            True if deleted successfully
        """
        project = self.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        return self.db.delete_project(project_id)
    
    def get_project_statistics(
        self,
        project_id: str,
        pdf_storage: "PdfStorage | None" = None,
    ) -> dict[str, Any]:
        """
        Get project statistics.

        Pass ``pdf_storage`` to include audited PDF FAIL/WARN counts in
        the totals. Callers without web app context (e.g. CLI tools)
        may omit it; the totals will then reflect HTML page tests only.
        """
        project = self.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        return self.db.get_project_stats(project_id, pdf_storage=pdf_storage)
    
    def archive_project(self, project_id: str) -> bool:
        """
        Archive a project
        
        Args:
            project_id: Project ID
            
        Returns:
            True if archived successfully
        """
        return self.update_project(project_id, status=ProjectStatus.ARCHIVED)
    
    def activate_project(self, project_id: str) -> bool:
        """
        Activate an archived project
        
        Args:
            project_id: Project ID
            
        Returns:
            True if activated successfully
        """
        return self.update_project(project_id, status=ProjectStatus.ACTIVE)