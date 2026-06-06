FROM ubuntu:24.04

ARG AOR_BUILD_AUDIO=0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    ffmpeg \
    git \
    openssl \
    python3 \
    python3-pip \
    python3-venv \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/openfabric

COPY scripts/build-whisper-cli.sh ./scripts/build-whisper-cli.sh

RUN if [ "${AOR_BUILD_AUDIO}" = "1" ]; then \
      bash ./scripts/build-whisper-cli.sh /usr/local/bin/whisper-cli /tmp/whisper.cpp \
      && rm -rf /tmp/whisper.cpp; \
    else \
      echo "Skipping whisper-cli build because AOR_BUILD_AUDIO=${AOR_BUILD_AUDIO}"; \
    fi

RUN python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/opt/openfabric/src"

COPY pyproject.toml README.md ./
COPY src ./src
COPY artifacts/agent_memory.db artifacts/prompts.db ./artifacts/

RUN pip install --upgrade pip setuptools wheel \
    && pip install . \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY docker/server-entrypoint.sh /usr/local/bin/openfabric-server-entrypoint
COPY docker/audio-entrypoint.sh /usr/local/bin/openfabric-audio-entrypoint
RUN chmod +x /usr/local/bin/openfabric-server-entrypoint /usr/local/bin/openfabric-audio-entrypoint

EXPOSE 8011

ENTRYPOINT ["openfabric-server-entrypoint"]
