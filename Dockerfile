# syntax=docker/dockerfile:1
# Xray is intentionally frozen for production stability. Upgrade only by an explicit, tested commit.
ARG XRAY_VERSION=26.3.27
ARG XRAY_IMAGE_DIGEST=sha256:592ec4d11f656db95598d01e76dbcc6e002d67360b96a5436500a938230f52c7
ARG CLOUDFLARED_VERSION=2026.7.3
FROM ghcr.io/xtls/xray-core:${XRAY_VERSION}@${XRAY_IMAGE_DIGEST} AS xray
FROM cloudflare/cloudflared:${CLOUDFLARED_VERSION} AS cloudflared
FROM python:3.12-alpine3.22
ARG XRAY_VERSION
ARG XRAY_IMAGE_DIGEST
ARG CLOUDFLARED_VERSION
ENV XRAY_VERSION=${XRAY_VERSION} XRAY_IMAGE_DIGEST=${XRAY_IMAGE_DIGEST} CLOUDFLARED_VERSION=${CLOUDFLARED_VERSION} PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NODE_IDENTITY_POLICY=INITIALIZE_ONCE_REUSE_FOREVER
RUN apk add --no-cache openssl ca-certificates && mkdir -p /etc/xray /data /opt/xray/scripts /opt/xray/config /opt/xray/site
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY scripts/ /opt/xray/scripts/
COPY config/ /opt/xray/config/
COPY site/ /opt/xray/site/
# Build-time invariants: runtime artifacts are derived, while node identity is initialized once and reused forever.
RUN set -eu; \
    check() { label="$1"; shift; if "$@"; then echo "BUILD_CHECK ${label}=PASS"; else echo "BUILD_CHECK ${label}=FAIL" >&2; exit 1; fi; }; \
    check xray_version sh -c 'test "$(/usr/local/bin/xray version | head -n 1)" = "Xray 26.3.27 (Xray, Penetrates Everything.)"'; \
    check xray_digest_pinned sh -c 'test "${XRAY_IMAGE_DIGEST}" = "sha256:592ec4d11f656db95598d01e76dbcc6e002d67360b96a5436500a938230f52c7"'; \
    check py_compile python3 -m py_compile /opt/xray/scripts/*.py; \
    check identity_init_present test -f /opt/xray/scripts/identity-init.py; \
    check cloudflare_generator grep -q 'vless-xhttp-cloudflare' /opt/xray/scripts/generate.py; \
    check xhttp_network grep -q 'type":"xhttp"' /opt/xray/scripts/generate.py; \
    check cloudflare_xhttp_tls grep -q 'cloudflare-xhttp-tls' /opt/xray/scripts/generate.py; \
    check tcp_proxy_target grep -q 'tcp_proxy_expected_target":8080' /opt/xray/scripts/runtime-manifest.py; \
    check boot_identity_init grep -q 'identity-init.py' /opt/xray/scripts/boot.sh; \
    check identity_policy grep -q 'NODE_IDENTITY_POLICY=INITIALIZE_ONCE_REUSE_FOREVER' /opt/xray/scripts/boot.sh; \
    check identity_reuse grep -q 'NODE_IDENTITY=REUSED' /opt/xray/scripts/identity-init.py; \
    check identity_fail_closed grep -q 'refusing to rotate identity' /opt/xray/scripts/identity-init.py; \
    check short_ids_immutable grep -q 'Short IDs are part of node identity' /opt/xray/scripts/generate.py; \
    check gateway_early grep -q 'GATEWAY_BIND_EARLY=PASS' /opt/xray/scripts/boot.sh; \
    check deployment_summary grep -q 'DEPLOYMENT SUMMARY' /opt/xray/scripts/boot.sh; \
    check gateway_json grep -q 'application/json' /opt/xray/scripts/gateway.py; \
    check cloudflared_ready grep -q 'CLOUDFLARED_READY=PASS' /opt/xray/scripts/boot.sh; \
    check supervisor_unhealthy grep -q 'READY_UNHEALTHY' /opt/xray/scripts/supervise.sh; \
    check supervisor_identity_policy grep -q 'never changes Railway variables or node identity' /opt/xray/scripts/supervise.sh; \
    check no_ws_legacy sh -c '! grep -q "cloudflare-ws-tls" /opt/xray/scripts/generate.py'; \
    check no_ws_transport sh -c '! grep -q "type\\\":\\\"ws\\\"" /opt/xray/scripts/generate.py'; \
    check no_runtime_identity_generation sh -c '! grep -q "secrets.token_" /opt/xray/scripts/generate.py'; \
    chmod 0755 /usr/local/bin/xray /usr/local/bin/cloudflared /opt/xray/scripts/*.sh /opt/xray/scripts/*.py; \
    chmod 0644 /opt/xray/config/* /opt/xray/site/*
RUN echo "SOURCE_BUILD=runtime-derived BUILD_ID=runtime-derived NODE5=VLESS_XHTTP_TLS_CLOUDFLARE NODE_IDENTITY=INITIALIZE_ONCE_REUSE_FOREVER XRAY=26.3.27 XRAY_PINNED=true"
EXPOSE 8080
RUN test -x /opt/xray/scripts/railway_setup.py && python3 -m py_compile /opt/xray/scripts/railway_setup.py && test -x /opt/xray/scripts/supervise.sh
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=3).read()"
WORKDIR /opt/xray
ENTRYPOINT ["/opt/xray/scripts/supervise.sh"]
