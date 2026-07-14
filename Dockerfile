FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/cpa_keeper ./src/cpa_keeper

RUN pip install . \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin keeper

USER keeper

ENTRYPOINT ["cpa-keeper"]
CMD ["daemon"]
