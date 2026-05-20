# audio — chaînes pour l'interface de téléversement et de traitement
# vidéo audioA11y (phase 7 du plan d'intégration audioA11y)

# === Formulaire de téléversement ===
audio-upload-heading                    = Téléverser une vidéo d'audit
audio-upload-intro                      = Téléversez une vidéo MP4 d'un audit d'accessibilité manuel. Le pipeline extraira l'audio, le transcrira, identifiera les intervenants, exécutera l'analyse de Claude et importera les résultats sous forme de problèmes d'enregistrement.
audio-upload-file-label                 = Fichier vidéo (MP4)
audio-upload-file-help                  = Sélectionnez le MP4 de la session d'audit. Maximum { $max } Mo.
audio-upload-project-label              = Projet
audio-upload-project-help               = À quel projet cet enregistrement doit-il être associé ?
audio-upload-title-label                = Titre de l'enregistrement
audio-upload-title-placeholder          = par ex. Audit lecteur d'écran du site principal
audio-upload-context-label              = Contexte de l'audit
audio-upload-context-audit              = Audit
audio-upload-context-lived-experience   = Expérience vécue
audio-upload-context-navilens           = NaviLens
audio-upload-languages-label            = Langues
audio-upload-languages-help             = Sélectionnez au moins une langue à analyser.
audio-upload-language-en                = Anglais
audio-upload-language-fr                = Français
audio-upload-speaker-remap-label        = Réassocier les intervenants entre les segments (recommandé)
audio-upload-extended-context-label     = Utiliser le contexte étendu Opus 1M
audio-upload-callouts-label             = Générer la vidéo avec annotations
audio-upload-submit-button              = Continuer vers l'estimation de coût
audio-upload-cancel-link                = Annuler

# === Étape de confirmation ===
audio-confirm-heading                   = Confirmer le traitement
audio-confirm-intro                     = Vérifiez l'estimation de coût ci-dessous, puis cliquez sur « Traiter maintenant » pour lancer le pipeline.
audio-confirm-estimated-cost            = Coût estimé
audio-confirm-duration                  = Durée de la vidéo
audio-confirm-breakdown-heading         = Détail du coût
audio-confirm-pricing-asof              = Tarifs valides au { $date } ; ils peuvent avoir changé depuis.
audio-confirm-process-button            = Traiter maintenant
audio-confirm-cancel-link               = Annuler

# === Panneau de coût ===
audio-cost-deepgram                     = Deepgram (transcription)
audio-cost-claude-en                    = Analyse Claude (anglais)
audio-cost-claude-fr                    = Analyse Claude (français)
audio-cost-total                        = Total

# === Erreurs ===
audio-error-no-language                 = Sélectionnez au moins une langue.
audio-error-file-required               = Choisissez un fichier MP4 à téléverser.
audio-error-file-too-large              = Vidéo trop volumineuse ; maximum { $max } Mo.
audio-error-invalid-mp4                 = Le fichier n'est pas un MP4 valide.
audio-error-no-project                  = Le projet est obligatoire.
audio-error-no-project-access           = Vous n'avez pas accès à ce projet.
audio-error-runner-not-configured       = Le processus vidéo n'est pas configuré sur le serveur.
audio-error-already-processing          = Cet enregistrement n'est pas à l'état « téléversé » et ne peut pas être traité.
