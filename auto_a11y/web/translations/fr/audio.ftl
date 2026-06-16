# audio — chaînes pour l'interface de téléversement et de traitement
# vidéo audioA11y (phase 7 du plan d'intégration audioA11y)

# === Formulaire de téléversement ===
audio-upload-heading                    = Téléverser une vidéo d'audit
audio-upload-intro                      = Téléversez une vidéo d'un audit d'accessibilité manuel. Le pipeline extraira l'audio, le transcrira, identifiera les intervenants, exécutera l'analyse de Claude et importera les résultats sous forme de problèmes d'enregistrement.
audio-upload-file-label                 = Fichier vidéo
audio-upload-file-help                  = Sélectionnez la vidéo de la session d'audit (MP4, MOV, WebM, etc.). Maximum { $max } Mo.
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
audio-upload-about-heading              = À propos de l'analyse vidéo
audio-upload-import-json-instead        = Importer plutôt un fichier JSON Dictaphone

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
audio-error-file-required               = Choisissez un fichier vidéo à téléverser.
audio-error-file-too-large              = Vidéo trop volumineuse ; maximum { $max } Mo.
audio-error-invalid-mp4                 = Le fichier n'est pas une vidéo valide.
audio-error-no-project                  = Le projet est obligatoire.
audio-error-no-project-access           = Vous n'avez pas accès à ce projet.
audio-error-runner-not-configured       = Le processus vidéo n'est pas configuré sur le serveur.
audio-error-already-processing          = Cet enregistrement n'est pas à l'état « téléversé » et ne peut pas être traité.

# === Étiquettes d'étape du pipeline ===
audio-stage-segmenting                  = Extraction audio
audio-stage-transcribing                = Transcription
audio-stage-speaker-remap               = Identification des intervenants
audio-stage-merging-vtt                 = Fusion des sous-titres
audio-stage-analyzing                   = Analyse
audio-stage-callouts                    = Génération de la vidéo avec annotations
audio-stage-importing                   = Importation des problèmes

# === Étiquettes d'état d'enregistrement ===
audio-status-uploaded                   = Téléversé
audio-status-processing                 = En cours de traitement
audio-status-complete                   = Terminé
audio-status-failed                     = Échec
audio-status-cancelling                 = Annulation en cours
audio-status-cancelled                  = Annulé

# === Carte de progression (phase 8) ===
audio-progress-elapsed                  = Temps écoulé
audio-progress-running-cost             = Coût jusqu'à présent
audio-progress-cancel                   = Annuler
audio-progress-aria-label               = Progression du traitement : étape { $stage }, { $current } sur { $total }

# === Page de détail (phase 8) ===
audio-detail-failed-heading             = Échec du traitement
audio-detail-cost-panel-heading         = Détail du coût
audio-detail-cost-estimated-was         = Coût estimé initialement

# === Vidéo avec annotations (phase 9) ===
audio-callouts-download                 = Télécharger la vidéo annotée
audio-callouts-failed                   = La génération de la vidéo annotée a échoué ; le reste de l'audit s'est terminé avec succès.
audio-callouts-pending                  = Génération de la vidéo annotée en cours…

# === Récupération des paramètres (phase 10) ===
recovery-title                          = Récupération des paramètres
recovery-intro                          = Certains paramètres de configuration sont manquants ou incorrects. Corrigez les éléments signalés ci-dessous pour démarrer l'application.
recovery-failed-checks-heading          = Vérifications échouées
recovery-settings-form-heading          = Paramètres
recovery-check-failed                   = Échec
recovery-check-passed                   = Réussi
recovery-test-button                    = Tester
recovery-test-working                   = En cours…
recovery-test-success                   = OK
recovery-test-failed                    = Ne fonctionne pas
recovery-save                           = Enregistrer
recovery-save-success                   = Paramètres enregistrés. Veuillez quitter et rouvrir l'application.
recovery-save-failed                    = Impossible d'écrire le fichier de paramètres.
recovery-form-mongo                     = URI MongoDB
recovery-form-anthropic                 = Clé d'API Anthropic
recovery-form-deepgram                  = Clé d'API Deepgram
recovery-form-ffmpeg                    = Chemin du binaire ffmpeg
recovery-form-ffprobe                   = Chemin du binaire ffprobe
recovery-form-huggingface               = Jeton HuggingFace (facultatif)
