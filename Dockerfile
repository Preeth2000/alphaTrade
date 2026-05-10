FROM python:3.11-slim

# TA-Lib system dependency
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    wget \
    libta-lib-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
# Install deps before copying source for layer caching
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

# models/ and state.db mounted at runtime; overrides.yaml may also be mounted
VOLUME ["/app/models", "/app/state.db"]

ENV PYTHONUNBUFFERED=1 \
    T212_ENV=demo \
    DATA_PROVIDER=yfinance \
    MODELS_DIR=/app/models \
    STATE_DB_PATH=/app/state.db

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD wget -qO- http://localhost:8080/healthz || exit 1

ENTRYPOINT ["alphalink"]
CMD ["run"]
