# Docker Host Localhost Access — Design

**Status:** Draft
**Author:** Claude + @tait
**Date:** 2026-04-16

## Problem

When auto-a11y runs inside the Docker/Podman container, it cannot reach
HTTP servers running on the host machine's `localhost` (e.g., a dev
server on port 80, 8000, or 8080). The container's network namespace is
isolated, so `http://localhost:8080` from inside the container resolves
to the *container's* loopback, not the host's. This blocks a common
testing workflow: pointing auto-a11y at a locally-developed site to
check accessibility before deploying it.

## Goal

Make host-local HTTP servers reachable from the containerised app via a
stable hostname, without sacrificing container network isolation, and
document the workflow so users know how to use it.

## Non-goals

- Switching to `network_mode: host` (would remove container isolation
  and change how the app/MongoDB ports are exposed).
- Modifying any application code. The app already accepts arbitrary
  URLs; this is purely a Docker-networking enable.
- Allowing the `mongo` service to reach the host (it has no need to).
- Auto-rewriting user-entered `localhost` URLs (out of scope; users
  supply the correct hostname).

## Design

### Overview

Add Docker's standard host-gateway alias to the `app` service in
`docker-compose.yml`:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

The `host-gateway` magic string is resolved by the container runtime to
the host's gateway IP at container start. After this change, anything
inside the `app` container can reach a server bound to the host's
loopback (or any host interface) by addressing
`host.docker.internal:PORT`.

This is supported on:

- **Docker Desktop** (macOS / Windows): `host.docker.internal` is
  built-in; the `extra_hosts` entry is a harmless re-declaration.
- **Docker Engine on Linux** 20.10+: requires the `extra_hosts` entry.
- **Podman** 4.1+ (the project's `docker-compose.override.yml`
  references `podman-compose`): supports the same `host-gateway`
  keyword.

### Files changed

1. **`docker-compose.yml`** — add `extra_hosts` under the `app` service,
   with a one-line inline comment explaining its purpose.

2. **`README.md`** — add a short subsection under the existing Docker
   instructions ("Testing host-local servers" or similar) explaining
   the hostname swap, with a worked example: a server running on the
   host at `http://localhost:8080` is reached from auto-a11y as
   `http://host.docker.internal:8080`.

3. **`README.fr.md`** — matching French translation of the new
   subsection (per CLAUDE.md's bilingual rule that the two READMEs
   stay in sync).

No other files are touched. No application code, no compose override,
no test fixtures.

### Usage

A user wanting to test their dev server running at `http://localhost:8080`
on their host:

1. Start the container stack (`docker compose up` or `podman-compose up`).
2. In the auto-a11y UI, when adding a website/page, enter
   `http://host.docker.internal:8080` (instead of `http://localhost:8080`).
3. The container reaches the host server via the host gateway and tests
   it normally.

### Failure modes

- **Container runtime too old to support `host-gateway`** (Docker
  Engine < 20.10, Podman < 4.1): container start fails with a clear
  Compose error naming `extra_hosts`. Documented in the README note as
  a minimum-version requirement.
- **Host firewall blocks the gateway IP**: connection from container
  times out. The user sees a normal auto-a11y "could not load page"
  error. Out of scope to detect or work around.
- **User enters `http://localhost:PORT`**: behaves as today (fails to
  connect). The README note tells them what to use instead. We do not
  silently rewrite.

## Testing

Manual verification:

1. Start a trivial HTTP server on the host, e.g.
   `python -m http.server 8000`.
2. `docker compose up` (or `podman-compose up`).
3. From the container shell:
   `docker compose exec app curl -sS http://host.docker.internal:8000/`
   should return the directory listing.
4. From the auto-a11y UI, add a page at
   `http://host.docker.internal:8000/` and run a test; confirm the page
   loads and produces results.

No automated tests; this is a one-line networking config change whose
behaviour is fully exercised by the manual workflow above.

## Rollout

Single PR. No migration, no flags, no compatibility shim. Existing
users on Docker Desktop already had `host.docker.internal`; existing
Linux users gain the alias on next `docker compose up --build`.
