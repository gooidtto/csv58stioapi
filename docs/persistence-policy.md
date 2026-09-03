# Persistence and node identity policy

## Goals

- `/data` must be a Railway Persistent Volume for stable node identity.
- Node identity is generated only when the identity file is missing on the mounted volume.
- Existing identity files are never overwritten during normal startup, redeploy, or self-healing restart.
- Runtime endpoints are derived from the current Railway deployment environment, not persisted runtime artifacts.
- `RAILWAY_TOKEN`, Node 5 Cloudflare configuration, and country/region are treated as external configuration. Runtime self-healing must not edit them.
- Runtime self-healing may terminate an unhealthy boot process so Railway `ON_FAILURE` restarts the same service with the same mounted volume.

## Persisted identity

The following files belong to the node identity and must live on `/data`:

- `uuid.txt`
- `reality_private_key.txt`
- `reality_public_key.txt`
- `reality_short_ids.json`
- `subscription_token.txt`

The runtime files (`runtime.json`, `state.json`, `manifest.json`, `runtime-manifest.json`, `subscription.txt`) are derived artifacts and are regenerated from the current deployment environment.

## First deployment

If `/data` is empty, the image bootstrap initializes the identity once. Subsequent deployments must reuse the same Volume and therefore reuse those identity files.

## Important boundary

Changing `RAILWAY_TOKEN`, changing Node 5 Cloudflare variables, changing Railway networking, changing region/country, or deleting/replacing the Persistent Volume can legitimately change deployment conditions. The application must not silently rewrite external configuration to preserve old endpoints.
