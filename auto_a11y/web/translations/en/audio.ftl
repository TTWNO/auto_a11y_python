# audio — strings for the audioA11y video-upload + processing UI
# (Phase 7 of the audioA11y integration plan)

# === Upload form ===
audio-upload-heading                    = Upload audit video
audio-upload-intro                      = Upload a video of a manual accessibility audit. The pipeline will extract audio, transcribe it, identify speakers, run Claude analysis, and import the findings as recording issues.
audio-upload-file-label                 = Video file
audio-upload-file-help                  = Select the video of the audit session (MP4, MOV, WebM, …). Max { $max } MB.
audio-upload-project-label              = Project
audio-upload-project-help               = Which project should this recording be associated with?
audio-upload-title-label                = Recording title
audio-upload-title-placeholder          = e.g. Main website screen-reader audit
audio-upload-context-label              = Audit context
audio-upload-context-audit              = Audit
audio-upload-context-lived-experience   = Lived experience
audio-upload-context-navilens           = NaviLens
audio-upload-languages-label            = Languages
audio-upload-languages-help             = Select at least one language to analyse.
audio-upload-language-en                = English
audio-upload-language-fr                = French
audio-upload-speaker-remap-label        = Remap speakers across segments (recommended)
audio-upload-extended-context-label     = Use Opus 1M extended context
audio-upload-callouts-label             = Render callouts video
audio-upload-submit-button              = Continue to cost estimate
audio-upload-cancel-link                = Cancel
audio-upload-about-heading              = About video analysis
audio-upload-import-json-instead        = Import a Dictaphone JSON file instead

# === Confirm step ===
audio-confirm-heading                   = Confirm processing
audio-confirm-intro                     = Review the cost estimate below, then click Process now to start the pipeline.
audio-confirm-estimated-cost            = Estimated cost
audio-confirm-duration                  = Video duration
audio-confirm-breakdown-heading         = Cost breakdown
audio-confirm-pricing-asof              = Pricing as of { $date }; rates may have changed.
audio-confirm-process-button            = Process now
audio-confirm-cancel-link               = Cancel

# === Cost panel ===
audio-cost-deepgram                     = Deepgram (transcription)
audio-cost-claude-en                    = Claude analysis (English)
audio-cost-claude-fr                    = Claude analysis (French)
audio-cost-total                        = Total

# === Errors ===
audio-error-no-language                 = Select at least one language.
audio-error-file-required               = Choose a video file to upload.
audio-error-file-too-large              = Video too large; max { $max } MB.
audio-error-invalid-mp4                 = Not a valid video file.
audio-error-no-project                  = Project is required.
audio-error-no-project-access           = You do not have access to this project.
audio-error-runner-not-configured       = Video runner is not configured on the server.
audio-error-keys-not-configured         = Can't start processing — these API keys are not configured: { $keys }. Add them in Settings, then try again.
audio-error-already-processing          = This recording is not in the uploaded state and cannot be processed.

# === Pipeline stage labels ===
audio-stage-segmenting                  = Extracting audio
audio-stage-transcribing                = Transcribing
audio-stage-speaker-remap               = Identifying speakers
audio-stage-merging-vtt                 = Merging captions
audio-stage-analyzing                   = Analyzing
audio-stage-callouts                    = Rendering callouts video
audio-stage-importing                   = Importing issues

# === Recording status labels ===
audio-status-uploaded                   = Uploaded
audio-status-processing                 = Processing
audio-status-complete                   = Complete
audio-status-failed                     = Failed
audio-status-cancelling                 = Cancelling
audio-status-cancelled                  = Cancelled

# === Progress card (Phase 8) ===
audio-progress-elapsed                  = Elapsed
audio-progress-running-cost             = Cost so far
audio-progress-cancel                   = Cancel
audio-progress-aria-label               = Processing progress: stage { $stage }, { $current } of { $total }

# === Detail page (Phase 8) ===
audio-detail-failed-heading             = Processing failed
audio-detail-cost-panel-heading         = Cost breakdown
audio-detail-cost-estimated-was         = Estimated cost was

# === Callouts video (Phase 9) ===
audio-callouts-download                 = Download annotated video
audio-callouts-failed                   = Annotated video rendering failed; the rest of the audit completed successfully.
audio-callouts-failed-help              = The audit finished, but the branded video (title cards, CNIB AccessLabs logo, watermark, and callouts) could not be rendered. The technical reason is below and in the application log.
audio-callouts-failed-details           = Show technical details
audio-callouts-pending                  = Annotated video rendering in progress…
audio-analysis-partial-failed           = Some analyses could not be generated
audio-analysis-partial-failed-help      = The recording completed, but one or more AI analyses (e.g. issues, key takeaways) failed and were skipped. The rest of the recording is intact. The reasons are below and in the application log.
audio-analysis-partial-failed-details   = Show which analyses failed

# === Settings Recovery (Phase 10) ===
recovery-title                          = Settings Recovery
recovery-intro                          = Some configuration is missing or incorrect. Fix the highlighted items below to start the application.
recovery-failed-checks-heading          = Failed checks
recovery-settings-form-heading          = Settings
recovery-check-failed                   = Failed
recovery-check-passed                   = Passed
recovery-test-button                    = Test
recovery-test-working                   = Working…
recovery-test-success                   = OK
recovery-test-failed                    = Not working
recovery-save                           = Save
recovery-save-success                   = Settings saved. Please quit and reopen the application.
recovery-save-failed                    = Failed to write settings file.
recovery-form-mongo                     = MongoDB URI
recovery-form-anthropic                 = Anthropic API key
recovery-form-deepgram                  = Deepgram API key
recovery-form-ffmpeg                    = ffmpeg binary path
recovery-form-ffprobe                   = ffprobe binary path
recovery-form-huggingface               = HuggingFace token (optional)
