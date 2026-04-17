"""
Font configuration utilities for managing inaccessible font lists
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FontConfigManager:
    """Manages font accessibility configuration with defaults and project overrides"""

    def __init__(self) -> None:
        self.defaults_path: Path = Path(__file__).parent.parent / 'config' / 'inaccessible_fonts_defaults.json'
        self._defaults_cache: set[str] | None = None

    def get_default_inaccessible_fonts(self) -> set[str]:
        """
        Load the default inaccessible fonts list from the system configuration

        Returns:
            Set of lowercase font names that are considered inaccessible
        """
        if self._defaults_cache is not None:
            return self._defaults_cache

        try:
            with open(self.defaults_path, 'r', encoding='utf-8') as f:
                config: dict[str, Any] = json.load(f)

            # Flatten all fonts from all categories into a single set
            fonts: set[str] = set()
            categories: dict[str, Any] = config.get('categories', {})
            for _, category_data in categories.items():
                category_fonts: list[str] = category_data.get('fonts', [])
                fonts.update(category_fonts)

            # Cache for performance
            self._defaults_cache = fonts

            logger.info(f"Loaded {len(fonts)} default inaccessible fonts from {len(categories)} categories")
            return fonts

        except FileNotFoundError:
            logger.error(f"Default inaccessible fonts file not found at {self.defaults_path}")
            return set()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse default inaccessible fonts JSON: {e}")
            return set()
        except Exception as e:
            logger.error(f"Unexpected error loading default inaccessible fonts: {e}")
            return set()

    def get_project_inaccessible_fonts(self, project_config: dict[str, Any]) -> set[str]:
        """
        Get the complete list of inaccessible fonts for a specific project,
        merging defaults with project-specific configuration

        Args:
            project_config: Project configuration dictionary (from Project.config)

        Returns:
            Set of lowercase font names to flag as inaccessible for this project
        """
        font_config: dict[str, Any] = project_config.get('font_accessibility', {})

        # Start with defaults if enabled
        use_defaults: bool = font_config.get('use_defaults', True)
        fonts: set[str]
        if use_defaults:
            fonts = self.get_default_inaccessible_fonts().copy()
        else:
            fonts = set()

        # Add project-specific fonts
        additional: list[Any] = font_config.get('additional_inaccessible_fonts', [])
        for font in additional:
            if isinstance(font, str):
                fonts.add(font.lower().strip())

        # Remove excluded fonts
        excluded: list[Any] = font_config.get('excluded_fonts', [])
        for font in excluded:
            if isinstance(font, str):
                fonts.discard(font.lower().strip())

        logger.debug(
            f"Project font config: {len(fonts)} inaccessible fonts "
            + f"(defaults: {use_defaults}, added: {len(additional)}, excluded: {len(excluded)})"
        )

        return fonts

    def is_font_inaccessible(self, font_name: str, project_config: dict[str, Any] | None = None) -> bool:
        """
        Check if a font is considered inaccessible

        Args:
            font_name: Name of the font to check
            project_config: Optional project configuration. If None, uses only defaults

        Returns:
            True if the font is in the inaccessible list
        """
        if not font_name:
            return False

        # Normalize font name (lowercase, strip quotes and whitespace)
        normalized = font_name.lower().strip().strip('"').strip("'")

        # Get the appropriate font list
        if project_config:
            inaccessible_fonts = self.get_project_inaccessible_fonts(project_config)
        else:
            inaccessible_fonts = self.get_default_inaccessible_fonts()

        return normalized in inaccessible_fonts

    def get_font_category(self, font_name: str) -> str | None:
        """
        Get the category of an inaccessible font for better error messaging

        Args:
            font_name: Name of the font to check

        Returns:
            Category name (e.g., 'script_cursive', 'decorative') or None if not found
        """
        try:
            with open(self.defaults_path, 'r', encoding='utf-8') as f:
                config: dict[str, Any] = json.load(f)

            normalized = font_name.lower().strip().strip('"').strip("'")
            categories: dict[str, Any] = config.get('categories', {})

            for category_name, category_data in categories.items():
                if normalized in category_data.get('fonts', []):
                    return str(category_name)

            return None

        except Exception as e:
            logger.error(f"Error getting font category: {e}")
            return None


# Singleton instance for easy access
font_config_manager: FontConfigManager = FontConfigManager()
