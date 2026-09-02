---
description: Docker/Compose patterns — layer caching, multi-stage builds, hardened runtimes, secret handling, and log hygiene
tags: docker,compose,containers,security,devops
origin: ECC (enriched)
---

# Docker & Container Operations Patterns

Practical Docker and Docker Compose patterns for reproducible builds, safe
local development, and hardened runtime: multi-stage image layering, health
checks, resource limits, secret handling, and log hygiene. One process per
container; containers are ephemeral — persistent state lives in volumes.

## When to Reference

- Writing or reviewing Dockerfiles and docker-compose.yml
- Setting up local dev stacks with hot reload (app + db + cache)
- Hardening container security or configuring resource limits
- Debugging container networking, logs, or rebuild issues
- Testing installers/tools against disposable isolated project copies

## Image Layering

Every instruction creates a cached layer. Order layers from least to most
frequently changing: dependencies first, source code last — so a code edit
doesn't invalidate the dependency install.

```dockerfile
# GOOD: dependency layers cached; source changes don't re-run npm ci
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
```

- Pin base image versions — never `:latest` (`node:22.12-alpine3.20`).
- Keep a complete `.dockerignore` (`node_modules`, `.git`, `.env*`, `dist`,
  `coverage`, `*.log`, caches) so builds are small and reproducible.
- Never bake secrets into layers (they persist even in deleted layers).

## Multi-Stage Builds

One Dockerfile, named stages: dev gets hot-reload tools, production gets a
minimal non-root image with only built artifacts.

```dockerfile
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS dev
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
CMD ["npm", "run", "dev"]

FROM node:22-alpine AS production
RUN addgroup -g 1001 -S app && adduser -S app -u 1001
USER app
WORKDIR /app
COPY --from=deps --chown=app:app /app/node_modules ./node_modules
COPY --from=deps --chown=app:app /app/package.json ./
HEALTHCHECK --interval=30s --timeout=3s \
  CMD wget -qO- http://localhost:3000/health || exit 1
CMD ["node", "dist/server.js"]
```

Select per environment with Compose `build: { target: dev }` for development
and `target: production` for prod.

## Compose for Local Development

- Bind mount the source (`.:/app`) for hot reload; shadow container-generated
  dirs with anonymous volumes (`/app/node_modules`) so host state doesn't clobber them.
- Gate startup order with health conditions:

```yaml
depends_on:
  db:
    condition: service_healthy
```

- Health checks per service (`pg_isready -U postgres` for Postgres).
- `docker-compose.override.yml` auto-loads for dev; prod uses explicit
  `-f docker-compose.yml -f docker-compose.prod.yml`.
- Persist data in named volumes; init scripts via `/docker-entrypoint-initdb.d/`.

## Networking

- Services resolve each other by service name (`postgres://...@db:5432/app`).
- Segment with custom networks: only api and db share `backend-net`, so the
  frontend can't reach the DB.
- Expose only what's needed: bind host ports to `127.0.0.1` locally; in
  production omit `ports:` entirely for internal services.
- Debug: `docker compose exec app nslookup db`, `docker network inspect <net>`.

## Health Checks

Every long-running service gets one; orchestrators and `depends_on` rely on it.

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U postgres"]
  interval: 5s
  timeout: 3s
  retries: 5
```

Check an actual dependency-touching endpoint (`/health`), not just "process alive".

## Resource Limits & Security

```yaml
services:
  app:
    deploy:
      resources: { limits: { cpus: "1.0", memory: 512M } }
    security_opt: [no-new-privileges:true]
    read_only: true          # where the app tolerates it
    tmpfs: [/tmp]            # writable scratch on read-only roots
    cap_drop: [ALL]
    pids_limit: 100
```

- Run as non-root, drop all capabilities, add back only what's needed.
- One process per container; use a real orchestrator (not Compose) for
  multi-container production workloads.

## Secret Handling

- **Never** in Dockerfile `ENV` or committed compose files — layers and git
  history persist them.
- Use `env_file: .env` (gitignored) or compose `environment: [API_KEY]` inheriting
  the host env; Docker/Swarm `secrets:` for swarm deployments.
- Isolated test harnesses: no host credentials mounted by default, no network
  by default (`network_mode: none`), opt-in networked profiles, read-only
  source mounts with writable tmpfs copies. Rotate any secret that leaked.

## .dockerignore (complete baseline)

```
node_modules
.git
.env
.env.*
dist
coverage
*.log
.next
.cache
docker-compose*.yml
Dockerfile*
tests/
```

## Log Hygiene & Debugging

```bash
docker compose logs -f app              # follow; --tail=50 for last lines
docker compose exec app sh              # shell in
docker stats                            # resource usage
docker compose up --build               # rebuild; add --no-cache to force
```

- Log to stdout/stderr only; let the runtime collect logs (one line per event,
  structured when possible). Don't log to files inside the container.
- Use `docker compose down -v` only when you truly mean to delete volumes;
  `docker system prune` removes unused images/containers.

## Anti-Patterns

- `:latest` tags, running as root, secrets in images/compose
- Data stored in containers without volumes (ephemeral → lost on restart)
- One giant container running multiple services
- Binding internal service ports to `0.0.0.0` in production
