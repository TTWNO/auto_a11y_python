"""
ProjectUser model for managing test users with different roles at the project level
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from bson import ObjectId
from enum import Enum


class AuthenticationMethod(Enum):
    """Authentication method types"""
    FORM_LOGIN = "form_login"  # Standard username/password form
    BASIC_AUTH = "basic_auth"  # HTTP Basic Authentication
    MANUAL_LOGIN = "manual_login"  # User logs in interactively in a visible browser (2FA, multi-step)
    OAUTH = "oauth"  # OAuth flow (future support)
    SSO = "sso"  # Single Sign-On (future support)


@dataclass
class LoginConfig:
    """Configuration for logging in as this user"""
    authentication_method: AuthenticationMethod = AuthenticationMethod.FORM_LOGIN

    # Form login configuration
    login_url: str | None = None  # URL of login page
    username_field_selector: str | None = None  # CSS selector for username field
    password_field_selector: str | None = None  # CSS selector for password field
    submit_button_selector: str | None = None  # CSS selector for submit button
    success_indicator_selector: str | None = None  # Selector to verify successful login

    # Logout configuration
    logout_url: str | None = None  # URL to visit for logout (e.g., /logout)
    logout_button_selector: str | None = None  # CSS selector for logout button/link
    logout_success_indicator_selector: str | None = None  # Selector to verify successful logout

    # Additional login steps (for multi-step logins)
    additional_steps: list[dict[str, Any]] = field(default_factory=lambda: [])  # PageSetupScript-like steps

    # Manual login configuration (used when authentication_method == MANUAL_LOGIN)
    # The user is given this many seconds to complete login in a visible browser window
    # before testing proceeds with whatever cookies/storage state were established.
    manual_login_wait_seconds: int = 120

    # Session configuration
    session_timeout_minutes: int = 30  # How long the session is valid

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            'authentication_method': self.authentication_method.value,
            'login_url': self.login_url,
            'username_field_selector': self.username_field_selector,
            'password_field_selector': self.password_field_selector,
            'submit_button_selector': self.submit_button_selector,
            'success_indicator_selector': self.success_indicator_selector,
            'logout_url': self.logout_url,
            'logout_button_selector': self.logout_button_selector,
            'logout_success_indicator_selector': self.logout_success_indicator_selector,
            'additional_steps': self.additional_steps,
            'manual_login_wait_seconds': self.manual_login_wait_seconds,
            'session_timeout_minutes': self.session_timeout_minutes
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoginConfig:
        """Create from dictionary"""
        if not data:
            return cls()

        auth_method = data.get('authentication_method', 'form_login')
        if isinstance(auth_method, str):
            auth_method = AuthenticationMethod(auth_method)

        return cls(
            authentication_method=auth_method,
            login_url=data.get('login_url'),
            username_field_selector=data.get('username_field_selector'),
            password_field_selector=data.get('password_field_selector'),
            submit_button_selector=data.get('submit_button_selector'),
            success_indicator_selector=data.get('success_indicator_selector'),
            logout_url=data.get('logout_url'),
            logout_button_selector=data.get('logout_button_selector'),
            logout_success_indicator_selector=data.get('logout_success_indicator_selector'),
            additional_steps=data.get('additional_steps', []),
            manual_login_wait_seconds=data.get('manual_login_wait_seconds', 120),
            session_timeout_minutes=data.get('session_timeout_minutes', 30)
        )


@dataclass
class ProjectUser:
    """Test user for authenticated testing at the project level"""

    project_id: str  # Which project this user belongs to
    username: str  # Username for login
    password: str  # Password (will be encrypted in future)

    # User identification
    display_name: str | None = None  # Friendly name like "Student User" or "John Doe"
    roles: list[str] = field(default_factory=lambda: [])  # User roles: ["student", "premium"], etc.
    description: str | None = None  # Notes about this user

    # Login configuration
    login_config: LoginConfig = field(default_factory=LoginConfig)

    # Status and metadata
    enabled: bool = True  # Can be disabled without deleting
    last_used: datetime | None = None  # When this user was last used for testing
    last_login_success: bool | None = None  # Did last login attempt succeed?
    last_login_error: str | None = None  # Error message from last failed login

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    _id: ObjectId | None = None

    @property
    def id(self) -> str | None:
        """Get user ID as string"""
        return str(self._id) if self._id else None

    @property
    def mongo_id(self) -> ObjectId | None:
        """Get the raw MongoDB _id value."""
        return self._id

    @mongo_id.setter
    def mongo_id(self, value: ObjectId | None) -> None:
        """Set the raw MongoDB _id value."""
        self._id = value

    @property
    def role_display(self) -> str:
        """Get comma-separated roles for display"""
        return ", ".join(self.roles) if self.roles else "No roles"

    @property
    def name_display(self) -> str:
        """Get display name or username"""
        return self.display_name or self.username

    def update_timestamp(self) -> None:
        """Update the updated_at timestamp"""
        self.updated_at = datetime.now()

    def mark_login_attempt(self, success: bool, error: str | None = None) -> None:
        """Record the result of a login attempt"""
        self.last_used = datetime.now()
        self.last_login_success = success
        self.last_login_error = error if not success else None
        self.update_timestamp()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for MongoDB"""
        data: dict[str, Any] = {
            'project_id': self.project_id,
            'username': self.username,
            'password': self.password,  # TODO: Encrypt in production
            'display_name': self.display_name,
            'roles': self.roles,
            'description': self.description,
            'login_config': self.login_config.to_dict(),
            'enabled': self.enabled,
            'last_used': self.last_used,
            'last_login_success': self.last_login_success,
            'last_login_error': self.last_login_error,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }
        if self._id:
            data['_id'] = self._id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectUser:
        """Create from MongoDB document"""
        return cls(
            project_id=data['project_id'],
            username=data['username'],
            password=data['password'],
            display_name=data.get('display_name'),
            roles=data.get('roles', []),
            description=data.get('description'),
            login_config=LoginConfig.from_dict(data.get('login_config', {})),
            enabled=data.get('enabled', True),
            last_used=data.get('last_used'),
            last_login_success=data.get('last_login_success'),
            last_login_error=data.get('last_login_error'),
            created_at=data.get('created_at', datetime.now()),
            updated_at=data.get('updated_at', datetime.now()),
            _id=data.get('_id')
        )
