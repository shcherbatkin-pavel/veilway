FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10001 veilway \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin veilway

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN python -m pip install --no-cache-dir --upgrade pip==26.2 \
    && python -m pip install --no-cache-dir .

COPY container-entrypoint.py /usr/local/bin/veilway-container-entrypoint
RUN chmod 0555 /usr/local/bin/veilway-container-entrypoint \
    && mkdir -p /run/veilway \
    && chown 10001:10001 /run/veilway

ENTRYPOINT ["/usr/local/bin/veilway-container-entrypoint"]
CMD ["serve"]
