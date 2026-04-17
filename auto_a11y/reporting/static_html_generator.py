from __future__ import annotations

"""
Static HTML Report Generator

Generates multi-page static HTML reports that can be viewed offline.
Includes index, summary, and individual page detail HTML files.
Packages everything into a downloadable ZIP file.
"""

import json
import logging
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import jinja2

logger = logging.getLogger(__name__)
from auto_a11y.web.fluent import ftl, ftl_enum, force_locale

from auto_a11y.core.database import Database
from auto_a11y.reporting.issue_catalog import IssueCatalog
from auto_a11y.web.fluent import ftl_translate_issue
from config import config


def _narrow_dict(obj: Any) -> dict[str, Any]:
    """Narrow a runtime dict (possibly typed as Any / dict[Unknown, Unknown])
    to dict[str, Any] for static analysis.  Returns empty dict for non-dicts."""
    val: dict[str, Any] = obj if obj else {}
    return val


def _narrow_str_list(obj: Any) -> list[str]:
    """Narrow a runtime list to list[str] for static analysis.
    Returns empty list for non-lists."""
    val: list[str] = obj if obj else []
    return val


# WCAG 2.2 French translations
WCAG_FR_TRANSLATIONS = {
    # Level A
    '1.1.1': 'Contenu non textuel',
    '1.2.1': 'Contenus seulement audio et seulement vidéo (pré-enregistrés)',
    '1.2.2': 'Sous-titres (pré-enregistrés)',
    '1.2.3': 'Audio-description ou version de remplacement pour un média temporel (pré-enregistré)',
    '1.3.1': 'Information et relations',
    '1.3.2': 'Ordre séquentiel logique',
    '1.3.3': 'Caractéristiques sensorielles',
    '1.4.1': 'Utilisation de la couleur',
    '1.4.2': 'Contrôle du son',
    '2.1.1': 'Clavier',
    '2.1.2': 'Pas de piège au clavier',
    '2.1.4': 'Raccourcis clavier utilisant des caractères',
    '2.2.1': 'Réglage du délai',
    '2.2.2': 'Mettre en pause, arrêter, masquer',
    '2.4.1': 'Contourner des blocs',
    '2.4.2': 'Titre de page',
    '2.4.3': 'Parcours du focus',
    '2.4.4': 'Fonction du lien (selon le contexte)',
    '3.1.1': 'Langue de la page',
    '3.2.1': 'Au focus',
    '3.2.2': 'À la saisie',
    '3.3.1': 'Identification des erreurs',
    '3.3.2': 'Étiquettes ou instructions',
    '3.2.6': 'Aide cohérente',
    '3.3.7': 'Saisie redondante',
    '4.1.2': 'Nom, rôle et valeur',
    '4.1.3': 'Messages d\'état',
    # Level AA
    '1.2.4': 'Sous-titres (en direct)',
    '1.2.5': 'Audio-description (pré-enregistrée)',
    '1.3.4': 'Orientation',
    '1.3.5': 'Identifier la finalité de la saisie',
    '1.4.3': 'Contraste (minimum)',
    '1.4.4': 'Redimensionnement du texte',
    '1.4.5': 'Texte sous forme d\'image',
    '1.4.10': 'Redistribution',
    '1.4.11': 'Contraste du contenu non textuel',
    '1.4.12': 'Espacement du texte',
    '1.4.13': 'Contenu au survol ou au focus',
    '2.4.5': 'Accès multiples',
    '2.4.6': 'En-têtes et étiquettes',
    '2.4.7': 'Visibilité du focus',
    '2.4.11': 'Focus non masqué (minimum)',
    '2.5.1': 'Gestes pour le contrôle du pointeur',
    '2.5.2': 'Annulation de l\'action du pointeur',
    '2.5.3': 'Étiquette dans le nom',
    '2.5.7': 'Mouvements de glissement',
    '2.5.8': 'Taille de la cible (minimum)',
    '3.2.3': 'Navigation cohérente',
    '3.2.4': 'Identification cohérente',
    '3.3.3': 'Suggestion après une erreur',
    '3.3.4': 'Prévention des erreurs (juridiques, financières, de données)',
    '3.3.8': 'Authentification accessible (minimum)',
    # Level AAA
    '1.2.6': 'Langue des signes (pré-enregistrée)',
    '1.2.7': 'Audio-description étendue (pré-enregistrée)',
    '1.2.8': 'Version de remplacement pour un média temporel (pré-enregistrée)',
    '1.2.9': 'Seulement audio (en direct)',
    '1.3.6': 'Identifier la fonction',
    '1.4.6': 'Contraste (amélioré)',
    '1.4.7': 'Arrière-plan sonore de faible volume ou absent',
    '1.4.8': 'Présentation visuelle',
    '1.4.9': 'Texte sous forme d\'image (sans exception)',
    '2.1.3': 'Clavier (pas d\'exception)',
    '2.2.3': 'Pas de délai d\'exécution',
    '2.2.4': 'Interruptions',
    '2.2.5': 'Nouvelle authentification',
    '2.2.6': 'Délais d\'expiration',
    '2.3.1': 'Pas plus de trois flashs ou sous le seuil critique',
    '2.3.2': 'Trois flashs',
    '2.3.3': 'Animation résultant d\'interactions',
    '2.4.8': 'Localisation',
    '2.4.9': 'Fonction du lien (lien uniquement)',
    '2.4.10': 'En-têtes de section',
    '2.4.12': 'Focus non masqué (amélioré)',
    '2.4.13': 'Apparence du focus',
    '2.5.4': 'Activation par le mouvement',
    '2.5.5': 'Taille de la cible (amélioré)',
    '2.5.6': 'Modalités d\'entrées concurrentes',
    '3.1.2': 'Langue d\'un passage',
    '3.1.3': 'Mots rares',
    '3.1.4': 'Abréviations',
    '3.1.5': 'Niveau de lecture',
    '3.1.6': 'Prononciation',
    '3.2.5': 'Changement à la demande',
    '3.3.5': 'Aide',
    '3.3.6': 'Prévention des erreurs (toutes)',
    '3.3.9': 'Authentification accessible (amélioré)',
}

# Level translations
WCAG_LEVEL_TRANSLATIONS = {
    'en': {
        'A': 'Level A',
        'AA': 'Level AA',
        'AAA': 'Level AAA',
    },
    'fr': {
        'A': 'Niveau A',
        'AA': 'Niveau AA',
        'AAA': 'Niveau AAA',
    }
}

# WCAG URL slug mappings for Understanding and Quick Reference pages
WCAG_URL_SLUGS = {
    '1.1.1': 'non-text-content',
    '1.2.1': 'audio-only-and-video-only-prerecorded',
    '1.2.2': 'captions-prerecorded',
    '1.2.3': 'audio-description-or-media-alternative-prerecorded',
    '1.2.4': 'captions-live',
    '1.2.5': 'audio-description-prerecorded',
    '1.2.6': 'sign-language-prerecorded',
    '1.2.7': 'extended-audio-description-prerecorded',
    '1.2.8': 'media-alternative-prerecorded',
    '1.2.9': 'audio-only-live',
    '1.3.1': 'info-and-relationships',
    '1.3.2': 'meaningful-sequence',
    '1.3.3': 'sensory-characteristics',
    '1.3.4': 'orientation',
    '1.3.5': 'identify-input-purpose',
    '1.3.6': 'identify-purpose',
    '1.4.1': 'use-of-color',
    '1.4.2': 'audio-control',
    '1.4.3': 'contrast-minimum',
    '1.4.4': 'resize-text',
    '1.4.5': 'images-of-text',
    '1.4.6': 'contrast-enhanced',
    '1.4.7': 'low-or-no-background-audio',
    '1.4.8': 'visual-presentation',
    '1.4.9': 'images-of-text-no-exception',
    '1.4.10': 'reflow',
    '1.4.11': 'non-text-contrast',
    '1.4.12': 'text-spacing',
    '1.4.13': 'content-on-hover-or-focus',
    '2.1.1': 'keyboard',
    '2.1.2': 'no-keyboard-trap',
    '2.1.3': 'keyboard-no-exception',
    '2.1.4': 'character-key-shortcuts',
    '2.2.1': 'timing-adjustable',
    '2.2.2': 'pause-stop-hide',
    '2.2.3': 'no-timing',
    '2.2.4': 'interruptions',
    '2.2.5': 're-authenticating',
    '2.2.6': 'timeouts',
    '2.3.1': 'three-flashes-or-below-threshold',
    '2.3.2': 'three-flashes',
    '2.3.3': 'animation-from-interactions',
    '2.4.1': 'bypass-blocks',
    '2.4.2': 'page-titled',
    '2.4.3': 'focus-order',
    '2.4.4': 'link-purpose-in-context',
    '2.4.5': 'multiple-ways',
    '2.4.6': 'headings-and-labels',
    '2.4.7': 'focus-visible',
    '2.4.8': 'location',
    '2.4.9': 'link-purpose-link-only',
    '2.4.10': 'section-headings',
    '2.4.11': 'focus-not-obscured-minimum',
    '2.4.12': 'focus-not-obscured-enhanced',
    '2.4.13': 'focus-appearance',
    '2.5.1': 'pointer-gestures',
    '2.5.2': 'pointer-cancellation',
    '2.5.3': 'label-in-name',
    '2.5.4': 'motion-actuation',
    '2.5.5': 'target-size-enhanced',
    '2.5.6': 'concurrent-input-mechanisms',
    '2.5.7': 'dragging-movements',
    '2.5.8': 'target-size-minimum',
    '3.1.1': 'language-of-page',
    '3.1.2': 'language-of-parts',
    '3.1.3': 'unusual-words',
    '3.1.4': 'abbreviations',
    '3.1.5': 'reading-level',
    '3.1.6': 'pronunciation',
    '3.2.1': 'on-focus',
    '3.2.2': 'on-input',
    '3.2.3': 'consistent-navigation',
    '3.2.4': 'consistent-identification',
    '3.2.5': 'change-on-request',
    '3.2.6': 'consistent-help',
    '3.3.1': 'error-identification',
    '3.3.2': 'labels-or-instructions',
    '3.3.3': 'error-suggestion',
    '3.3.4': 'error-prevention-legal-financial-data',
    '3.3.5': 'help',
    '3.3.6': 'error-prevention-all',
    '3.3.7': 'redundant-entry',
    '3.3.8': 'accessible-authentication-minimum',
    '3.3.9': 'accessible-authentication-enhanced',
    '4.1.1': 'parsing',
    '4.1.2': 'name-role-value',
    '4.1.3': 'status-messages',
}


def translate_wcag_criterion(criterion_text: str, language: str = 'en') -> str:
    """
    Translate a WCAG criterion from English to the specified language.

    Args:
        criterion_text: WCAG criterion in format "2.5.3 Label in Name (Level A)" or "1.3.1, 3.3.2"
        language: Target language ('en' or 'fr')

    Returns:
        Translated criterion text
    """
    if language == 'en':
        return criterion_text

    import re

    # Handle multiple criteria separated by commas
    if ',' in criterion_text:
        criteria = [c.strip() for c in criterion_text.split(',')]
        translated = [translate_wcag_criterion(c, language) for c in criteria]
        return ', '.join(translated)

    # Parse format like "2.5.3 Label in Name (Level A)"
    match = re.match(r'([\d.]+)\s+([^(]+)\s*\(Level\s+(A{1,3})\)', criterion_text)
    if match:
        criterion_num = match.group(1)
        level = match.group(3)

        french_title = WCAG_FR_TRANSLATIONS.get(criterion_num, match.group(2).strip())
        french_level = WCAG_LEVEL_TRANSLATIONS['fr'].get(level, f'Niveau {level}')

        return f"{criterion_num} {french_title} ({french_level})"

    # Parse format like "2.5.3" (just the number)
    match = re.match(r'^([\d.]+)$', criterion_text.strip())
    if match:
        criterion_num = match.group(1)
        french_title = WCAG_FR_TRANSLATIONS.get(criterion_num, criterion_text)
        return f"{criterion_num} {french_title}" if french_title != criterion_text else criterion_text

    # If no pattern matches, return original
    return criterion_text


class StaticHTMLReportGenerator:
    """Generates self-contained multi-page static HTML accessibility reports"""

    def __init__(self, database: Database, output_dir: Path | None = None, language: str = 'en') -> None:
        """
        Initialize the static HTML report generator

        Args:
            database: Database manager instance
            output_dir: Base directory for generated reports (defaults to reports/ in project root)
            language: Language code for translations ('en' or 'fr', defaults to 'en')
        """
        self.db = database
        self.output_dir = output_dir or Path(__file__).parent.parent.parent / 'reports'
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.language = language

        # Setup Jinja2 template environment - set to templates directory so relative paths work
        template_dir = Path(__file__).parent.parent / 'web' / 'templates'
        self.template_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            autoescape=jinja2.select_autoescape(['html', 'xml']),
            extensions=['jinja2.ext.i18n']
        )

        # Configure Jinja2 for Flask-Babel translations
        # These callables will be replaced by force_locale context manager
        # install_gettext_callables is provided by jinja2.ext.i18n at runtime
        install_fn = getattr(self.template_env, 'install_gettext_callables', None)
        if install_fn is not None:
            def _gettext(x: str) -> str:
                return x

            def _ngettext(s: str, p: str, n: int) -> str:
                return s if n == 1 else p

            install_fn(
                gettext=_gettext,
                ngettext=_ngettext,
                newstyle=True
            )

        # Add custom filters
        self._setup_template_filters()

        # Register Fluent translation functions so templates can use {{ ftl(...) }}
        getattr(self.template_env, 'globals')['ftl'] = ftl
        getattr(self.template_env, 'globals')['ftl_enum'] = ftl_enum

    def _get_translations(self) -> dict[str, dict[str, str]]:
        """Get translations for both EN and FR languages"""
        translations = {
            'en': {
                # Index page
                'accessibility_report': 'Accessibility Report',
                'deduplicated': 'Deduplicated',
                'deduplicated_report': 'Deduplicated Accessibility Report',
                'issues_grouped_by_components': 'Issues grouped by common components',
                'accessibility_score': 'Accessibility Score',
                'compliance_score': 'Compliance Score',
                'violations': 'Violations',
                'warnings': 'Warnings',
                'info_items': 'Info Items',
                'components': 'Components',
                'pages': 'Pages',
                'common_components': 'Common Components',
                'click_component_to_view': 'Click a component to view its deduplicated issues',
                'pages_with_non_component_issues': 'Pages with Non-Component Issues',
                'issues_not_in_components': 'Issues not associated with common components, organized by page',
                'search_pages': 'Search pages...',
                'no_common_components': 'No common components found',

                # Common Issues section
                'common_issues': 'Common Issues',
                'common_issues_description': 'Issues appearing on multiple pages across the site',
                'no_common_issues': 'No common issues found',
                'appears_on': 'Appears on',
                'of_pages': 'of pages',
                'instances': 'instances',

                # Component detail page
                'component_deduplicated_issues': 'Component Deduplicated Issues',
                'back_to_index': 'Back to Index',
                'found_on': 'Found on',
                'page_s': 'page(s)',
                'deduplicated_issue_s': 'deduplicated issue(s)',
                'no_issues_found': 'No issues found for this component',

                # Page detail / unassigned page
                'index': 'Index',
                'untitled_page': 'Untitled Page',
                'status': 'Status',
                'tested': 'Tested',
                'last_tested': 'Last Tested',
                'recently': 'Recently',
                'issues_found': 'Issues Found',
                'errors': 'Errors',
                'score': 'Score',
                'full_page_accessibility_score': 'Full Page Accessibility Score',
                'all_issues_full_page': 'All issues (full page)',
                'non_component_issues_score': 'Non-Component Issues Score',
                'issues_not_in_common_components': 'Issues not in common components',
                'compliance_score': 'Compliance Score',
                'tests_passed': 'tests passed',
                'n_a': 'N/A',
                'no_data_available': 'No data available',
                'filter_test_results': 'Filter Test Results',
                'clear_all_filters': 'Clear All Filters',
                'active_filters': 'Active Filters',
                'no_filters_active': 'No filters active',
                'showing': 'Showing',
                'of': 'of',
                'items': 'items',
                'issue_type': 'Issue Type',
                'impact_level': 'Impact Level',
                'high': 'High',
                'medium': 'Medium',
                'low': 'Low',
                'issue_touchpoint': 'Issue Touchpoint',
                'all_touchpoints': 'All Touchpoints',
                'general': 'General',

                # Impact levels (all variations)
                'critical': 'Critical',
                'high': 'High',
                'serious': 'Serious',
                'moderate': 'Moderate',
                'medium': 'Medium',
                'minor': 'Minor',
                'low': 'Low',
                'quick_search': 'Quick Search',
                'search_in_issues': 'Search in issue descriptions, error codes, or XPaths...',
                'search_placeholder': 'Search in issue descriptions, error codes, or XPaths...',
                'non_component_issues_for_page': 'Non-Component Issues for This Page',
                'issues_not_part_of_common': 'Issues not part of common components',
                'informational': 'Informational',
                'discovery': 'Discovery',

                # Issue details
                'instance': 'Instance',
                'guest': 'Guest',
                'about_this_issue': 'About this issue:',
                'what_the_issue_is': 'What the issue is:',
                'why_this_is_important': 'Why this is important:',
                'who_it_affects': 'Who it affects:',
                'how_to_remediate': 'How to remediate:',
                'relevant_test_criteria': 'Relevant test criteria:',
                'wcag_success_criteria': 'WCAG Success Criteria:',
                'touchpoint': 'Touchpoint',
                'impact': 'Impact',
                'more_about': 'More about',
                'ways_to_meet': 'Ways to meet',
                'affected_pages': 'Affected Pages',
                'show_all_pages': 'Show all',
                'element_details': 'Element Details:',
                'element': 'Element',
                'xpath': 'XPath',
                'location': 'Location',
                'responsive_breakpoint': 'Responsive Breakpoint',
                'px_width': 'px width',
                'css_state': 'CSS State',
                'code_snippet': 'Code Snippet',
                'rule': 'Rule',
                'help': 'Help',
                'how_to_fix': 'How to fix:',

                # Component types
                'form': 'Form',
                'navigation': 'Navigation',
                'aside': 'Aside',
                'section': 'Section',
                'header': 'Header',
                'footer': 'Footer',
                'fields': 'fields',

                # Touchpoints
                'accessible_names': 'Accessible Names',
                'animation': 'Animation',
                'colors': 'Colors',
                'dialogs': 'Dialogs',
                'electronic_documents': 'Electronic Documents',
                'event_handling': 'Event Handling',
                'fonts': 'Fonts',
                'forms': 'Forms',
                'headings': 'Headings',
                'images': 'Images',
                'landmarks': 'Landmarks',
                'links': 'Links',
                'lists': 'Lists',
                'page': 'Page',
                'styles': 'Styles',
                'tables': 'Tables',
                'color_contrast': 'Color Contrast',
                'tabindex': 'Tab Index',
                'carousels': 'Carousels',
                'multimedia': 'Multimedia',
                'keyboard': 'Keyboard',
                'focus': 'Focus',
                'focus_management': 'Focus Management',
                'focus management': 'Focus Management',

                # User roles
                'no login': 'Guest (no login)',

                # State descriptions
                'initial_page_state': 'Initial page state (before script execution)',
                'after_executing_script': 'After executing script:',

                # Misc
                'language': 'Language',
                'violation_s': 'violation(s)',
                'warning_s': 'warning(s)',
                'info': 'info',
                'hidden': 'hidden',
                'text': 'text',
                'search': 'search',

                # Static report index page
                'search_by_page_title_or_url': 'Search by page title or URL...',
                'all_pages': 'All Pages',
                'has_errors': 'Has Errors',
                'has_warnings': 'Has Warnings',
                'no_errors': 'No Errors',
                'sort_by_title': 'Sort by Title',
                'errors_high_to_low': 'Errors (High to Low)',
                'errors_low_to_high': 'Errors (Low to High)',
                'score_low_to_high': 'Score (Low to High)',
                'score_high_to_low': 'Score (High to Low)',
                'reset': 'Reset',
                'view_details': 'View Details',
                'no_pages_found': 'No Pages Found',
                'try_adjusting_search': 'Try adjusting your search or filter criteria.',
            },
            'fr': {
                # Index page
                'accessibility_report': 'Rapport_d_accessibilite',
                'deduplicated': 'Dédupliqué',
                'deduplicated_report': 'Rapport d\'accessibilité dédupliqué',
                'issues_grouped_by_components': 'Problèmes regroupés par composants communs',
                'accessibility_score': 'Score d\'accessibilité',
                'compliance_score': 'Score de conformité',
                'violations': 'Violations',
                'warnings': 'Avertissements',
                'info_items': 'Éléments informatifs',
                'components': 'Composants',
                'pages': 'Pages',
                'common_components': 'Composants communs',
                'click_component_to_view': 'Cliquez sur un composant pour afficher ses problèmes dédupliqués',
                'pages_with_non_component_issues': 'Pages avec problèmes hors composants',
                'issues_not_in_components': 'Problèmes non associés aux composants communs, organisés par page',
                'search_pages': 'Rechercher des pages...',
                'no_common_components': 'Aucun composant commun trouvé',

                # Common Issues section
                'common_issues': 'Problèmes communs',
                'common_issues_description': 'Problèmes apparaissant sur plusieurs pages du site',
                'no_common_issues': 'Aucun problème commun trouvé',
                'appears_on': 'Apparaît sur',
                'of_pages': 'des pages',
                'instances': 'instances',

                # Component detail page
                'component_deduplicated_issues': 'Problèmes dédupliqués du composant',
                'back_to_index': 'Retour à l\'index',
                'found_on': 'Trouvé sur',
                'page_s': 'page(s)',
                'deduplicated_issue_s': 'problème(s) dédupliqué(s)',
                'no_issues_found': 'Aucun problème trouvé pour ce composant',

                # Page detail / unassigned page
                'index': 'Index',
                'untitled_page': 'Page sans titre',
                'status': 'Statut',
                'tested': 'Testé',
                'last_tested': 'Dernier test',
                'recently': 'Récemment',
                'issues_found': 'Problèmes trouvés',
                'errors': 'Erreurs',
                'score': 'Score',
                'full_page_accessibility_score': 'Score d\'accessibilité de la page complète',
                'all_issues_full_page': 'Tous les problèmes (page complète)',
                'non_component_issues_score': 'Score des problèmes hors composants',
                'issues_not_in_common_components': 'Problèmes non présents dans les composants communs',
                'compliance_score': 'Score de conformité',
                'tests_passed': 'tests réussis',
                'n_a': 'N/D',
                'no_data_available': 'Aucune donnée disponible',
                'based_on_automated_checks': 'Basé sur des vérifications automatisées',
                'filter_test_results': 'Filtrer les résultats',
                'clear_all_filters': 'Effacer tous les filtres',
                'active_filters': 'Filtres actifs',
                'no_filters_active': 'Aucun filtre actif',
                'showing': 'Affichage',
                'of': 'sur',
                'items': 'éléments',
                'no_items': 'Aucun élément',
                'issue_type': 'Type de problème',
                'impact_level': 'Niveau d\'impact',
                'high': 'Élevé',
                'medium': 'Moyen',
                'low': 'Faible',
                'issue_touchpoint': 'Point de contact',
                'all_touchpoints': 'Tous les points de contact',
                'general': 'Général',

                # Impact levels (all variations)
                'critical': 'Critique',
                'high': 'Élevé',
                'serious': 'Grave',
                'moderate': 'Modéré',
                'medium': 'Moyen',
                'minor': 'Mineur',
                'low': 'Faible',
                'quick_search': 'Recherche rapide',
                'search_in_issues': 'Rechercher dans les descriptions, codes d\'erreur ou XPath...',
                'search_placeholder': 'Rechercher dans les descriptions, codes d\'erreur ou XPath...',
                'non_component_issues_for_page': 'Problèmes hors composants pour cette page',
                'issues_not_part_of_common': 'Problèmes ne faisant pas partie de composants communs',
                'informational': 'Informatif',
                'discovery': 'Découverte',

                # Issue details
                'instance': 'Instance',
                'guest': 'Invité',
                'about_this_issue': 'À propos de ce problème :',
                'what_the_issue_is': 'En quoi consiste le problème :',
                'why_this_is_important': 'Pourquoi c\'est important :',
                'who_it_affects': 'Qui est affecté :',
                'how_to_remediate': 'Comment corriger :',
                'relevant_test_criteria': 'Critères de test pertinents :',
                'wcag_success_criteria': 'Critères de succès WCAG :',
                'touchpoint': 'Point de contact',
                'impact': 'Impact',
                'more_about': 'En savoir plus sur',
                'ways_to_meet': 'Comment satisfaire',
                'affected_pages': 'Pages affectées',
                'show_all_pages': 'Afficher toutes',
                'element_details': 'Détails de l\'élément :',
                'element': 'Élément',
                'xpath': 'XPath',
                'location': 'Emplacement',
                'responsive_breakpoint': 'Point d\'arrêt responsive',
                'px_width': 'px de largeur',
                'css_state': 'État CSS',
                'code_snippet': 'Extrait de code',
                'rule': 'Règle',
                'help': 'Aide',
                'how_to_fix': 'Comment corriger :',

                # Component types
                'form': 'Formulaire',
                'navigation': 'Navigation',
                'aside': 'Aparté',
                'section': 'Section',
                'header': 'En-tête',
                'footer': 'Pied de page',
                'fields': 'champs',

                # Touchpoints
                'accessible_names': 'Noms accessibles',
                'animation': 'Animation',
                'colors': 'Couleurs',
                'dialogs': 'Dialogues',
                'electronic_documents': 'Documents électroniques',
                'event_handling': 'Gestion des événements',
                'fonts': 'Polices',
                'forms': 'Formulaires',
                'headings': 'Titres',
                'images': 'Images',
                'landmarks': 'Points de repère',
                'links': 'Liens',
                'lists': 'Listes',
                'page': 'Page',
                'styles': 'Styles',
                'tables': 'Tableaux',
                'color_contrast': 'Contraste des couleurs',
                'tabindex': 'Index de tabulation',
                'carousels': 'Carrousels',
                'multimedia': 'Multimédia',
                'keyboard': 'Clavier',
                'focus': 'Focus',
                'focus_management': 'Gestion du focus',
                'focus management': 'Gestion du focus',

                # User roles
                'no login': 'Invité (sans connexion)',

                # State descriptions
                'initial_page_state': 'État initial de la page (avant exécution du script)',
                'after_executing_script': 'Après exécution du script :',

                # Misc
                'language': 'Langue',
                'violation_s': 'violation(s)',
                'warning_s': 'avertissement(s)',
                'info': 'info',
                'hidden': 'caché',
                'text': 'texte',
                'search': 'recherche',

                # Static report index page
                'search_by_page_title_or_url': 'Rechercher par titre de page ou URL...',
                'all_pages': 'Toutes les pages',
                'has_errors': 'Avec erreurs',
                'has_warnings': 'Avec avertissements',
                'no_errors': 'Sans erreurs',
                'sort_by_title': 'Trier par titre',
                'errors_high_to_low': 'Erreurs (Élevé à faible)',
                'errors_low_to_high': 'Erreurs (Faible à élevé)',
                'score_low_to_high': 'Score (Faible à élevé)',
                'score_high_to_low': 'Score (Élevé à faible)',
                'reset': 'Réinitialiser',
                'view_details': 'Voir les détails',
                'no_pages_found': 'Aucune page trouvée',
                'try_adjusting_search': 'Essayez d\'ajuster vos critères de recherche ou de filtre.',
            }
        }
        return translations

    def _get_issue_description_translations(self) -> None:
        """DEPRECATED: Inline issue translations are now resolved via Fluent.

        Use ``ftl_translate_issue(text)`` inside a ``force_locale('fr')`` block
        instead of dictionary lookups.  This method is kept for backwards
        compatibility but returns None.
        """
        return None

    def _setup_template_filters(self) -> None:
        """Setup custom Jinja2 filters"""

        def error_code_only(code: str) -> str:
            """Extract error code from full violation ID"""
            if ':' in code:
                return code.split(':')[1]
            return code

        def wcag_name(criterion: str) -> str:
            """Extract WCAG criterion name from full string"""
            parts = criterion.split()
            return parts[0] if parts else criterion

        def wcag_understanding_url(criterion_code: str) -> str:
            """Generate WCAG Understanding URL with correct slug

            Note: Both English and French link to English Understanding pages as the
            official French WCAG translation does (marked as 'en anglais')
            """
            slug = WCAG_URL_SLUGS.get(criterion_code, criterion_code)
            return f"https://www.w3.org/WAI/WCAG22/Understanding/{slug}"

        def wcag_quickref_url(criterion_code: str) -> str:
            """Generate WCAG Quick Reference URL with correct slug

            Note: Both English and French link to English Quick Reference pages as the
            official French WCAG translation does (marked as 'en anglais')
            """
            slug = WCAG_URL_SLUGS.get(criterion_code, criterion_code)
            return f"https://www.w3.org/WAI/WCAG22/quickref/#{slug}"

        def translate_wcag(criterion_text: str, language: str = 'en') -> str:
            """Translate WCAG criterion text"""
            return translate_wcag_criterion(criterion_text, language)

        # Register filters
        self.template_env.filters['error_code_only'] = error_code_only
        self.template_env.filters['wcag_name'] = wcag_name
        self.template_env.filters['wcag_understanding_url'] = wcag_understanding_url
        self.template_env.filters['wcag_quickref_url'] = wcag_quickref_url
        self.template_env.filters['translate_wcag'] = translate_wcag

    def _read_embedded_assets(self) -> dict[str, str]:
        """Read Bootstrap CSS and JS files for embedding inline"""
        import re
        static_dir = Path(__file__).parent.parent / 'web' / 'static'

        assets = {}
        try:
            # Read Bootstrap CSS
            bootstrap_css_path = static_dir / 'css' / 'bootstrap.min.css'
            bootstrap_css = bootstrap_css_path.read_text(encoding='utf-8')
            # Remove source map reference to prevent browser from trying to load it
            bootstrap_css = bootstrap_css.replace('/*# sourceMappingURL=bootstrap.min.css.map */', '')
            assets['bootstrap_css'] = bootstrap_css

            # Read Bootstrap Icons CSS with embedded font
            bootstrap_icons_path = static_dir / 'css' / 'bootstrap-icons.css'
            bootstrap_icons_css = bootstrap_icons_path.read_text(encoding='utf-8')
            # Embed woff2 font as base64 data URI so icons render in static HTML
            woff2_path = static_dir / 'css' / 'fonts' / 'bootstrap-icons.woff2'
            if woff2_path.exists():
                import base64
                woff2_b64 = base64.b64encode(woff2_path.read_bytes()).decode('ascii')
                data_uri = f'url("data:font/woff2;base64,{woff2_b64}") format("woff2")'
                bootstrap_icons_css = re.sub(
                    r'src:\s*url\([^)]+\)(\s*format\([^)]+\))?(,\s*url\([^)]+\)(\s*format\([^)]+\))?)*;',
                    f'src: {data_uri};',
                    bootstrap_icons_css
                )
            else:
                # Fallback: strip font URLs to avoid 404 errors (icons won't display)
                bootstrap_icons_css = re.sub(
                    r'src:\s*url\([^)]+\)(\s*format\([^)]+\))?(,\s*url\([^)]+\)(\s*format\([^)]+\))?)*;',
                    'src: url("");',
                    bootstrap_icons_css
                )
            assets['bootstrap_icons_css'] = bootstrap_icons_css

            # Read Bootstrap JS
            bootstrap_js_path = static_dir / 'js' / 'bootstrap.bundle.min.js'
            bootstrap_js = bootstrap_js_path.read_text(encoding='utf-8')
            # Remove source map reference to prevent browser from trying to load it
            bootstrap_js = bootstrap_js.replace('//# sourceMappingURL=bootstrap.bundle.min.js.map', '')
            assets['bootstrap_js'] = bootstrap_js

            # Read design tokens CSS (lives in public static dir)
            tokens_css_path = static_dir / 'public' / 'css' / 'tokens.css'
            if tokens_css_path.exists():
                assets['tokens_css'] = tokens_css_path.read_text(encoding='utf-8')
            else:
                assets['tokens_css'] = ''

            # Read custom style CSS
            style_css_path = static_dir / 'css' / 'style.css'
            if style_css_path.exists():
                assets['style_css'] = style_css_path.read_text(encoding='utf-8')
            else:
                assets['style_css'] = ''

        except Exception as e:
            logger.error(f"Failed to read embedded assets: {e}")
            # Return empty strings if files can't be read
            assets = {
                'bootstrap_css': '',
                'bootstrap_icons_css': '',
                'bootstrap_js': '',
                'tokens_css': '',
                'style_css': ''
            }

        return assets

    def generate_report(
        self,
        page_ids: list[str],
        project_name: str = "Accessibility Report",
        website_url: str | None = None,
        wcag_level: str = "AA",
        touchpoints_tested: list[str] | None = None,
        include_screenshots: bool = True,
        include_discovery: bool = True,
        ai_tests_enabled: bool = True,
        progress_callback: Callable[..., object] | None = None
    ) -> Path:
        """
        Generate complete static HTML report

        Args:
            page_ids: List of page IDs to include in report
            project_name: Name of the project
            website_url: Base URL of the website
            wcag_level: WCAG conformance level (A, AA, AAA)
            touchpoints_tested: List of touchpoints included in testing
            include_screenshots: Whether to include page screenshots
            include_discovery: Whether to include discovery items
            ai_tests_enabled: Whether AI tests were enabled

        Returns:
            Path to generated ZIP file
        """
        # Create temporary directory for report
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = self.output_dir / f'static_report_{timestamp}'
        report_dir.mkdir(parents=True, exist_ok=True)

        try:
            # === Pass 1: Collect summary stats without loading full results ===
            streaming_stats = self._collect_summary_stats(page_ids, progress_callback=progress_callback)

            # Sort page_info alphabetically by title for consistent ordering
            page_info = sorted(streaming_stats['page_info'], key=lambda p: p['title'].lower())

            # Build summary dict from streaming stats (same shape as _generate_summary_stats)
            summary = self._build_summary_from_stats(streaming_stats)

            # Create directory structure
            self._create_directory_structure(report_dir)

            # Copy static assets (screenshots use lightweight page_info)
            self._copy_assets(report_dir, include_screenshots, page_info if include_screenshots else None)

            # Generate index.html using lightweight page_info (no full issue arrays needed)
            self._generate_index_html(report_dir, page_info, summary, project_name,
                                     website_url, wcag_level, touchpoints_tested)

            # === Pass 2: Generate page detail HTMLs one at a time ===
            self._generate_page_detail_htmls_streaming(
                report_dir, page_info, include_discovery,
                project_name, wcag_level, touchpoints_tested,
                progress_callback=progress_callback)

            # Create manifest using lightweight page_info
            self._create_manifest(report_dir, page_info, summary, project_name,
                                website_url, wcag_level, touchpoints_tested, ai_tests_enabled)

            # Package as ZIP
            zip_path = self._package_as_zip(report_dir, timestamp)

            return zip_path

        finally:
            # Clean up temporary directory
            if report_dir.exists():
                shutil.rmtree(report_dir)

    def _collect_pages_data(self, page_ids: list[str], include_discovery: bool, progress_callback: Callable[..., object] | None = None) -> list[dict[str, Any]]:
        """
        Collect all data for pages to be included in report

        Args:
            page_ids: List of page IDs
            include_discovery: Whether to include discovery items

        Returns:
            List of page data dictionaries
        """
        pages_data: list[dict[str, Any]] = []

        for i, page_id in enumerate(page_ids):
            if progress_callback:
                with force_locale(self.language):
                    progress_callback(i, len(page_ids), ftl('reports-collecting-data-for-page-current-of-total', current=i + 1, total=len(page_ids)))
            # Get page from database
            page = self.db.get_page(page_id)
            if not page:
                continue

            # Get latest test result
            latest_result = self.db.get_latest_test_result(page_id)
            if not latest_result:
                # Include page even without results
                pages_data.append({
                    'id': page_id,
                    'title': page.title or 'Untitled Page',
                    'url': page.url,
                    'test_date': None,
                    'score': 0,
                    'screenshot_path': page.screenshot_path,
                    'violations': [],
                    'warnings': [],
                    'informational': [],
                    'discovery': [],
                    'issues': {
                        'errors': 0,
                        'warnings': 0,
                        'info': 0,
                        'discovery': 0
                    }
                })
                continue

            # Organize issues by severity - TestResult already has them separated
            violations: list[dict[str, Any]] = []
            warnings: list[dict[str, Any]] = []
            informational: list[dict[str, Any]] = []
            discovery: list[dict[str, Any]] = []

            # Process violations (errors)
            for violation in latest_result.violations or []:
                issue_dict: dict[str, Any] = {
                    'id': violation.id,
                    'description': violation.description,
                    'touchpoint': violation.touchpoint,
                    'impact': violation.impact.value if hasattr(violation.impact, 'value') else violation.impact,
                    'xpath': violation.xpath,
                    'html_snippet': violation.html,
                    'metadata': violation.metadata,
                    'wcag_criteria': violation.wcag_criteria
                }

                # Enrich with IssueCatalog for bilingual descriptions
                with force_locale('en'):
                    enriched_en = IssueCatalog.enrich_issue(issue_dict.copy())
                with force_locale('fr'):
                    enriched_fr = IssueCatalog.enrich_issue(issue_dict.copy())

                # Add bilingual descriptions
                issue_dict['description_en'] = enriched_en.get('description_full', enriched_en.get('description', violation.description or violation.id))
                issue_dict['description_fr'] = enriched_fr.get('description_full', enriched_fr.get('description', violation.description or violation.id))

                # Merge enriched metadata for bilingual support
                if not issue_dict['metadata']:
                    issue_dict['metadata'] = {}
                # Copy bilingual metadata from original issue (already translated when stored in DB)
                orig_metadata = violation.metadata if hasattr(violation, 'metadata') and violation.metadata else {}
                # Title for accordion display
                issue_dict['metadata']['title_en'] = orig_metadata.get('title_en', enriched_en.get('title', enriched_en.get('description_full', '')))
                issue_dict['metadata']['title_fr'] = orig_metadata.get('title_fr', enriched_fr.get('title', enriched_fr.get('description_full', '')))
                # Generic description for grouped issues
                issue_dict['metadata']['what_generic_en'] = orig_metadata.get('what_generic_en', enriched_en.get('what_generic', enriched_en.get('description_full', '')))
                issue_dict['metadata']['what_generic_fr'] = orig_metadata.get('what_generic_fr', enriched_fr.get('what_generic', enriched_fr.get('description_full', '')))
                issue_dict['metadata']['what_generic'] = issue_dict['metadata']['what_generic_en'] if self.language == 'en' else issue_dict['metadata']['what_generic_fr']
                # Specific what description
                issue_dict['metadata']['what_en'] = orig_metadata.get('what_en', enriched_en.get('description_full', enriched_en.get('description', '')))
                issue_dict['metadata']['what_fr'] = orig_metadata.get('what_fr', enriched_fr.get('description_full', enriched_fr.get('description', '')))
                issue_dict['metadata']['why_en'] = orig_metadata.get('why_en', enriched_en.get('why_it_matters', ''))
                issue_dict['metadata']['why_fr'] = orig_metadata.get('why_fr', enriched_fr.get('why_it_matters', ''))
                issue_dict['metadata']['who_en'] = orig_metadata.get('who_en', enriched_en.get('who_it_affects', ''))
                issue_dict['metadata']['who_fr'] = orig_metadata.get('who_fr', enriched_fr.get('who_it_affects', ''))
                issue_dict['metadata']['full_remediation_en'] = orig_metadata.get('full_remediation_en', enriched_en.get('how_to_fix', ''))
                issue_dict['metadata']['full_remediation_fr'] = orig_metadata.get('full_remediation_fr', enriched_fr.get('how_to_fix', ''))
                # Ensure wcag_full is always a list
                wcag_full_raw = orig_metadata.get('wcag_full', enriched_en.get('wcag_full', []))
                if isinstance(wcag_full_raw, str):
                    issue_dict['metadata']['wcag_full'] = [c.strip() for c in wcag_full_raw.split(',') if c.strip()]
                elif isinstance(wcag_full_raw, list):
                    issue_dict['metadata']['wcag_full'] = wcag_full_raw
                else:
                    issue_dict['metadata']['wcag_full'] = []

                violations.append(issue_dict)

            # Process warnings
            for warning in latest_result.warnings or []:
                issue_dict = {
                    'id': warning.id,
                    'description': warning.description,
                    'touchpoint': warning.touchpoint,
                    'impact': warning.impact.value if hasattr(warning.impact, 'value') else warning.impact,
                    'xpath': warning.xpath,
                    'html_snippet': warning.html,
                    'metadata': warning.metadata,
                    'wcag_criteria': warning.wcag_criteria
                }

                # Enrich with IssueCatalog for bilingual descriptions
                with force_locale('en'):
                    enriched_en = IssueCatalog.enrich_issue(issue_dict.copy())
                with force_locale('fr'):
                    enriched_fr = IssueCatalog.enrich_issue(issue_dict.copy())

                # Add bilingual descriptions
                issue_dict['description_en'] = enriched_en.get('description_full', enriched_en.get('description', warning.description or warning.id))
                issue_dict['description_fr'] = enriched_fr.get('description_full', enriched_fr.get('description', warning.description or warning.id))

                # Merge enriched metadata for bilingual support
                if not issue_dict['metadata']:
                    issue_dict['metadata'] = {}
                # Copy bilingual metadata from original issue (already translated when stored in DB)
                orig_metadata = warning.metadata if hasattr(warning, 'metadata') and warning.metadata else {}
                # Title for accordion display
                issue_dict['metadata']['title_en'] = orig_metadata.get('title_en', enriched_en.get('title', enriched_en.get('description_full', '')))
                issue_dict['metadata']['title_fr'] = orig_metadata.get('title_fr', enriched_fr.get('title', enriched_fr.get('description_full', '')))
                # Generic description for grouped issues
                issue_dict['metadata']['what_generic_en'] = orig_metadata.get('what_generic_en', enriched_en.get('what_generic', enriched_en.get('description_full', '')))
                issue_dict['metadata']['what_generic_fr'] = orig_metadata.get('what_generic_fr', enriched_fr.get('what_generic', enriched_fr.get('description_full', '')))
                issue_dict['metadata']['what_generic'] = issue_dict['metadata']['what_generic_en'] if self.language == 'en' else issue_dict['metadata']['what_generic_fr']
                # Specific what description
                issue_dict['metadata']['what_en'] = orig_metadata.get('what_en', enriched_en.get('description_full', enriched_en.get('description', '')))
                issue_dict['metadata']['what_fr'] = orig_metadata.get('what_fr', enriched_fr.get('description_full', enriched_fr.get('description', '')))
                issue_dict['metadata']['why_en'] = orig_metadata.get('why_en', enriched_en.get('why_it_matters', ''))
                issue_dict['metadata']['why_fr'] = orig_metadata.get('why_fr', enriched_fr.get('why_it_matters', ''))
                issue_dict['metadata']['who_en'] = orig_metadata.get('who_en', enriched_en.get('who_it_affects', ''))
                issue_dict['metadata']['who_fr'] = orig_metadata.get('who_fr', enriched_fr.get('who_it_affects', ''))
                issue_dict['metadata']['full_remediation_en'] = orig_metadata.get('full_remediation_en', enriched_en.get('how_to_fix', ''))
                issue_dict['metadata']['full_remediation_fr'] = orig_metadata.get('full_remediation_fr', enriched_fr.get('how_to_fix', ''))
                # Ensure wcag_full is always a list
                wcag_full_raw = orig_metadata.get('wcag_full', enriched_en.get('wcag_full', []))
                if isinstance(wcag_full_raw, str):
                    issue_dict['metadata']['wcag_full'] = [c.strip() for c in wcag_full_raw.split(',') if c.strip()]
                elif isinstance(wcag_full_raw, list):
                    issue_dict['metadata']['wcag_full'] = wcag_full_raw
                else:
                    issue_dict['metadata']['wcag_full'] = []

                warnings.append(issue_dict)

            # Process info items
            for info_item in latest_result.info or []:
                issue_dict = {
                    'id': info_item.id,
                    'description': info_item.description,
                    'touchpoint': info_item.touchpoint,
                    'impact': info_item.impact.value if hasattr(info_item.impact, 'value') else info_item.impact,
                    'xpath': info_item.xpath,
                    'html_snippet': info_item.html,
                    'metadata': info_item.metadata,
                    'wcag_criteria': info_item.wcag_criteria
                }

                # Enrich with IssueCatalog for bilingual descriptions
                with force_locale('en'):
                    enriched_en = IssueCatalog.enrich_issue(issue_dict.copy())
                with force_locale('fr'):
                    enriched_fr = IssueCatalog.enrich_issue(issue_dict.copy())

                # Add bilingual descriptions
                issue_dict['description_en'] = enriched_en.get('description_full', enriched_en.get('description', info_item.description or info_item.id))
                issue_dict['description_fr'] = enriched_fr.get('description_full', enriched_fr.get('description', info_item.description or info_item.id))

                # Merge enriched metadata for bilingual support
                if not issue_dict['metadata']:
                    issue_dict['metadata'] = {}
                # Copy bilingual metadata from original issue (already translated when stored in DB)
                orig_metadata = info_item.metadata if hasattr(info_item, 'metadata') and info_item.metadata else {}
                # Title for accordion display
                issue_dict['metadata']['title_en'] = orig_metadata.get('title_en', enriched_en.get('title', enriched_en.get('description_full', '')))
                issue_dict['metadata']['title_fr'] = orig_metadata.get('title_fr', enriched_fr.get('title', enriched_fr.get('description_full', '')))
                # Generic description for grouped issues
                issue_dict['metadata']['what_generic_en'] = orig_metadata.get('what_generic_en', enriched_en.get('what_generic', enriched_en.get('description_full', '')))
                issue_dict['metadata']['what_generic_fr'] = orig_metadata.get('what_generic_fr', enriched_fr.get('what_generic', enriched_fr.get('description_full', '')))
                issue_dict['metadata']['what_generic'] = issue_dict['metadata']['what_generic_en'] if self.language == 'en' else issue_dict['metadata']['what_generic_fr']
                # Specific what description
                issue_dict['metadata']['what_en'] = orig_metadata.get('what_en', enriched_en.get('description_full', enriched_en.get('description', '')))
                issue_dict['metadata']['what_fr'] = orig_metadata.get('what_fr', enriched_fr.get('description_full', enriched_fr.get('description', '')))
                issue_dict['metadata']['why_en'] = orig_metadata.get('why_en', enriched_en.get('why_it_matters', ''))
                issue_dict['metadata']['why_fr'] = orig_metadata.get('why_fr', enriched_fr.get('why_it_matters', ''))
                issue_dict['metadata']['who_en'] = orig_metadata.get('who_en', enriched_en.get('who_it_affects', ''))
                issue_dict['metadata']['who_fr'] = orig_metadata.get('who_fr', enriched_fr.get('who_it_affects', ''))
                issue_dict['metadata']['full_remediation_en'] = orig_metadata.get('full_remediation_en', enriched_en.get('how_to_fix', ''))
                issue_dict['metadata']['full_remediation_fr'] = orig_metadata.get('full_remediation_fr', enriched_fr.get('how_to_fix', ''))
                # Ensure wcag_full is always a list
                wcag_full_raw = orig_metadata.get('wcag_full', enriched_en.get('wcag_full', []))
                if isinstance(wcag_full_raw, str):
                    issue_dict['metadata']['wcag_full'] = [c.strip() for c in wcag_full_raw.split(',') if c.strip()]
                elif isinstance(wcag_full_raw, list):
                    issue_dict['metadata']['wcag_full'] = wcag_full_raw
                else:
                    issue_dict['metadata']['wcag_full'] = []

                informational.append(issue_dict)

            # Process discovery items (if included)
            if include_discovery:
                for disco_item in latest_result.discovery or []:
                    issue_dict = {
                        'id': disco_item.id,
                        'description': disco_item.description,
                        'touchpoint': disco_item.touchpoint,
                        'impact': disco_item.impact.value if hasattr(disco_item.impact, 'value') else disco_item.impact,
                        'xpath': disco_item.xpath,
                        'html_snippet': disco_item.html,
                        'metadata': disco_item.metadata,
                        'wcag_criteria': disco_item.wcag_criteria
                    }

                    # Enrich with IssueCatalog for bilingual descriptions
                    with force_locale('en'):
                        enriched_en = IssueCatalog.enrich_issue(issue_dict.copy())
                    with force_locale('fr'):
                        enriched_fr = IssueCatalog.enrich_issue(issue_dict.copy())

                    # Add bilingual descriptions
                    issue_dict['description_en'] = enriched_en.get('description_full', enriched_en.get('description', disco_item.description or disco_item.id))
                    issue_dict['description_fr'] = enriched_fr.get('description_full', enriched_fr.get('description', disco_item.description or disco_item.id))

                    # Merge enriched metadata for bilingual support
                    if not issue_dict['metadata']:
                        issue_dict['metadata'] = {}
                    # Copy bilingual metadata from original issue (already translated when stored in DB)
                    orig_metadata = disco_item.metadata if hasattr(disco_item, 'metadata') and disco_item.metadata else {}
                    # Title for accordion display
                    issue_dict['metadata']['title_en'] = orig_metadata.get('title_en', enriched_en.get('title', enriched_en.get('description_full', '')))
                    issue_dict['metadata']['title_fr'] = orig_metadata.get('title_fr', enriched_fr.get('title', enriched_fr.get('description_full', '')))
                    # Generic description for grouped issues
                    issue_dict['metadata']['what_generic_en'] = orig_metadata.get('what_generic_en', enriched_en.get('what_generic', enriched_en.get('description_full', '')))
                    issue_dict['metadata']['what_generic_fr'] = orig_metadata.get('what_generic_fr', enriched_fr.get('what_generic', enriched_fr.get('description_full', '')))
                    issue_dict['metadata']['what_generic'] = issue_dict['metadata']['what_generic_en'] if self.language == 'en' else issue_dict['metadata']['what_generic_fr']
                    # Specific what description
                    issue_dict['metadata']['what_en'] = orig_metadata.get('what_en', enriched_en.get('description_full', enriched_en.get('description', '')))
                    issue_dict['metadata']['what_fr'] = orig_metadata.get('what_fr', enriched_fr.get('description_full', enriched_fr.get('description', '')))
                    issue_dict['metadata']['why_en'] = orig_metadata.get('why_en', enriched_en.get('why_it_matters', ''))
                    issue_dict['metadata']['why_fr'] = orig_metadata.get('why_fr', enriched_fr.get('why_it_matters', ''))
                    issue_dict['metadata']['who_en'] = orig_metadata.get('who_en', enriched_en.get('who_it_affects', ''))
                    issue_dict['metadata']['who_fr'] = orig_metadata.get('who_fr', enriched_fr.get('who_it_affects', ''))
                    issue_dict['metadata']['full_remediation_en'] = orig_metadata.get('full_remediation_en', enriched_en.get('how_to_fix', ''))
                    issue_dict['metadata']['full_remediation_fr'] = orig_metadata.get('full_remediation_fr', enriched_fr.get('how_to_fix', ''))
                    # Ensure wcag_full is always a list
                    wcag_full_raw = orig_metadata.get('wcag_full', enriched_en.get('wcag_full', []))
                    if isinstance(wcag_full_raw, str):
                        issue_dict['metadata']['wcag_full'] = [c.strip() for c in wcag_full_raw.split(',') if c.strip()]
                    elif isinstance(wcag_full_raw, list):
                        issue_dict['metadata']['wcag_full'] = wcag_full_raw
                    else:
                        issue_dict['metadata']['wcag_full'] = []

                    discovery.append(issue_dict)

            # Calculate score
            score = self._calculate_page_score(latest_result)

            # Calculate compliance score
            compliance_score = self._calculate_compliance_score(latest_result)

            # Check for multi-state testing
            state_results: list[dict[str, Any]] = []
            if hasattr(latest_result, 'session_id') and latest_result.session_id:
                # Get all state results for this session
                all_session_results = self.db.get_test_results_by_session(latest_result.session_id)

                # Process each state
                for state_result in all_session_results:
                    state_violations: list[dict[str, Any]] = []
                    state_warnings: list[dict[str, Any]] = []
                    state_informational: list[dict[str, Any]] = []
                    state_discovery: list[dict[str, Any]] = []

                    # Process violations for this state
                    for violation in state_result.violations or []:
                        issue_dict = {
                            'id': violation.id,
                            'description': violation.description,
                            'touchpoint': violation.touchpoint,
                            'impact': violation.impact.value if hasattr(violation.impact, 'value') else violation.impact,
                            'xpath': violation.xpath,
                            'html_snippet': violation.html,
                            'metadata': violation.metadata,
                            'wcag_criteria': violation.wcag_criteria
                        }

                        # Enrich with IssueCatalog for bilingual descriptions
                        with force_locale('en'):
                            enriched_en = IssueCatalog.enrich_issue(issue_dict.copy())
                        with force_locale('fr'):
                            enriched_fr = IssueCatalog.enrich_issue(issue_dict.copy())

                        # Add bilingual descriptions
                        desc_en = enriched_en.get('description_full', enriched_en.get('description', violation.description or violation.id))
                        desc_fr = enriched_fr.get('description_full', enriched_fr.get('description', violation.description or violation.id))
                        # Apply Fluent inline translation if French is same as English (not translated)
                        if desc_fr == desc_en:
                            with force_locale('fr'):
                                desc_fr = ftl_translate_issue(desc_en)
                        issue_dict['description_en'] = desc_en
                        issue_dict['description_fr'] = desc_fr

                        # Merge enriched metadata for bilingual support
                        if not issue_dict['metadata']:
                            issue_dict['metadata'] = {}
                        # enrich_issue returns top-level keys, not nested 'metadata'
                        orig_metadata = violation.metadata if hasattr(violation, 'metadata') and violation.metadata else {}

                        # Get title with Fluent translation fallback
                        title_en = orig_metadata.get('title_en', enriched_en.get('title', enriched_en.get('description_full', '')))
                        title_fr = orig_metadata.get('title_fr', enriched_fr.get('title', enriched_fr.get('description_full', '')))
                        if title_fr == title_en:
                            with force_locale('fr'):
                                title_fr = ftl_translate_issue(title_en)
                        issue_dict['metadata']['title_en'] = title_en
                        issue_dict['metadata']['title_fr'] = title_fr

                        # Get what_generic with Fluent translation fallback
                        what_generic_en = orig_metadata.get('what_generic_en', enriched_en.get('what_generic', enriched_en.get('description_full', '')))
                        what_generic_fr = orig_metadata.get('what_generic_fr', enriched_fr.get('what_generic', enriched_fr.get('description_full', '')))
                        if what_generic_fr == what_generic_en:
                            with force_locale('fr'):
                                what_generic_fr = ftl_translate_issue(what_generic_en)
                        issue_dict['metadata']['what_generic_en'] = what_generic_en
                        issue_dict['metadata']['what_generic_fr'] = what_generic_fr
                        issue_dict['metadata']['what_generic'] = what_generic_en if self.language == 'en' else what_generic_fr

                        # Get what with Fluent translation fallback
                        what_en = orig_metadata.get('what_en', enriched_en.get('description_full', enriched_en.get('description', '')))
                        what_fr = orig_metadata.get('what_fr', enriched_fr.get('description_full', enriched_fr.get('description', '')))
                        if what_fr == what_en:
                            with force_locale('fr'):
                                what_fr = ftl_translate_issue(what_en)
                        issue_dict['metadata']['what_en'] = what_en
                        issue_dict['metadata']['what_fr'] = what_fr
                        issue_dict['metadata']['why_en'] = orig_metadata.get('why_en', enriched_en.get('why_it_matters', ''))
                        issue_dict['metadata']['why_fr'] = orig_metadata.get('why_fr', enriched_fr.get('why_it_matters', ''))
                        issue_dict['metadata']['who_en'] = orig_metadata.get('who_en', enriched_en.get('who_it_affects', ''))
                        issue_dict['metadata']['who_fr'] = orig_metadata.get('who_fr', enriched_fr.get('who_it_affects', ''))
                        issue_dict['metadata']['full_remediation_en'] = orig_metadata.get('full_remediation_en', enriched_en.get('how_to_fix', ''))
                        issue_dict['metadata']['full_remediation_fr'] = orig_metadata.get('full_remediation_fr', enriched_fr.get('how_to_fix', ''))
                        # Ensure wcag_full is always a list
                        wcag_full_raw = orig_metadata.get('wcag_full', enriched_en.get('wcag_full', []))
                        if isinstance(wcag_full_raw, str):
                            issue_dict['metadata']['wcag_full'] = [c.strip() for c in wcag_full_raw.split(',') if c.strip()]
                        elif isinstance(wcag_full_raw, list):
                            issue_dict['metadata']['wcag_full'] = wcag_full_raw
                        else:
                            issue_dict['metadata']['wcag_full'] = []

                        state_violations.append(issue_dict)

                    # Process warnings for this state
                    for warning in state_result.warnings or []:
                        issue_dict = {
                            'id': warning.id,
                            'description': warning.description,
                            'touchpoint': warning.touchpoint,
                            'impact': warning.impact.value if hasattr(warning.impact, 'value') else warning.impact,
                            'xpath': warning.xpath,
                            'html_snippet': warning.html,
                            'metadata': warning.metadata,
                            'wcag_criteria': warning.wcag_criteria
                        }

                        # Enrich with IssueCatalog for bilingual descriptions
                        with force_locale('en'):
                            enriched_en = IssueCatalog.enrich_issue(issue_dict.copy())
                        with force_locale('fr'):
                            enriched_fr = IssueCatalog.enrich_issue(issue_dict.copy())

                        # Add bilingual descriptions
                        desc_en = enriched_en.get('description_full', enriched_en.get('description', warning.description or warning.id))
                        desc_fr = enriched_fr.get('description_full', enriched_fr.get('description', warning.description or warning.id))
                        if desc_fr == desc_en:
                            with force_locale('fr'):
                                desc_fr = ftl_translate_issue(desc_en)
                        issue_dict['description_en'] = desc_en
                        issue_dict['description_fr'] = desc_fr

                        # Merge enriched metadata for bilingual support
                        if not issue_dict['metadata']:
                            issue_dict['metadata'] = {}
                        # enrich_issue returns top-level keys, not nested 'metadata'
                        orig_metadata = warning.metadata if hasattr(warning, 'metadata') and warning.metadata else {}

                        # Get title with Fluent translation fallback
                        title_en = orig_metadata.get('title_en', enriched_en.get('title', enriched_en.get('description_full', '')))
                        title_fr = orig_metadata.get('title_fr', enriched_fr.get('title', enriched_fr.get('description_full', '')))
                        if title_fr == title_en:
                            with force_locale('fr'):
                                title_fr = ftl_translate_issue(title_en)
                        issue_dict['metadata']['title_en'] = title_en
                        issue_dict['metadata']['title_fr'] = title_fr

                        # Get what_generic with Fluent translation fallback
                        what_generic_en = orig_metadata.get('what_generic_en', enriched_en.get('what_generic', enriched_en.get('description_full', '')))
                        what_generic_fr = orig_metadata.get('what_generic_fr', enriched_fr.get('what_generic', enriched_fr.get('description_full', '')))
                        if what_generic_fr == what_generic_en:
                            with force_locale('fr'):
                                what_generic_fr = ftl_translate_issue(what_generic_en)
                        issue_dict['metadata']['what_generic_en'] = what_generic_en
                        issue_dict['metadata']['what_generic_fr'] = what_generic_fr
                        issue_dict['metadata']['what_generic'] = what_generic_en if self.language == 'en' else what_generic_fr

                        # Get what with Fluent translation fallback
                        what_en = orig_metadata.get('what_en', enriched_en.get('description_full', enriched_en.get('description', '')))
                        what_fr = orig_metadata.get('what_fr', enriched_fr.get('description_full', enriched_fr.get('description', '')))
                        if what_fr == what_en:
                            with force_locale('fr'):
                                what_fr = ftl_translate_issue(what_en)
                        issue_dict['metadata']['what_en'] = what_en
                        issue_dict['metadata']['what_fr'] = what_fr
                        issue_dict['metadata']['why_en'] = orig_metadata.get('why_en', enriched_en.get('why_it_matters', ''))
                        issue_dict['metadata']['why_fr'] = orig_metadata.get('why_fr', enriched_fr.get('why_it_matters', ''))
                        issue_dict['metadata']['who_en'] = orig_metadata.get('who_en', enriched_en.get('who_it_affects', ''))
                        issue_dict['metadata']['who_fr'] = orig_metadata.get('who_fr', enriched_fr.get('who_it_affects', ''))
                        issue_dict['metadata']['full_remediation_en'] = orig_metadata.get('full_remediation_en', enriched_en.get('how_to_fix', ''))
                        issue_dict['metadata']['full_remediation_fr'] = orig_metadata.get('full_remediation_fr', enriched_fr.get('how_to_fix', ''))
                        # Ensure wcag_full is always a list
                        wcag_full_raw = orig_metadata.get('wcag_full', enriched_en.get('wcag_full', []))
                        if isinstance(wcag_full_raw, str):
                            issue_dict['metadata']['wcag_full'] = [c.strip() for c in wcag_full_raw.split(',') if c.strip()]
                        elif isinstance(wcag_full_raw, list):
                            issue_dict['metadata']['wcag_full'] = wcag_full_raw
                        else:
                            issue_dict['metadata']['wcag_full'] = []

                        state_warnings.append(issue_dict)

                    # Process informational items for this state
                    for info_item in state_result.info or []:
                        issue_dict = {
                            'id': info_item.id,
                            'description': info_item.description,
                            'touchpoint': info_item.touchpoint,
                            'impact': info_item.impact.value if hasattr(info_item.impact, 'value') else info_item.impact,
                            'xpath': info_item.xpath,
                            'html_snippet': info_item.html,
                            'metadata': info_item.metadata,
                            'wcag_criteria': info_item.wcag_criteria
                        }

                        with force_locale('en'):
                            enriched_en = IssueCatalog.enrich_issue(issue_dict.copy())
                        with force_locale('fr'):
                            enriched_fr = IssueCatalog.enrich_issue(issue_dict.copy())

                        desc_en = enriched_en.get('description_full', enriched_en.get('description', info_item.description or info_item.id))
                        desc_fr = enriched_fr.get('description_full', enriched_fr.get('description', info_item.description or info_item.id))
                        if desc_fr == desc_en:
                            with force_locale('fr'):
                                desc_fr = ftl_translate_issue(desc_en)
                        issue_dict['description_en'] = desc_en
                        issue_dict['description_fr'] = desc_fr

                        if not issue_dict['metadata']:
                            issue_dict['metadata'] = {}
                        orig_metadata = info_item.metadata if hasattr(info_item, 'metadata') and info_item.metadata else {}

                        what_generic_en = orig_metadata.get('what_generic_en', enriched_en.get('what_generic', enriched_en.get('description_full', '')))
                        what_generic_fr = orig_metadata.get('what_generic_fr', enriched_fr.get('what_generic', enriched_fr.get('description_full', '')))
                        if what_generic_fr == what_generic_en:
                            with force_locale('fr'):
                                what_generic_fr = ftl_translate_issue(what_generic_en)
                        issue_dict['metadata']['what_generic_en'] = what_generic_en
                        issue_dict['metadata']['what_generic_fr'] = what_generic_fr
                        issue_dict['metadata']['what_generic'] = what_generic_en if self.language == 'en' else what_generic_fr

                        state_informational.append(issue_dict)

                    # Process discovery items for this state
                    for disco_item in state_result.discovery or []:
                        issue_dict = {
                            'id': disco_item.id,
                            'description': disco_item.description,
                            'touchpoint': disco_item.touchpoint,
                            'impact': disco_item.impact.value if hasattr(disco_item.impact, 'value') else disco_item.impact,
                            'xpath': disco_item.xpath,
                            'html_snippet': disco_item.html,
                            'metadata': disco_item.metadata,
                            'wcag_criteria': disco_item.wcag_criteria
                        }

                        with force_locale('en'):
                            enriched_en = IssueCatalog.enrich_issue(issue_dict.copy())
                        with force_locale('fr'):
                            enriched_fr = IssueCatalog.enrich_issue(issue_dict.copy())

                        desc_en = enriched_en.get('description_full', enriched_en.get('description', disco_item.description or disco_item.id))
                        desc_fr = enriched_fr.get('description_full', enriched_fr.get('description', disco_item.description or disco_item.id))
                        if desc_fr == desc_en:
                            with force_locale('fr'):
                                desc_fr = ftl_translate_issue(desc_en)
                        issue_dict['description_en'] = desc_en
                        issue_dict['description_fr'] = desc_fr

                        if not issue_dict['metadata']:
                            issue_dict['metadata'] = {}
                        orig_metadata = disco_item.metadata if hasattr(disco_item, 'metadata') and disco_item.metadata else {}

                        what_generic_en = orig_metadata.get('what_generic_en', enriched_en.get('what_generic', enriched_en.get('description_full', '')))
                        what_generic_fr = orig_metadata.get('what_generic_fr', enriched_fr.get('what_generic', enriched_fr.get('description_full', '')))
                        if what_generic_fr == what_generic_en:
                            with force_locale('fr'):
                                what_generic_fr = ftl_translate_issue(what_generic_en)
                        issue_dict['metadata']['what_generic_en'] = what_generic_en
                        issue_dict['metadata']['what_generic_fr'] = what_generic_fr
                        issue_dict['metadata']['what_generic'] = what_generic_en if self.language == 'en' else what_generic_fr

                        state_discovery.append(issue_dict)

                    # Get state description - handle both object and dict
                    state_description = f"State {state_result.state_sequence if hasattr(state_result, 'state_sequence') else 0}"
                    if hasattr(state_result, 'page_state') and state_result.page_state:
                        page_state_dict: dict[str, Any] = state_result.page_state
                        state_description = str(page_state_dict.get('description', state_description))

                    # Create bilingual state descriptions
                    translations = self._get_translations()
                    state_description_en = state_description
                    state_description_fr = state_description
                    
                    # Translate common state description patterns
                    if 'Initial page state' in state_description or state_description.startswith('State 0'):
                        state_description_en = translations['en'].get('initial_page_state', state_description)
                        state_description_fr = translations['fr'].get('initial_page_state', state_description)
                    elif 'After executing script:' in state_description:
                        # Extract script name and translate the prefix
                        script_name = state_description.replace('After executing script:', '').strip()
                        state_description_en = f"{translations['en'].get('after_executing_script', 'After executing script:')} {script_name}"
                        state_description_fr = f"{translations['fr'].get('after_executing_script', 'Après exécution du script :')} {script_name}"

                    # Get script info and auth user from state_result metadata
                    script_info = None
                    auth_user_info = None
                    if hasattr(state_result, 'metadata') and state_result.metadata:
                        script_info = state_result.metadata.get('script_executed')
                        auth_user_info = state_result.metadata.get('authenticated_user')
                    
                    state_data: dict[str, Any] = {
                        'sequence': state_result.state_sequence if hasattr(state_result, 'state_sequence') else 0,
                        'description': state_description,
                        'description_en': state_description_en,
                        'description_fr': state_description_fr,
                        'violations': state_violations,
                        'warnings': state_warnings,
                        'informational': state_informational,
                        'discovery': state_discovery,
                        'issues': {
                            'errors': len(state_violations),
                            'warnings': len(state_warnings),
                            'info': len(state_informational),
                            'discovery': len(state_discovery)
                        },
                        'script_info': script_info,
                        'auth_user': auth_user_info
                    }
                    state_results.append(state_data)

                # Sort by sequence
                state_results.sort(key=lambda s: int(s['sequence']))

            pages_data.append({
                'id': page_id,
                'title': page.title or 'Untitled Page',
                'url': page.url,
                'test_date': latest_result.test_date.strftime('%Y-%m-%d %H:%M:%S') if latest_result.test_date else None,
                'score': score,
                'compliance_score': compliance_score,
                'screenshot_path': page.screenshot_path,
                'violations': violations,
                'warnings': warnings,
                'informational': informational,
                'discovery': discovery,
                'issues': {
                    'errors': len(violations),
                    'warnings': len(warnings),
                    'info': len(informational),
                    'discovery': len(discovery)
                },
                'states': state_results  # Add multi-state results
            })

        return pages_data

    def _collect_summary_stats(self, page_ids: list[str], progress_callback: Callable[..., object] | None = None) -> dict[str, Any]:
        """Pass 1: Stream pages collecting only aggregate statistics.

        Memory-efficient replacement for _collect_pages_data() + _generate_summary_stats().
        Does not load full test result item arrays — uses summary counts from DB
        and streams individual items only for per-issue/per-touchpoint counting.

        Returns:
            dict with aggregate stats and lightweight page_info list for index/manifest.
        """
        from collections import defaultdict

        stats: dict[str, Any] = {
            'total_pages': len(page_ids),
            'total_errors': 0,
            'total_warnings': 0,
            'total_info': 0,
            'total_discovery': 0,
            'pages_with_errors': 0,
            'pages_with_warnings': 0,
            'pages_with_info': 0,
            'pages_with_discovery': 0,
            'scores': [],
            'issue_counts': {},       # code -> {count, pages: set, impact, touchpoint}
            'touchpoint_counts': defaultdict(int),
            'wcag_counts': defaultdict(int),
            'page_info': [],          # lightweight per-page info for index/manifest
        }

        for i, page_id in enumerate(page_ids):
            if progress_callback:
                progress_callback(i, len(page_ids),
                                  ftl('reports-collecting-summary-current-total', current=i + 1, total=len(page_ids)))

            # Collect lightweight page metadata
            page = self.db.get_page(page_id)
            page_title = page.title or 'Untitled Page' if page else 'Untitled Page'
            page_url = page.url if page else ''
            page_screenshot = page.screenshot_path if page else None

            result_summary = self.db.get_latest_test_result_summary(page_id)
            if not result_summary:
                # Include page in page_info even without results
                stats['page_info'].append({
                    'id': page_id,
                    'title': page_title,
                    'url': page_url,
                    'score': 0,
                    'screenshot_path': page_screenshot,
                    'test_date': None,
                    'issues': {'errors': 0, 'warnings': 0, 'info': 0, 'discovery': 0},
                })
                continue

            v = result_summary.get('violation_count', 0)
            w = result_summary.get('warning_count', 0)
            info_c = result_summary.get('info_count', 0)
            disc = result_summary.get('discovery_count', 0)

            stats['total_errors'] += v
            stats['total_warnings'] += w
            stats['total_info'] += info_c
            stats['total_discovery'] += disc
            if v > 0:
                stats['pages_with_errors'] += 1
            if w > 0:
                stats['pages_with_warnings'] += 1
            if info_c > 0:
                stats['pages_with_info'] += 1
            if disc > 0:
                stats['pages_with_discovery'] += 1

            score = result_summary.get('score')
            if score is not None:
                stats['scores'].append(score)

            # Lightweight page info for index and manifest
            stats['page_info'].append({
                'id': page_id,
                'title': page_title,
                'url': page_url,
                'score': score if score is not None else 0,
                'screenshot_path': page_screenshot,
                'test_date': result_summary.get('test_date'),
                'issues': {
                    'errors': v,
                    'warnings': w,
                    'info': info_c,
                    'discovery': disc,
                },
            })

            # Stream items for per-issue and per-touchpoint counting
            for item in self.db.yield_test_result_items(result_summary['id']):
                code = item.get('issue_id', 'unknown')
                tp = item.get('touchpoint', 'unknown')
                impact = item.get('impact', 'medium')
                wcag = item.get('wcag_criteria', [])

                if code not in stats['issue_counts']:
                    stats['issue_counts'][code] = {
                        'count': 0,
                        'pages': set(),
                        'impact': impact,
                        'touchpoint': tp,
                    }
                stats['issue_counts'][code]['count'] += 1
                stats['issue_counts'][code]['pages'].add(page_id)
                stats['touchpoint_counts'][tp] += 1
                if isinstance(wcag, list):
                    wcag_str_list: list[str] = _narrow_str_list(wcag)
                    for criterion_str in wcag_str_list:
                        stats['wcag_counts'][criterion_str] += 1

        return stats

    def _build_summary_from_stats(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Build a summary dict (same shape as _generate_summary_stats) from streaming stats.

        Converts the output of _collect_summary_stats into the summary format
        expected by index.html template, manifest, and other consumers.
        """
        scores = stats['scores']
        average_score = sum(scores) / len(scores) if scores else 0.0

        total_errors = stats['total_errors']
        total_warnings = stats['total_warnings']

        # Determine compliance level
        if total_errors == 0:
            compliance_level = 'pass'
        elif average_score >= 70:
            compliance_level = 'partial'
        else:
            compliance_level = 'fail'

        # Build top issues from issue_counts
        top_issues = sorted(
            stats['issue_counts'].values(),
            key=lambda x: x['count'],
            reverse=True
        )[:10]
        # Convert page sets to counts for JSON serialization
        for issue in top_issues:
            issue['pages'] = len(issue['pages'])

        # Build by_touchpoint from touchpoint_counts
        by_touchpoint = {}
        for tp, count in stats['touchpoint_counts'].items():
            by_touchpoint[tp] = {'errors': count, 'warnings': 0, 'info': 0}

        # by_wcag placeholder (same as _group_by_wcag)
        by_wcag: dict[str, Any] = {}

        return {
            'total_errors': total_errors,
            'total_warnings': total_warnings,
            'total_info': stats['total_info'],
            'total_discovery': stats['total_discovery'],
            'pages_with_errors': stats['pages_with_errors'],
            'pages_with_warnings': stats['pages_with_warnings'],
            'pages_with_info': stats['pages_with_info'],
            'pages_with_discovery': stats['pages_with_discovery'],
            'average_score': average_score,
            'compliance_level': compliance_level,
            'top_issues': top_issues,
            'by_touchpoint': by_touchpoint,
            'by_wcag': by_wcag,
            'recommendations': self._generate_recommendations(total_errors, total_warnings, average_score),
        }

    def _generate_page_detail_htmls_streaming(
        self, report_dir: Path, page_info: list[dict[str, Any]],
        include_discovery: bool, project_name: str, wcag_level: str,
        touchpoints_tested: list[str] | None, progress_callback: Callable[..., object] | None = None
    ) -> None:
        """Generate individual page detail HTML files one at a time (Pass 2).

        Loads the full test result for each page, processes it via _collect_pages_data
        for that single page, generates the HTML, then discards the data before
        moving to the next page. This keeps memory usage proportional to one page
        at a time instead of all pages simultaneously.
        """
        template = self.template_env.get_template('static_report/page_detail.html')

        # Read CSS and JS files to inline them (same as _generate_page_detail_htmls)
        static_dir = Path(__file__).parent.parent / 'web' / 'static'
        import urllib.request

        bootstrap_css = ''
        bootstrap_css_path = static_dir / 'css' / 'bootstrap.min.css'
        if bootstrap_css_path.exists():
            bootstrap_css = bootstrap_css_path.read_text(encoding='utf-8')
        else:
            try:
                with urllib.request.urlopen('https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css') as response:
                    bootstrap_css = response.read().decode('utf-8')
            except Exception as e:
                logger.warning(f"Could not fetch Bootstrap CSS from CDN: {e}")

        bootstrap_icons_css = ''
        bootstrap_icons_path = static_dir / 'css' / 'bootstrap-icons.css'
        if bootstrap_icons_path.exists():
            bootstrap_icons_css = bootstrap_icons_path.read_text(encoding='utf-8')
        else:
            try:
                with urllib.request.urlopen('https://cdn.jsdelivr.net/npm/bootstrap-icons@1.8.1/font/bootstrap-icons.css') as response:
                    bootstrap_icons_css = response.read().decode('utf-8')
            except Exception as e:
                logger.warning(f"Could not fetch Bootstrap Icons CSS from CDN: {e}")

        # Embed woff2 font as base64 data URI
        import re as _re
        import base64 as _b64
        woff2_path = static_dir / 'css' / 'fonts' / 'bootstrap-icons.woff2'
        if woff2_path.exists() and bootstrap_icons_css:
            woff2_b64 = _b64.b64encode(woff2_path.read_bytes()).decode('ascii')
            data_uri = f'url("data:font/woff2;base64,{woff2_b64}") format("woff2")'
            bootstrap_icons_css = _re.sub(
                r'src:\s*url\([^)]+\)(\s*format\([^)]+\))?(,\s*url\([^)]+\)(\s*format\([^)]+\))?)*;',
                f'src: {data_uri};',
                bootstrap_icons_css
            )

        bootstrap_js = ''
        bootstrap_js_path = static_dir / 'js' / 'bootstrap.bundle.min.js'
        if bootstrap_js_path.exists():
            bootstrap_js = bootstrap_js_path.read_text(encoding='utf-8')
        else:
            try:
                with urllib.request.urlopen('https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js') as response:
                    bootstrap_js = response.read().decode('utf-8')
            except Exception as e:
                logger.warning(f"Could not fetch Bootstrap JS from CDN: {e}")

        filters_js = ''
        filters_js_path = static_dir / 'js' / 'filters.js'
        if filters_js_path.exists():
            filters_js = filters_js_path.read_text(encoding='utf-8')

        # Get translations
        all_translations = self._get_translations()
        translations_en = all_translations['en']
        translations_fr = all_translations['fr']
        t = all_translations[self.language]

        total_pages = len(page_info)

        for index, pi in enumerate(page_info, start=1):
            if progress_callback:
                with force_locale(self.language):
                    progress_callback(index - 1, total_pages,
                                      ftl('reports-generating-page-current-of-total', current=index, total=total_pages))

            page_id = pi['id']

            # Load full test result data for this ONE page, then discard after writing
            single_page_data = self._collect_pages_data([page_id], include_discovery)
            if not single_page_data:
                continue
            page = single_page_data[0]

            # Collect all unique touchpoints for filters
            all_touchpoints: set[str] = set()
            for issue_list in [page['violations'], page['warnings'],
                               page['informational'], page['discovery']]:
                for issue in issue_list:
                    tp_val: str = str(issue.get('touchpoint', 'general'))
                    all_touchpoints.add(tp_val)

            # Create navigation context using lightweight page_info list
            navigation: dict[str, Any] = {
                'previous': page_info[index - 2] if index > 1 else None,
                'next': page_info[index] if index < total_pages else None,
                'pages': [{'number': i + 1, 'title': p['title'], 'current': i + 1 == index}
                          for i, p in enumerate(page_info)]
            }

            if navigation['previous']:
                prev_nav: dict[str, Any] = dict(navigation['previous'])
                prev_nav['number'] = index - 1
                navigation['previous'] = prev_nav
            if navigation['next']:
                next_nav: dict[str, Any] = dict(navigation['next'])
                next_nav['number'] = index + 1
                navigation['next'] = next_nav

            with force_locale(self.language):
                html = template.render(
                    page=page,
                    violations=page['violations'],
                    warnings=page['warnings'],
                    informational=page['informational'],
                    discovery=page['discovery'],
                    errors_count=page['issues']['errors'],
                    warnings_count=page['issues']['warnings'],
                    info_count=page['issues']['info'],
                    discovery_count=page['issues']['discovery'],
                    compliance_score=page.get('compliance_score'),
                    all_touchpoints=sorted(all_touchpoints),
                    navigation=navigation,
                    project_name=project_name,
                    wcag_level=wcag_level,
                    touchpoints_tested=touchpoints_tested or [],
                    generation_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    asset_path='../assets/',
                    index_path='../',
                    # Language support
                    language=self.language,
                    translations_en=translations_en,
                    translations_fr=translations_fr,
                    translations_json=json.dumps({'en': translations_en, 'fr': translations_fr}),
                    t=t,
                    # Inlined CSS and JS
                    bootstrap_css=bootstrap_css,
                    bootstrap_icons_css=bootstrap_icons_css,
                    bootstrap_js=bootstrap_js,
                    filters_js=filters_js,
                    inline_mode=True,
                    show_error_codes=config.SHOW_ERROR_CODES
                )

            filename = f'page_{str(index).zfill(3)}.html'
            (report_dir / 'pages' / filename).write_text(html, encoding='utf-8')

            # Explicitly discard the full page data to free memory
            del single_page_data
            del page

    def _calculate_page_score(self, test_result: Any) -> float:
        """Calculate accessibility score for a page using result_processor's scoring logic"""
        from auto_a11y.testing.result_processor import ResultProcessor

        processor = ResultProcessor()
        score_data = processor.calculate_score(test_result)

        return float(score_data['score'])

    def _calculate_compliance_score(self, test_result: Any) -> dict[str, Any]:
        """Calculate compliance score (percentage of tests with zero violations)"""
        # Get unique test codes that have violations/warnings
        failed_test_codes: set[str] = set()

        # Extract test codes from violations
        violations_raw: list[Any] = test_result.violations or []
        for v in violations_raw:
            test_code: str = str(v.id) if hasattr(v, 'id') else ''
            if test_code:
                # Extract base code (before underscore if present)
                base_code: str = test_code.split('_')[0] if '_' in test_code else test_code
                failed_test_codes.add(base_code)

        # Extract test codes from warnings
        warnings_raw: list[Any] = test_result.warnings or []
        for w in warnings_raw:
            test_code_w: str = str(w.id) if hasattr(w, 'id') else ''
            if test_code_w:
                base_code_w: str = test_code_w.split('_')[0] if '_' in test_code_w else test_code_w
                failed_test_codes.add(base_code_w)

        # Count total tests run from metadata
        total_tests: int = int(test_result.metadata.get('test_count', 0)) if hasattr(test_result, 'metadata') and test_result.metadata else 0
        failed_tests = len(failed_test_codes)
        passed_tests = max(0, total_tests - failed_tests)

        return {
            'score': (passed_tests / total_tests * 100) if total_tests > 0 else 0,
            'passed_tests': passed_tests,
            'failed_tests': failed_tests,
            'total_tests': total_tests
        }

    def _generate_summary_stats(self, pages_data: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate summary statistics across all pages"""
        total_errors = sum(p['issues']['errors'] for p in pages_data)
        total_warnings = sum(p['issues']['warnings'] for p in pages_data)
        total_info = sum(p['issues']['info'] for p in pages_data)
        total_discovery = sum(p['issues']['discovery'] for p in pages_data)

        pages_with_errors = sum(1 for p in pages_data if p['issues']['errors'] > 0)
        pages_with_warnings = sum(1 for p in pages_data if p['issues']['warnings'] > 0)
        pages_with_info = sum(1 for p in pages_data if p['issues']['info'] > 0)
        pages_with_discovery = sum(1 for p in pages_data if p['issues']['discovery'] > 0)

        scores = [p['score'] for p in pages_data if p['score'] > 0]
        average_score = sum(scores) / len(scores) if scores else 0.0

        # Determine compliance level
        if total_errors == 0:
            compliance_level = 'pass'
        elif average_score >= 70:
            compliance_level = 'partial'
        else:
            compliance_level = 'fail'

        # Calculate top issues
        top_issues = self._calculate_top_issues(pages_data)

        # Group by touchpoint
        by_touchpoint = self._group_by_touchpoint(pages_data)

        # Group by WCAG
        by_wcag = self._group_by_wcag(pages_data)

        return {
            'total_errors': total_errors,
            'total_warnings': total_warnings,
            'total_info': total_info,
            'total_discovery': total_discovery,
            'pages_with_errors': pages_with_errors,
            'pages_with_warnings': pages_with_warnings,
            'pages_with_info': pages_with_info,
            'pages_with_discovery': pages_with_discovery,
            'average_score': average_score,
            'compliance_level': compliance_level,
            'top_issues': top_issues,
            'by_touchpoint': by_touchpoint,
            'by_wcag': by_wcag,
            'recommendations': self._generate_recommendations(total_errors, total_warnings, average_score)
        }

    def _calculate_top_issues(self, pages_data: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
        """Calculate top issues across all pages"""
        issue_counts: dict[str, dict[str, Any]] = {}

        for page in pages_data:
            for issue_list in [page['violations'], page['warnings'], page['informational']]:
                for issue in issue_list:
                    code: str = str(issue['id'])
                    if code not in issue_counts:
                        meta_dict: dict[str, Any] = issue.get('metadata', {})
                        issue_counts[code] = {
                            'title': str(meta_dict.get('title', code)),
                            'count': 0,
                            'pages': set(),
                            'severity': 'error' if issue in page['violations'] else 'warning',
                            'impact': str(issue.get('impact', 'medium'))
                        }
                    issue_counts[code]['count'] += 1
                    issue_counts[code]['pages'].add(page['id'])

        # Sort by count and convert to list
        top_issues: list[dict[str, Any]] = sorted(
            issue_counts.values(),
            key=lambda x: int(x['count']),
            reverse=True
        )[:limit]

        # Convert sets to counts
        for top_issue in top_issues:
            top_issue['pages'] = len(top_issue['pages'])

        return top_issues

    def _group_by_touchpoint(self, pages_data: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
        """Group issues by touchpoint"""
        touchpoint_stats: dict[str, dict[str, int]] = {}

        for page in pages_data:
            issue_severity_pairs: list[tuple[list[dict[str, Any]], str]] = [
                (page['violations'], 'errors'),
                (page['warnings'], 'warnings'),
                (page['informational'], 'info')
            ]
            for issue_list, severity in issue_severity_pairs:
                for issue in issue_list:
                    touchpoint: str = str(issue.get('touchpoint', 'general'))
                    if touchpoint not in touchpoint_stats:
                        touchpoint_stats[touchpoint] = {'errors': 0, 'warnings': 0, 'info': 0}
                    touchpoint_stats[touchpoint][severity] += 1

        return touchpoint_stats

    def _group_by_wcag(self, pages_data: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Group issues by WCAG principle"""
        # This would require WCAG mapping - placeholder for now
        return {}

    def _generate_recommendations(self, errors: int, warnings: int, score: float) -> list[dict[str, str]]:
        """Generate priority recommendations"""
        recommendations: list[dict[str, str]] = []

        if errors > 0:
            recommendations.append({
                'title': 'Fix Critical Errors',
                'description': f'Address all {errors} error-level issues first as these represent serious accessibility barriers.'
            })

        if warnings > 0:
            recommendations.append({
                'title': 'Review Warnings',
                'description': f'Review and fix {warnings} warning-level issues to improve accessibility.'
            })

        if score < 70:
            recommendations.append({
                'title': 'Improve Overall Score',
                'description': f'Current average score is {score:.1f}%. Aim for at least 80% to ensure good accessibility.'
            })

        return recommendations

    def _create_directory_structure(self, report_dir: Path) -> None:
        """Create directory structure for static report"""
        (report_dir / 'pages').mkdir(exist_ok=True)
        (report_dir / 'assets' / 'css').mkdir(parents=True, exist_ok=True)
        (report_dir / 'assets' / 'js').mkdir(parents=True, exist_ok=True)
        (report_dir / 'assets' / 'images' / 'screenshots').mkdir(parents=True, exist_ok=True)
        (report_dir / 'assets' / 'fonts').mkdir(parents=True, exist_ok=True)
        (report_dir / 'data').mkdir(exist_ok=True)

    def _copy_assets(self, report_dir: Path, include_screenshots: bool, pages_data: list[dict[str, Any]] | None) -> None:
        """Copy CSS, JS, fonts, and images to report directory"""
        static_dir = Path(__file__).parent.parent / 'web' / 'static'

        # Copy CSS files
        css_files = ['bootstrap.min.css', 'bootstrap-icons.css', 'style.css', 'custom.css']
        for css_file in css_files:
            src = static_dir / 'css' / css_file
            if src.exists():
                shutil.copy(src, report_dir / 'assets' / 'css' / css_file)

        # Copy tokens.css from public static directory
        tokens_src = static_dir / 'public' / 'css' / 'tokens.css'
        if tokens_src.exists():
            shutil.copy(tokens_src, report_dir / 'assets' / 'css' / 'tokens.css')

        # Copy JS files
        js_files = ['bootstrap.bundle.min.js', 'filters.js', 'navigation.js', 'search.js']
        for js_file in js_files:
            src = static_dir / 'js' / js_file
            if src.exists():
                shutil.copy(src, report_dir / 'assets' / 'js' / js_file)

        # Copy fonts (Bootstrap Icons)
        fonts_dir = static_dir / 'fonts'
        if fonts_dir.exists():
            for font_file in fonts_dir.glob('*'):
                shutil.copy(font_file, report_dir / 'assets' / 'fonts' / font_file.name)

        # Copy screenshots
        if include_screenshots and pages_data:
            screenshots_dir = Path(__file__).parent.parent.parent / 'screenshots'
            for page in pages_data:
                if page.get('screenshot_path'):
                    screenshot_file = screenshots_dir / page['screenshot_path']
                    if screenshot_file.exists():
                        shutil.copy(
                            screenshot_file,
                            report_dir / 'assets' / 'images' / 'screenshots' / screenshot_file.name
                        )

    def _generate_index_html(self, report_dir: Path, pages_data: list[dict[str, Any]],
                            summary: dict[str, Any], project_name: str, website_url: str | None,
                            wcag_level: str, touchpoints_tested: list[str] | None) -> None:
        """Generate index.html file"""
        template = self.template_env.get_template('static_report/index.html')

        # Get translations for both languages
        all_translations = self._get_translations()
        translations_en = all_translations['en']
        translations_fr = all_translations['fr']
        t = all_translations[self.language]  # Current language translations

        with force_locale(self.language):
            html = template.render(
                pages=pages_data,
                summary=summary,
                project_name=project_name,
                website_url=website_url,
                wcag_level=wcag_level,
                touchpoints_tested=touchpoints_tested or [],
                generation_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                current_page='index',
                asset_path='assets/',
                index_path='',
                # Language support
                language=self.language,
                translations_en=translations_en,
                translations_fr=translations_fr,
                translations_json=json.dumps({'en': translations_en, 'fr': translations_fr}),
                t=t
            )

        (report_dir / 'index.html').write_text(html, encoding='utf-8')

    def _generate_summary_html(self, report_dir: Path, pages_data: list[dict[str, Any]],
                               summary: dict[str, Any], project_name: str, website_url: str | None,
                               wcag_level: str, touchpoints_tested: list[str] | None,
                               ai_tests_enabled: bool) -> None:
        """Generate summary.html file"""
        template = self.template_env.get_template('static_report/summary.html')

        with force_locale(self.language):
            html = template.render(
                pages=pages_data,
                summary=summary,
                project_name=project_name,
                website_url=website_url,
                wcag_level=wcag_level,
                touchpoints_tested=touchpoints_tested or [],
                ai_tests_enabled=ai_tests_enabled,
                generation_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                current_page='summary',
                asset_path='assets/',
                index_path='',
                report_version='1.0.0'
            )

        (report_dir / 'summary.html').write_text(html, encoding='utf-8')

    def _generate_page_detail_htmls(self, report_dir: Path, pages_data: list[dict[str, Any]],
                                    project_name: str, wcag_level: str,
                                    touchpoints_tested: list[str] | None,
                                    progress_callback: Callable[..., object] | None = None) -> None:
        """Generate individual page detail HTML files with inlined CSS/JS"""
        template = self.template_env.get_template('static_report/page_detail.html')

        # Read CSS and JS files to inline them
        static_dir = Path(__file__).parent.parent / 'web' / 'static'
        import urllib.request

        # Read Bootstrap CSS - use CDN if local file doesn't exist
        bootstrap_css = ''
        bootstrap_css_path = static_dir / 'css' / 'bootstrap.min.css'
        if bootstrap_css_path.exists():
            bootstrap_css = bootstrap_css_path.read_text(encoding='utf-8')
        else:
            # Fetch from CDN
            try:
                with urllib.request.urlopen('https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css') as response:
                    bootstrap_css = response.read().decode('utf-8')
            except Exception as e:
                print(f"Warning: Could not fetch Bootstrap CSS from CDN: {e}")

        # Read Bootstrap Icons CSS - use CDN if local file doesn't exist
        bootstrap_icons_css = ''
        bootstrap_icons_path = static_dir / 'css' / 'bootstrap-icons.css'
        if bootstrap_icons_path.exists():
            bootstrap_icons_css = bootstrap_icons_path.read_text(encoding='utf-8')
        else:
            # Fetch from CDN
            try:
                with urllib.request.urlopen('https://cdn.jsdelivr.net/npm/bootstrap-icons@1.8.1/font/bootstrap-icons.css') as response:
                    bootstrap_icons_css = response.read().decode('utf-8')
            except Exception as e:
                print(f"Warning: Could not fetch Bootstrap Icons CSS from CDN: {e}")

        # Embed woff2 font as base64 data URI so icons render in static HTML
        import re as _re
        import base64 as _b64
        woff2_path = static_dir / 'css' / 'fonts' / 'bootstrap-icons.woff2'
        if woff2_path.exists() and bootstrap_icons_css:
            woff2_b64 = _b64.b64encode(woff2_path.read_bytes()).decode('ascii')
            data_uri = f'url("data:font/woff2;base64,{woff2_b64}") format("woff2")'
            bootstrap_icons_css = _re.sub(
                r'src:\s*url\([^)]+\)(\s*format\([^)]+\))?(,\s*url\([^)]+\)(\s*format\([^)]+\))?)*;',
                f'src: {data_uri};',
                bootstrap_icons_css
            )

        # Read Bootstrap JS - use CDN if local file doesn't exist
        bootstrap_js = ''
        bootstrap_js_path = static_dir / 'js' / 'bootstrap.bundle.min.js'
        if bootstrap_js_path.exists():
            bootstrap_js = bootstrap_js_path.read_text(encoding='utf-8')
        else:
            # Fetch from CDN
            try:
                with urllib.request.urlopen('https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js') as response:
                    bootstrap_js = response.read().decode('utf-8')
            except Exception as e:
                print(f"Warning: Could not fetch Bootstrap JS from CDN: {e}")

        # Read filters.js
        filters_js = ''
        filters_js_path = static_dir / 'js' / 'filters.js'
        if filters_js_path.exists():
            filters_js = filters_js_path.read_text(encoding='utf-8')

        # Get translations for both languages
        all_translations = self._get_translations()
        translations_en = all_translations['en']
        translations_fr = all_translations['fr']
        t = all_translations[self.language]  # Current language translations

        for index, page in enumerate(pages_data, start=1):
            if progress_callback:
                with force_locale(self.language):
                    progress_callback(index - 1, len(pages_data), ftl('reports-generating-page-current-of-total', current=index, total=len(pages_data)))
            # Collect all unique touchpoints for filters
            all_touchpoints: set[str] = set()
            for issue_list in [page['violations'], page['warnings'], page['informational'], page['discovery']]:
                for issue in issue_list:
                    tp_val: str = str(issue.get('touchpoint', 'general'))
                    all_touchpoints.add(tp_val)

            # Create navigation context
            navigation: dict[str, Any] = {
                'previous': pages_data[index - 2] if index > 1 else None,
                'next': pages_data[index] if index < len(pages_data) else None,
                'pages': [{'number': i + 1, 'title': p['title'], 'current': i + 1 == index}
                         for i, p in enumerate(pages_data)]
            }

            if navigation['previous']:
                prev_nav2: dict[str, Any] = navigation['previous']
                prev_nav2['number'] = index - 1
            if navigation['next']:
                next_nav2: dict[str, Any] = navigation['next']
                next_nav2['number'] = index + 1

            with force_locale(self.language):
                html = template.render(
                    page=page,
                    violations=page['violations'],
                    warnings=page['warnings'],
                    informational=page['informational'],
                    discovery=page['discovery'],
                    errors_count=page['issues']['errors'],
                    warnings_count=page['issues']['warnings'],
                    info_count=page['issues']['info'],
                    discovery_count=page['issues']['discovery'],
                    compliance_score=page.get('compliance_score'),
                    all_touchpoints=sorted(all_touchpoints),
                    navigation=navigation,
                    project_name=project_name,
                    wcag_level=wcag_level,
                    touchpoints_tested=touchpoints_tested or [],
                    generation_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    asset_path='../assets/',
                    index_path='../',
                    # Language support
                    language=self.language,
                    translations_en=translations_en,
                    translations_fr=translations_fr,
                    translations_json=json.dumps({'en': translations_en, 'fr': translations_fr}),
                    t=t,
                    # Inlined CSS and JS
                    bootstrap_css=bootstrap_css,
                    bootstrap_icons_css=bootstrap_icons_css,
                    bootstrap_js=bootstrap_js,
                    filters_js=filters_js,
                    inline_mode=True,
                    show_error_codes=config.SHOW_ERROR_CODES
                )

            filename = f'page_{str(index).zfill(3)}.html'
            (report_dir / 'pages' / filename).write_text(html, encoding='utf-8')

    def _create_manifest(self, report_dir: Path, pages_data: list[dict[str, Any]],
                        summary: dict[str, Any], project_name: str, website_url: str | None,
                        wcag_level: str, touchpoints_tested: list[str] | None, ai_tests_enabled: bool) -> None:
        """Create manifest.json with report metadata"""
        manifest = {
            'report_id': f'report_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
            'generated_at': datetime.now().isoformat(),
            'generator_version': '1.0.0',
            'project': {
                'name': project_name,
                'url': website_url
            },
            'test_config': {
                'wcag_level': wcag_level,
                'touchpoints_tested': touchpoints_tested or [],
                'ai_tests_enabled': ai_tests_enabled
            },
            'statistics': {
                'total_pages': len(pages_data),
                'pages_tested': len(pages_data),
                'total_issues': summary['total_errors'] + summary['total_warnings'] + summary['total_info'],
                'errors': summary['total_errors'],
                'warnings': summary['total_warnings'],
                'info': summary['total_info'],
                'discovery': summary['total_discovery'],
                'average_score': summary['average_score']
            },
            'pages': [
                {
                    'id': p['id'],
                    'title': p['title'],
                    'url': p['url'],
                    'file': f'pages/page_{str(i + 1).zfill(3)}.html',
                    'score': p['score'],
                    'issues': p['issues']
                }
                for i, p in enumerate(pages_data)
            ]
        }

        (report_dir / 'data' / 'manifest.json').write_text(
            json.dumps(manifest, indent=2),
            encoding='utf-8'
        )

    def _package_as_zip(self, source_dir: Path, timestamp: str) -> Path:
        """Package report directory as ZIP file"""
        all_translations = self._get_translations()
        t = all_translations[self.language]
        report_name = t.get('accessibility_report', 'Accessibility_Report').replace(' ', '_')
        zip_filename = f'{report_name}_{timestamp}.zip'
        zip_path = self.output_dir / zip_filename

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in source_dir.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(source_dir)
                    zipf.write(file_path, arcname)

        return zip_path

    def generate_project_deduplicated_report(
        self,
        project_id: str | None = None,
        website_id: str | None = None,
        progress_callback: Callable[..., object] | None = None
    ) -> Path:
        """
        Generate deduplicated offline HTML report for an entire project or specific website.
        Groups issues by common components and allows filtering by component.

        Args:
            project_id: ID of the project to generate report for (optional if website_id provided)
            website_id: ID of specific website to generate report for (optional)

        Returns:
            Path to generated ZIP file
        """
        # Determine scope
        if website_id:
            # Generate for specific website
            website = self.db.get_website(website_id)
            if not website:
                raise ValueError(f"Website {website_id} not found")

            project = None
            if website.project_id:
                project = self.db.get_project(website.project_id)

            websites: list[Any] = [website]
            # Use project name if available, otherwise use website name
            project_name = project.name if project else website.name
        elif project_id:
            # Generate for entire project
            project = self.db.get_project(project_id)
            if not project:
                raise ValueError(f"Project {project_id} not found")

            websites = self.db.get_websites(project_id)
            project_name = project.name
        else:
            # Generate for all projects
            project = None
            projects = self.db.get_projects()
            websites = []
            for p in projects:
                if p.id:
                    websites.extend(self.db.get_websites(p.id))
            project_name = "All_Projects"

        # Streaming data collection — processes one page at a time to avoid OOM
        common_components, deduplicated_issues, all_page_scores, all_compliance_scores, total_pages, page_metadata = \
            self._collect_dedup_data_streaming(websites, progress_callback)

        # Group issues by component (operates on the dedup index, not raw page data)
        issues_by_component = self._group_issues_by_component(deduplicated_issues, common_components)

        # Calculate averages
        overall_accessibility_score = sum(all_page_scores) / len(all_page_scores) if all_page_scores else 0
        overall_compliance_score = sum(all_compliance_scores) / len(all_compliance_scores) if all_compliance_scores else 0

        # Create temporary directory for report
        import tempfile
        import shutil

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        generation_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        temp_dir = Path(tempfile.mkdtemp(prefix='dedup_report_'))

        try:
            # Create directory structure
            (temp_dir / 'components').mkdir(exist_ok=True)
            (temp_dir / 'pages').mkdir(exist_ok=True)
            (temp_dir / 'assets' / 'css').mkdir(parents=True, exist_ok=True)
            (temp_dir / 'assets' / 'js').mkdir(parents=True, exist_ok=True)

            # Copy assets
            self._copy_dedup_assets(temp_dir)

            # Group unassigned issues by page (streaming: re-loads only needed pages)
            pages_with_unassigned = self._group_unassigned_by_page_streaming(
                issues_by_component.get('unassigned', []), common_components, page_metadata
            )

            # Extract common issues from unassigned issues
            # These are violations/warnings appearing on multiple pages but not inside components
            common_issues = self._extract_common_issues(
                issues_by_component.get('unassigned', []), total_pages
            )

            # Calculate statistics based on actual deduplicated issues that will be displayed
            # Count issues in components
            total_violations = 0
            total_warnings = 0
            total_info = 0
            total_discovery = 0

            for component_signature, component_issues in issues_by_component.items():
                if component_signature != 'unassigned':
                    for issue in component_issues:
                        if issue['type'] == 'violation':
                            total_violations += 1
                        elif issue['type'] == 'warning':
                            total_warnings += 1
                        elif issue['type'] == 'info':
                            total_info += 1
                        elif issue['type'] == 'discovery':
                            total_discovery += 1

            # Count issues in unassigned (non-component) pages
            for page_data in pages_with_unassigned:
                total_violations += len(page_data['issues']['violations'])
                total_warnings += len(page_data['issues']['warnings'])
                total_info += len(page_data['issues']['info'])
                total_discovery += len(page_data['issues'].get('discovery', []))

            resolved_project_name: str = project_name or "Unknown_Project"

            # Generate index page
            self._generate_dedup_index(
                temp_dir, project, resolved_project_name, common_components,
                issues_by_component, pages_with_unassigned, common_issues,
                total_violations, total_warnings, total_info, total_discovery, total_pages,
                overall_accessibility_score, overall_compliance_score
            )

            # Generate component detail pages
            self._generate_component_pages(
                temp_dir, common_components, issues_by_component
            )

            # Generate page detail pages for unassigned issues
            self._generate_page_detail_pages(
                temp_dir, pages_with_unassigned, project, resolved_project_name, generation_date
            )

            # Package as ZIP
            safe_project_name = resolved_project_name.replace(' ', '_')
            all_translations = self._get_translations()
            t = all_translations[self.language]
            deduplicated_word = t.get('deduplicated', 'Deduplicated').replace(' ', '_')
            zip_filename = f'{safe_project_name}_{deduplicated_word}_{timestamp}.zip'
            zip_path = self.output_dir / zip_filename

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in temp_dir.rglob('*'):
                    if file_path.is_file():
                        arcname = file_path.relative_to(temp_dir)
                        zipf.write(file_path, arcname)

            return zip_path
        finally:
            # Clean up temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _extract_common_components(self, data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """
        Extract common components (forms, navs, asides, sections, headers) from discovery issues.

        Args:
            data: Project report data

        Returns:
            Dictionary mapping signature -> component info with xpaths per page
        """
        common_components: dict[str, dict[str, Any]] = {}

        # Iterate through all websites and pages
        for website_data in data.get('websites', []):
            _website = website_data.get('website', {})

            for page_result in website_data.get('pages', []):
                page = page_result.get('page', {})
                page_url = page.url if hasattr(page, 'url') else page.get('url', '')

                test_result = page_result.get('test_result')
                if not test_result:
                    continue

                # Get discovery items
                discovery_items: list[Any] = test_result.discovery if hasattr(test_result, 'discovery') else []

                for d in discovery_items:
                    d_dict: dict[str, Any]
                    if hasattr(d, 'to_dict'):
                        d_dict = d.to_dict()
                    else:
                        d_dict = _narrow_dict(d)

                    issue_id: str = str(d_dict.get('id', ''))
                    metadata: dict[str, Any] = d_dict.get('metadata', {})

                    # Extract signature and xpath for different component types
                    signature: str | None = None
                    component_type: str | None = None
                    label: str | None = None

                    if issue_id in ['DiscoFormOnPage', 'forms_DiscoFormOnPage']:
                        signature = str(metadata.get('formSignature')) if metadata.get('formSignature') else None
                        component_type = 'Form'
                        field_count: int = int(metadata.get('fieldCount', 0))
                        label = f"({field_count})"  # Just the count - template adds translated text
                    elif issue_id in ['DiscoNavFound', 'landmarks_DiscoNavFound']:
                        signature = str(metadata.get('navSignature')) if metadata.get('navSignature') else None
                        component_type = 'Navigation'
                        label = str(metadata.get('navLabel', 'Navigation'))
                    elif issue_id in ['DiscoAsideFound', 'landmarks_DiscoAsideFound']:
                        signature = str(metadata.get('asideSignature')) if metadata.get('asideSignature') else None
                        component_type = 'Aside'
                        label = str(metadata.get('asideLabel', 'Aside'))
                    elif issue_id in ['DiscoSectionFound', 'landmarks_DiscoSectionFound']:
                        signature = str(metadata.get('sectionSignature')) if metadata.get('sectionSignature') else None
                        component_type = 'Section'
                        label = str(metadata.get('sectionLabel', 'Section'))
                    elif issue_id in ['DiscoHeaderFound', 'landmarks_DiscoHeaderFound']:
                        signature = str(metadata.get('headerSignature')) if metadata.get('headerSignature') else None
                        component_type = 'Header'
                        label = str(metadata.get('headerLabel', 'Header'))
                    elif issue_id in ['DiscoFooterFound', 'landmarks_DiscoFooterFound']:
                        signature = str(metadata.get('footerSignature')) if metadata.get('footerSignature') else None
                        component_type = 'Footer'
                        label = str(metadata.get('footerLabel', 'Footer'))
                    elif issue_id in ['DiscoSearchFound', 'landmarks_DiscoSearchFound']:
                        signature = str(metadata.get('searchSignature')) if metadata.get('searchSignature') else None
                        component_type = 'Search'
                        label = str(metadata.get('searchLabel', 'Search'))

                    if signature and signature != 'unknown':
                        # Get component language (default to 'en' if not specified)
                        component_lang: str = str(metadata.get('pageLang', 'en'))

                        # Get user context - components seen by different users are separate
                        # This handles cases where nav content varies by authentication state
                        auth_user_raw: dict[str, Any] = metadata.get('authenticated_user', {})
                        user_context: str = str(auth_user_raw.get('display_name', 'Guest')) if auth_user_raw else 'Guest'

                        # Create composite key: signature + user context
                        # This ensures "guest nav" and "Marie Tremblay nav" are separate components
                        component_key = f"{signature}|{user_context}"

                        if component_key not in common_components:
                            common_components[component_key] = {
                                'type': component_type,
                                'label': label,
                                'signature': signature,  # Keep original signature for display
                                'xpaths_by_page': {},
                                'pages': set(),
                                'lang': component_lang,  # Store the component's language
                                'user_context': user_context  # Store user context for display
                            }

                        xpath: str = str(d_dict.get('xpath', '') or metadata.get('xpath', ''))
                        common_components[component_key]['xpaths_by_page'][page_url] = xpath
                        common_components[component_key]['pages'].add(page_url)

        # Filter to only include components that appear on 2+ pages
        filtered_components: dict[str, dict[str, Any]] = {
            sig: comp_data
            for sig, comp_data in common_components.items()
            if len(comp_data['pages']) >= 2
        }

        # Fallback: if exact signatures didn't match across pages (e.g., old XPath-based
        # signatures that differ per page), merge components of the same type+label+user_context
        # that each appear on only 1 page. This handles legacy data where signatures included
        # the XPath, making them unique per page even for identical structural components.
        if not filtered_components:
            single_page_components: dict[str, dict[str, Any]] = {
                sig: comp_data
                for sig, comp_data in common_components.items()
                if len(comp_data['pages']) == 1
            }

            if single_page_components:
                # Group single-page components by (type, label, user_context)
                merge_groups: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
                for sig, comp_data in single_page_components.items():
                    merge_key: tuple[str, str] = (str(comp_data['type']), str(comp_data.get('user_context', 'Guest')))
                    if merge_key not in merge_groups:
                        merge_groups[merge_key] = []
                    merge_groups[merge_key].append((sig, comp_data))

                # Merge groups with 2+ components into single combined components
                for merge_key, group in merge_groups.items():
                    if len(group) >= 2:
                        # Use the first component as the base, merge pages and xpaths
                        _base_sig, base_data = group[0]
                        merged: dict[str, Any] = {
                            'type': base_data['type'],
                            'label': base_data['label'],
                            'signature': base_data['signature'],
                            'xpaths_by_page': dict(base_data['xpaths_by_page']),
                            'pages': set(base_data['pages']),
                            'lang': base_data.get('lang', 'en'),
                            'user_context': base_data.get('user_context', 'Guest')
                        }
                        for _, comp_data in group[1:]:
                            merged['xpaths_by_page'].update(comp_data['xpaths_by_page'])
                            merged['pages'].update(comp_data['pages'])

                        merged_key_str = f"merged_{merge_key[0]}|{merge_key[1]}"
                        filtered_components[merged_key_str] = merged

                if filtered_components:
                    logger.info(
                        "Signature-based matching found 0 common components; " +
                        f"fallback merge by type found {len(filtered_components)}"
                    )

        return filtered_components

    def _deduplicate_issues_by_component(
        self,
        data: dict[str, Any],
        common_components: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Deduplicate issues by grouping them by component signature and rule ID.

        Args:
            data: Project report data
            common_components: Dictionary of common components

        Returns:
            List of deduplicated issue dictionaries
        """
        from auto_a11y.reporting.issue_catalog import IssueCatalog

        # Track unique issues: (rule_id, component_signature_or_xpath) -> issue data
        unique_issues: dict[tuple[str, ...], dict[str, Any]] = {}

        # Iterate through all websites and their pages
        for website_data in data.get('websites', []):
            for page_result in website_data.get('pages', []):
                page = page_result.get('page', {})
                page_url = page.url if hasattr(page, 'url') else page.get('url', '')

                test_result = page_result.get('test_result')
                if not test_result:
                    continue

                # Process all issue types except discovery
                for issue_type, issue_list_attr in [('violation', 'violations'),
                                                      ('warning', 'warnings'),
                                                      ('info', 'info')]:
                    issues = getattr(test_result, issue_list_attr, []) if hasattr(test_result, issue_list_attr) else []

                    for issue in issues:
                        issue_dict: dict[str, Any]
                        if hasattr(issue, 'to_dict'):
                            issue_dict = issue.to_dict()
                        else:
                            issue_dict = _narrow_dict(issue)

                        rule_id: str = str(issue_dict.get('id', ''))
                        issue_xpath: str = str(issue_dict.get('xpath', ''))

                        # Get authenticated user info
                        metadata: dict[str, Any] = issue_dict.get('metadata', {})
                        auth_user: dict[str, Any] = metadata.get('authenticated_user', {})
                        user_name: str = str(auth_user.get('display_name', '')) if auth_user else 'Guest'
                        user_roles: list[str] = list(auth_user.get('roles', [])) if auth_user else []

                        # Find which common component contains this issue
                        # Must match both xpath containment AND user context
                        component_signature: str | None = None
                        component_type: str | None = None
                        component_label: str | None = None

                        # Get issue's user context for matching
                        issue_user_context: str = str(auth_user.get('display_name', 'Guest')) if auth_user else 'Guest'

                        for comp_sig, comp_data in common_components.items():
                            comp_xpath_val: str = str(comp_data['xpaths_by_page'].get(page_url, ''))
                            comp_user_ctx: str = str(comp_data.get('user_context', 'Guest'))
                            # Match both xpath AND user context
                            if comp_xpath_val and self._xpath_is_within(issue_xpath, comp_xpath_val) and comp_user_ctx == issue_user_context:
                                component_signature = comp_sig
                                component_type = str(comp_data['type'])
                                component_label = str(comp_data['label'])
                                break

                        # Create deduplication key
                        dedup_key: tuple[str, ...]
                        if component_signature:
                            dedup_key = (rule_id, component_signature)
                        else:
                            dedup_key = (rule_id, issue_xpath)

                        # Initialize or update issue data
                        if dedup_key not in unique_issues:
                            # Get original metadata which already has translations stored from DB
                            orig_metadata: dict[str, Any] = issue_dict.get('metadata', {})

                            # Ensure wcag_full is a list (split if it's a string)
                            wcag_full_raw_val: object = orig_metadata.get('wcag_full', [])
                            wcag_full: list[str]
                            if isinstance(wcag_full_raw_val, str):
                                wcag_full = [c.strip() for c in wcag_full_raw_val.split(',') if c.strip()]
                            elif isinstance(wcag_full_raw_val, list):
                                wcag_full = _narrow_str_list(wcag_full_raw_val)
                            else:
                                wcag_full = []

                            # DB stores single-language substituted text - need to get both languages
                            # The metadata.what/why/who/full_remediation have placeholders substituted
                            # but are in a single language. We need to re-enrich for both languages.

                            # Build metadata with element for placeholder substitution
                            enrich_metadata: dict[str, Any] = dict(orig_metadata)
                            enrich_metadata['element'] = issue_dict.get('element', '')

                            # Re-enrich to get both language versions with substitution
                            issue_for_enrich: dict[str, Any] = dict(issue_dict)
                            issue_for_enrich['metadata'] = enrich_metadata
                            
                            with force_locale('en'):
                                enriched_en = IssueCatalog.enrich_issue(issue_for_enrich)
                            with force_locale('fr'):
                                enriched_fr = IssueCatalog.enrich_issue(issue_for_enrich)

                            wcag_list: list[str] = list(issue_dict.get('wcag', []))
                            unique_issues[dedup_key] = {
                                'type': issue_type,
                                'rule_id': rule_id,
                                'description': issue_dict.get('description', ''),
                                'impact': issue_dict.get('impact', 'moderate'),
                                'wcag': ', '.join(wcag_list),
                                'wcag_full': wcag_full,
                                'touchpoint': issue_dict.get('touchpoint', ''),
                                'element': issue_dict.get('element', ''),
                                'xpath': issue_xpath,
                                'component_signature': component_signature,
                                'component_type': component_type,
                                'component_label': component_label,
                                # Use enriched data which has placeholder substitution
                                'description_en': enriched_en.get('what') or enriched_en.get('description_full') or issue_dict.get('description', ''),
                                'description_fr': enriched_fr.get('what') or enriched_fr.get('description_full') or issue_dict.get('description', ''),
                                'why_en': enriched_en.get('why') or enriched_en.get('why_it_matters', ''),
                                'why_fr': enriched_fr.get('why') or enriched_fr.get('why_it_matters', ''),
                                'who_en': enriched_en.get('who') or enriched_en.get('who_it_affects', ''),
                                'who_fr': enriched_fr.get('who') or enriched_fr.get('who_it_affects', ''),
                                'full_remediation_en': enriched_en.get('full_remediation') or enriched_en.get('how_to_fix', ''),
                                'full_remediation_fr': enriched_fr.get('full_remediation') or enriched_fr.get('how_to_fix', ''),
                                'pages': set(),
                                'test_users': set(),
                                'user_roles': set()
                            }

                        # Add page and user info
                        unique_issues[dedup_key]['pages'].add(page_url)
                        if user_name:
                            unique_issues[dedup_key]['test_users'].add(user_name)
                        if user_roles:
                            for role in user_roles:
                                unique_issues[dedup_key]['user_roles'].add(str(role))
                        if not auth_user:
                            unique_issues[dedup_key]['user_roles'].add('no login')

        # Convert to list and add page count
        result: list[dict[str, Any]] = []
        for issue_data in unique_issues.values():
            issue_data['pages'] = sorted(list(issue_data['pages']))
            issue_data['page_count'] = len(issue_data['pages'])
            issue_data['test_users'] = sorted(list(issue_data['test_users']))
            issue_data['user_roles'] = sorted(list(issue_data['user_roles']))
            result.append(issue_data)

        # Sort by impact (high -> medium -> low) then by type then by rule_id
        impact_order: dict[str, int] = {'critical': 0, 'high': 1, 'serious': 1, 'moderate': 2, 'medium': 2, 'minor': 3, 'low': 3}
        type_order: dict[str, int] = {'violation': 0, 'warning': 1, 'info': 2}

        result.sort(key=lambda x: (
            type_order.get(str(x['type']), 99),
            impact_order.get(str(x['impact']).lower(), 99),
            str(x['rule_id'])
        ))

        return result

    def _collect_dedup_data_streaming(self, websites: Any, progress_callback: Callable[..., object] | None = None) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[float], list[float], int, dict[str, dict[str, Any]]]:
        """Stream through all pages once, building component and dedup indexes incrementally.

        Memory: O(unique_components + unique_issues + page_metadata), NOT O(all_pages * all_violations).
        Each page's full TestResult is loaded, processed, then discarded.

        Returns:
            (common_components, unique_issues_list, all_page_scores, all_compliance_scores,
             total_pages, page_metadata)
        where page_metadata is {page_url: {page_id, title, page_score, website_id}} for
        targeted re-reads in _group_unassigned_by_page_streaming.
        """
        from auto_a11y.reporting.issue_catalog import IssueCatalog

        # These dicts grow proportional to UNIQUE signatures, not total issues
        all_components: dict[str, dict[str, Any]] = {}    # component_key -> component data
        unique_issues: dict[tuple[str, ...], dict[str, Any]] = {}     # (rule_id, component_sig_or_xpath) -> issue data

        all_page_scores: list[float] = []
        all_compliance_scores: list[float] = []
        total_pages = 0
        page_count = 0
        page_metadata: dict[str, dict[str, Any]] = {}     # page_url -> lightweight metadata for targeted re-reads

        # Count total pages for progress (lightweight - page objects are small)
        total_page_count = sum(len(self.db.get_pages(w.id)) for w in websites)

        for website in websites:
            for page in self.db.yield_pages(website.id):
                page_count += 1
                if progress_callback:
                    progress_callback(page_count, max(total_page_count, 1),
                                      f'Processing page {page_count} of {total_page_count}...')

                if not page.id:
                    continue
                test_result = self.db.get_latest_test_result(page.id)
                if not test_result:
                    continue
                total_pages += 1
                page_url = page.url if hasattr(page, 'url') else ''

                # Store lightweight page metadata for targeted re-reads later
                page_metadata[page_url] = {
                    'page_id': page.id,
                    'title': page.title if hasattr(page, 'title') else None,
                    'website_id': website.id,
                }

                # --- Component extraction (mirrors _extract_common_components) ---
                discovery_items_raw: list[Any] = test_result.discovery if hasattr(test_result, 'discovery') else []
                for d in discovery_items_raw:
                    d_dict: dict[str, Any] = d.to_dict() if hasattr(d, 'to_dict') else _narrow_dict(d)
                    issue_id_str: str = str(d_dict.get('id', ''))
                    metadata_d: dict[str, Any] = d_dict.get('metadata', {})

                    signature_val: str | None = None
                    component_type_val: str | None = None
                    label_val: str | None = None

                    if issue_id_str in ['DiscoFormOnPage', 'forms_DiscoFormOnPage']:
                        signature_val = str(metadata_d.get('formSignature')) if metadata_d.get('formSignature') else None
                        component_type_val = 'Form'
                        label_val = f"({metadata_d.get('fieldCount', 0)})"
                    elif issue_id_str in ['DiscoNavFound', 'landmarks_DiscoNavFound']:
                        signature_val = str(metadata_d.get('navSignature')) if metadata_d.get('navSignature') else None
                        component_type_val = 'Navigation'
                        label_val = str(metadata_d.get('navLabel', 'Navigation'))
                    elif issue_id_str in ['DiscoAsideFound', 'landmarks_DiscoAsideFound']:
                        signature_val = str(metadata_d.get('asideSignature')) if metadata_d.get('asideSignature') else None
                        component_type_val = 'Aside'
                        label_val = str(metadata_d.get('asideLabel', 'Aside'))
                    elif issue_id_str in ['DiscoSectionFound', 'landmarks_DiscoSectionFound']:
                        signature_val = str(metadata_d.get('sectionSignature')) if metadata_d.get('sectionSignature') else None
                        component_type_val = 'Section'
                        label_val = str(metadata_d.get('sectionLabel', 'Section'))
                    elif issue_id_str in ['DiscoHeaderFound', 'landmarks_DiscoHeaderFound']:
                        signature_val = str(metadata_d.get('headerSignature')) if metadata_d.get('headerSignature') else None
                        component_type_val = 'Header'
                        label_val = str(metadata_d.get('headerLabel', 'Header'))
                    elif issue_id_str in ['DiscoFooterFound', 'landmarks_DiscoFooterFound']:
                        signature_val = str(metadata_d.get('footerSignature')) if metadata_d.get('footerSignature') else None
                        component_type_val = 'Footer'
                        label_val = str(metadata_d.get('footerLabel', 'Footer'))
                    elif issue_id_str in ['DiscoSearchFound', 'landmarks_DiscoSearchFound']:
                        signature_val = str(metadata_d.get('searchSignature')) if metadata_d.get('searchSignature') else None
                        component_type_val = 'Search'
                        label_val = str(metadata_d.get('searchLabel', 'Search'))

                    if signature_val and signature_val != 'unknown':
                        component_lang_str: str = str(metadata_d.get('pageLang', 'en'))
                        auth_user_d: dict[str, Any] = metadata_d.get('authenticated_user', {})
                        user_context_str: str = str(auth_user_d.get('display_name', 'Guest')) if auth_user_d else 'Guest'
                        component_key = f"{signature_val}|{user_context_str}"

                        if component_key not in all_components:
                            all_components[component_key] = {
                                'type': component_type_val,
                                'label': label_val,
                                'signature': signature_val,
                                'xpaths_by_page': {},
                                'pages': set(),
                                'lang': component_lang_str,
                                'user_context': user_context_str
                            }

                        xpath_str: str = str(d_dict.get('xpath', '') or metadata_d.get('xpath', ''))
                        all_components[component_key]['xpaths_by_page'][page_url] = xpath_str
                        all_components[component_key]['pages'].add(page_url)

                # --- Issue deduplication (mirrors _deduplicate_issues_by_component) ---
                for issue_type, issue_list_attr in [('violation', 'violations'),
                                                      ('warning', 'warnings'),
                                                      ('info', 'info')]:
                    issues = getattr(test_result, issue_list_attr, []) if hasattr(test_result, issue_list_attr) else []

                    for issue in issues:
                        issue_dict_s: dict[str, Any] = issue.to_dict() if hasattr(issue, 'to_dict') else _narrow_dict(issue)
                        rule_id_s: str = str(issue_dict_s.get('id', ''))
                        issue_xpath_s: str = str(issue_dict_s.get('xpath', ''))
                        metadata_s: dict[str, Any] = issue_dict_s.get('metadata', {})
                        auth_user_s: dict[str, Any] = metadata_s.get('authenticated_user', {})
                        user_name_s: str = str(auth_user_s.get('display_name', '')) if auth_user_s else 'Guest'
                        user_roles_s: list[str] = list(auth_user_s.get('roles', [])) if auth_user_s else []
                        issue_user_context_s: str = str(auth_user_s.get('display_name', 'Guest')) if auth_user_s else 'Guest'

                        # Match against ALL components (not just common ones yet).
                        # After the pass we filter to 2+ page components and reclassify.
                        component_signature_s: str | None = None
                        component_type_matched: str | None = None
                        component_label_matched: str | None = None

                        for comp_key, comp_data in all_components.items():
                            comp_xpath_s: str = str(comp_data['xpaths_by_page'].get(page_url, ''))
                            comp_user_ctx_s: str = str(comp_data.get('user_context', 'Guest'))
                            if comp_xpath_s and self._xpath_is_within(issue_xpath_s, comp_xpath_s) and comp_user_ctx_s == issue_user_context_s:
                                component_signature_s = comp_key
                                component_type_matched = str(comp_data['type'])
                                component_label_matched = str(comp_data['label'])
                                break

                        # Dedup key — for component issues, key on (rule_id, component).
                        # For unassigned issues, key on rule_id ONLY (not xpath).
                        # Using xpath as key would create one entry per element per page,
                        # defeating deduplication and causing O(total_issues) memory.
                        dedup_key_s: tuple[str, ...]
                        if component_signature_s:
                            dedup_key_s = (rule_id_s, component_signature_s)
                        else:
                            dedup_key_s = (rule_id_s,)

                        if dedup_key_s not in unique_issues:
                            orig_metadata_s: dict[str, Any] = issue_dict_s.get('metadata', {})
                            wcag_full_raw_s: object = orig_metadata_s.get('wcag_full', [])
                            wcag_full_s: list[str]
                            if isinstance(wcag_full_raw_s, str):
                                wcag_full_s = [c.strip() for c in wcag_full_raw_s.split(',') if c.strip()]
                            elif isinstance(wcag_full_raw_s, list):
                                wcag_full_s = _narrow_str_list(wcag_full_raw_s)
                            else:
                                wcag_full_s = []

                            enrich_metadata_s: dict[str, Any] = dict(orig_metadata_s)
                            enrich_metadata_s['element'] = issue_dict_s.get('element', '')
                            issue_for_enrich_s: dict[str, Any] = dict(issue_dict_s)
                            issue_for_enrich_s['metadata'] = enrich_metadata_s

                            with force_locale('en'):
                                enriched_en_s = IssueCatalog.enrich_issue(issue_for_enrich_s)
                            with force_locale('fr'):
                                enriched_fr_s = IssueCatalog.enrich_issue(issue_for_enrich_s)

                            wcag_list_s: list[str] = list(issue_dict_s.get('wcag', []))
                            unique_issues[dedup_key_s] = {
                                'type': issue_type,
                                'rule_id': rule_id_s,
                                'description': issue_dict_s.get('description', ''),
                                'impact': issue_dict_s.get('impact', 'moderate'),
                                'wcag': ', '.join(wcag_list_s),
                                'wcag_full': wcag_full_s,
                                'touchpoint': issue_dict_s.get('touchpoint', ''),
                                'element': issue_dict_s.get('element', ''),
                                'xpath': issue_xpath_s,
                                'component_signature': component_signature_s,
                                'component_type': component_type_matched,
                                'component_label': component_label_matched,
                                'description_en': enriched_en_s.get('what') or enriched_en_s.get('description_full') or issue_dict_s.get('description', ''),
                                'description_fr': enriched_fr_s.get('what') or enriched_fr_s.get('description_full') or issue_dict_s.get('description', ''),
                                'why_en': enriched_en_s.get('why') or enriched_en_s.get('why_it_matters', ''),
                                'why_fr': enriched_fr_s.get('why') or enriched_fr_s.get('why_it_matters', ''),
                                'who_en': enriched_en_s.get('who') or enriched_en_s.get('who_it_affects', ''),
                                'who_fr': enriched_fr_s.get('who') or enriched_fr_s.get('who_it_affects', ''),
                                'full_remediation_en': enriched_en_s.get('full_remediation') or enriched_en_s.get('how_to_fix', ''),
                                'full_remediation_fr': enriched_fr_s.get('full_remediation') or enriched_fr_s.get('how_to_fix', ''),
                                'pages': set(),
                                'test_users': set(),
                                'user_roles': set()
                            }

                        unique_issues[dedup_key_s]['pages'].add(page_url)
                        if user_name_s:
                            unique_issues[dedup_key_s]['test_users'].add(user_name_s)
                        if user_roles_s:
                            for role_s in user_roles_s:
                                unique_issues[dedup_key_s]['user_roles'].add(str(role_s))
                        if not auth_user_s:
                            unique_issues[dedup_key_s]['user_roles'].add('no login')

                # --- Score calculation (mirrors lines 2365-2383) ---
                page_score = self._calculate_page_score(test_result)
                all_page_scores.append(page_score)
                page_metadata[page_url]['page_score'] = page_score

                violations_list_s: list[Any] = test_result.violations if hasattr(test_result, 'violations') else []
                warnings_list_s: list[Any] = test_result.warnings if hasattr(test_result, 'warnings') else []
                info_list_s: list[Any] = test_result.info if hasattr(test_result, 'info') else []
                total_tests = len(violations_list_s) + len(warnings_list_s) + len(info_list_s)
                passed_tests = total_tests - len(violations_list_s)
                if total_tests > 0:
                    all_compliance_scores.append((passed_tests / total_tests) * 100)

                # DISCARD the test_result - this is the key memory savings
                del test_result

        # --- Post-processing: filter components and reclassify issues ---

        # Filter to components appearing on 2+ pages (same as _extract_common_components)
        common_components: dict[str, dict[str, Any]] = {
            sig: comp_data for sig, comp_data in all_components.items()
            if len(comp_data['pages']) >= 2
        }

        # Fallback merge logic (same as _extract_common_components lines 2591-2626)
        if not common_components:
            single_page_components: dict[str, dict[str, Any]] = {
                sig: comp_data for sig, comp_data in all_components.items()
                if len(comp_data['pages']) == 1
            }
            if single_page_components:
                merge_groups_s: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
                for sig, comp_data in single_page_components.items():
                    merge_key_s: tuple[str, str] = (str(comp_data['type']), str(comp_data.get('user_context', 'Guest')))
                    if merge_key_s not in merge_groups_s:
                        merge_groups_s[merge_key_s] = []
                    merge_groups_s[merge_key_s].append((sig, comp_data))

                for merge_key_s, group in merge_groups_s.items():
                    if len(group) >= 2:
                        _base_sig_s, base_data = group[0]
                        merged_s: dict[str, Any] = {
                            'type': base_data['type'],
                            'label': base_data['label'],
                            'signature': base_data['signature'],
                            'xpaths_by_page': dict(base_data['xpaths_by_page']),
                            'pages': set(base_data['pages']),
                            'lang': base_data.get('lang', 'en'),
                            'user_context': base_data.get('user_context', 'Guest')
                        }
                        for _, comp_data in group[1:]:
                            merged_s['xpaths_by_page'].update(comp_data['xpaths_by_page'])
                            merged_s['pages'].update(comp_data['pages'])
                        merged_key_str_s = f"merged_{merge_key_s[0]}|{merge_key_s[1]}"
                        common_components[merged_key_str_s] = merged_s

                if common_components:
                    logger.info(
                        "Signature-based matching found 0 common components; " +
                        f"fallback merge by type found {len(common_components)}"
                    )

        # Reclassify issues whose component_signature was filtered out.
        # Issues matched to single-page components should become unassigned.
        reclassified: dict[tuple[str, ...], dict[str, Any]] = {}
        for dedup_key, issue_data in unique_issues.items():
            comp_sig = issue_data.get('component_signature')
            if comp_sig and comp_sig not in common_components:
                # This component didn't make the 2+ page cut; reclassify as unassigned
                issue_data['component_signature'] = None
                issue_data['component_type'] = None
                issue_data['component_label'] = None
                new_key = (issue_data['rule_id'],)
                if new_key in reclassified:
                    # Merge into existing unassigned entry
                    reclassified[new_key]['pages'].update(issue_data['pages'])
                    reclassified[new_key]['test_users'].update(issue_data['test_users'])
                    reclassified[new_key]['user_roles'].update(issue_data['user_roles'])
                else:
                    reclassified[new_key] = issue_data
            else:
                reclassified[dedup_key] = issue_data
        unique_issues = reclassified

        # Convert to sorted list (same as _deduplicate_issues_by_component lines 2780-2797)
        result_issues: list[dict[str, Any]] = []
        for issue_data in unique_issues.values():
            issue_data['pages'] = sorted(list(issue_data['pages']))
            issue_data['page_count'] = len(issue_data['pages'])
            issue_data['test_users'] = sorted(list(issue_data['test_users']))
            issue_data['user_roles'] = sorted(list(issue_data['user_roles']))
            result_issues.append(issue_data)

        impact_order_s: dict[str, int] = {'critical': 0, 'high': 1, 'serious': 1, 'moderate': 2, 'medium': 2, 'minor': 3, 'low': 3}
        type_order_s: dict[str, int] = {'violation': 0, 'warning': 1, 'info': 2}
        result_issues.sort(key=lambda x: (
            type_order_s.get(str(x['type']), 99),
            impact_order_s.get(str(x['impact']).lower() if isinstance(x['impact'], str) else 'moderate', 99),
            str(x['rule_id'])
        ))

        return common_components, result_issues, all_page_scores, all_compliance_scores, total_pages, page_metadata

    def _group_unassigned_by_page_streaming(
        self,
        unassigned_issues: list[dict[str, Any]],
        common_components: dict[str, dict[str, Any]],
        page_metadata: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Group unassigned issues by page using ONLY data from the dedup index.

        Does NOT re-load TestResults from the database. All needed data
        (descriptions, impacts, types, page URLs) is already in the
        deduplicated issue dicts from _collect_dedup_data_streaming().

        Memory: O(unassigned_issues) — no DB reads, no full TestResult loading.

        Args:
            unassigned_issues: List of unassigned deduplicated issues
            common_components: Dictionary of common components (unused but kept for API compat)
            page_metadata: {page_url: {page_id, title, page_score, website_id}} from streaming pass

        Returns:
            List of page data dictionaries with issue counts
        """
        import hashlib

        # Group dedup issues by page URL
        # Each dedup issue may appear on multiple pages
        page_issues: dict[str, dict[str, list[dict[str, Any]]]] = {}  # page_url -> {'violations': [...], 'warnings': [...], ...}
        for issue in unassigned_issues:
            for page_url in issue['pages']:
                if page_url not in page_issues:
                    page_issues[page_url] = {
                        'violations': [],
                        'warnings': [],
                        'info': [],
                        'discovery': []
                    }
                issue_type = issue.get('type', 'info')
                if issue_type == 'violation':
                    page_issues[page_url]['violations'].append(issue)
                elif issue_type == 'warning':
                    page_issues[page_url]['warnings'].append(issue)
                elif issue_type == 'info':
                    page_issues[page_url]['info'].append(issue)
                elif issue_type == 'discovery':
                    page_issues[page_url]['discovery'].append(issue)

        pages_with_unassigned: list[dict[str, Any]] = []
        for page_url, issues in page_issues.items():
            meta: dict[str, Any] = page_metadata.get(page_url, {})
            page_title: str | None = str(meta.get('title')) if meta.get('title') else None
            page_score: float = float(meta.get('page_score', 0))

            safe_url = hashlib.md5(page_url.encode()).hexdigest()[:12]

            v_count = len(issues['violations'])
            w_count = len(issues['warnings'])
            i_count = len(issues['info'])
            d_count = len(issues['discovery'])

            # Approximate dedup score: if no violations remain, score is high
            dedup_page_score = 100.0 if v_count == 0 else page_score

            pages_with_unassigned.append({
                'url': page_url,
                'title': page_title,
                'safe_url': safe_url,
                'test_date': None,
                'score': page_score,
                'dedup_score': dedup_page_score,
                'compliance_score': None,
                'errors_count': v_count,
                'warnings_count': w_count,
                'info_count': i_count,
                'discovery_count': d_count,
                'violations': v_count,
                'warnings': w_count,
                'info': i_count,
                'discovery': d_count,
                'total_issues': v_count + w_count + i_count + d_count,
                'issues': issues,
            })

        # Sort alphabetically by title (fallback to URL if no title)
        pages_with_unassigned.sort(key=lambda x: str(x.get('title') or x['url']).lower())  # noqa: E501

        return pages_with_unassigned

    def _xpath_is_within(self, issue_xpath: str, component_xpath: str) -> bool:
        """
        Check if an issue's XPath is within a component's XPath.

        Args:
            issue_xpath: XPath of the issue
            component_xpath: XPath of the component

        Returns:
            True if issue_xpath is within or equal to component_xpath
        """
        if not issue_xpath or not component_xpath:
            return False

        # Normalize xpaths by removing trailing slashes
        issue_xpath = issue_xpath.rstrip('/')
        component_xpath = component_xpath.rstrip('/')

        # Check if issue xpath starts with component xpath
        return issue_xpath == component_xpath or issue_xpath.startswith(component_xpath + '/')

    def _group_issues_by_component(
        self,
        deduplicated_issues: list[dict[str, Any]],
        common_components: dict[str, dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Group deduplicated issues by their component signature.

        Args:
            deduplicated_issues: List of deduplicated issues
            common_components: Dictionary of common components

        Returns:
            Dictionary mapping component signature -> list of issues
        """
        grouped: dict[str, list[dict[str, Any]]] = {}

        for issue in deduplicated_issues:
            component_sig = issue.get('component_signature')
            if component_sig:
                if component_sig not in grouped:
                    grouped[component_sig] = []
                grouped[component_sig].append(issue)
            else:
                # Issues without components go into 'unassigned' group
                if 'unassigned' not in grouped:
                    grouped['unassigned'] = []
                grouped['unassigned'].append(issue)

        return grouped

    def _extract_common_issues(
        self,
        unassigned_issues: list[dict[str, Any]],
        total_pages: int
    ) -> list[dict[str, Any]]:
        """
        Extract common issues from unassigned (non-component) issues.

        Groups unassigned issues by rule_id and identifies those appearing on
        multiple pages. These are issues like heading hierarchy violations,
        missing skip links, etc. that appear across many pages but aren't
        inside any structural component.

        Args:
            unassigned_issues: List of deduplicated issues not assigned to components
            total_pages: Total number of pages tested

        Returns:
            List of common issue dictionaries, sorted by page count descending
        """
        # Group unassigned issues by rule_id, collecting all pages
        issues_by_rule: dict[str, dict[str, Any]] = {}

        for issue in unassigned_issues:
            rule_id = issue.get('rule_id', '')
            if not rule_id:
                continue

            if rule_id not in issues_by_rule:
                issues_by_rule[rule_id] = {
                    'rule_id': rule_id,
                    'type': issue.get('type', 'violation'),
                    'impact': issue.get('impact', 'medium'),
                    'touchpoint': issue.get('touchpoint', ''),
                    'description_en': issue.get('description_en', ''),
                    'description_fr': issue.get('description_fr', ''),
                    'why_en': issue.get('why_en', ''),
                    'why_fr': issue.get('why_fr', ''),
                    'who_en': issue.get('who_en', ''),
                    'who_fr': issue.get('who_fr', ''),
                    'full_remediation_en': issue.get('full_remediation_en', ''),
                    'full_remediation_fr': issue.get('full_remediation_fr', ''),
                    'wcag': issue.get('wcag', ''),
                    'wcag_full': issue.get('wcag_full', []),
                    'pages': set(),
                    'instance_count': 0  # Total instances across all pages
                }

            # Add all pages from this deduplicated entry
            for page_url in issue.get('pages', []):
                issues_by_rule[rule_id]['pages'].add(page_url)
            issues_by_rule[rule_id]['instance_count'] += 1

        # Filter to issues appearing on 2+ pages
        common_issues: list[dict[str, Any]] = []
        for rule_id, issue_data in issues_by_rule.items():
            page_count = len(issue_data['pages'])
            if page_count >= 2:
                issue_data['pages'] = sorted(list(issue_data['pages']))
                issue_data['page_count'] = page_count
                issue_data['percentage'] = round((page_count / total_pages) * 100) if total_pages > 0 else 0
                common_issues.append(issue_data)

        # Sort by type (violations first), then by page count descending
        type_order_ci: dict[str, int] = {'violation': 0, 'warning': 1, 'info': 2}
        common_issues.sort(key=lambda x: (
            type_order_ci.get(str(x['type']), 99),
            -int(x['page_count']),
            str(x['rule_id'])
        ))

        return common_issues

    def _copy_dedup_assets(self, report_dir: Path) -> None:
        """Copy CSS and JS assets to report directory"""
        static_dir = Path(__file__).parent.parent / 'web' / 'static'

        # Copy CSS files
        css_files = ['bootstrap.min.css', 'bootstrap-icons.css']
        for css_file in css_files:
            src = static_dir / 'css' / css_file
            if src.exists():
                shutil.copy(src, report_dir / 'assets' / 'css' / css_file)

        # Copy custom CSS (if exists)
        custom_css = static_dir / 'css' / 'custom.css'
        if custom_css.exists():
            shutil.copy(custom_css, report_dir / 'assets' / 'css' / 'custom.css')

        # Copy JS files
        js_files = ['bootstrap.bundle.min.js']
        for js_file in js_files:
            src = static_dir / 'js' / js_file
            if src.exists():
                shutil.copy(src, report_dir / 'assets' / 'js' / js_file)

    def _generate_dedup_index(
        self,
        report_dir: Path,
        project: Any,
        project_name: str,
        common_components: dict[str, dict[str, Any]],
        issues_by_component: dict[str, list[dict[str, Any]]],
        pages_with_unassigned: list[dict[str, Any]],
        common_issues: list[dict[str, Any]],
        total_violations: int,
        total_warnings: int,
        total_info: int,
        total_discovery: int,
        total_pages: int,
        overall_accessibility_score: float,
        overall_compliance_score: float
    ) -> None:
        """Generate the index page for deduplicated report"""

        # Prepare components data with issue counts
        components_with_issues: list[dict[str, Any]] = []
        for signature, comp_data in common_components.items():
            issues = issues_by_component.get(signature, [])

            # Count issues by type
            violations = sum(1 for i in issues if i['type'] == 'violation')
            warnings = sum(1 for i in issues if i['type'] == 'warning')
            info_count = sum(1 for i in issues if i['type'] == 'info')
            discovery_count = sum(1 for i in issues if i['type'] == 'discovery')

            # Calculate score for this component
            component_score = self._calculate_dedup_score(issues)

            # Create safe filename
            import re
            safe_sig = re.sub(r'[^a-zA-Z0-9_-]', '_', signature)

            components_with_issues.append({
                'signature': signature,
                'safe_signature': safe_sig,
                'type': comp_data['type'],
                'label': comp_data['label'],
                'page_count': len(comp_data['pages']),
                'violations': violations,
                'warnings': warnings,
                'info': info_count,
                'discovery': discovery_count,
                'total_issues': len(issues),
                'score': component_score,
                'lang': comp_data.get('lang', 'en')  # Component language for filtering
            })

        # Sort by type then by violation count
        type_order_cwi: dict[str, int] = {'Navigation': 0, 'Header': 1, 'Footer': 2, 'Search': 3, 'Form': 4, 'Aside': 5, 'Section': 6}
        components_with_issues.sort(
            key=lambda x: (type_order_cwi.get(str(x['type']), 99), -int(x['violations']), -int(x['total_issues']))
        )

        # Get translations
        all_translations = self._get_translations()
        translations_en = all_translations['en']
        translations_fr = all_translations['fr']
        t = all_translations[self.language]  # Current language translations

        # Read embedded assets for standalone HTML
        embedded_assets = self._read_embedded_assets()

        # Render template
        template = self.template_env.get_template('static_report/dedup_index.html')

        with force_locale(self.language):
            html = template.render(
                asset_path='assets/',
                project=project,
                project_name=project_name,
                components_with_issues=components_with_issues,
                common_issues=common_issues,
                pages_with_unassigned=pages_with_unassigned,
                total_violations=total_violations,
                total_warnings=total_warnings,
                total_info=total_info,
                total_discovery=total_discovery,
                total_components=len(common_components),
                total_pages=total_pages,
                overall_accessibility_score=overall_accessibility_score,
                overall_compliance_score=overall_compliance_score,
                report_date=datetime.now(),
                # Translation support
                language=self.language,
                translations_en=translations_en,
                translations_fr=translations_fr,
                translations_json=json.dumps({'en': translations_en, 'fr': translations_fr}),
                t=t,
                # Embedded assets for standalone HTML
                bootstrap_css=embedded_assets['bootstrap_css'],
                bootstrap_icons_css=embedded_assets['bootstrap_icons_css'],
                bootstrap_js=embedded_assets['bootstrap_js'],
                tokens_css=embedded_assets.get('tokens_css', ''),
                style_css=embedded_assets.get('style_css', ''),
                show_error_codes=config.SHOW_ERROR_CODES
            )

        # Write to file
        (report_dir / 'index.html').write_text(html, encoding='utf-8')

    def _generate_component_pages(
        self,
        report_dir: Path,
        common_components: dict[str, dict[str, Any]],
        issues_by_component: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Generate individual component detail pages"""
        template = self.template_env.get_template('static_report/dedup_component.html')

        import re

        # Get translations
        all_translations = self._get_translations()
        translations_en = all_translations['en']
        translations_fr = all_translations['fr']
        t = all_translations[self.language]

        # Read embedded assets for standalone HTML
        embedded_assets = self._read_embedded_assets()

        # Generate page for each component
        for signature, comp_data in common_components.items():
            issues = issues_by_component.get(signature, [])

            # Calculate score for this component
            component_score = self._calculate_dedup_score(issues)

            # Create safe filename
            safe_sig = re.sub(r'[^a-zA-Z0-9_-]', '_', signature)

            with force_locale(self.language):
                html = template.render(
                    asset_path='../assets/',
                    component_type=comp_data['type'],
                    component_label=comp_data['label'],
                    component_signature=comp_data['signature'],  # Original signature without user context
                    page_count=len(comp_data['pages']),
                    pages=sorted(list(comp_data['pages'])),
                    issues=issues,
                    total_issues=len(issues),
                    component_score=component_score,
                    report_date=datetime.now(),
                    # Translation support
                    language=self.language,
                    translations_en=translations_en,
                    translations_fr=translations_fr,
                    translations_json=json.dumps({'en': translations_en, 'fr': translations_fr}),
                    t=t,
                    # Embedded assets for standalone HTML
                    bootstrap_css=embedded_assets['bootstrap_css'],
                    bootstrap_icons_css=embedded_assets['bootstrap_icons_css'],
                    bootstrap_js=embedded_assets['bootstrap_js'],
                    show_error_codes=config.SHOW_ERROR_CODES
                )

            # Write to file
            (report_dir / 'components' / f'{safe_sig}.html').write_text(html, encoding='utf-8')

        # Note: Unassigned issues are now handled by _generate_page_detail_pages()
        # which creates individual page files in the pages/ directory

    def _calculate_dedup_score(self, issues: list[dict[str, Any]]) -> float:
        """
        Calculate accessibility score for deduplicated issues.
        Uses similar logic to ResultProcessor but adapted for deduplicated data.

        Args:
            issues: List of deduplicated issues (violations only count toward score)

        Returns:
            Score as a percentage (0-100)
        """
        if not issues:
            return 100.0

        # Count violations only
        violations = [i for i in issues if i.get('type') == 'violation']

        if not violations:
            return 100.0

        # Weight by impact
        impact_weights = {
            'critical': 5,
            'serious': 5,
            'high': 5,
            'moderate': 3,
            'medium': 3,
            'minor': 1,
            'low': 1
        }

        total_weight = 0
        for issue in violations:
            impact = issue.get('impact', 'moderate')
            # Handle both string and enum formats
            if hasattr(impact, 'name'):
                impact = impact.name.lower()
            else:
                impact = str(impact).replace('IMPACT_LEVEL.', '').replace('ImpactLevel.', '').lower()
            weight = impact_weights.get(impact, 3)
            total_weight += weight

        # Calculate score (diminishing returns for more issues)
        # Using formula: 100 - (min(total_weight, 100))
        score = max(0, 100 - total_weight)

        return float(score)

    def _calculate_score_from_violations(self, violations: list[Any]) -> float:
        """
        Calculate accessibility score from raw violation objects (TestResult violations).

        Args:
            violations: List of violation objects from TestResult

        Returns:
            Score as a percentage (0-100)
        """
        if not violations:
            return 100.0

        # Weight by impact
        impact_weights = {
            'critical': 5,
            'serious': 5,
            'high': 5,
            'moderate': 3,
            'medium': 3,
            'minor': 1,
            'low': 1
        }

        total_weight = 0
        for issue in violations:
            impact_raw: object = issue.impact if hasattr(issue, 'impact') else 'moderate'
            # Handle both string and enum formats
            impact_str: str
            if hasattr(impact_raw, 'name'):
                impact_str = str(getattr(impact_raw, 'name')).lower()
            else:
                impact_str = str(impact_raw).replace('IMPACT_LEVEL.', '').replace('ImpactLevel.', '').lower()
            weight = impact_weights.get(impact_str, 3)
            total_weight += weight

        # Calculate score (diminishing returns for more issues)
        # Using formula: 100 - (min(total_weight, 100))
        score = max(0, 100 - total_weight)

        return float(score)

    def _group_unassigned_by_page(
        self,
        project_data: dict[str, Any],
        unassigned_issues: list[dict[str, Any]],
        common_components: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Group unassigned issues by page and prepare page data for index.

        Args:
            project_data: Project report data
            unassigned_issues: List of unassigned issues
            common_components: Dictionary of common components for filtering

        Returns:
            List of page data dictionaries with issue counts
        """
        import hashlib

        # Group issues by page URL
        issues_by_page: dict[str, list[dict[str, Any]]] = {}
        for issue in unassigned_issues:
            for page_url in issue['pages']:
                if page_url not in issues_by_page:
                    issues_by_page[page_url] = []
                issues_by_page[page_url].append(issue)

        # Create page data list
        pages_with_unassigned: list[dict[str, Any]] = []
        test_result: Any = None
        violations_list: list[Any] = []
        warnings_list: list[Any] = []
        for page_url in issues_by_page.keys():
            # Find page metadata and actual test results from project_data
            page_title: str | None = None
            page_test_date: str | None = None
            page_score: float = 0
            compliance_score: dict[str, Any] | None = None
            _errors_count = 0
            _warnings_count = 0
            _info_count = 0
            _discovery_count = 0
            original_metadata: dict[str, Any] = {}
            _total_page_violations = 0
            _total_page_warnings = 0
            # Get actual non-deduplicated violations/warnings/info/discovery for this page only
            page_violations: list[Any] = []
            page_warnings: list[Any] = []
            page_info: list[Any] = []
            page_discovery: list[Any] = []

            for website_data in project_data.get('websites', []):
                for page_result in website_data.get('pages', []):
                    page = page_result.get('page', {})
                    if page.url == page_url:
                        page_title = str(page.title) if hasattr(page, 'title') and page.title else None
                        page_test_date = page_result.get('test_date')

                        # Get the actual test result with individual instances
                        test_result = page_result.get('test_result')
                        if test_result:
                            # Calculate page score from the full test result
                            page_score = self._calculate_page_score(test_result)
                            # Save metadata for score calculation later
                            original_metadata = test_result.metadata if hasattr(test_result, 'metadata') else {}
                            # Access TestResult object attributes
                            violations_list = list(test_result.violations) if hasattr(test_result, 'violations') and test_result.violations else []
                            warnings_list = list(test_result.warnings) if hasattr(test_result, 'warnings') and test_result.warnings else []
                            info_list: list[Any] = list(test_result.info) if hasattr(test_result, 'info') and test_result.info else []
                            discovery_list: list[Any] = list(test_result.discovery) if hasattr(test_result, 'discovery') and test_result.discovery else []

                            # Store totals for score calculation later
                            _total_page_violations = len(violations_list)
                            _total_page_warnings = len(warnings_list)

                            total_tests = len(violations_list) + len(warnings_list) + len(info_list)
                            passed_tests = total_tests - len(violations_list)
                            if total_tests > 0:
                                compliance_score = {
                                    'score': (passed_tests / total_tests) * 100,
                                    'passed_tests': passed_tests,
                                    'total_tests': total_tests
                                }

                            # Filter to only non-component issues for this page
                            # Note: Issue counts will be set after filtering is complete
                            # Match the logic from _extract_common_components to identify which issues
                            # are NOT part of common components
                            for v_issue in violations_list:
                                # Check if this issue's xpath matches any common component
                                is_component_issue = False
                                v_xpath: str = str(v_issue.xpath) if hasattr(v_issue, 'xpath') else ''

                                # Check against all common components for this page
                                if v_xpath:
                                    for _sig, comp_data in common_components.items():
                                        comp_xpath_v: str = str(comp_data['xpaths_by_page'].get(page_url, ''))
                                        if comp_xpath_v and self._xpath_is_within(v_xpath, comp_xpath_v):
                                            is_component_issue = True
                                            break

                                # Only include non-component issues
                                if not is_component_issue:
                                    page_violations.append(v_issue)

                            # For warnings, info, and discovery, also filter to non-component only
                            for w_issue in warnings_list:
                                is_component_issue = False
                                w_xpath: str = str(w_issue.xpath) if hasattr(w_issue, 'xpath') else ''

                                if w_xpath:
                                    for _sig, comp_data in common_components.items():
                                        comp_xpath_w: str = str(comp_data['xpaths_by_page'].get(page_url, ''))
                                        if comp_xpath_w and self._xpath_is_within(w_xpath, comp_xpath_w):
                                            is_component_issue = True
                                            break

                                if not is_component_issue:
                                    page_warnings.append(w_issue)

                            for i_issue in info_list:
                                is_component_issue = False
                                i_xpath: str = str(i_issue.xpath) if hasattr(i_issue, 'xpath') else ''

                                if i_xpath:
                                    for _sig, comp_data in common_components.items():
                                        comp_xpath_i: str = str(comp_data['xpaths_by_page'].get(page_url, ''))
                                        if comp_xpath_i and self._xpath_is_within(i_xpath, comp_xpath_i):
                                            is_component_issue = True
                                            break

                                if not is_component_issue:
                                    page_info.append(i_issue)

                            for d_issue in discovery_list:
                                is_component_issue = False
                                d_xpath: str = str(d_issue.xpath) if hasattr(d_issue, 'xpath') else ''

                                if d_xpath:
                                    for _sig, comp_data in common_components.items():
                                        comp_xpath_d: str = str(comp_data['xpaths_by_page'].get(page_url, ''))
                                        if comp_xpath_d and self._xpath_is_within(d_xpath, comp_xpath_d):
                                            is_component_issue = True
                                            break

                                if not is_component_issue:
                                    page_discovery.append(d_issue)

                        break
                if page_title:
                    break

            # Create safe URL for filename
            safe_url = hashlib.md5(page_url.encode()).hexdigest()[:12]

            # Set issue counts based on filtered non-component issues
            errors_count = len(page_violations)
            warnings_count = len(page_warnings)
            info_count = len(page_info)
            discovery_count = len(page_discovery)

            # Calculate score for non-component issues on this page
            # Use the same scoring system as online test reporting (ResultProcessor.calculate_score)
            # Create a synthetic TestResult with only the filtered non-component issues
            if test_result and hasattr(test_result, 'metadata'):
                from types import SimpleNamespace

                # Adjust metadata to reflect the reduced number of violations
                # The scoring algorithm uses: score = (passed_checks / applicable_checks) * 100
                original_metadata = test_result.metadata if hasattr(test_result, 'metadata') else {}

                # Calculate how many violations and warnings were removed
                original_violations = len(violations_list)
                original_warnings = len(warnings_list)
                filtered_violations = len(page_violations)
                filtered_warnings = len(page_warnings)

                removed_violations = original_violations - filtered_violations
                removed_warnings = original_warnings - filtered_warnings
                removed_total = removed_violations + removed_warnings

                # Adjust the metadata counts
                # If we removed violations, that means those checks now pass (they were in components)
                adjusted_metadata = dict(original_metadata)
                adjusted_failed_checks = adjusted_metadata.get('failed_checks', 0) - removed_total
                adjusted_passed_checks = adjusted_metadata.get('passed_checks', 0) + removed_total
                # applicable_checks stays the same - same tests were run

                adjusted_metadata['failed_checks'] = max(0, adjusted_failed_checks)
                adjusted_metadata['passed_checks'] = adjusted_passed_checks

                # Create synthetic test result with only non-component issues
                synthetic_result = SimpleNamespace(
                    violations=page_violations,
                    warnings=page_warnings,
                    info=page_info,
                    discovery=page_discovery,
                    passes=test_result.passes if hasattr(test_result, 'passes') else [],
                    metadata=adjusted_metadata  # Use adjusted metadata
                )

                # Use the SAME scoring method as for full page score
                dedup_page_score = self._calculate_page_score(synthetic_result)
            else:
                # Fallback if no test_result available
                dedup_page_score = 100.0 if not page_violations else 0.0

            pages_with_unassigned.append({
                'url': page_url,
                'title': page_title,
                'safe_url': safe_url,
                'test_date': page_test_date,
                'score': page_score,  # Original full page score
                'dedup_score': dedup_page_score,  # Score for non-component issues only
                'compliance_score': compliance_score,
                'errors_count': errors_count,
                'warnings_count': warnings_count,
                'info_count': info_count,
                'discovery_count': discovery_count,
                'violations': len(page_violations),  # Use actual filtered non-component issue counts
                'warnings': len(page_warnings),
                'info': len(page_info),
                'discovery': len(page_discovery),
                'total_issues': len(page_violations) + len(page_warnings) + len(page_info) + len(page_discovery),
                'issues': {
                    'violations': page_violations,  # Use actual test result issues, not deduplicated
                    'warnings': page_warnings,
                    'info': page_info,
                    'discovery': page_discovery
                }
            })

        # Sort alphabetically by title (fallback to URL if no title)
        pages_with_unassigned.sort(key=lambda x: str(x.get('title') or x['url']).lower())

        return pages_with_unassigned

    def _generate_page_detail_pages(
        self,
        report_dir: Path,
        pages_with_unassigned: list[dict[str, Any]],
        project: Any,
        project_name: str,
        generation_date: str
    ) -> None:
        """Generate individual page detail pages for unassigned issues"""
        template = self.template_env.get_template('static_report/dedup_unassigned.html')

        wcag_level = project.wcag_level if project and hasattr(project, 'wcag_level') else 'AA'

        # Get translations
        all_translations = self._get_translations()
        translations_en = all_translations['en']
        translations_fr = all_translations['fr']
        t = all_translations[self.language]

        # Read embedded assets for standalone HTML
        embedded_assets = self._read_embedded_assets()

        for page_data in pages_with_unassigned:
            # Create page object for template
            from types import SimpleNamespace
            page = SimpleNamespace(
                title=page_data.get('title', 'Untitled Page'),
                url=page_data['url'],
                score=page_data.get('score', 0),
                test_date=page_data.get('test_date', 'Recently')
            )

            # Enrich issues in both EN and FR for client-side switching
            def enrich_issue_bilingual(issue: Any) -> dict[str, Any]:
                """Convert issue (Violation object or dedup dict) into a dict
                with bilingual enrichment.

                Accepts either:
                  - a Violation object (from test_result.violations / warnings / ...)
                  - a deduplicated issue dict from _collect_dedup_data_streaming
                    (keys: rule_id, description_en/fr, why_en/fr, who_en/fr,
                    full_remediation_en/fr, wcag_full, ...)

                Dedup dicts already have their bilingual fields populated, so we
                copy them straight into the metadata instead of re-enriching.
                Violation objects are enriched via IssueCatalog in both locales.
                """
                # Save reference before isinstance narrows the type
                issue_ref: object = issue
                is_dict = isinstance(issue, dict)

                # Uniform getter for either source type.
                def _get(name: str, default: Any = '') -> Any:
                    if is_dict:
                        val = issue.get(name)
                    else:
                        val = getattr(issue, name, None)
                    return default if val is None else val

                # Dedup dicts use 'rule_id' instead of 'id'.
                issue_id = _get('id') or _get('rule_id')

                metadata_src: dict[str, Any] = _narrow_dict(_get('metadata', None) or {})

                issue_dict: dict[str, Any] = {
                    'id': issue_id,
                    'description': _get('description'),
                    'impact': _get('impact', 'moderate'),
                    'xpath': _get('xpath'),
                    'html_snippet': _get('html_snippet') or _get('html'),
                    'touchpoint': _get('touchpoint'),
                    'failure_summary': _get('failure_summary'),
                    'metadata': dict(metadata_src)
                }

                # If this is a dedup dict that already has bilingual text
                # (produced by _collect_dedup_data_streaming), trust it and
                # skip the redundant IssueCatalog re-enrichment pass.
                issue_as_dict: dict[str, Any] = _narrow_dict(issue) if is_dict else {}
                if is_dict and (issue_as_dict.get('description_en') or issue_as_dict.get('description_fr')):
                    issue_dict['description_en'] = issue_as_dict.get('description_en', '')
                    issue_dict['description_fr'] = issue_as_dict.get('description_fr', '')
                    issue_dict['metadata']['what_en'] = issue_as_dict.get('description_en', '')
                    issue_dict['metadata']['what_fr'] = issue_as_dict.get('description_fr', '')
                    issue_dict['metadata']['what_generic_en'] = issue_as_dict.get('description_en', '')
                    issue_dict['metadata']['what_generic_fr'] = issue_as_dict.get('description_fr', '')
                    issue_dict['metadata']['why_en'] = issue_as_dict.get('why_en', '')
                    issue_dict['metadata']['why_fr'] = issue_as_dict.get('why_fr', '')
                    issue_dict['metadata']['who_en'] = issue_as_dict.get('who_en', '')
                    issue_dict['metadata']['who_fr'] = issue_as_dict.get('who_fr', '')
                    issue_dict['metadata']['full_remediation_en'] = issue_as_dict.get('full_remediation_en', '')
                    issue_dict['metadata']['full_remediation_fr'] = issue_as_dict.get('full_remediation_fr', '')
                    wcag_full_raw: object = issue_as_dict.get('wcag_full', [])
                    if isinstance(wcag_full_raw, str):
                        issue_dict['metadata']['wcag_full'] = [
                            c.strip() for c in wcag_full_raw.split(',') if c.strip()
                        ]
                    elif isinstance(wcag_full_raw, list):
                        issue_dict['metadata']['wcag_full'] = wcag_full_raw
                    else:
                        issue_dict['metadata']['wcag_full'] = []
                    return issue_dict

                # Enrich with IssueCatalog in both languages
                with force_locale('en'):
                    enriched_en = IssueCatalog.enrich_issue(issue_dict.copy())

                with force_locale('fr'):
                    enriched_fr = IssueCatalog.enrich_issue(issue_dict.copy())

                # DEBUG: Log what we got
                if issue_dict.get('id') == 'ErrAriaLabelMayNotBeFoundByVoiceControl':
                    logger.warning("=" * 60)
                    logger.warning(f"DEBUG BILINGUAL ENRICHMENT - Issue ID: {issue_dict.get('id')}")
                    logger.warning(f"EN description_full: {enriched_en.get('description_full', 'N/A')[:80]}")
                    logger.warning(f"FR description_full: {enriched_fr.get('description_full', 'N/A')[:80]}")
                    logger.warning(f"Same? {enriched_en.get('description_full') == enriched_fr.get('description_full')}")
                    logger.warning("=" * 60)

                # Merge bilingual fields into the issue_dict
                issue_dict['description_en'] = enriched_en.get('description_full', enriched_en.get('description', ''))
                issue_dict['description_fr'] = enriched_fr.get('description_full', enriched_fr.get('description', ''))

                # Store bilingual metadata fields - IssueCatalog.enrich_issue() returns these exact field names
                issue_dict['metadata']['what_en'] = enriched_en['description_full']
                issue_dict['metadata']['what_fr'] = enriched_fr['description_full']
                issue_dict['metadata']['what_generic_en'] = enriched_en['description_full']
                issue_dict['metadata']['what_generic_fr'] = enriched_fr['description_full']
                issue_dict['metadata']['why_en'] = enriched_en['why_it_matters']
                issue_dict['metadata']['why_fr'] = enriched_fr['why_it_matters']
                issue_dict['metadata']['who_en'] = enriched_en['who_it_affects']
                issue_dict['metadata']['who_fr'] = enriched_fr['who_it_affects']
                issue_dict['metadata']['full_remediation_en'] = enriched_en['how_to_fix']
                issue_dict['metadata']['full_remediation_fr'] = enriched_fr['how_to_fix']

                # Convert wcag_full from string to list for template iteration
                wcag_full_raw = enriched_en['wcag_full']
                if isinstance(wcag_full_raw, str):
                    # Split comma-separated string and clean up whitespace
                    issue_dict['metadata']['wcag_full'] = [c.strip() for c in wcag_full_raw.split(',') if c.strip()]
                elif isinstance(wcag_full_raw, list):
                    issue_dict['metadata']['wcag_full'] = wcag_full_raw
                else:
                    issue_dict['metadata']['wcag_full'] = []

                # Copy other metadata fields that might exist
                if hasattr(issue_ref, 'metadata'):
                    issue_meta: object = getattr(issue_ref, 'metadata', None)
                    if issue_meta:
                        if hasattr(issue_meta, 'authenticated_user'):
                            issue_dict['metadata']['authenticated_user'] = getattr(issue_meta, 'authenticated_user')
                        if hasattr(issue_meta, 'breakpoint'):
                            issue_dict['metadata']['breakpoint'] = getattr(issue_meta, 'breakpoint')
                        if hasattr(issue_meta, 'pseudoclass'):
                            issue_dict['metadata']['pseudoclass'] = getattr(issue_meta, 'pseudoclass')

                return issue_dict

            # Enrich all issues
            violations_enriched = [enrich_issue_bilingual(v) for v in page_data['issues']['violations']]
            warnings_enriched = [enrich_issue_bilingual(w) for w in page_data['issues']['warnings']]
            info_enriched = [enrich_issue_bilingual(i) for i in page_data['issues']['info']]
            discovery_enriched = [enrich_issue_bilingual(d) for d in page_data['issues']['discovery']]

            html = template.render(
                page=page,
                page_url=page_data['url'],
                page_title=page_data.get('title'),
                violations=violations_enriched,
                warnings=warnings_enriched,
                info=info_enriched,
                discovery=discovery_enriched,
                total_issues=page_data['total_issues'],
                errors_count=page_data.get('errors_count', 0),
                warnings_count=page_data.get('warnings_count', 0),
                info_count=page_data.get('info_count', 0),
                discovery_count=page_data.get('discovery_count', 0),
                compliance_score=page_data.get('compliance_score'),
                dedup_score=page_data.get('dedup_score', 0),  # Score for non-component issues
                report_date=datetime.now(),
                generation_date=generation_date,
                project_name=project_name,
                wcag_level=wcag_level,
                # Translation support
                language=self.language,
                translations_en=translations_en,
                translations_fr=translations_fr,
                translations_json=json.dumps({'en': translations_en, 'fr': translations_fr}),
                t=t,
                # Embedded assets for standalone HTML
                bootstrap_css=embedded_assets['bootstrap_css'],
                bootstrap_icons_css=embedded_assets['bootstrap_icons_css'],
                bootstrap_js=embedded_assets['bootstrap_js'],
                tokens_css=embedded_assets.get('tokens_css', ''),
                style_css=embedded_assets.get('style_css', ''),
                show_error_codes=config.SHOW_ERROR_CODES
            )

            # Write to file
            (report_dir / 'pages' / f"{page_data['safe_url']}.html").write_text(html, encoding='utf-8')
