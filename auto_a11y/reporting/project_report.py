"""
Project-level Accessibility Report Generator
Aggregates data from multiple websites in a project
"""
from __future__ import annotations

import html as html_module
import logging
from typing import Any, Callable
from datetime import datetime
import json
from pathlib import Path

from auto_a11y.web.fluent import ftl, force_locale
from auto_a11y.models import Project, Website, Page, PageStatus
from auto_a11y.core.database import Database

logger = logging.getLogger(__name__)


class ProjectReport:
    """Generates project-level accessibility reports"""
    
    def __init__(self, database: Database, project: Project, websites: list[Website], pages_by_website: dict[str, list[Page]], language: str = 'en') -> None:
        """
        Initialize project report
        
        Args:
            database: Database instance
            project: Project object
            websites: List of websites in the project
            pages_by_website: Dictionary mapping website_id to list of pages
        """
        self.database = database
        self.project = project
        self.websites = websites
        self.pages_by_website = pages_by_website
        self.language = language
        self.report_data: dict[str, Any] | None = None
    
    def generate(self, progress_callback: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
        """
        Generate the project-level report

        Returns:
            Report data dictionary
        """
        logger.info(f"Generating project-level report for {self.project.name}")

        # Pre-compute total pages for progress tracking
        total_page_count = sum(len(self.pages_by_website.get(w.id or '', [])) for w in self.websites)
        page_count = 0

        # Calculate aggregate statistics
        total_pages = 0
        tested_pages = 0
        pages_with_issues = 0
        total_violations = 0
        total_warnings = 0

        website_summaries: list[dict[str, Any]] = []

        for website in self.websites:
            if progress_callback:
                with force_locale(self.language):
                    progress_callback(page_count, max(total_page_count, 1), ftl('reports-processing-name', name=website.name))
            pages = self.pages_by_website.get(website.id or '', [])
            
            website_tested = sum(1 for p in pages if p.status == PageStatus.TESTED)
            website_issues = sum(1 for p in pages if p.has_issues)
            website_violations = sum(p.violation_count for p in pages)
            website_warnings = sum(p.warning_count for p in pages)
            
            website_summaries.append({
                'id': website.id,
                'name': website.name,
                'url': website.url,
                'total_pages': len(pages),
                'tested_pages': website_tested,
                'pages_with_issues': website_issues,
                'total_violations': website_violations,
                'total_warnings': website_warnings,
                'test_coverage': (website_tested / len(pages) * 100) if pages else 0
            })
            
            total_pages += len(pages)
            tested_pages += website_tested
            pages_with_issues += website_issues
            total_violations += website_violations
            total_warnings += website_warnings
            page_count += len(pages)

        # Generate report data
        self.report_data = {
            'report_type': 'project_accessibility',
            'project': {
                'id': self.project.id,
                'name': self.project.name,
                'description': self.project.description,
                'status': self.project.status.value,
                'config': self.project.config
            },
            'generated_at': datetime.now().isoformat(),
            'summary': {
                'website_count': len(self.websites),
                'total_pages': total_pages,
                'tested_pages': tested_pages,
                'pages_with_issues': pages_with_issues,
                'total_violations': total_violations,
                'total_warnings': total_warnings,
                'test_coverage': (tested_pages / total_pages * 100) if total_pages else 0
            },
            'websites': website_summaries,
            'wcag_level': self.project.config.get('wcag_level', 'AA')
        }
        
        logger.info(f"Project report generated with {len(self.websites)} websites and {total_pages} pages")
        return self.report_data
    
    def to_html(self) -> str:
        """
        Generate HTML report
        
        Returns:
            HTML string
        """
        if not self.report_data:
            self.generate()
        assert self.report_data is not None

        # User-entered free text must be HTML-escaped to prevent stored XSS.
        project_name = html_module.escape(self.project.name or '')
        project_description = html_module.escape(self.project.description or 'No description')

        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Project Accessibility Report - {project_name}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.8.1/font/bootstrap-icons.css" rel="stylesheet">
    <style>
        .website-card {{
            margin-bottom: 20px;
            border-left: 4px solid #007bff;
        }}
        .website-card:hover {{
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }}
        .stat-card {{
            text-align: center;
            padding: 15px;
            border-radius: 8px;
            background: #f8f9fa;
            margin-bottom: 10px;
        }}
        .stat-value {{
            font-size: 2rem;
            font-weight: bold;
        }}
        .stat-label {{
            color: #4a4a4a;
            font-size: 0.9rem;
        }}
        .progress-bar-animated {{
            animation: progress-bar-stripes 1s linear infinite;
        }}
        .severity-high {{
            color: #922b21;
        }}
        .severity-medium {{
            color: #7d6608;
        }}
        .severity-low {{
            color: #1e8449;
        }}
    </style>
</head>
<body>
    <div class="container-fluid py-4">
        <div class="row">
            <div class="col-12">
                <h1>Project Accessibility Report</h1>
                <div class="mb-3">
                    <h3>{project_name}</h3>
                    <p class="text-muted">
                        {project_description}<br>
                        <strong>WCAG Level:</strong> {self.report_data['wcag_level']}<br>
                        <strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    </p>
                </div>
            </div>
        </div>
        
        <!-- Overall Summary -->
        <div class="row mb-4">
            <div class="col-12">
                <h4>Overall Summary</h4>
            </div>
            <div class="col-md-2">
                <div class="stat-card">
                    <div class="stat-value">{self.report_data['summary']['website_count']}</div>
                    <div class="stat-label">Websites</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="stat-card">
                    <div class="stat-value">{self.report_data['summary']['total_pages']}</div>
                    <div class="stat-label">Total Pages</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="stat-card">
                    <div class="stat-value">{self.report_data['summary']['tested_pages']}</div>
                    <div class="stat-label">Tested Pages</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="stat-card">
                    <div class="stat-value text-severity-high">{self.report_data['summary']['total_violations']}</div>
                    <div class="stat-label">Violations</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="stat-card">
                    <div class="stat-value text-severity-medium">{self.report_data['summary']['total_warnings']}</div>
                    <div class="stat-label">Warnings</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="stat-card">
                    <div class="stat-value">{self.report_data['summary']['test_coverage']:.1f}%</div>
                    <div class="stat-label">Coverage</div>
                </div>
            </div>
        </div>
        
        <!-- Test Coverage Progress -->
        <div class="row mb-4">
            <div class="col-12">
                <h5>Overall Test Coverage</h5>
                <div class="progress" style="height: 30px;">
                    <div class="progress-bar {'progress-pass' if self.report_data['summary']['test_coverage'] >= 80 else 'progress-medium' if self.report_data['summary']['test_coverage'] >= 50 else 'progress-high'}" 
                         role="progressbar" 
                         style="width: {self.report_data['summary']['test_coverage']}%"
                         aria-valuenow="{self.report_data['summary']['test_coverage']}"
                         aria-valuemin="0" 
                         aria-valuemax="100">
                        {self.report_data['summary']['test_coverage']:.1f}%
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Website Breakdowns -->
        <div class="row">
            <div class="col-12">
                <h4>Website Breakdown</h4>
            </div>
        </div>
        
        <div class="row">
"""
        
        # Add website cards
        for website in self.report_data['websites']:
            coverage_color = 'pass' if website['test_coverage'] >= 80 else 'medium' if website['test_coverage'] >= 50 else 'high'

            # User-entered free text must be HTML-escaped to prevent stored XSS.
            website_name = html_module.escape(website['name'] or '')
            website_url_text = html_module.escape(website['url'] or '')
            website_url_attr = html_module.escape(website['url'] or '', quote=True)

            html += f"""
            <div class="col-md-6 col-lg-4">
                <div class="card website-card">
                    <div class="card-body">
                        <h5 class="card-title">
                            {website_name}
                            <a href="{website_url_attr}" target="_blank" class="float-end">
                                <i class="bi bi-box-arrow-up-right"></i>
                            </a>
                        </h5>
                        <p class="card-text text-muted small">{website_url_text}</p>
                        
                        <div class="mb-3">
                            <div class="progress" style="height: 20px;">
                                <div class="progress-bar progress-{coverage_color}" 
                                     role="progressbar" 
                                     style="width: {website['test_coverage']}%">
                                    {website['test_coverage']:.0f}% tested
                                </div>
                            </div>
                        </div>
                        
                        <div class="row text-center">
                            <div class="col-4">
                                <div class="fw-bold">{website['total_pages']}</div>
                                <small class="text-muted">Pages</small>
                            </div>
                            <div class="col-4">
                                <div class="fw-bold text-severity-high">{website['total_violations']}</div>
                                <small class="text-muted">Violations</small>
                            </div>
                            <div class="col-4">
                                <div class="fw-bold text-severity-medium">{website['total_warnings']}</div>
                                <small class="text-muted">Warnings</small>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
"""
        
        html += """
        </div>
        
        <!-- Issues Summary -->
        <div class="row mt-4">
            <div class="col-12">
                <h4>Issues Overview</h4>
                <div class="alert alert-info">
                    <i class="bi bi-info-circle"></i> 
                    This report aggregates accessibility testing results across all websites in the project.
                    For detailed issue information, please view individual website reports.
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""
        return html
    
    def to_json(self) -> str:
        """
        Generate JSON report
        
        Returns:
            JSON string
        """
        if not self.report_data:
            self.generate()
        assert self.report_data is not None

        return json.dumps(self.report_data, indent=2, default=str)
    
    def save(self, format: str = 'html', reports_dir: str | None = None) -> str:
        """
        Save report to file

        Args:
            format: Output format (html, json)
            reports_dir: Optional path to reports directory

        Returns:
            Path to saved file
        """
        resolved_dir: Path
        if reports_dir:
            resolved_dir = Path(reports_dir)
        else:
            # Try getting from Flask current_app if available
            resolved_dir = Path('reports')  # default
            try:
                from flask import current_app
                if current_app and hasattr(current_app, 'app_config'):
                    app_cfg = getattr(current_app, 'app_config')
                    resolved_dir = Path(str(getattr(app_cfg, 'REPORTS_DIR')))
            except Exception:
                pass

            # Fall back to environment variable or default
            if str(resolved_dir) == 'reports':
                import os
                reports_dir_str = os.environ.get('REPORTS_DIR', 'reports')
                resolved_dir = Path(reports_dir_str)

        # Ensure directory exists
        resolved_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"project_report_{self.project.id}_{timestamp}.{format}"
        filepath = resolved_dir / filename
        
        # Generate content based on format
        if format == 'html':
            content = self.to_html()
        elif format == 'json':
            content = self.to_json()
        else:
            raise ValueError(f"Unsupported format: {format}")
        
        # Write to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"Project report saved to {filepath}")
        return str(filepath)