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
        # GraphQL errors are normally semantic/auth/schema errors. Do not
        # blindly repeat mutations on these responses.
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

    # A real Project Token is documented to use Project-Access-Token.
    # Workspace/Account tokens may be stored by users under RAILWAY_TOKEN;
    # detect that case without requiring projectToken discovery first.
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


def setup():
    if not TOKEN:
        print("RAILWAY_API_SETUP=SKIP reason=no_token")
        return 0

    resolve_ids()
    print("RAILWAY_API_SETUP=CHECK")

    env_data = gql(
        """query($id:String!){
             environment(id:$id){
               config(decryptVariables:false)
             }
           }""",
        {"id": ENVIRONMENT_ID},
    )
    env_config = ((env_data.get("environment") or {}).get("config")) or {}
    if isinstance(env_config, str):
        try:
            env_config = json.loads(env_config)
        except Exception:
            env_config = {}

    service_cfg = ((env_config.get("services") or {}).get(SERVICE_ID)) or {}
    networking = service_cfg.get("networking") or {}
    service_domains = networking.get("serviceDomains") or {}

    has_domain = bool(os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip())
    if not has_domain and isinstance(service_domains, dict):
        for item in service_domains.values():
            node = item if isinstance(item, dict) else {}
            if str(node.get("domain", "")).strip():
                has_domain = True
                break
    elif not has_domain and isinstance(service_domains, list):
        has_domain = any(
            isinstance(item, dict) and str(item.get("domain", "")).strip()
            for item in service_domains
        )

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

    changed = False

    if not has_domain:
        print("RAILWAY_API_ACTION=CREATE_PUBLIC_DOMAIN")
        gql(
            """mutation($input:ServiceDomainCreateInput!){
                 serviceDomainCreate(input:$input){domain}
               }""",
            {"input": {"serviceId": SERVICE_ID, "environmentId": ENVIRONMENT_ID}},
        )
        print("RAILWAY_API_PUBLIC_DOMAIN=CREATED")
        changed = True
    else:
        print("RAILWAY_API_PUBLIC_DOMAIN=EXISTS")

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
