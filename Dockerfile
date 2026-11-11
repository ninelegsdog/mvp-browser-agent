# --- Build stage ---
# bookworm закреплён намеренно: в trixie `playwright install --with-deps` для
# версии 1.49 просит ttf-unifont и ttf-ubuntu-font-family, которых там уже нет,
# и сборка падает на установке браузера.
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Install Playwright browsers
RUN playwright install --with-deps chromium

# --- Runtime stage ---
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

# PLAYWRIGHT_BROWSERS_PATH: браузеры лежат не в /root — у непривилегированного
# пользователя туда нет доступа, и Chromium не запустился бы.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Системные библиотеки для Chromium ставит тот же инструмент, что и в builder.
# Ручной список пакетов устаревает молча: сборка проходит, браузер не стартует.
RUN playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user (UID 1001, соответствие требованиям клиента)
RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -m -s /bin/bash appuser

# Copy Playwright browsers from builder
COPY --from=builder /root/.cache/ms-playwright /ms-playwright
RUN chown -R appuser:appgroup /ms-playwright

# Copy application code
COPY --chown=appuser:appgroup . /app

# Create directories with correct permissions
RUN mkdir -p /app/evidence /app/browser-profile && \
    chown -R appuser:appgroup /app/evidence /app/browser-profile && \
    chmod 700 /app/browser-profile

USER appuser:appgroup

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')" || exit 1

EXPOSE 9090

CMD ["python3", "demo.py"]
