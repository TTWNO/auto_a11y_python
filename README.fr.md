# Auto A11y Python

Un outil complet de test d'accessibilité web qui combine des tests DOM automatisés avec une analyse visuelle alimentée par l'IA. Il s'agit d'un portage Python de l'application Node.js originale autoA11y.js, conservant les scripts de test JavaScript de base tout en ajoutant des fonctionnalités améliorées.

## Apercu

Auto A11y Python est une plateforme complète de test d'accessibilité web qui :
- Explore les sites web pour découvrir les pages automatiquement avec Pyppeteer
- Exécute des scripts de test d'accessibilité JavaScript éprouvés dans le contexte du navigateur
- Intègre l'IA Claude pour l'analyse visuelle d'accessibilité au-delà des tests DOM
- Offre une organisation par projet pour gérer plusieurs sites web
- Génère des rapports détaillés de conformité WCAG 2.1 dans plusieurs formats

## Fonctionnalités

- **Tests d'accessibilité automatisés** : Tests complets de conformité WCAG 2.1 utilisant les scripts de test JavaScript du projet autoA11y.js original
- **Analyse visuelle par IA** : Intégration optionnelle de l'IA Claude pour détecter les problèmes visuels que les tests DOM pourraient manquer
- **Exploration web intelligente** : Découverte automatique des pages avec respect du fichier robots.txt
- **Tests basés sur le navigateur** : Tests DOM réels utilisant Pyppeteer (portage Python de Puppeteer)
- **Gestion de projets** : Organisation des tests à travers plusieurs projets et sites web
- **Rapports complets** : Génération de rapports HTML, JSON, CSV et PDF
- **Traitement asynchrone** : Exécuteur de tâches en arrière-plan pour les opérations non bloquantes
- **Interface web** : Application Flask moderne avec interface Bootstrap 5

## Pile technologique

- **Backend** : Python 3.8+
- **Automatisation du navigateur** : Pyppeteer (Puppeteer pour Python)
- **Base de données** : MongoDB 4.0+
- **Intégration IA** : IA Claude par Anthropic
- **Framework web** : Flask avec Blueprints
- **Scripts de test** : JavaScript (conservés du projet autoA11y.js original)
- **File de tâches** : Exécuteur de tâches asynchrone pour les travaux en arrière-plan

## Prérequis

- Python 3.8 ou supérieur
- MongoDB 4.0 ou supérieur
- Navigateur Google Chrome ou Chromium

## Installation

```bash
# Cloner le dépôt
git clone https://github.com/[your-username]/auto_a11y_python.git
cd auto_a11y_python

# Créer l'environnement virtuel
python -m venv venv
source venv/bin/activate  # Sous Windows : venv\Scripts\activate

# Installer les dépendances
pip install -r requirements.txt

# Activer les hooks git (validation des traductions lors du commit)
git config core.hooksPath .githooks

# Configurer MongoDB (s'il n'est pas déjà en cours d'exécution)
# Installer MongoDB et démarrer le service
# Connexion par défaut : mongodb://localhost:27017/

# Configurer l'application
cp config.example.py config.py
# Modifier config.py avec vos paramètres

# Exécuter la configuration initiale
python run.py --setup
```

Le processus de configuration va :
- Créer les répertoires nécessaires
- Configurer les index de la base de données
- Télécharger le navigateur Chromium pour Pyppeteer
- Créer un projet exemple pour démarrer

## Docker

Auto A11y inclut un fichier `docker-compose.yml` qui démarre
l'application et MongoDB ensemble. Avec Docker (20.10+) ou Podman
(4.1+) installé :

```bash
docker compose up
# ou : podman-compose up
```

L'interface est ensuite accessible à l'adresse http://localhost:5001.

### Tester des serveurs locaux sur l'hôte

Pour exécuter des tests d'accessibilité sur un serveur HTTP qui tourne
sur **votre machine hôte** (par exemple un serveur de développement
sur le port `8080`), utilisez le nom d'hôte `host.docker.internal` à
la place de `localhost` :

| Sur l'hôte | À saisir dans Auto A11y |
|---|---|
| `http://localhost:80` | `http://host.docker.internal` |
| `http://localhost:8080` | `http://host.docker.internal:8080` |
| `http://localhost:8000` | `http://host.docker.internal:8000` |

Ce comportement est activé par l'entrée `extra_hosts` du fichier
`docker-compose.yml`. Aucune configuration supplémentaire n'est
requise. Si le serveur de l'hôte écoute uniquement sur `127.0.0.1`,
reconfigurez-le pour écouter sur `0.0.0.0` afin que le conteneur
puisse l'atteindre.

## Configuration

Modifiez `config.py` pour personnaliser vos paramètres :

```python
# Paramètres MongoDB
MONGO_URI = "mongodb://localhost:27017/"
DATABASE_NAME = "auto_a11y"

# Paramètres du serveur
HOST = "127.0.0.1"
PORT = 5000
DEBUG = False

# Paramètres IA (optionnel)
RUN_AI_ANALYSIS = False  # Mettre à True pour activer l'IA Claude
CLAUDE_API_KEY = "your-anthropic-api-key"

# Répertoires
SCREENSHOTS_DIR = "screenshots"
REPORTS_DIR = "reports"

# Paramètres d'exploration
MAX_DEPTH = 3
MAX_PAGES_PER_WEBSITE = 100
```

### Courriel SMTP / Réinitialisation du mot de passe (optionnel)

La réinitialisation du mot de passe par courriel est optionnelle. Si les variables d'environnement SMTP sont laissées vides, le lien « Mot de passe oublié ? » est masqué de la page de connexion et les routes de réinitialisation retournent un code 404. Les administrateurs verront toujours la carte « Réinitialisation du mot de passe par courriel » sur la page de modification d'utilisateur, mais le bouton est désactivé avec un indice de configuration.

Ajoutez les éléments suivants à votre `.env` :

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD=your-smtp-password
SMTP_USE_TLS=True
SMTP_FROM_EMAIL=noreply@example.com
SMTP_FROM_NAME=CNIB Access Labs | AutoA11y
```

| Variable | Requis | Défaut | Description |
|---|---|---|---|
| `SMTP_HOST` | Oui | *(vide)* | Nom d'hôte du serveur SMTP |
| `SMTP_PORT` | Non | `587` | Port du serveur SMTP |
| `SMTP_USERNAME` | Non | *(vide)* | Nom d'utilisateur d'authentification SMTP (omettre si le relais ne nécessite pas d'authentification) |
| `SMTP_PASSWORD` | Non | *(vide)* | Mot de passe d'authentification SMTP |
| `SMTP_USE_TLS` | Non | `True` | Utiliser STARTTLS |
| `SMTP_FROM_EMAIL` | Oui | *(vide)* | Adresse courriel de l'expéditeur |
| `SMTP_FROM_NAME` | Non | `CNIB Access Labs \| AutoA11y` | Nom d'affichage de l'expéditeur |

`SMTP_HOST` et `SMTP_FROM_EMAIL` doivent tous deux être définis pour que le courriel soit activé. Une fois configuré :

- Un lien **« Mot de passe oublié ? »** apparaît sur la page de connexion, permettant aux utilisateurs de demander un lien de réinitialisation (valide pendant 15 minutes).
- Les administrateurs peuvent envoyer un courriel de réinitialisation depuis la **page de modification d'utilisateur** (`/auth/users/<id>/edit`).

### SSO Microsoft 365 (optionnel)

La page de connexion prend en charge « Se connecter avec Microsoft » pour les utilisateurs clients. Ceci est optionnel — si les variables d'environnement sont laissées vides, le bouton SSO est masqué et seule la connexion par courriel/mot de passe est disponible.

#### 1. Enregistrer une application dans Azure AD

1. Rendez-vous sur le [portail Azure](https://portal.azure.com/) et naviguez vers **Microsoft Entra ID** (anciennement Azure Active Directory) > **Inscriptions d'applications** > **Nouvelle inscription**.
2. Remplissez les champs suivants :
   - **Nom** : ce que vous voulez (par ex. « Auto A11y Public »).
   - **Types de comptes pris en charge** : choisissez **Comptes dans n'importe quel annuaire organisationnel (tout locataire Microsoft Entra ID - Multilocataire)** pour permettre les utilisateurs de n'importe quelle organisation Microsoft 365, ou choisissez **Locataire unique** si vous ne voulez que les utilisateurs de votre propre organisation.
   - **URI de redirection** : sélectionnez **Web** et entrez votre URL de rappel. Pour le développement local, c'est `http://localhost:5001/auth/microsoft/callback`. Pour la production, utilisez votre vrai domaine (par ex. `https://reports.example.com/auth/microsoft/callback`).
3. Cliquez sur **Inscrire**.

#### 2. Collecter les valeurs nécessaires

Après l'inscription, sur la page **Vue d'ensemble** de l'application :

| Valeur | Où la trouver | Variable `.env` |
|---|---|---|
| ID d'application (client) | Page Vue d'ensemble, en haut | `MICROSOFT_CLIENT_ID` |
| ID d'annuaire (locataire) | Page Vue d'ensemble, en haut (nécessaire uniquement pour un locataire unique ; utiliser `common` pour multilocataire) | `MICROSOFT_TENANT_ID` |

#### 3. Créer un secret client

1. Allez dans **Certificats et secrets** > **Secrets client** > **Nouveau secret client**.
2. Donnez-lui une description et une période d'expiration, puis cliquez sur **Ajouter**.
3. Copiez la **Valeur** immédiatement (elle n'est affichée qu'une seule fois).

| Valeur | Variable `.env` |
|---|---|
| Valeur du secret client | `MICROSOFT_CLIENT_SECRET` |

#### 4. Permissions de l'API

L'inscription par défaut accorde déjà la permission déléguée `User.Read` sous Microsoft Graph, ce qui est tout ce qui est nécessaire. Vérifiez cela sous **Permissions de l'API** — vous devriez voir `Microsoft Graph > User.Read` listé. Aucun consentement administrateur n'est requis pour cette permission.

#### 5. Ajouter à votre `.env`

```bash
MICROSOFT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MICROSOFT_CLIENT_SECRET=your-secret-value
MICROSOFT_TENANT_ID=common
```

Définissez `MICROSOFT_TENANT_ID` à `common` (la valeur par défaut) pour le multilocataire, ou à votre ID de locataire spécifique pour le locataire unique.

#### 6. Vérifier

Démarrez l'application et visitez `/auth/login`. Le bouton « Se connecter avec Microsoft » devrait apparaître au-dessus du formulaire courriel/mot de passe.

### SSO Google (optionnel)

La page de connexion prend également en charge « Se connecter avec Google ». Comme le SSO Microsoft, ceci est optionnel — si les variables d'environnement sont laissées vides, le bouton Google est masqué.

#### 1. Créer un projet dans Google Cloud Console

1. Rendez-vous sur la [Google Cloud Console](https://console.cloud.google.com/) et créez un nouveau projet (ou sélectionnez un projet existant).
2. Naviguez vers **APIs et services** > **Écran de consentement OAuth**.
3. Choisissez le type d'utilisateur **Externe** (permet n'importe quel compte Google) ou **Interne** (restreint à votre organisation Google Workspace uniquement). Cliquez sur **Créer**.
4. Remplissez les champs requis :
   - **Nom de l'application** : ce que vous voulez (par ex. « Auto A11y Public »).
   - **Adresse e-mail d'assistance utilisateur** : votre adresse courriel.
   - **Coordonnées du développeur** : votre adresse courriel.
5. Cliquez sur **Enregistrer et continuer**.
6. Sur l'écran **Champs d'application**, cliquez sur **Ajouter ou supprimer des champs d'application** et ajoutez :
   - `openid`
   - `email`
   - `profile`
7. Cliquez sur **Enregistrer et continuer** sur les écrans restants.

#### 2. Créer des identifiants OAuth

1. Naviguez vers **APIs et services** > **Identifiants**.
2. Cliquez sur **Créer des identifiants** > **ID client OAuth**.
3. Définissez le **Type d'application** à **Application Web**.
4. Donnez-lui un nom (par ex. « Auto A11y Public »).
5. Sous **URI de redirection autorisés**, ajoutez votre URL de rappel. Pour le développement local, c'est `http://localhost:5001/auth/google/callback`. Pour la production, utilisez votre vrai domaine (par ex. `https://reports.example.com/auth/google/callback`).
6. Cliquez sur **Créer**.

#### 3. Collecter les valeurs nécessaires

Après la création, une boîte de dialogue affiche vos identifiants :

| Valeur | Variable `.env` |
|---|---|
| ID client | `GOOGLE_CLIENT_ID` |
| Secret client | `GOOGLE_CLIENT_SECRET` |

Vous pouvez également les retrouver plus tard sous **APIs et services** > **Identifiants** en cliquant sur le client OAuth que vous avez créé.

#### 4. Ajouter à votre `.env`

```bash
GOOGLE_CLIENT_ID=xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxxxxxx
```

#### 5. Vérifier

Démarrez l'application et visitez `/auth/login`. Le bouton « Se connecter avec Google » devrait apparaître au-dessus du formulaire courriel/mot de passe.

## Utilisation

### Démarrage de l'application

```bash
# Exécuter avec les paramètres par défaut
python run.py

# Exécuter avec des options
python run.py --host 0.0.0.0 --port 8080 --debug

# Autres commandes
python run.py --test-db          # Tester la connexion à la base de données
python run.py --download-browser  # Télécharger Chromium
python run.py --setup            # Exécuter la configuration initiale
```

### Interface web

Ouvrez votre navigateur à l'adresse `http://localhost:5000`

#### Flux de travail :

1. **Créer un projet** : Organisez vos efforts de test
2. **Ajouter des sites web** : Ajoutez des sites web à tester dans les projets
3. **Découvrir les pages** : Explorez et découvrez automatiquement les pages
4. **Exécuter les tests** : Lancez les tests d'accessibilité sur les pages
5. **Générer des rapports** : Exportez des rapports de conformité détaillés

## Scripts de test JavaScript

Les tests d'accessibilité de base sont des modules JavaScript exécutés dans le contexte du navigateur :

- **headings.js** : Hiérarchie et structure des titres
- **images.js** : Texte alternatif et images décoratives
- **forms.js et forms2.js** : Étiquettes de formulaires et accessibilité
- **landmarks.js** : Points de repère et régions ARIA
- **colorContrast.js** : Ratios de contraste des couleurs WCAG
- **focus.js** : Navigation au clavier et focus
- **language.js** : Déclarations de langue
- **pageTitle.js** : Exigences du titre de page
- **tabindex.js** : Ordre de tabulation et accès au clavier
- **ariaRoles.js** : Validation des attributs ARIA
- **svg.js** : Accessibilité SVG
- **pdf.js** : Détection des liens PDF

## Fonctionnalités d'analyse par IA

Lorsque l'IA Claude est activée, le système détecte :

- **Titres visuels** : Texte qui ressemble à des titres mais qui manque de balisage sémantique
- **Ordre de lecture** : Décalages entre l'ordre visuel et l'ordre du DOM
- **Accessibilité des fenêtres modales** : Problèmes avec les dialogues et les superpositions
- **Changements de langue** : Contenu en langue étrangère non balisé
- **Mouvement et animation** : Animations problématiques
- **Éléments interactifs** : Contrôles personnalisés manquant d'ARIA

## Structure du projet

```
auto_a11y_python/
├── auto_a11y/
│   ├── core/            # Fonctionnalités de base
│   │   ├── browser_manager.py
│   │   ├── database.py
│   │   └── scraper.py
│   ├── models/          # Modèles de données
│   ├── scripts/         # Fichiers de test JavaScript
│   │   └── tests/       # Modules de test individuels
│   ├── testing/         # Exécuteur de tests
│   │   ├── test_runner.py
│   │   ├── script_injector.py
│   │   └── result_processor.py
│   ├── ai/              # Intégration de l'IA Claude
│   │   ├── claude_analyzer.py
│   │   └── analysis_modules.py
│   ├── reporting/       # Génération de rapports
│   │   ├── report_generator.py
│   │   └── formatters.py
│   └── web/            # Application Flask
│       ├── app.py
│       └── routes/
├── templates/          # Gabarits HTML
├── static/            # CSS, JS, images
├── docs/              # Documentation
├── Fixtures/          # Fixtures de test pour validation
├── config.py          # Configuration
├── test_fixtures.py   # Script d'exécution des fixtures
└── run.py            # Point d'entrée
```

## Tests

### Tests de fixtures

Auto A11y inclut un système complet de tests de fixtures pour valider que les tests d'accessibilité fonctionnent correctement avant leur déploiement en production. Pour la documentation complète :

- **[Guide des tests de fixtures](docs/FIXTURE_TESTING.md)** - Guide complet avec flux de travail et exemples
- **[Référence rapide](FIXTURE_TESTING_QUICKREF.md)** - Aide-mémoire de commandes pour l'utilisation quotidienne

**Démarrage rapide :**
```bash
# Tester toutes les fixtures (environ 1 heure pour environ 900 fixtures)
python test_fixtures.py

# Tester uniquement les fixtures de découverte (environ 5 minutes)
python test_fixtures.py --type Disco

# Tester une catégorie spécifique
python test_fixtures.py --category Images

# Tester un code d'erreur spécifique
python test_fixtures.py --code ErrNoAlt

# Combiner les filtres
python test_fixtures.py --type Err --category Headings --limit 10
```

Seuls les tests qui réussissent TOUTES leurs fixtures sont activés en production. Consultez l'état des fixtures à l'adresse `http://localhost:5001/testing/fixture-status`

## Vérification des types

Ce projet impose une vérification stricte des types au moyen de trois outils complémentaires : **mypy**, **pyright** et **ty**. Les trois doivent réussir à chaque commit et dans la CI.

### Configuration unique (par clone)

Après le clonage :
```bash
python run.py --install-hooks
```

Cela configure `git` pour utiliser le répertoire `.githooks/` du dépôt. Le crochet pre-commit exécute les trois vérificateurs de types sur les chemins ciblés et bloque le commit en cas d'échec.

### Exécution manuelle des vérifications

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
```

### Politique

Aucun commentaire `# type: ignore` ni équivalent. Aucun contournement `cast(Any, ...)`. Aucun `git commit --no-verify`. Les erreurs de typage doivent être corrigées, pas supprimées. Voir [CLAUDE.md](./CLAUDE.md#type-checking-mandatory) pour la politique complète.

## Utilisation de l'API

L'application fournit des points d'accès API REST pour l'automatisation :

```python
# Exemple : Tester une page
POST /api/pages/{page_id}/test
{
    "include_ai": true,
    "take_screenshot": true
}

# Exemple : Générer un rapport
POST /api/reports/generate
{
    "type": "website",
    "id": "website_id",
    "format": "html",
    "include_ai": true
}

# Exemple : Découvrir les pages
POST /api/websites/{website_id}/discover
{
    "max_depth": 3,
    "max_pages": 50
}
```

## Dépannage

### Problèmes courants

1. **Erreur de connexion MongoDB**
   ```bash
   # S'assurer que MongoDB est en cours d'exécution
   mongod
   # Tester la connexion
   python run.py --test-db
   ```

2. **Échec du téléchargement du navigateur**
   ```bash
   # Téléchargement manuel
   python run.py --download-browser
   # Ou installer Chrome/Chromium au niveau du système
   ```

3. **Scripts de test JavaScript non chargés**
   - Vérifier que tous les fichiers existent dans `/auto_a11y/scripts/tests/`
   - Examiner la console du navigateur pour les erreurs
   - S'assurer que Pyppeteer est correctement installé

4. **Analyse par IA non fonctionnelle**
   - Vérifier la clé API Claude dans config.py
   - Vérifier les limites de débit de l'API
   - S'assurer que `RUN_AI_ANALYSIS = True` dans la configuration

## Migration

Exportez et importez la base de données MongoDB entre les environnements (par ex. cloud <-> local) en utilisant `mongodump` et `mongorestore`.

### Exportation (dump) depuis la source

```bash
# Depuis MongoDB local (par défaut)
mongodump --db auto_a11y --out ./dump

# Depuis un MongoDB distant/cloud (avec chaîne de connexion)
mongodump --uri "mongodb+srv://user:password@cluster.example.net/" --db auto_a11y --out ./dump
```

Cela crée un répertoire `./dump/auto_a11y/` contenant les fichiers BSON pour chaque collection.

### Importation (restauration) vers la cible

```bash
# Vers MongoDB local (par défaut)
mongorestore --db auto_a11y --drop ./dump/auto_a11y

# Vers un MongoDB distant/cloud
mongorestore --uri "mongodb+srv://user:password@cluster.example.net/" --db auto_a11y --drop ./dump/auto_a11y
```

L'option `--drop` supprime chaque collection avant la restauration, assurant une importation propre. Omettez-la pour fusionner avec les données existantes.

### Flux de travail courants

```bash
# Cloud -> Local
mongodump --uri "$CLOUD_MONGODB_URI" --db auto_a11y --out ./dump
mongorestore --db auto_a11y --drop ./dump/auto_a11y

# Local -> Cloud
mongodump --db auto_a11y --out ./dump
mongorestore --uri "$CLOUD_MONGODB_URI" --db auto_a11y --drop ./dump/auto_a11y
```

Si le nom de votre base de données diffère de la valeur par défaut `auto_a11y`, remplacez-le par la valeur de `DATABASE_NAME` de votre `.env` ou `config.py`.

### Prérequis

Installez les [outils de base de données MongoDB](https://www.mongodb.com/docs/database-tools/installation/installation/) (`mongodump` et `mongorestore`). Ceux-ci sont séparés du serveur MongoDB et doivent être installés individuellement sur la plupart des systèmes.

## Compilation de l'application de bureau

Auto A11y peut être empaqueté en tant qu'application de bureau autonome avec Electron. La compilation de bureau inclut un environnement d'exécution Python portable, une instance MongoDB intégrée et Playwright Chromium — aucune dépendance externe n'est requise sur la machine cible.

### Architecture

Le shell Electron (`electron/main.js`) agit comme une enveloppe légère :

1. Démarre une instance **MongoDB** intégrée (`mongod`)
2. Démarre le serveur **Flask** en utilisant un Python portable inclus
3. Ouvre une fenêtre de navigateur pointant vers le serveur Flask local
4. Arrête proprement les deux services à la fermeture

Les quatre composants (serveur, base de données, navigateur, LLM) sont commutables entre interne (inclus) et externe (fourni par l'utilisateur) via un panneau de paramètres dans l'application. Les paramètres sont stockés dans le répertoire standard de données utilisateur de la plateforme.

### Prérequis (toutes les plateformes)

- **Node.js 18+** et **npm**
- **curl** (pour télécharger Python portable et MongoDB)
- Une connexion Internet pendant la compilation (pour télécharger les dépendances)

### Linux (AppImage)

```bash
# 1. Installer les dépendances Electron (première fois seulement)
cd electron
npm install
cd ..

# 2. Exécuter le script de compilation
chmod +x build/build-linux.sh
./build/build-linux.sh
```

Le script :
1. Télécharge [Python 3.12 portable](https://github.com/indygreg/python-build-standalone) (x86_64)
2. Installe les dépendances pip depuis `requirements.txt` dans le Python portable
3. Télécharge MongoDB 7.0 Community (uniquement le binaire `mongod`)
4. Télécharge Playwright Chromium via le Python portable
5. Copie le code source de l'application dans `build/staging/`
6. Empaquette le tout avec `electron-builder` en AppImage

**Sortie :** `electron/dist/Auto A11y-<version>.AppImage`

Exécutez l'AppImage directement — aucune installation nécessaire :
```bash
chmod +x "electron/dist/Auto A11y-1.0.0.AppImage"
./"electron/dist/Auto A11y-1.0.0.AppImage"
```

### macOS (DMG)

```bash
# 1. Installer les dépendances natives pour la génération de PDF WeasyPrint
brew install cairo pango gdk-pixbuf gobject-introspection libffi

# 2. Installer les dépendances Electron (première fois seulement)
cd electron
npm install
cd ..

# 3. Exécuter le script de compilation
chmod +x build/build-mac.sh
./build/build-mac.sh
```

La compilation macOS comporte une étape supplémentaire par rapport à Linux : elle inclut les bibliothèques natives WeasyPrint (dylibs) de Homebrew dans l'application et réécrit leurs noms d'installation avec `@loader_path` afin que l'application fonctionne sans Homebrew sur la machine cible. Un script `python3.12-wrapper` définit `DYLD_LIBRARY_PATH` à l'exécution.

Le script détecte automatiquement l'architecture (`arm64` pour Apple Silicon, `x86_64` pour Intel).

**Sortie :** `electron/dist/Auto A11y-<version>.dmg`

### Windows

Il n'existe pas encore de script de compilation pour Windows. Le shell Electron et le gestionnaire de processus gèrent déjà les chemins Windows (y compris `taskkill` pour le nettoyage des processus), mais le pipeline de compilation n'a pas été implémenté.

Un script de compilation Windows devrait :
1. Télécharger [Python portable pour Windows](https://github.com/indygreg/python-build-standalone) (`x86_64-pc-windows-msvc`)
2. Installer les dépendances pip
3. Télécharger [MongoDB Community pour Windows](https://www.mongodb.com/try/download/community) (uniquement `mongod.exe`)
4. Télécharger Playwright Chromium
5. Gérer la dépendance d'exécution GTK3 de WeasyPrint sous Windows (par ex., inclure depuis MSYS2/vcpkg ou utiliser le wheel `weasyprint` avec les DLL incluses)
6. Empaqueter avec `electron-builder --win` (produit un installateur NSIS ou un EXE portable)

### Mode développement

Pour exécuter le shell Electron en mode développement (en utilisant le MongoDB de votre système et l'environnement virtuel Python du projet au lieu des binaires inclus) :

```bash
cd electron
npm install   # première fois seulement
./dev-start.sh
# ou : npx electron . --dev
```

Cela nécessite que MongoDB et l'environnement virtuel Python (`.venv/`) soient déjà configurés sur votre machine. Le gestionnaire de processus se rabat sur le `mongod` installé sur le système et `.venv/bin/python` lorsque les binaires inclus ne sont pas trouvés.

### Structure de sortie de la compilation

L'application empaquetée inclut ces ressources aux côtés du binaire Electron :

```
resources/
├── app/            # Code source de l'application (auto_a11y/, config.py, run.py, Fixtures/)
├── python/         # Python 3.12 portable + dépendances pip
│   └── bin/
│       ├── python3.12
│       └── python3.12-wrapper  # macOS uniquement : définit DYLD_LIBRARY_PATH
├── mongodb/
│   └── bin/
│       └── mongod              # Binaire du serveur MongoDB 7.0
└── chromium/                   # Navigateur Chromium géré par Playwright
```

### Données utilisateur

À l'exécution, l'application stocke ses données dans le répertoire de données utilisateur de la plateforme :

| Plateforme | Emplacement |
|----------|----------|
| Linux | `~/.config/auto-a11y/` |
| macOS | `~/Library/Application Support/auto-a11y/` |
| Windows | `%APPDATA%\auto-a11y\` |

Contenu : `settings.json`, `mongodb/data/` (fichiers de la base de données), `logs/`, `reports/`, `screenshots/`.

## Système de couleurs

Cette application utilise un **système de jetons de design personnalisés** pour toutes les couleurs. Les classes utilitaires de couleur Bootstrap (par ex. `btn-primary`, `bg-danger`, `text-warning`) **ne sont pas utilisées** — à la place, des classes personnalisées sont directement liées aux jetons de design définis dans `auto_a11y/web/static/public/css/tokens.css`.

Cela donne un contrôle total sur toutes les couleurs en mode clair, mode sombre et impression, avec une conformité WCAG 2.2 AA intégrée. Consultez la section `Colour System` dans `CLAUDE.md` pour la référence complète des classes.

**Fichiers clés :**
- `auto_a11y/web/static/public/css/tokens.css` — jetons de design (toutes les valeurs de couleur)
- `auto_a11y/web/static/css/style.css` — classes utilitaires personnalisées

## Contribuer

Les contributions sont les bienvenues ! Veuillez :

1. Forker le dépôt
2. Créer une branche de fonctionnalité (`git checkout -b feature/fonctionnalite-geniale`)
3. Valider vos modifications (`git commit -m 'Ajouter une fonctionnalité géniale'`)
4. Pousser la branche (`git push origin feature/fonctionnalite-geniale`)
5. Ouvrir une Pull Request

## Licence

Licence publique générale GNU v3.0

## Auteur

Bob Dodd

## Remerciements

- Application Node.js originale autoA11y.js pour la suite de tests JavaScript
- Équipe Pyppeteer pour le portage Python de Puppeteer
- IA Claude par Anthropic pour l'analyse visuelle d'accessibilité
- Communautés Flask et MongoDB

## Soutien

Pour les problèmes, questions ou contributions, veuillez utiliser le système de suivi des problèmes GitHub.
