FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0

# TA-Lib system dependency
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    wget \
    && wget -q https://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz \
    && tar -xzf ta-lib-0.4.0-src.tar.gz \
    && cd ta-lib && ./configure --prefix=/usr && make && make install \
    && cd .. && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY alphaTrade/ alphaTrade/
RUN pip install --no-cache-dir ".[dev]"

COPY . .

# models/ and state.db mounted at runtime; overrides.yaml may also be mounted
VOLUME ["/app/models", "/app/state.db"]

ENV PYTHONUNBUFFERED=1 \
    DATA_PROVIDER=yfinance \
    MODELS_DIR=/app/models \
    STATE_DB_PATH=/app/state.db

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD wget -qO- http://localhost:8080/healthz || exit 1

ENTRYPOINT ["alphaTrade"]
CMD ["run"]
