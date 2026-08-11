# Admin settings — interface de configuration système réservée aux super-administrateurs.

admin-settings-title = Paramètres système
admin-settings-intro = Configurez les intégrations et les indicateurs de fonctionnalité. Les valeurs enregistrées ici remplacent les variables d'environnement correspondantes.
admin-settings-save = Enregistrer les modifications
admin-settings-clear = Effacer les paramètres
admin-settings-toc-label = Sections de paramètres

# Indicateurs de la source de configuration pour chaque section.
admin-settings-source-database = Ces valeurs proviennent de la base de données et remplacent toute variable d'environnement.
admin-settings-source-environment = Aucune valeur n'est enregistrée ici. La configuration actuelle provient des variables d'environnement ; les enregistrer ci-dessous les remplacera.
admin-settings-source-unset = Cette intégration n'est pas configurée. Enregistrer des valeurs ci-dessous l'activera.
admin-settings-source-default = Aucune valeur n'est enregistrée ici et aucune variable d'environnement n'est définie. L'application utilise les valeurs par défaut intégrées ; les enregistrer ci-dessous les remplacera.

# Secrets de l'application — stockés dans le fichier de paramètres de
# l'utilisateur (lu avant que la base de données ne soit disponible). URI
# MongoDB, clés d'API facultatives, remplacements ffmpeg.
admin-settings-user-secrets-heading = Secrets de l'application
admin-settings-user-secrets-description = Valeurs de connexion et clés d'API utilisées au démarrage et par des fonctionnalités facultatives. Elles sont stockées dans le fichier de paramètres local de cet ordinateur, distinct des paramètres de base de données ci-dessus.
admin-settings-user-secrets-restart-note = Les valeurs enregistrées s'appliquent immédiatement aux nouveaux traitements. Redémarrez l'application pour les appliquer partout.
admin-settings-user-secrets-mongodb-uri = URI de connexion MongoDB
admin-settings-user-secrets-mongodb-uri-help = Laissez vide pour utiliser la connexion à la base de données intégrée par défaut.
admin-settings-user-secrets-anthropic = Clé d'API Anthropic
admin-settings-user-secrets-anthropic-help = Active les fonctionnalités d'analyse par IA.
admin-settings-user-secrets-deepgram = Clé d'API Deepgram
admin-settings-user-secrets-deepgram-help = Active les fonctionnalités de transcription audio.
admin-settings-user-secrets-huggingface = Jeton Hugging Face
admin-settings-user-secrets-huggingface-help = Facultatif ; utilisé pour les modèles de diarisation des locuteurs.
admin-settings-user-secrets-ffmpeg = Chemin ffmpeg (remplacement facultatif)
admin-settings-user-secrets-ffmpeg-help = Laissez vide pour utiliser le ffmpeg intégré ou celui de votre PATH.
admin-settings-user-secrets-ffprobe = Chemin ffprobe (remplacement facultatif)
admin-settings-user-secrets-ffprobe-help = Laissez vide pour utiliser le ffprobe intégré ou celui de votre PATH.
admin-settings-user-secrets-secret-set = Une valeur est déjà enregistrée — laissez vide pour la conserver.
admin-settings-user-secrets-secret-unset = Aucune valeur n'est encore enregistrée.
admin-settings-user-secrets-placeholder-set = •••••••• (enregistré)
admin-settings-user-secrets-remove = Supprimer la valeur enregistrée
admin-settings-user-secrets-saved = Secrets de l'application enregistrés.
admin-settings-user-secrets-save-failed = Impossible d'écrire le fichier de paramètres : { $detail }

# Aides communes partagées entre les sections de variables d'environnement.
admin-settings-restart-note = La plupart des modifications prennent effet immédiatement. Quelques paramètres (par exemple l'adresse d'écoute du serveur, le démarrage du planificateur) ne s'appliquent qu'après un redémarrage de l'application.
admin-settings-password-placeholder-set = (laisser vide pour conserver la valeur existante)
admin-settings-password-help-set = Une valeur est actuellement enregistrée. Saisissez-en une nouvelle pour la remplacer, ou laissez vide pour la conserver.
admin-settings-password-help-unset = Stocké en base de données ; non affiché une fois enregistré.
admin-settings-section-saved = Paramètres « { $heading } » enregistrés.
admin-settings-section-cleared = Paramètres « { $heading } » effacés. L'application utilisera désormais les variables d'environnement.
admin-settings-section-clear-confirm = Effacer les paramètres « { $heading } » enregistrés ? L'application utilisera les variables d'environnement.
admin-settings-unknown-section = Section de paramètres inconnue.
admin-settings-field-invalid = { $label } : { $detail }

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

# Section Claude AI.
admin-settings-claude-heading = Claude AI
admin-settings-claude-description = Identifiants et réglages Anthropic Claude pour l'analyse visuelle d'accessibilité par IA.
admin-settings-claude-api-key = Clé API
admin-settings-claude-api-key-help = Émise par Anthropic. Requise lorsque l'analyse IA est activée.
admin-settings-claude-model = Modèle
admin-settings-claude-model-help = Identifiant du modèle Claude (ex. claude-opus-4-20250514).
admin-settings-claude-max-tokens = Jetons de sortie maximum
admin-settings-claude-budget-tokens = Budget de jetons de réflexion
admin-settings-claude-temperature = Température
admin-settings-claude-use-thinking = Utiliser la réflexion étendue
admin-settings-run-ai-analysis = Exécuter l'analyse IA sur les pages testées
admin-settings-run-ai-analysis-help = Désactivée, seuls les tests JavaScript basés sur le DOM s'exécutent. La clé API reste nécessaire si vous réactivez cette option ultérieurement.

# Section Navigateur.
admin-settings-browser-heading = Automatisation du navigateur
admin-settings-browser-description = Paramètres Playwright pour le Chromium sans interface qui exécute les tests d'accessibilité.
admin-settings-browser-mode = Mode
admin-settings-browser-mode-help = « local » exécute Chromium sur cette machine, « remote » délègue à un travailleur distant, « disabled » désactive les tests par navigateur.
admin-settings-browser-headless = Exécuter sans interface
admin-settings-browser-timeout = Délai d'expiration (ms)
admin-settings-browser-timeout-help = Durée maximale d'attente d'une navigation ou d'une opération avant échec.
admin-settings-browser-viewport-width = Largeur de la fenêtre (px)
admin-settings-browser-viewport-height = Hauteur de la fenêtre (px)

# Section Exploration & Scraping.
admin-settings-scraping-heading = Exploration & Scraping
admin-settings-scraping-description = Limites et contrôles de politesse pour l'explorateur de pages.
admin-settings-max-pages-per-site = Nombre maximal de pages par site
admin-settings-max-crawl-depth = Profondeur d'exploration maximale
admin-settings-request-delay = Délai entre requêtes (secondes)
admin-settings-request-delay-help = Pause entre les chargements de pages. Une valeur plus élevée ménage le site cible.
admin-settings-user-agent = Agent utilisateur
admin-settings-respect-robots-txt = Respecter robots.txt

# Section Tests & Travailleurs.
admin-settings-testing-heading = Tests & Travailleurs
admin-settings-testing-description = Parallélisme, délais d'attente et options de mode développeur pour l'exécuteur de tests.
admin-settings-parallel-tests = Tests en parallèle
admin-settings-test-timeout = Délai d'expiration des tests (ms)
admin-settings-test-timeout-help = Durée maximale d'un test sur une page avant qu'il ne soit interrompu.
admin-settings-max-test-workers = Nombre maximal de travailleurs de test
admin-settings-worker-stagger-seconds = Décalage entre travailleurs (secondes)
admin-settings-show-error-codes = Afficher les codes d'erreur dans les rapports
admin-settings-show-error-codes-help = Mode développeur — affiche les codes d'erreur bruts à côté des descriptions humaines.
admin-settings-pages-per-page = Pages par page (interface)
admin-settings-max-pages-per-page = Nombre maximal de pages par page (interface)

# Section Planificateur.
admin-settings-scheduler-heading = Planificateur
admin-settings-scheduler-description = Paramètres APScheduler pour les tests d'accessibilité récurrents/planifiés.
admin-settings-scheduler-enabled = Planificateur activé
admin-settings-scheduler-timezone = Fuseau horaire
admin-settings-scheduler-timezone-help = Nom de fuseau IANA (ex. America/Toronto). Affecte le déclenchement des tests planifiés.
admin-settings-scheduler-max-instances = Nombre maximal d'instances de tâche
admin-settings-scheduler-misfire-grace-time = Tolérance de retard (secondes)
admin-settings-scheduler-misfire-grace-time-help = Délai au-delà duquel une tâche en retard est ignorée.
admin-settings-scheduler-coalesce = Regrouper les exécutions manquées

# Section Réseau / Limitation.
admin-settings-network-heading = Réseau & Limitation de débit
admin-settings-network-description = Liste blanche CORS et limites de débit pour les points de terminaison publics.
admin-settings-cors-origins = Origines CORS autorisées
admin-settings-cors-origins-help = Liste d'origines séparées par des virgules. Laisser vide pour désactiver CORS.
admin-settings-ratelimit-default = Limite de débit par défaut
admin-settings-ratelimit-default-help = Expression Flask-Limiter, ex. « 60/minute ».

# Section SMTP.
admin-settings-smtp-heading = Courriel SMTP
admin-settings-smtp-description = Paramètres d'envoi de courriels utilisés pour les réinitialisations de mot de passe et les notifications transactionnelles.
admin-settings-smtp-host = Hôte
admin-settings-smtp-port = Port
admin-settings-smtp-username = Nom d'utilisateur
admin-settings-smtp-password = Mot de passe
admin-settings-smtp-use-tls = Utiliser TLS
admin-settings-smtp-from-email = Adresse expéditrice
admin-settings-smtp-from-name = Nom expéditeur

# Section Microsoft SSO.
admin-settings-microsoft-sso-heading = SSO Microsoft
admin-settings-microsoft-sso-description = Identifiants d'application Microsoft Entra ID / Azure AD.
admin-settings-microsoft-client-id = ID client
admin-settings-microsoft-client-secret = Secret client
admin-settings-microsoft-tenant-id = ID de tenant
admin-settings-microsoft-tenant-id-help = Utilisez « common » pour tout compte Microsoft, ou le GUID de votre tenant pour un accès limité à votre organisation.

# Section Google SSO.
admin-settings-google-sso-heading = SSO Google
admin-settings-google-sso-description = Identifiants client OAuth Google.
admin-settings-google-client-id = ID client
admin-settings-google-client-secret = Secret client

# Section PDF.
admin-settings-pdf-heading = Audit PDF
admin-settings-pdf-description = Chemins de stockage, limites de taille et binaires auxiliaires pour le vérificateur d'accessibilité PDF.
admin-settings-pdf-storage-dir = Répertoire de stockage
admin-settings-pdf-storage-dir-help = Emplacement où sont conservés les PDF téléchargés. Les chemins relatifs sont résolus par rapport à la racine du projet.
admin-settings-pdf-max-size-mb = Taille maximale d'un PDF (Mo)
admin-settings-pdf-download-timeout-seconds = Délai de téléchargement (secondes)
admin-settings-pdf-audit-max-parallel = Audits PDF parallèles maximum
