# Admin settings — interface de configuration système réservée aux super-administrateurs.

admin-settings-title = Paramètres système
admin-settings-intro = Configurez les intégrations et les indicateurs de fonctionnalité. Les valeurs enregistrées ici remplacent les variables d'environnement correspondantes.
admin-settings-save = Enregistrer les modifications

# Indicateurs de la source de configuration pour chaque section.
admin-settings-source-database = Ces valeurs proviennent de la base de données et remplacent toute variable d'environnement.
admin-settings-source-environment = Aucune valeur n'est enregistrée ici. La configuration actuelle provient des variables d'environnement ; les enregistrer ci-dessous les remplacera.
admin-settings-source-unset = Cette intégration n'est pas configurée. Enregistrer des valeurs ci-dessous l'activera.

# Section Drupal.
admin-settings-drupal-heading = Synchronisation Drupal
admin-settings-drupal-description = Paramètres de connexion à l'API JSON Drupal utilisée pour synchroniser les pages découvertes, les enregistrements et les problèmes.
admin-settings-drupal-base-url = URL de base
admin-settings-drupal-base-url-help = URL racine du site Drupal, incluant https://.
admin-settings-drupal-username = Nom d'utilisateur
admin-settings-drupal-password = Mot de passe
admin-settings-drupal-password-placeholder-set = (laisser vide pour conserver le mot de passe existant)
admin-settings-drupal-password-help-set = Un mot de passe est actuellement enregistré. Saisissez-en un nouveau pour le remplacer, ou laissez vide pour le conserver.
admin-settings-drupal-password-help-unset = Obligatoire. Stocké en base de données ; non affiché une fois enregistré.
admin-settings-drupal-enabled = Synchronisation Drupal activée
admin-settings-drupal-saved = Paramètres Drupal enregistrés.
admin-settings-drupal-cleared = Paramètres Drupal effacés. L'application utilisera désormais les variables d'environnement.
admin-settings-drupal-clear = Effacer les paramètres
admin-settings-drupal-clear-confirm = Effacer les paramètres Drupal enregistrés ? L'application utilisera les variables d'environnement.
admin-settings-drupal-invalid-url = L'URL de base Drupal doit commencer par http:// ou https://.
admin-settings-drupal-username-required = Le nom d'utilisateur Drupal est requis.
admin-settings-drupal-password-required = Le mot de passe Drupal est requis.
