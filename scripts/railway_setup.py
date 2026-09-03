#!/usr/bin/env python3
"""Idempotent Railway Networking bootstrap.

Uses Railway runtime IDs when available. Authentication is:
- RAILWAY_TOKEN + Project-Access-Token for a Project Token.
- If that fails, the same RAILWAY_TOKEN is retried as a Bearer token so a
  Workspace/Account token can also be used.
- RAILWAY_API_TOKEN is supported as a Bearer-token compatibility variable.

The API layer is deliberately retryable for transient transport/rate-limit/
server failures. It never changes node identity; it only reconciles runtime
networking resources.

Railway service domains are reconciled to exactly one Railway-provided
*.up.railway.app domain. Existing duplicates are deleted instead of creating
another domain. If RAILWAY_PUBLIC_DOMAIN identifies one of the existing
service domains, that domain is kept so the current deployment endpoint does
not drift unnecessarily.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://backboard.railway.com/graphql/v2"
TARGET_PORT = 8080
API_RETRIES = max(1, int(os.environ.get("RAILWAY_API_RETRIES", "3")))
API_RETRY_DELAY = max(1.0, float(os.environ.get("RAILWAY_API_RETRY_DELAY", "2.5")))

PROJECT_TOKEN = os.environ.get("RAILWAY_TOKEN", "").strip()
ACCOUNT_TOKEN = os.environ.get("RAILWAY_API_TOKEN", "").strip()
TOKEN = PROJECT_TOKEN or ACCOUNT_TOKEN

PROJECT_ID = os.environ.get("RAILWAY_PROJECT_ID", "").strip()
ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip()
SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "").strip()
AUTH_MODE = None


class ApiError(RuntimeError):
    pass


def _request_once(query, variables, mode):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "railway-universal-stable/5.6",
    }
    if mode == "project":
        headers["Project-Access-Token"] = TOKEN
    else:
        headers["Authorization"] = f"Bearer {TOKEN}"

    req = urllib.request.Request(
        API_URL,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        retryable = exc.code == 429 or 500 <= exc.code <= 599
        error = ApiError(f"HTTP {exc.code}: {detail[:500]}")
        if retryable:
            error.retryable = True
        raise error
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error = ApiError(f"request failed: {exc}")
        error.retryable = True
        raise error
    except Exception as exc:
        raise ApiError(f"request failed: {exc}")

    if body.get("errors"):
        raise ApiError(json.dumps(body["errors"], ensure_ascii=False)[:1200])
    return body.get("data") or {}


def _request(query, variables, mode):
    last = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            return _request_once(query, variables, mode)
        except ApiError as exc:
            last = exc
            if not getattr(exc, "retryable", False) or attempt >= API_RETRIES:
                raise
            print(
                f"RAILWAY_API_RETRY={attempt}/{API_RETRIES} "
                f"delay={API_RETRY_DELAY:g}s reason=transient"
            )
            time.sleep(API_RETRY_DELAY)
    raise last or ApiError("Railway API request failed")


def gql(query, variables=None):
    global AUTH_MODE
    if not TOKEN:
        raise ApiError("no Railway token")

    if AUTH_MODE:
        return _request(query, variables or {}, AUTH_MODE)

    if PROJECT_TOKEN:
        try:
            data = _request(query, variables or {}, "project")
            AUTH_MODE = "project"
            return data
        except ApiError as project_error:
            if ACCOUNT_TOKEN:
                try:
                    data = _request(query, variables or {}, "bearer")
                    AUTH_MODE = "bearer"
                    print("RAILWAY_API_AUTH=BEARER_FALLBACK")
                    return data
                except ApiError:
                    raise project_error
            try:
                data = _request(query, variables or {}, "bearer")
                AUTH_MODE = "bearer"
                print("RAILWAY_API_AUTH=BEARER_FALLBACK")
                return data
            except ApiError:
                raise project_error

    AUTH_MODE = "bearer"
    return _request(query, variables or {}, "bearer")


def resolve_ids():
    global PROJECT_ID, ENVIRONMENT_ID, SERVICE_ID

    if not PROJECT_ID or not ENVIRONMENT_ID:
        if not PROJECT_TOKEN:
            raise ApiError("Railway project/environment IDs are unavailable")
        data = gql("query { projectToken { projectId environmentId } }")
        info = data.get("projectToken") or {}
        PROJECT_ID = PROJECT_ID or str(info.get("projectId", ""))
        ENVIRONMENT_ID = ENVIRONMENT_ID or str(info.get("environmentId", ""))

    if not PROJECT_ID or not ENVIRONMENT_ID:
        raise ApiError("unable to resolve Railway project/environment ID")

    if not SERVICE_ID:
        data = gql(
            """query($id:String!){
                 project(id:$id){
                   services{edges{node{id name}}}
                 }
               }""",
            {"id": PROJECT_ID},
        )
        services = (((data.get("project") or {}).get("services") or {}).get("edges") or [])
        wanted = os.environ.get("RAILWAY_SERVICE_NAME", "").strip()
        matches = [
            e["node"] for e in services
            if e.get("node", {}).get("name") == wanted
        ]
        if len(matches) == 1:
            SERVICE_ID = matches[0]["id"]
        elif len(services) == 1:
            SERVICE_ID = services[0]["node"]["id"]
        else:
            raise ApiError("unable to identify target Railway service")


def _normalize_domain_entries(raw):
    """Normalize both observed Railway config-map shapes.

    Depending on the Railway API/config revision, serviceDomains has been
    observed as either {domainId: {domain: ...}} or {domain: {}}. The latter
    has no deletion ID, so a second service-scoped query fills IDs in later.
    """
    result = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            key = str(key).strip()
            if not key:
                continue
            if isinstance(value, dict):
                domain = str(value.get("domain", "")).strip()
                domain_id = key
                if not domain and key.endswith(".up.railway.app"):
                    domain = key
                    domain_id = ""
                if domain:
                    result.append({"id": domain_id, "domain": domain})
            elif isinstance(value, str):
                domain = value.strip()
                if domain:
                    result.append({"id": key, "domain": domain})
    elif isinstance(raw, list):
        for value in raw:
            if not isinstance(value, dict):
                continue
            domain = str(value.get("domain", "")).strip()
            domain_id = str(value.get("id", "")).strip()
            if domain:
                result.append({"id": domain_id, "domain": domain})
    return result


def list_service_domains():
    """Return all Railway-provided service domains, including deletion IDs.

    First read environment.config(), which is the configuration source used by
    Railway's current tooling. Some config revisions key serviceDomains by the
    domain string itself, so those entries have no deletion ID. In that case we
    cross-reference the service-scoped domains collection, which supplies the
    stable IDs required by serviceDomainDelete. Most importantly, an empty or
    differently-shaped config response is never allowed to trigger blind
    creation if the service-scoped query proves a domain already exists.
    """
    data = gql(
        """query($id:String!){
             environment(id:$id){
               config(decryptVariables:false)
             }
           }""",
        {"id": ENVIRONMENT_ID},
    )
    config = ((data.get("environment") or {}).get("config")) or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception as exc:
            raise ApiError(f"unable to parse Railway environment config: {exc}")

    service_cfg = ((config.get("services") or {}).get(SERVICE_ID)) or {}
    networking = service_cfg.get("networking") or {}
    config_entries = _normalize_domain_entries(networking.get("serviceDomains") or {})

    # Cross-check with Railway's service-scoped domain collection. This is
    # essential when config uses {domain:{}} entries or when the dashboard has
    # retained a domain that the config snapshot does not fully materialize.
    service_data = gql(
        """query($id:String!){
             service(id:$id){
               domains{
                 serviceDomains{ id domain suffix }
               }
             }
           }""",
        {"id": SERVICE_ID},
    )
    service_domains = (((service_data.get("service") or {}).get("domains") or {}).get("serviceDomains") or [])
    service_entries = []
    for value in service_domains:
        if not isinstance(value, dict):
            continue
        domain = str(value.get("domain", "")).strip()
        domain_id = str(value.get("id", "")).strip()
        if domain:
            service_entries.append({"id": domain_id, "domain": domain})

    # Prefer the service-scoped collection when it contains entries. Merge in
    # any config-only names so a dashboard-visible duplicate is not silently
    # discarded if the two API projections are temporarily inconsistent.
    merged = {entry["domain"]: entry for entry in service_entries}
    for entry in config_entries:
        existing = merged.get(entry["domain"])
        if existing is None:
            merged[entry["domain"]] = entry
        elif not existing.get("id") and entry.get("id"):
            existing["id"] = entry["id"]

    return list(merged.values())


def delete_service_domain(domain_id, domain):
    if not domain_id:
        raise ApiError(
            f"Railway duplicate domain has no deletion ID: {domain}; "
            "refusing to create another domain"
        )
    print(f"RAILWAY_API_ACTION=DELETE_DUPLICATE_PUBLIC_DOMAIN domain={domain}")
    gql(
        """mutation($id:String!){
             serviceDomainDelete(id:$id)
           }""",
        {"id": domain_id},
    )
    print(f"RAILWAY_API_PUBLIC_DOMAIN=DELETED domain={domain}")


def create_service_domain():
    print("RAILWAY_API_ACTION=CREATE_PUBLIC_DOMAIN")
    result = gql(
        """mutation($input:ServiceDomainCreateInput!){
             serviceDomainCreate(input:$input){domain}
           }""",
        {"input": {"serviceId": SERVICE_ID, "environmentId": ENVIRONMENT_ID}},
    )
    domain = (result.get("serviceDomainCreate") or {}).get("domain", "")
    print(f"RAILWAY_API_PUBLIC_DOMAIN=CREATED domain={domain}")


def setup():
    if not TOKEN:
        print("RAILWAY_API_SETUP=SKIP reason=no_token")
        return 0

    resolve_ids()
    print("RAILWAY_API_SETUP=CHECK")

    service_domains = list_service_domains()
    current_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()

    changed = False
    if len(service_domains) == 0:
        create_service_domain()
        changed = True
    elif len(service_domains) == 1:
        print(f"RAILWAY_API_PUBLIC_DOMAIN=EXISTS domain={service_domains[0].get('domain','')}")
    else:
        keep = None
        if current_domain:
            keep = next(
                (d for d in service_domains if str(d.get("domain", "")).strip() == current_domain),
                None,
            )
        if keep is None:
            keep = service_domains[0]

        keep_domain = str(keep.get("domain", "")).strip()
        print(
            f"RAILWAY_API_PUBLIC_DOMAIN=DUPLICATES count={len(service_domains)} "
            f"keep={keep_domain}"
        )
        for domain in service_domains:
            if domain is keep:
                continue
            delete_service_domain(
                str(domain.get("id", "")).strip(),
                str(domain.get("domain", "")).strip(),
            )
            changed = True
        print(f"RAILWAY_API_PUBLIC_DOMAIN=RECONCILED count=1 keep={keep_domain}")

    proxies = gql(
        """query($serviceId:String!,$environmentId:String!){
             tcpProxies(serviceId:$serviceId,environmentId:$environmentId){
               id domain proxyPort applicationPort
             }
           }""",
        {"serviceId": SERVICE_ID, "environmentId": ENVIRONMENT_ID},
    ).get("tcpProxies") or []

    has_tcp = any(
        isinstance(p, dict) and int(p.get("applicationPort", -1)) == TARGET_PORT
        for p in proxies
    )

    if not has_tcp:
        print("RAILWAY_API_ACTION=CREATE_TCP_PROXY target=8080")
        result = gql(
            """mutation($input:TCPProxyCreateInput!){
                 tcpProxyCreate(input:$input){
                   id domain proxyPort applicationPort
                 }
               }""",
            {
                "input": {
                    "serviceId": SERVICE_ID,
                    "environmentId": ENVIRONMENT_ID,
                    "applicationPort": TARGET_PORT,
                }
            },
        )
        proxy = result.get("tcpProxyCreate") or {}
        print(
            "RAILWAY_API_TCP_PROXY=CREATED "
            f"domain={proxy.get('domain','')} port={proxy.get('proxyPort','')} target=8080"
        )
        changed = True
    else:
        print("RAILWAY_API_TCP_PROXY=EXISTS target=8080")

    if changed:
        print("RAILWAY_API_ACTION=REDEPLOY")
        gql(
            """mutation($serviceId:String!,$environmentId:String!){
                 serviceInstanceRedeploy(
                   serviceId:$serviceId,
                   environmentId:$environmentId
                 )
               }""",
            {"serviceId": SERVICE_ID, "environmentId": ENVIRONMENT_ID},
        )
        print("RAILWAY_API_SETUP=REDEPLOY_REQUESTED")
        return 10

    print("RAILWAY_API_SETUP=READY")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(setup())
    except Exception as exc:
        print(f"RAILWAY_API_SETUP=ERROR {exc}", file=sys.stderr)
        sys.exit(20)
