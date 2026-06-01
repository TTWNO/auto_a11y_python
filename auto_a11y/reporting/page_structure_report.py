"""
Site Structure Report Generator
Generates a tree view of website pages based on URL hierarchy
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING
from urllib.parse import urlparse, unquote
from datetime import datetime
import json
import csv
from io import StringIO

from auto_a11y.web.fluent import ftl, force_locale
from auto_a11y.models import Page, PageStatus

if TYPE_CHECKING:
    from auto_a11y.core.database import Database
    from auto_a11y.models import Website, Project

logger = logging.getLogger(__name__)


def resolve_reports_dir() -> Path:
    """Resolve the directory reports are written to.

    Resolution order:
    1. The Flask ``current_app``'s ``app_config.REPORTS_DIR`` when an
       application context is active and exposes it.
    2. The ``REPORTS_DIR`` environment variable.
    3. A repo-relative ``reports`` directory (mirrors the default used by
       ``ReportGenerator`` in ``report_generator.py``).

    Returns:
        Path to the reports directory (not guaranteed to exist yet).
    """
    # Try getting from Flask current_app if available.
    try:
        from flask import current_app
        if current_app and hasattr(current_app, 'app_config'):
            app_cfg = getattr(current_app, 'app_config')
            return Path(str(getattr(app_cfg, 'REPORTS_DIR')))
    except Exception as exc:
        # No active app context, or app_config missing/raising — fall through
        # to the env-var/default resolution below. Narrowed from a bare
        # ``except`` so KeyboardInterrupt/SystemExit still propagate.
        logger.debug("Flask current_app REPORTS_DIR lookup failed: %s", exc)

    # Fall back to environment variable, then a repo-relative default.
    return Path(os.environ.get('REPORTS_DIR', 'reports'))


class PageNode:
    """Represents a node in the page tree structure"""
    
    def __init__(self, name: str, url: str | None = None, is_directory: bool = False) -> None:
        self.name = name
        self.url = url
        self.is_directory = is_directory
        self.children: list[PageNode] = []
        self.page_data: Page | None = None
        self.stats = {
            'total_pages': 0,
            'tested_pages': 0,
            'pages_with_issues': 0,
            'total_violations': 0,
            'total_warnings': 0
        }
    
    def add_child(self, child: PageNode) -> None:
        """Add a child node"""
        self.children.append(child)

    def find_or_create_child(self, name: str, is_directory: bool = False) -> PageNode:
        """Find existing child or create new one"""
        for child in self.children:
            if child.name == name and child.is_directory == is_directory:
                return child
        
        new_child = PageNode(name, is_directory=is_directory)
        self.add_child(new_child)
        return new_child
    
    def update_stats(self, page: Page) -> None:
        """Update statistics based on page data"""
        self.stats['total_pages'] += 1
        if page.status == PageStatus.TESTED:
            self.stats['tested_pages'] += 1
        if page.has_issues:
            self.stats['pages_with_issues'] += 1
        self.stats['total_violations'] += page.violation_count
        self.stats['total_warnings'] += page.warning_count
    
    def aggregate_stats(self) -> None:
        """Aggregate statistics from children"""
        for child in self.children:
            child.aggregate_stats()
            self.stats['total_pages'] += child.stats['total_pages']
            self.stats['tested_pages'] += child.stats['tested_pages']
            self.stats['pages_with_issues'] += child.stats['pages_with_issues']
            self.stats['total_violations'] += child.stats['total_violations']
            self.stats['total_warnings'] += child.stats['total_warnings']
    
    def to_dict(self) -> dict[str, Any]:
        """Convert node to dictionary"""
        return {
            'name': self.name,
            'url': self.url,
            'is_directory': self.is_directory,
            'stats': self.stats,
            'children': [child.to_dict() for child in self.children]
        }


class PageStructureReport:
    """Generates site structure tree report"""
    
    def __init__(self, database: Database, website: Website, pages: list[Page], project: Project | None = None, language: str = 'en') -> None:
        """
        Initialize site structure report

        Args:
            database: Database instance
            website: Website object
            pages: List of pages to include in report
            project: Optional Project object
            language: Language code for report (e.g., 'en', 'fr')
        """
        self.database = database
        self.website = website
        self.pages = pages
        self.project = project
        self.language = language
        self.root: PageNode | None = None
        self.tree_data: dict[str, Any] | None = None

    def _get_translations(self) -> dict[str, str]:
        """Get translations for the current language"""
        translations = {
            'en': {
                'site_structure_report': 'Site Structure Report',
                'project': 'Project',
                'website': 'Website',
                'generated': 'Generated',
                'no_project': 'No Project',
                'summary': 'Summary',
                'total_pages': 'Total Pages',
                'tested_pages': 'Tested Pages',
                'pages_with_issues': 'Pages with Issues',
                'total_violations': 'Total Violations',
                'total_warnings': 'Total Warnings',
                'max_depth': 'Max Depth',
                'levels': 'levels',
                'legend': 'Legend',
                'directory_section': 'Directory/Section',
                'page': 'Page',
                'tested': 'Tested',
                'page_has_been_tested': 'Page has been tested',
                'issues': 'Issues',
                'page_has_issues': 'Page has accessibility issues',
                'not_tested': 'Not tested',
                'page_not_tested': 'Page not yet tested',
                'expand_all': 'Expand All',
                'collapse_all': 'Collapse All',
                'site_structure_tree': 'Site Structure Tree',
                'language': 'Language',
                'pages_with_issues_count': 'pages with issues',
                'page_with_issues_count': 'page with issues',
                'total_violations_count': 'total violations',
                'x_tested': 'tested',
                'csv_path': 'Path',
                'csv_url': 'URL',
                'csv_type': 'Type',
                'csv_level': 'Level',
                'csv_directory': 'Directory',
                'filename_prefix': 'page_structure',
            },
            'fr': {
                'site_structure_report': 'Rapport de structure du site',
                'project': 'Projet',
                'website': 'Site Web',
                'generated': 'Généré',
                'no_project': 'Aucun projet',
                'summary': 'Résumé',
                'total_pages': 'Total des pages',
                'tested_pages': 'Pages testées',
                'pages_with_issues': 'Pages avec problèmes',
                'total_violations': 'Total des violations',
                'total_warnings': 'Total des avertissements',
                'max_depth': 'Profondeur maximale',
                'levels': 'niveaux',
                'legend': 'Légende',
                'directory_section': 'Répertoire/Section',
                'page': 'Page',
                'tested': 'Testé',
                'page_has_been_tested': 'La page a été testée',
                'issues': 'Problèmes',
                'page_has_issues': 'La page a des problèmes d\'accessibilité',
                'not_tested': 'Non testé',
                'page_not_tested': 'Page pas encore testée',
                'expand_all': 'Tout développer',
                'collapse_all': 'Tout réduire',
                'site_structure_tree': 'Arborescence du site',
                'language': 'Langue',
                'pages_with_issues_count': 'pages avec problèmes',
                'page_with_issues_count': 'page avec problèmes',
                'total_violations_count': 'violations totales',
                'x_tested': 'testées',
                'csv_path': 'Chemin',
                'csv_url': 'URL',
                'csv_type': 'Type',
                'csv_level': 'Niveau',
                'csv_directory': 'Répertoire',
                'filename_prefix': 'structure_du_site',
            }
        }
        return translations.get(self.language, translations['en'])

    def generate(self, progress_callback: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
        """
        Generate the page structure report

        Returns:
            Report data dictionary
        """
        logger.info(f"Generating site structure report for website {self.website.id}")

        # Build the tree structure
        self.root = self._build_tree(progress_callback=progress_callback)
        
        # Aggregate statistics
        self.root.aggregate_stats()
        
        # Generate report data
        self.tree_data = {
            'report_type': 'site_structure',
            'project': {
                'id': self.project.id if self.project else None,
                'name': self.project.name if self.project else 'No Project'
            },
            'website': {
                'id': self.website.id,
                'name': self.website.name,
                'url': self.website.url
            },
            'generated_at': datetime.now().isoformat(),
            'summary': {
                'total_pages': len(self.pages),
                'tested_pages': sum(1 for p in self.pages if p.status == PageStatus.TESTED),
                'pages_with_issues': sum(1 for p in self.pages if p.has_issues),
                'total_violations': sum(p.violation_count for p in self.pages),
                'total_warnings': sum(p.warning_count for p in self.pages),
                'max_depth': self._calculate_max_depth(self.root, 0)
            },
            'tree': self.root.to_dict()
        }
        
        logger.info(f"Site structure report generated with {len(self.pages)} pages")
        return self.tree_data
    
    def _build_tree(self, progress_callback: Callable[[int, int, str], None] | None = None) -> PageNode:
        """
        Build tree structure from pages

        Returns:
            Root node of the tree
        """
        # Parse the website URL to get the base
        base_url = urlparse(self.website.url)
        root_name = base_url.netloc or 'Root'
        root = PageNode(root_name, self.website.url, is_directory=True)

        # Process each page
        for i, page in enumerate(self.pages):
            if progress_callback:
                with force_locale(self.language):
                    progress_callback(i, len(self.pages), ftl('reports-building-tree-page-current-of-total', current=i + 1, total=len(self.pages)))
            self._add_page_to_tree(root, page)

        if progress_callback:
            with force_locale(self.language):
                progress_callback(len(self.pages), len(self.pages), ftl('reports-tree-structure-complete'))

        return root
    
    def _add_page_to_tree(self, root: PageNode, page: Page) -> None:
        """
        Add a page to the tree structure
        
        Args:
            root: Root node of the tree
            page: Page to add
        """
        parsed_url = urlparse(page.url)
        
        # Get the path components
        path = parsed_url.path
        if not path or path == '/':
            # This is the homepage
            root.url = page.url
            root.page_data = page
            root.update_stats(page)
            return
        
        # Split path into components
        path_parts = [p for p in path.split('/') if p]
        
        # Handle query parameters and fragments
        if parsed_url.query:
            # Add query as a separate component
            query_part = f"?{parsed_url.query}"
            path_parts.append(query_part)
        
        if parsed_url.fragment:
            # Add fragment as a separate component
            fragment_part = f"#{parsed_url.fragment}"
            path_parts.append(fragment_part)
        
        # Navigate/create the tree structure
        current_node = root
        
        for i, part in enumerate(path_parts):
            # Decode URL encoding
            part = unquote(part)
            
            # Check if this is the last part (the actual page)
            is_last = (i == len(path_parts) - 1)
            
            if is_last:
                # This is a page, not a directory
                child = current_node.find_or_create_child(part, is_directory=False)
                child.url = page.url
                child.page_data = page
                child.update_stats(page)
            else:
                # This is a directory
                child = current_node.find_or_create_child(part, is_directory=True)
                if not child.url:
                    # Set URL for directory based on path so far
                    dir_path = '/'.join(path_parts[:i+1])
                    child.url = f"{parsed_url.scheme}://{parsed_url.netloc}/{dir_path}/"
            
            current_node = child
    
    def _calculate_max_depth(self, node: PageNode, current_depth: int) -> int:
        """
        Calculate maximum depth of the tree
        
        Args:
            node: Current node
            current_depth: Current depth level
            
        Returns:
            Maximum depth found
        """
        if not node.children:
            return current_depth
        
        max_depth = current_depth
        for child in node.children:
            child_depth = self._calculate_max_depth(child, current_depth + 1)
            max_depth = max(max_depth, child_depth)
        
        return max_depth
    
    def to_html(self) -> str:
        """
        Generate HTML report with interactive tree view

        Returns:
            HTML string
        """
        if not self.tree_data:
            self.generate()
        assert self.tree_data is not None
        assert self.root is not None

        # Get all translations as JSON for JavaScript
        # Temporarily change language to get translations for both languages
        original_lang = self.language
        self.language = 'en'
        translations_en = self._get_translations()
        self.language = 'fr'
        translations_fr = self._get_translations()
        self.language = original_lang

        all_translations = {
            'en': translations_en,
            'fr': translations_fr
        }

        # Get translations for the current language to use as default in HTML
        t = self._get_translations()

        html = f"""
<!DOCTYPE html>
<html lang="{self.language}" data-current-lang="{self.language}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title data-i18n="site_structure_report">{t['site_structure_report']} - {self.website.name}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.8.1/font/bootstrap-icons.css" rel="stylesheet">
    <style>
        /* Tree structure - uses <ul>/<li> with ARIA tree roles */
        .tree {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.8;
        }}
        .tree[role="tree"],
        .tree [role="group"] {{
            list-style: none;
            margin: 0;
            padding: 0;
        }}
        .tree [role="group"] {{
            margin-left: 35px;
            padding-left: 10px;
            border-left: 1px solid #e0e0e0;
        }}
        .tree[role="tree"] > [role="treeitem"] > [role="group"] {{
            margin-left: 0;
            padding-left: 0;
            border-left: none;
        }}
        .tree-node-content {{
            padding: 4px 0;
            margin: 2px 0;
            border-radius: 4px;
            transition: background-color 0.2s;
        }}
        .tree-node-content:hover {{
            background-color: rgba(0, 123, 255, 0.05);
        }}
        [role="treeitem"]:focus > .tree-node-content {{
            outline: 2px solid #0d6efd;
            outline-offset: 1px;
        }}
        [role="treeitem"]:focus {{
            /* @a11y-ignore: focus indicator is rendered on the child .tree-node-content above; removing it here avoids a duplicated outline */
            outline: none;
        }}
        .node-toggle {{
            cursor: pointer;
            user-select: none;
            margin-right: 8px;
            display: inline-block;
            width: 20px;
            text-align: center;
            font-family: monospace;
            font-size: 14px;
            color: #666;
            transition: transform 0.2s ease;
            transform-origin: center;
        }}
        [role="treeitem"][aria-expanded="true"] > .tree-node-content > .node-toggle {{
            transform: rotate(90deg);
        }}
        .node-toggle:hover {{
            color: #333;
        }}
        .node-icon {{
            margin-right: 8px;
            font-size: 16px;
        }}
        .node-name {{
            font-weight: 500;
            color: #333;
            word-break: break-word;
            max-width: 600px;
            display: inline-block;
            vertical-align: middle;
        }}
        .node-url {{
            color: #007bff;
            text-decoration: none;
            word-break: break-word;
            max-width: 600px;
            display: inline-block;
            vertical-align: middle;
        }}
        .node-url:hover {{
            text-decoration: underline;
            color: #0056b3;
        }}
        .node-stats {{
            font-size: 0.85em;
            color: #666;
            margin-left: 12px;
            display: inline-block;
        }}
        .stats-badge {{
            font-size: 0.75em;
            margin-left: 6px;
            padding: 2px 6px;
        }}
        [role="treeitem"][aria-expanded="false"] > [role="group"] {{
            display: none;
        }}
        .legend {{
            border: 1px solid #ddd;
            padding: 15px;
            margin-bottom: 20px;
            background: #f8f9fa;
            border-radius: 4px;
        }}
        .legend > div {{
            margin: 5px 0;
        }}
        /* Directory nodes should be bold */
        [role="treeitem"].is-directory > .tree-node-content > .node-name {{
            font-weight: 600;
        }}
        /* Improve button styling */
        .btn-outline-brand, .btn-outline-neutral {{
            margin-right: 5px;
            margin-top: 10px;
        }}
    </style>
</head>
<body>
    <div class="container-fluid py-4">
        <div class="row">
            <div class="col-12">
                <div class="d-flex justify-content-between align-items-start">
                    <div>
                        <h1 data-i18n="site_structure_report">{t['site_structure_report']}</h1>
                        <p class="text-muted">
                            <strong><span data-i18n="project">{t['project']}</span>:</strong> <span id="project-name">{self.project.name if self.project else ''}</span><span id="no-project" data-i18n="no_project" style="display:{'none' if self.project else 'inline'}">{t['no_project']}</span><br>
                            <strong><span data-i18n="website">{t['website']}</span>:</strong> {self.website.name} ({self.website.url})<br>
                            <strong><span data-i18n="generated">{t['generated']}</span>:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                        </p>
                    </div>
                    <div class="dropdown">
                        <button class="btn btn-outline-neutral dropdown-toggle" type="button" id="languageDropdown" data-bs-toggle="dropdown" aria-expanded="false">
                            <i class="bi bi-translate"></i> <span data-i18n="language">{t['language']}</span>: <span id="current-lang-display">{self.language.upper()}</span>
                        </button>
                        <ul class="dropdown-menu" aria-labelledby="languageDropdown">
                            <li><a class="dropdown-item" href="#" onclick="switchLanguage('en'); return false;">English</a></li>
                            <li><a class="dropdown-item" href="#" onclick="switchLanguage('fr'); return false;">Français</a></li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>

        <div class="row mt-3">
            <div class="col-md-4">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title" data-i18n="summary">{t['summary']}</h5>
                        <ul class="list-unstyled">
                            <li><strong><span data-i18n="total_pages">{t['total_pages']}</span>:</strong> {self.tree_data['summary']['total_pages']}</li>
                            <li><strong><span data-i18n="tested_pages">{t['tested_pages']}</span>:</strong> {self.tree_data['summary']['tested_pages']}</li>
                            <li><strong><span data-i18n="pages_with_issues">{t['pages_with_issues']}</span>:</strong> {self.tree_data['summary']['pages_with_issues']}</li>
                            <li><strong><span data-i18n="total_violations">{t['total_violations']}</span>:</strong> <span class="text-severity-high">{self.tree_data['summary']['total_violations']}</span></li>
                            <li><strong><span data-i18n="total_warnings">{t['total_warnings']}</span>:</strong> <span class="text-severity-medium">{self.tree_data['summary']['total_warnings']}</span></li>
                            <li><strong><span data-i18n="max_depth">{t['max_depth']}</span>:</strong> {self.tree_data['summary']['max_depth']} <span data-i18n="levels">{t['levels']}</span></li>
                        </ul>
                    </div>
                </div>

                <div class="card mt-3">
                    <div class="card-body">
                        <h5 class="card-title" data-i18n="legend">{t['legend']}</h5>
                        <div class="legend">
                            <div><i class="bi bi-folder text-severity-medium"></i> <span data-i18n="directory_section">{t['directory_section']}</span></div>
                            <div><i class="bi bi-file-earmark text-brand"></i> <span data-i18n="page">{t['page']}</span></div>
                            <div><span class="badge badge-pass" data-i18n="tested">{t['tested']}</span> <span data-i18n="page_has_been_tested">{t['page_has_been_tested']}</span></div>
                            <div><span class="badge badge-high" data-i18n="issues">{t['issues']}</span> <span data-i18n="page_has_issues">{t['page_has_issues']}</span></div>
                            <div><span class="badge badge-neutral" data-i18n="not_tested">{t['not_tested']}</span> <span data-i18n="page_not_tested">{t['page_not_tested']}</span></div>
                        </div>
                        <button class="btn btn-sm btn-outline-brand" onclick="expandAll()">
                            <i class="bi bi-arrows-expand"></i> <span data-i18n="expand_all">{t['expand_all']}</span>
                        </button>
                        <button class="btn btn-sm btn-outline-neutral" onclick="collapseAll()">
                            <i class="bi bi-arrows-collapse"></i> <span data-i18n="collapse_all">{t['collapse_all']}</span>
                        </button>
                    </div>
                </div>
            </div>

            <div class="col-md-8">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title" id="tree-label" data-i18n="site_structure_tree">{t['site_structure_tree']}</h5>
                        <ul class="tree" role="tree" aria-labelledby="tree-label" id="tree-container">
                            {self._generate_tree_html(self.root, t=t)}
                        </ul>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // All translations embedded in the report
        const translations = {json.dumps(all_translations)};

        // Current language
        let currentLang = '{self.language}';

        function switchLanguage(lang) {{
            if (lang === currentLang) return;

            currentLang = lang;

            // Update HTML lang attribute
            document.documentElement.lang = lang;
            document.documentElement.setAttribute('data-current-lang', lang);

            // Update all elements with data-i18n attribute
            document.querySelectorAll('[data-i18n]').forEach(element => {{
                const key = element.getAttribute('data-i18n');
                if (translations[lang] && translations[lang][key]) {{
                    element.textContent = translations[lang][key];
                }}
            }});

            // Update current language display
            document.getElementById('current-lang-display').textContent = lang.toUpperCase();

            // Update document title
            if (translations[lang] && translations[lang]['site_structure_report']) {{
                document.title = translations[lang]['site_structure_report'] + ' - {self.website.name}';
            }}
        }}

        // --- Accessible Tree View (WAI-ARIA Tree View Pattern) ---

        const tree = document.getElementById('tree-container');

        // Get all visible treeitems in DOM order
        function getVisibleTreeItems() {{
            return Array.from(tree.querySelectorAll('[role="treeitem"]')).filter(item => {{
                // An item is visible if none of its ancestor groups are collapsed
                let parent = item.parentElement;
                while (parent && parent !== tree) {{
                    if (parent.getAttribute('role') === 'group') {{
                        const parentItem = parent.closest('[role="treeitem"]');
                        if (parentItem && parentItem.getAttribute('aria-expanded') === 'false') {{
                            return false;
                        }}
                    }}
                    parent = parent.parentElement;
                }}
                return true;
            }});
        }}

        function setFocus(item) {{
            // Roving tabindex: remove tabindex from current, set on new
            const current = tree.querySelector('[role="treeitem"][tabindex="0"]');
            if (current) current.setAttribute('tabindex', '-1');
            item.setAttribute('tabindex', '0');
            item.focus();
        }}

        function toggleItem(item) {{
            const expanded = item.getAttribute('aria-expanded');
            if (expanded === null) return; // leaf node
            item.setAttribute('aria-expanded', expanded === 'true' ? 'false' : 'true');
        }}

        function expandItem(item) {{
            if (item.getAttribute('aria-expanded') !== null) {{
                item.setAttribute('aria-expanded', 'true');
            }}
        }}

        function collapseItem(item) {{
            if (item.getAttribute('aria-expanded') !== null) {{
                item.setAttribute('aria-expanded', 'false');
            }}
        }}

        // Keyboard navigation
        tree.addEventListener('keydown', function(e) {{
            const target = e.target.closest('[role="treeitem"]');
            if (!target) return;

            const items = getVisibleTreeItems();
            const index = items.indexOf(target);
            let handled = true;

            switch (e.key) {{
                case 'ArrowDown':
                    if (index < items.length - 1) setFocus(items[index + 1]);
                    break;
                case 'ArrowUp':
                    if (index > 0) setFocus(items[index - 1]);
                    break;
                case 'ArrowRight':
                    if (target.getAttribute('aria-expanded') === 'false') {{
                        expandItem(target);
                    }} else if (target.getAttribute('aria-expanded') === 'true') {{
                        // Move to first child
                        const group = target.querySelector('[role="group"]');
                        if (group) {{
                            const firstChild = group.querySelector('[role="treeitem"]');
                            if (firstChild) setFocus(firstChild);
                        }}
                    }}
                    break;
                case 'ArrowLeft':
                    if (target.getAttribute('aria-expanded') === 'true') {{
                        collapseItem(target);
                    }} else {{
                        // Move to parent treeitem
                        const parentGroup = target.parentElement.closest('[role="treeitem"]');
                        if (parentGroup) setFocus(parentGroup);
                    }}
                    break;
                case 'Enter':
                case ' ':
                    if (target.getAttribute('aria-expanded') !== null) {{
                        toggleItem(target);
                    }} else {{
                        // Activate link if it's a leaf node
                        const link = target.querySelector('.node-url');
                        if (link) link.click();
                    }}
                    break;
                case 'Home':
                    if (items.length > 0) setFocus(items[0]);
                    break;
                case 'End':
                    if (items.length > 0) setFocus(items[items.length - 1]);
                    break;
                case '*':
                    // Expand all siblings at this level
                    const parentGroup = target.parentElement;
                    if (parentGroup) {{
                        parentGroup.querySelectorAll(':scope > [role="treeitem"][aria-expanded]').forEach(item => {{
                            item.setAttribute('aria-expanded', 'true');
                        }});
                    }}
                    break;
                default:
                    handled = false;
            }}

            if (handled) {{
                e.preventDefault();
                e.stopPropagation();
            }}
        }});

        // Click to toggle expand/collapse
        tree.addEventListener('click', function(e) {{
            const toggle = e.target.closest('.node-toggle');
            if (!toggle) return;
            const item = toggle.closest('[role="treeitem"]');
            if (item) {{
                toggleItem(item);
                setFocus(item);
            }}
        }});

        function expandAll() {{
            tree.querySelectorAll('[role="treeitem"][aria-expanded]').forEach(item => {{
                item.setAttribute('aria-expanded', 'true');
            }});
        }}

        function collapseAll() {{
            tree.querySelectorAll('[role="treeitem"][aria-expanded]').forEach(item => {{
                item.setAttribute('aria-expanded', 'false');
            }});
        }}
    </script>
</body>
</html>
"""
        return html
    
    def _generate_tree_html(self, node: PageNode, node_id: str = "root", depth: int = 0, t: dict[str, str] | None = None) -> str:
        """
        Generate HTML for a tree node using WAI-ARIA treeview pattern.

        Uses <li role="treeitem"> with aria-expanded for parent nodes
        and <ul role="group"> for children.

        Args:
            node: Node to generate HTML for
            node_id: Unique ID for the node
            depth: Current depth in tree
            t: Translation dictionary

        Returns:
            HTML string
        """
        # Get translations if not provided
        if t is None:
            t = self._get_translations()

        has_children = bool(node.children)
        is_expanded = depth < 2 if has_children else None

        # Build <li role="treeitem"> attributes
        li_classes = ''
        if node.is_directory:
            li_classes = ' class="is-directory"'

        aria_expanded = ''
        if has_children:
            aria_expanded = f' aria-expanded="{"true" if is_expanded else "false"}"'

        # First treeitem in the tree gets tabindex="0", all others get "-1"
        tabindex = '0' if node_id == 'root' else '-1'

        html = f'<li role="treeitem"{li_classes}{aria_expanded} tabindex="{tabindex}" id="{node_id}">'

        # Node content wrapper
        html += '<span class="tree-node-content">'

        if has_children:
            html += f'<span class="node-toggle" aria-hidden="true">▶</span>'
        else:
            html += '<span style="display: inline-block; width: 28px;" aria-hidden="true"></span>'

        # Icon
        if node.is_directory:
            html += '<i class="bi bi-folder-fill text-severity-medium node-icon" aria-hidden="true"></i>'
        else:
            html += '<i class="bi bi-file-earmark text-brand node-icon" aria-hidden="true"></i>'

        # Name/URL
        if node.url:
            html += f'<a href="{node.url}" target="_blank" class="node-url" tabindex="-1">{node.name}</a>'
        else:
            html += f'<span class="node-name">{node.name}</span>'

        # Stats badges
        if node.stats['total_pages'] > 0:
            html += '<span class="node-stats">'

            if node.stats['tested_pages'] == node.stats['total_pages']:
                html += f'<span class="badge badge-pass stats-badge" data-i18n="tested">{t["tested"]}</span>'
            elif node.stats['tested_pages'] > 0:
                html += f'<span class="badge badge-medium stats-badge">{node.stats["tested_pages"]}/{node.stats["total_pages"]} <span data-i18n="x_tested">{t["x_tested"]}</span></span>'
            else:
                html += f'<span class="badge badge-neutral stats-badge" data-i18n="not_tested">{t["not_tested"]}</span>'

            if node.stats['pages_with_issues'] > 0:
                page_text_key = "page_with_issues_count" if node.stats["pages_with_issues"] == 1 else "pages_with_issues_count"
                html += f'<span class="badge badge-high stats-badge">{node.stats["pages_with_issues"]} <span data-i18n="{page_text_key}">{t[page_text_key]}</span></span>'

            if node.stats['total_violations'] > 0:
                html += f'<span class="text-severity-high ms-2">({node.stats["total_violations"]} <span data-i18n="total_violations_count">{t["total_violations_count"]}</span>)</span>'

            html += '</span>'

        html += '</span>'  # close .tree-node-content

        # Children as <ul role="group">
        if has_children:
            html += f'<ul role="group">'
            for i, child in enumerate(node.children):
                child_id = f"{node_id}-{i}"
                html += self._generate_tree_html(child, child_id, depth + 1, t)
            html += '</ul>'

        html += '</li>'

        return html
    
    def to_json(self) -> str:
        """
        Generate JSON report
        
        Returns:
            JSON string
        """
        if not self.tree_data:
            self.generate()
        assert self.tree_data is not None

        return json.dumps(self.tree_data, indent=2, default=str)
    
    def to_csv(self) -> str:
        """
        Generate CSV report with flattened tree structure
        
        Returns:
            CSV string
        """
        if not self.tree_data:
            self.generate()
        assert self.root is not None

        t = self._get_translations()

        output = StringIO()
        writer = csv.writer(output)

        # Header - translated
        writer.writerow([
            t['csv_path'],
            t['csv_url'],
            t['csv_type'],
            t['csv_level'],
            t['total_pages'],
            t['tested_pages'],
            t['pages_with_issues'],
            t['total_violations'],
            t['total_warnings']
        ])

        # Flatten tree and write rows
        self._write_csv_node(writer, self.root, '', 0, t)
        
        return output.getvalue()
    
    def _write_csv_node(self, writer: Any, node: PageNode, path: str, level: int, t: dict[str, str]) -> None:
        """
        Write a node and its children to CSV
        
        Args:
            writer: CSV writer
            node: Node to write
            path: Current path
            level: Current depth level
            t: Translation dictionary
        """
        current_path = f"{path}/{node.name}" if path else node.name
        
        writer.writerow([
            current_path,
            node.url or '',
            t['csv_directory'] if node.is_directory else t['page'],
            level,
            node.stats['total_pages'],
            node.stats['tested_pages'],
            node.stats['pages_with_issues'],
            node.stats['total_violations'],
            node.stats['total_warnings']
        ])
        
        # Write children
        for child in node.children:
            self._write_csv_node(writer, child, current_path, level + 1, t)
    
    def save(self, format: str = 'html') -> str:
        """
        Save report to file

        Args:
            format: Output format (html, json, csv, pdf)

        Returns:
            Path to saved file
        """
        reports_dir = resolve_reports_dir()

        # Ensure directory exists
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        t = self._get_translations()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{t['filename_prefix']}_{self.website.id}_{timestamp}.{format}"
        filepath = reports_dir / filename
        
        # Generate content based on format
        if format == 'html':
            content = self.to_html()
        elif format == 'json':
            content = self.to_json()
        elif format == 'csv':
            content = self.to_csv()
        elif format == 'pdf':
            # For PDF, we'll generate HTML and note that PDF conversion would be needed
            content = self.to_html()
            # In production, you would use a library like weasyprint or pdfkit to convert
            logger.warning("PDF generation not fully implemented, saving as HTML instead")
            filename = filename.replace('.pdf', '.html')
            filepath = reports_dir / filename
        else:
            raise ValueError(f"Unsupported format: {format}")
        
        # Write to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"Page structure report saved to {filepath}")
        return str(filepath)