"""
Drupal Configuration Management

Handles loading and validation of Drupal connection settings from the
in-app admin settings page (preferred) or environment variables (fallback).
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auto_a11y.core.database import Database

logger = logging.getLogger(__name__)

DRUPAL_SETTINGS_KEY = "drupal"

# User-facing pointer to the in-app settings page. Kept as a path so the
# message renders correctly outside request contexts (CLI, scheduler).
SETTINGS_PATH = "/admin/settings"

MISSING_CONFIG_MESSAGE = (
    "Drupal integration is not configured. "
    f"An administrator can set the base URL, username, and password on the "
    f"settings page at {SETTINGS_PATH}."
)


@dataclass
class DrupalConfig:
    """Configuration for Drupal JSON:API connection"""

    base_url: str
    username: str
    password: str
    enabled: bool = True

    @classmethod
    def from_db(cls, db: Database) -> DrupalConfig | None:
        """Load configuration from the in-app system_settings collection.

        Returns ``None`` when the section is missing or incomplete so the
        caller can fall back to environment variables.
        """
        from auto_a11y.core.system_settings import SystemSettings

        section = SystemSettings(db).get_section(DRUPAL_SETTINGS_KEY)
        if section is None:
            return None

        base_url = _str_or_none(section.get("base_url"))
        username = _str_or_none(section.get("username"))
        password = _str_or_none(section.get("password"))
        enabled_raw = section.get("enabled", True)
        enabled = bool(enabled_raw) if not isinstance(enabled_raw, str) else enabled_raw.lower() == "true"

        if not base_url or not username or not password:
            return None

        logger.info("Loaded Drupal config from system_settings")
        return cls(
            base_url=base_url,
            username=username,
            password=password,
            enabled=enabled,
        )

    @classmethod
    def from_env(cls) -> DrupalConfig:
        """
        Load configuration from environment variables.

        Environment variables:
            DRUPAL_BASE_URL: Base URL of Drupal site
            DRUPAL_USERNAME: Username for authentication
            DRUPAL_PASSWORD: Password for authentication
            DRUPAL_EXPORT_ENABLED: Whether export is enabled (default: true)

        Returns:
            DrupalConfig instance

        Raises:
            ValueError: If required variables are missing
        """
        base_url = os.getenv('DRUPAL_BASE_URL')
        username = os.getenv('DRUPAL_USERNAME')
        password = os.getenv('DRUPAL_PASSWORD')
        enabled = os.getenv('DRUPAL_EXPORT_ENABLED', 'true').lower() == 'true'

        if not base_url or not username or not password:
            raise ValueError(MISSING_CONFIG_MESSAGE)

        return cls(
            base_url=base_url,
            username=username,
            password=password,
            enabled=enabled
        )

    @classmethod
    def from_config_file(cls, config_path: str | None = None) -> DrupalConfig:
        """
        Load configuration from a config file.

        Args:
            config_path: Path to config file. If None, uses default location.

        Returns:
            DrupalConfig instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If required settings are missing
        """
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'config',
                'drupal.conf'
            )

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Drupal config file not found: {config_path}")

        config: dict[str, str] = {}
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if '=' in line:
                    key, value = line.split('=', 1)
                    config[key.strip()] = value.strip()

        base_url = config.get('base_url')
        username = config.get('username')
        password = config.get('password')
        enabled = config.get('enabled', 'true').lower() == 'true'

        if not base_url or not username or not password:
            raise ValueError(
                f"Missing required Drupal configuration in {config_path}. "
                + "Need: base_url, username, password"
            )

        logger.info(f"Loaded Drupal config from {config_path}")

        return cls(
            base_url=base_url,
            username=username,
            password=password,
            enabled=enabled
        )

    @classmethod
    def default(cls) -> DrupalConfig:
        """
        Get default configuration (for development/testing).

        Returns:
            DrupalConfig with default values

        Note:
            This uses the values from the documentation for testing.
            In production, use from_env() or from_config_file() instead.
        """
        return cls(
            base_url='https://audits.frontier-cnib.ca',
            username='restuser',
            password='venez1a?',
            enabled=True
        )

    def validate(self) -> bool:
        """
        Validate the configuration.

        Returns:
            True if valid

        Raises:
            ValueError: If configuration is invalid
        """
        if not self.base_url:
            raise ValueError("base_url is required")

        if not self.base_url.startswith(('http://', 'https://')):
            raise ValueError("base_url must start with http:// or https://")

        if not self.username:
            raise ValueError("username is required")

        if not self.password:
            raise ValueError("password is required")

        return True


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def get_drupal_config(
    config_path: str | None = None,
    db: Database | None = None,
) -> DrupalConfig:
    """
    Get Drupal configuration, trying multiple sources.

    Tries in order:
    1. Config file (if path provided)
    2. ``system_settings`` MongoDB document (if ``db`` provided)
    3. Environment variables
    4. Default config file location

    Args:
        config_path: Optional path to config file
        db: Optional ``Database`` for reading admin-managed settings.
            Routes should pass ``get_db()`` here.

    Returns:
        DrupalConfig instance

    Raises:
        ValueError: If configuration cannot be loaded
    """
    if config_path:
        try:
            config = DrupalConfig.from_config_file(config_path)
            config.validate()
            return config
        except Exception as e:
            logger.warning(f"Could not load config from {config_path}: {e}")

    if db is not None:
        db_config = DrupalConfig.from_db(db)
        if db_config is not None:
            try:
                db_config.validate()
                return db_config
            except Exception as e:
                logger.warning(f"system_settings Drupal config failed validation: {e}")

    try:
        config = DrupalConfig.from_env()
        config.validate()
        logger.info("Loaded Drupal config from environment variables")
        return config
    except Exception as e:
        logger.debug(f"Could not load config from environment: {e}")

    try:
        config = DrupalConfig.from_config_file()
        config.validate()
        return config
    except Exception as e:
        logger.debug(f"Could not load config from default location: {e}")

    raise ValueError(MISSING_CONFIG_MESSAGE)
