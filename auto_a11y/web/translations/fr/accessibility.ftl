# Page de déclaration d'accessibilité
# Les identifiants de messages utilisent le préfixe « a11y-statement- ».

a11y-statement-page-title = Déclaration d'accessibilité
a11y-statement-heading = Déclaration d'accessibilité
a11y-statement-intro = L'Institut national canadien pour les aveugles (INCA) s'engage à garantir l'accessibilité numérique aux personnes en situation de handicap. Comme Auto A11y est elle-même une plateforme de test d'accessibilité, nous soumettons sa propre interface aux normes qu'elle évalue. Cette déclaration décrit l'accessibilité d'Auto A11y, les mesures que nous prenons pour la maintenir accessible, ses limites connues et la façon de nous transmettre vos commentaires.
a11y-statement-last-reviewed-label = Dernière révision
a11y-statement-last-reviewed-date = 27 mai 2026

# État de conformité
a11y-statement-conformance-heading = État de conformité
a11y-statement-conformance-standard = Auto A11y vise la conformité aux Règles pour l'accessibilité des contenus Web (WCAG) 2.2 au niveau AA. Les WCAG définissent les exigences que les concepteurs et les développeurs doivent respecter pour améliorer l'accessibilité aux personnes en situation de handicap.
a11y-statement-conformance-level = Auto A11y est partiellement conforme aux WCAG 2.2 niveau AA. « Partiellement conforme » signifie que certaines parties du contenu ne sont pas encore entièrement conformes à la norme d'accessibilité; les points précis sont énumérés ci-dessous sous Limites connues.
a11y-statement-conformance-note = Nous considérons tout écart entre notre interface et les WCAG 2.2 AA comme un défaut à corriger, et non comme un compromis acceptable, et nous en priorisons la correction.

# Mesures de soutien à l'accessibilité
a11y-statement-measures-heading = Mesures que nous prenons pour soutenir l'accessibilité
a11y-statement-measures-intro = L'accessibilité est prise en compte dès la phase de conception de chaque modification, et non ajoutée après coup. Concrètement, nous :
a11y-statement-measures-tokens = Utilisons un système de couleurs fondé sur des jetons de conception uniques dans lequel tout le texte présente un rapport de contraste d'au moins 4,5:1 et tous les composants d'interface non textuels d'au moins 3:1, en mode clair comme en mode sombre (WCAG 2.2 critères 1.4.3 et 1.4.11).
a11y-statement-measures-contrast-lint = Exécutons à chaque validation un vérificateur automatisé de contraste et d'usage des couleurs qui bloque toute modification introduisant un contraste insuffisant, des classes de couleur interdites, la suppression d'un indicateur de focus ou un texte de moins de 12 pixels.
a11y-statement-measures-semantics = Privilégions les éléments HTML natifs (boutons, liens, champs de formulaire, boîtes de dialogue) plutôt que des composants personnalisés, et n'ajoutons ARIA que lorsqu'aucun élément natif ne convient.
a11y-statement-measures-keyboard = Veillons à ce que chaque élément interactif soit accessible et utilisable au clavier seul, avec un indicateur de focus visible en tout temps.
a11y-statement-measures-live-regions = Annonçons les changements dynamiques — chargement, erreurs de validation, notifications éphémères et éléments qui apparaissent ou disparaissent — au moyen de régions actives afin qu'ils soient transmis aux personnes utilisant un lecteur d'écran.
a11y-statement-measures-bilingual = Offrons l'interface complète en français et en anglais.
a11y-statement-measures-reflow = Prenons en charge le redimensionnement et la redistribution du contenu afin que la mise en page fonctionne à un zoom de 200 % et jusqu'à une fenêtre de 320 pixels sans perte de contenu ni défilement horizontal.

# Compatibilité
a11y-statement-compatibility-heading = Compatibilité avec les navigateurs et les technologies d'assistance
a11y-statement-compatibility-intro = Auto A11y est conçue pour être compatible avec les versions récentes des éléments suivants :
a11y-statement-compatibility-browsers = Les versions actuelles de Firefox, Chrome, Edge et Safari sur ordinateur de bureau.
a11y-statement-compatibility-screenreaders = NVDA et JAWS avec Firefox ou Chrome sous Windows, VoiceOver avec Safari sous macOS et iOS, et Orca avec Firefox sous Linux.
a11y-statement-compatibility-note = Auto A11y peut ne pas fonctionner de façon optimale avec des versions de navigateur ou de technologie d'assistance antérieures aux deux dernières versions majeures.

# Spécifications techniques
a11y-statement-tech-heading = Spécifications techniques
a11y-statement-tech-intro = L'accessibilité d'Auto A11y repose sur les technologies suivantes pour fonctionner avec votre navigateur et les technologies d'assistance installées :
a11y-statement-tech-list = HTML, WAI-ARIA, CSS et JavaScript.
a11y-statement-tech-note = Ces technologies sont requises pour la conformité aux WCAG 2.2 niveau AA.

# Limites connues
a11y-statement-limitations-heading = Limites connues
a11y-statement-limitations-intro = Malgré nos efforts, certaines parties d'Auto A11y peuvent comporter des limites. Voici les problèmes connus que nous travaillons à résoudre :
a11y-statement-limitations-pdf = Les rapports PDF générés sont produits par un moteur de rendu externe et n'ont pas encore été audités selon la même norme que l'interface Web. Utilisez le format de rapport HTML pour obtenir la sortie la plus accessible.
a11y-statement-limitations-screenshots = Les captures d'écran des pages testées sont des artefacts visuels des sites évalués; leur accessibilité reflète le site source et non Auto A11y, et il n'est pas toujours possible de générer automatiquement un texte de remplacement pertinent pour des pages quelconques.
a11y-statement-limitations-tested-content = Les rapports décrivent et citent le code HTML des pages que vous testez. Ce contenu tiers est reproduit tel quel à des fins de diagnostic et n'est pas modifié pour le rendre accessible.
a11y-statement-limitations-manual-testing = Les vérifications automatisées de l'interface, ainsi que le moteur de test lui-même, ne remplacent pas les tests manuels avec lecteur d'écran. Certains composants interactifs n'ont pas encore été validés de bout en bout avec toutes les technologies d'assistance prises en charge.
a11y-statement-limitations-charts = Certaines visualisations de données et certains graphiques transmettent l'information de façon principalement visuelle; nous ajoutons des équivalents textuels et tabulaires.

# Évaluation
a11y-statement-assessment-heading = Comment nous évaluons l'accessibilité
a11y-statement-assessment-text = L'INCA a évalué l'accessibilité d'Auto A11y par auto-évaluation, en combinant le vérificateur automatisé de contraste et de couleurs du projet, la revue de code assortie de points de contrôle d'accessibilité et les tests manuels au clavier et avec lecteurs d'écran.

# Commentaires et coordonnées
a11y-statement-feedback-heading = Commentaires et coordonnées
a11y-statement-feedback-intro = Nous accueillons vos commentaires sur l'accessibilité d'Auto A11y. Si vous rencontrez un obstacle ou avez besoin d'information dans un format de remplacement, faites-le-nous savoir :
a11y-statement-feedback-email-label = Courriel
a11y-statement-feedback-email = accessibility@cnib.ca
a11y-statement-feedback-org-label = Organisation
a11y-statement-feedback-org = Laboratoires d'accessibilité de l'INCA
a11y-statement-feedback-response = Nous nous efforçons de répondre aux commentaires sur l'accessibilité dans un délai de cinq jours ouvrables.

# Barre latérale
a11y-statement-sidebar-heading = En bref
a11y-statement-sidebar-target = Norme visée
a11y-statement-sidebar-target-value = WCAG 2.2 niveau AA
a11y-statement-sidebar-status = État
a11y-statement-sidebar-status-value = Partiellement conforme
a11y-statement-related-heading = Pages connexes
