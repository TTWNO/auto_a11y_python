FROM docker.io/library/python:3.14-slim AS base

# Install Playwright system dependencies and other required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Playwright Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libx11-xcb1 \
    # WeasyPrint dependencies
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
	libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    shared-mime-info \
    # PDF audit engine — Ghostscript renders pages for contrast analysis
    ghostscript \
    # General utilities
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (auto_a11y first, then the pdfMax
# audit-tool deps. Listing the second batch separately keeps both
# pinning sets visible in the image and avoids one set silently
# shadowing the other.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# pdfMax verbatim audit tool — see auto_a11y/pdf/pdfmax_runner.py.
# We bake the *checker* directory directly into /opt/pdfmax/checker
# so the route can shell out to it without any extra wiring.
#
# The pdfMax checker lives in a sibling directory at build time
# (``../pdfMax/python/checker``), wired up via ``additional_contexts``
# in docker-compose.yml. ``COPY --from=pdfmax_checker`` pulls the
# files in over the duration of this build stage; nothing else in
# the image references the build-context name.
COPY --from=pdfmax_checker / /opt/pdfmax/checker/
RUN pip install --no-cache-dir -r /opt/pdfmax/checker/requirements.txt

# Install Playwright Chromium browser
RUN python -m playwright install chromium chromium-headless-shell

# Create required directories
RUN mkdir -p data reports screenshots logs temp

# Default the pdfMax checker dir to its in-container location. The
# config still honours $PDFMAX_CHECKER_DIR if a deployment moves it.
ENV PDFMAX_CHECKER_DIR=/opt/pdfmax/checker

# --- Dev target: no COPY, relies on bind mount ---
FROM base AS dev
EXPOSE 5001
CMD ["python", "run.py", "--host", "0.0.0.0", "--debug"]

# --- Prod target: bake source into image ---
FROM base AS prod
COPY . .
EXPOSE 5001
CMD ["python", "run.py", "--host", "0.0.0.0"]
