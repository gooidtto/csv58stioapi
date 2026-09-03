# syntax=docker/dockerfile:1
ARG XRAY_VERSION=26.3.27
ARG CLOUDFLARED_VERSION=2026.7.3
FROM ghcr.io/xtls/xray-core:${XRAY_VERSION} AS xray
FROM cloudflare/cloudflared:${CLOUDFLARED_VERSION} AS cloudflared
FROM python:3.12-alpine3.22
ARG XRAY_VERSION
ARG CLOUDFLARED_VERSION
ENV XRAY_VERSION=${XRAY_VERSION} CLOUDFLARED_VERSION=${CLOUDFLARED_VERSION} PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apk add --no-cache openssl ca-certificates && mkdir -p /etc/xray /data /opt/xray/scripts /opt/xray/config /opt/xray/site
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY scripts/ /opt/xray/scripts/
COPY config/ /opt/xray/config/
COPY site/ /opt/xray/site/
# Keep runtime source immutable: never patch protocol definitions during image build.
# The build must fail if Node 5 regresses to the deprecated WebSocket transport,
# or if the runtime loses the Railway endpoint/8080 invariants.
RUN python3 -m py_compile /opt/xray/scripts/*.py && \
    grep -q 'vless-xhttp-cloudflare' /opt/xray/scripts/generate.py && \
    grep -q 'type":"xhttp"' /opt/xray/scripts/generate.py && \
    grep -q 'cloudflare-xhttp-tls' /opt/xray/scripts/generate.py && \
    grep -q 'tcp_proxy_expected_target":8080' /opt/xray/scripts/runtime-manifest.py && \
    grep -q 'write_secret()' /opt/xray/scripts/boot.sh && \
    grep -q 'GATEWAY_BIND_EARLY=PASS' /opt/xray/scripts/boot.sh &&     grep -q 'DEPLOYMENT SUMMARY' /opt/xray/scripts/boot.sh &&     grep -q 'application/json' /opt/xray/scripts/gateway.py && \
    grep -q 'CLOUDFLARED_READY=PASS' /opt/xray/scripts/boot.sh && \
    ! grep -q 'cloudflare-ws-tls' /opt/xray/scripts/generate.py && \
    ! grep -q 'type":"ws"' /opt/xray/scripts/generate.py && \
    chmod 0755 /usr/local/bin/xray /usr/local/bin/cloudflared /opt/xray/scripts/*.sh /opt/xray/scripts/*.py && \
    chmod 0644 /opt/xray/config/* /opt/xray/site/*
RUN echo "SOURCE_BUILD=runtime-derived BUILD_ID=runtime-derived NODE5=VLESS_XHTTP_TLS_CLOUDFLARE"
EXPOSE 8080

# Verify the optional Railway API bootstrap script.
RUN test -x /opt/xray/scripts/railway_setup.py && python3 -m py_compile /opt/xray/scripts/railway_setup.py
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"
WORKDIR /opt/xray
ENTRYPOINT ["/opt/xray/scripts/boot.sh"]
