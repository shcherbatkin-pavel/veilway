FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ARG DEBIAN_FRONTEND=noninteractive
ARG OPENVPN_VERSION_PATTERN="2.6.*"
ARG UNBOUND_VERSION_PATTERN="1.19.*"

COPY wait-for-interface /usr/local/bin/wait-for-interface

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        dns-root-data \
        dumb-init \
        iproute2 \
        openvpn="${OPENVPN_VERSION_PATTERN}" \
        unbound="${UNBOUND_VERSION_PATTERN}" \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 900 --system openvpn \
    && useradd --uid 900 --gid 900 --system --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin openvpn \
    && chmod 0755 -- /usr/local/bin/wait-for-interface

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/usr/sbin/openvpn", "--config", "/etc/openvpn/server.conf"]
