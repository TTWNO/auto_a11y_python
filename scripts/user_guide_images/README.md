# User guide image regeneration

The screenshots and diagrams in `docs/images/user-guide/` are generated, not
hand-made. When the UI changes, regenerate them instead of recapturing by
hand. All scripts need the app running locally in desktop mode (auto-login)
and Playwright's Chromium.

```bash
# 1. App screenshots (01–16): pass the example project/website/page ids
python scripts/user_guide_images/capture_app.py \
  --base http://127.0.0.1:5001 \
  --project-id <id> --website-id <id> --page-id <id>

# 2. Diagrams (diagram-workflow.png, diagram-concepts.png)
python scripts/user_guide_images/render_diagrams.py

# 3. Report examples (17–23): generate the reports first (via the Reports
#    page or the API), then screenshot each output file:
python scripts/user_guide_images/capture_report_examples.py \
  17-html-report=/path/to/accessibility_report.html \
  18-offline-report=/path/to/offline_report/index.html \
  19-dedup-report=/path/to/dedup_report/index.html \
  20-discovery-report=/path/to/discovery.html \
  21-structure-report=/path/to/structure.html \
  22-recordings-report=/path/to/recordings.html
```

For `23-excel-report.png` (the Excel example), convert the first sheet via
LibreOffice and pdftoppm:

```bash
soffice --headless --convert-to pdf --outdir /tmp report.xlsx
pdftoppm -png -f 1 -l 1 -r 140 /tmp/report.pdf /tmp/xlsx_page
cp /tmp/xlsx_page-001.png docs/images/user-guide/23-excel-report.png
```

Screenshots are captured at 1440×900 (light mode, 2× device scale). The
guide's text references UI labels visible in these images — after
regenerating, re-read `docs/USER_GUIDE.md` for stale descriptions, then
re-export the Word/PDF versions (commands in the commit message of
`docs/Auto_A11y_User_Guide.docx`).
