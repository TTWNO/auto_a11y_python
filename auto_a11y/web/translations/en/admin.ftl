# Admin settings — superadmin-only system configuration UI.

admin-settings-title = System Settings
admin-settings-intro = Configure integrations and feature flags. Values saved here override matching environment variables.
admin-settings-save = Save changes

# Source-of-truth indicators on each section.
admin-settings-source-database = These values are loaded from the database and override any environment variables.
admin-settings-source-environment = No values are saved here. The current configuration comes from environment variables; saving below will override them.
admin-settings-source-unset = This integration is not configured. Saving values below will enable it.

# Drupal section.
admin-settings-drupal-heading = Drupal Sync
admin-settings-drupal-description = Connection settings for the Drupal JSON:API used to sync discovered pages, recordings, and issues.
admin-settings-drupal-base-url = Base URL
admin-settings-drupal-base-url-help = The root URL of the Drupal site, including https://.
admin-settings-drupal-username = Username
admin-settings-drupal-password = Password
admin-settings-drupal-password-placeholder-set = (leave blank to keep existing password)
admin-settings-drupal-password-help-set = A password is currently saved. Enter a new password to replace it, or leave blank to keep it.
admin-settings-drupal-password-help-unset = Required. Stored in the database; not displayed once saved.
admin-settings-drupal-enabled = Drupal sync enabled
admin-settings-drupal-saved = Drupal settings saved.
admin-settings-drupal-cleared = Drupal settings cleared. The application will fall back to environment variables.
admin-settings-drupal-clear = Clear settings
admin-settings-drupal-clear-confirm = Clear the saved Drupal settings? The application will fall back to environment variables.
admin-settings-drupal-invalid-url = Drupal base URL must start with http:// or https://.
admin-settings-drupal-username-required = Drupal username is required.
admin-settings-drupal-password-required = Drupal password is required.
