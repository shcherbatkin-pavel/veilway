FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install --yes --no-install-recommends openssl openvpn="2.6.*" \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10002 veilway-pki \
    && groupadd --gid 10003 veilway-pki-rpc \
    && useradd --uid 10002 --gid 10002 --groups 10003 --no-create-home --shell /usr/sbin/nologin veilway-pki
WORKDIR /app
RUN python -m pip install --no-cache-dir cryptography==50.0.2
COPY veilway_pki ./veilway_pki
COPY container-entrypoint.py /usr/local/bin/veilway-pki-entrypoint
RUN chmod 0555 /usr/local/bin/veilway-pki-entrypoint
ENTRYPOINT ["/usr/local/bin/veilway-pki-entrypoint"]
CMD ["serve"]
