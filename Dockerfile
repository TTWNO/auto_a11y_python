FROM docker.io/library/python:3.11-slim

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
    # General utilities
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser
RUN python -m playwright install chromium chromium-headless-shell

# Copy application code
COPY . .

# Create required directories
RUN mkdir -p data reports screenshots logs temp

EXPOSE 5001

CMD ["python", "run.py", "--host", "0.0.0.0", "--debug"]
