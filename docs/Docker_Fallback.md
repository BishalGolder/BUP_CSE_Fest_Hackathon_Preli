# GridWise — Docker Fallback Pull & Run

The judging harness may pull a pre-built container image as a fallback execution
path. This file is the **single source of truth** for the documented
`docker pull` / `docker run` commands, exposed port, and required
environment-variable names.

> The image must be **pullable** during evaluation, expose the service port,
> bind to `0.0.0.0`, and must **not** contain baked-in secrets.

---

## 1. Image

| Item | Value |
|------|-------|
| Registry | Docker Hub (or GHCR — see submission package) |
| Repository | `<team-dockerhub-user>/gridwise` |
| Tag (example) | `v1.0.0` |
| Digest (example) | `sha256:<digest>` |
| Base image | `python:3.11-slim` |
| Exposed port | `8000` |
| Bind address | `0.0.0.0` |

> Final tag/digest will be the one listed in the submission package after
> the round ends. Replace `<tag>` below with the submitted value.

## 2. Pull

```bash
docker pull <team-dockerhub-user>/gridwise:v1.0.0
```

(Alternative by digest:)

```bash
docker pull <team-dockerhub-user>/gridwise@sha256:<digest>
```

## 3. Run (no LLM key — uses the offline stub interpreter)

The image starts the FastAPI service with `uvicorn` on port 8000. Without an
LLM key it falls back to the deterministic `stub` interpreter, which
satisfies the public sample replay tests.

```bash
docker run --rm -p 8000:8000 \
  -e GRIDWISE_LLM_PROVIDER=stub \
  <team-dockerhub-user>/gridwise:v1.0.0
```

## 4. Run (with a real LLM provider — recommended)

```bash
docker run --rm -p 8000:8000 \
  -e GRIDWISE_LLM_PROVIDER=groq \
  -e GROQ_API_KEY="$GROQ_API_KEY" \
  -e GRIDWISE_LLM_MODEL="llama-3.3-70b-versatile" \
  <team-dockerhub-user>/gridwise:v1.0.0
```

Equivalent `OpenAI` / `Anthropic` / `Google` env-var names are documented in
`.env.example` and the README.

## 5. Health & smoke tests

```bash
# readiness
curl -fsS http://localhost:8000/health
# expected: {"status":"ok"}

# one public-sample /optimize-energy call (uses stub when no key is provided)
curl -fsS -X POST http://localhost:8000/optimize-energy \
  -H 'Content-Type: application/json' \
  --data @tests/fixtures/SAMPLE-01.request.json
```

## 6. Built-in safety guarantees

- The image runs as the unprivileged user `gridwise` (`USER gridwise`).
- The image contains **no** API keys or secrets — keys are passed at run-time
  via `-e` / `--env-file`. `.env` and `.env.local` are excluded by
  `.dockerignore`.
- A `HEALTHCHECK` curls `/health` every 30 s.
- The service binds to `0.0.0.0:8000` (Docker `EXPOSE 8000`) so the judging
  harness can reach it without a VPN or private network.

## 7. Reproducible rebuild (optional)

```bash
docker build -t <team-dockerhub-user>/gridwise:v1.0.0 .
docker push <team-dockerhub-user>/gridwise:v1.0.0
```

The `Dockerfile` is the canonical build definition; the
`docker-compose.yml` in the repo root provides the equivalent service
description for local development.
