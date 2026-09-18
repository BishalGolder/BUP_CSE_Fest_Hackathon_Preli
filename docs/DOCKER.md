# GridWise — Docker Build, Run, and Publish Guide

This is the canonical reference for the GridWise container image: how to
build it locally, run it for development or smoke-testing, and publish
it to a public registry (Docker Hub or GHCR) so the judging harness can
pull it during evaluation.

> **Security first.** The image never contains API keys. All secrets
> (`GROQ_API_KEY`, `LLM_API_KEY`, …) are passed at run time via
> `-e KEY=value` or `--env-file .env`. `.env` is excluded from the
> build context by `.dockerignore`.

---

## 1. Image facts

| Item            | Value                                                         |
|-----------------|---------------------------------------------------------------|
| Base image      | `python:3.11-slim`                                            |
| Build context   | repository root                                               |
| Working dir     | `/app`                                                        |
| Exposed port    | `8000`                                                        |
| Bind address    | `0.0.0.0`                                                     |
| Service user    | unprivileged `gridwise` (UID assigned by `useradd`)           |
| Healthcheck     | `curl -fsS http://localhost:8000/health` every 30 s           |
| Solver          | PuLP-CBC binary shipped inside the PuLP wheel (no apt step)   |

---

## 2. Build locally

From the repository root:

```bash
docker build -t gridwise:local .
```

Verify the image metadata:

```bash
docker inspect gridwise:local --format '{{.Config.User}} {{.Config.ExposedPorts}}'
# expect: gridwise map[8000/tcp:{}]
```

Sanity-check the entrypoint without starting the server:

```bash
docker run --rm gridwise:local python -c "import app.main; print('ok')"
```

---

## 3. Run locally

### 3.1 Offline stub (no API key)

Useful for smoke-testing the container itself. The API still serves
`/health`, `/docs`, and `/optimize-energy`, but the LLM interpreter
returns deterministic stubs.

```bash
docker run --rm -p 8000:8000 \
  -e GRIDWISE_LLM_PROVIDER=stub \
  gridwise:local
```

### 3.2 With a real Groq key (recommended)

```bash
docker run --rm -p 8000:8000 \
  -e GRIDWISE_LLM_PROVIDER=groq \
  -e GRIDWISE_LLM_MODEL=qwen/qwen3.8-27b \
  -e GROQ_API_KEY="$GROQ_API_KEY" \
  gridwise:local
```

On Windows PowerShell:

```powershell
$env:GROQ_API_KEY="gsk_..."
docker run --rm -p 8000:8000 `
  -e GRIDWISE_LLM_PROVIDER=groq `
  -e GRIDWISE_LLM_MODEL=qwen/qwen3.8-27b `
  -e GROQ_API_KEY="$env:GROQ_API_KEY" `
  gridwise:local
```

### 3.3 With an env file

```bash
# .env (gitignored)
GRIDWISE_LLM_PROVIDER=groq
GRIDWISE_LLM_MODEL=qwen/qwen3.8-27b
GROQ_API_KEY=gsk_...

docker run --rm -p 8000:8000 --env-file .env gridwise:local
```

### 3.4 With `docker compose`

```bash
docker compose up --build
# API at http://127.0.0.1:8000
```

The compose file reads variables from your shell environment (or a
local `.env`), so no secrets are baked into the image.

### 3.5 Smoke test

In a separate terminal after the container is up:

```bash
curl -fsS http://localhost:8000/health
# {"status":"ok"}

curl -fsS -X POST http://localhost:8000/optimize-energy \
  -H 'Content-Type: application/json' \
  --data @body.json
```

The repo does not ship pre-extracted request fixtures. To build one,
copy any `cases[i].input` from
`BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json` into `body.json`
and POST it.

---

## 4. Publish to a public registry

The judging harness may pull a pre-built image. Pick **one** of the two
options below.

### 4.1 Option A — GitHub Container Registry (GHCR) — recommended

GHCR is free for public images and the natural fit for a GitHub repo.

```bash
# 0. One-time: create a PAT with `write:packages` scope.
#    https://github.com/settings/tokens

# 1. Login
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <github-username> --password-stdin

# 2. Build with the canonical tag
docker build -t ghcr.io/<github-username>/gridwise:v1.0.0 .

# 3. Push
docker push ghcr.io/<github-username>/gridwise:v1.0.0

# Optional: also tag and push :latest
docker tag  ghcr.io/<github-username>/gridwise:v1.0.0 ghcr.io/<github-username>/gridwise:latest
docker push ghcr.io/<github-username>/gridwise:latest
```

Make the package public on GitHub:
`github.com/<user>?tab=packages` → click the package → "Package settings"
→ "Change visibility" → Public.

Anyone (including the judges) can then pull it:

```bash
docker pull ghcr.io/<github-username>/gridwise:v1.0.0
docker run --rm -p 8000:8000 \
  -e GRIDWISE_LLM_PROVIDER=groq \
  -e GROQ_API_KEY="$GROQ_API_KEY" \
  ghcr.io/<github-username>/gridwise:v1.0.0
```

### 4.2 Option B — Docker Hub

```bash
# 0. One-time: create a Docker Hub account and a public repo named
#    "gridwise" at https://hub.docker.com/

# 1. Login
docker login -u <dockerhub-username>

# 2. Build + tag
docker build -t <dockerhub-username>/gridwise:v1.0.0 .

# 3. Push
docker push <dockerhub-username>/gridwise:v1.0.0
```

Pull / run from anywhere:

```bash
docker pull <dockerhub-username>/gridwise:v1.0.0
docker run --rm -p 8000:8000 \
  -e GRIDWISE_LLM_PROVIDER=groq \
  -e GROQ_API_KEY="$GROQ_API_KEY" \
  <dockerhub-username>/gridwise:v1.0.0
```

### 4.3 Pinning by digest (most reproducible)

Tags are mutable; SHA256 digests are not. After pushing, capture the
digest and reference it:

```bash
docker build -t ghcr.io/<user>/gridwise:v1.0.0 .
docker push  ghcr.io/<user>/gridwise:v1.0.0

DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/<user>/gridwise:v1.0.0)
echo "$DIGEST"
# ghcr.io/<user>/gridwise@sha256:abc123…

# Pin by digest from now on:
docker pull ghcr.io/<user>/gridwise@sha256:abc123…
```

Add the digest line to your submission notes so the judging harness
can pull a bit-for-bit reproducible image.

### 4.4 Multi-arch build (arm64 + amd64)

If the judges may run on Apple Silicon or AWS Graviton, build a
multi-arch manifest:

```bash
docker buildx create --use --name gridwise-builder
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/<user>/gridwise:v1.0.0 \
  --push .
```

---

## 5. Versioning policy

Use [SemVer](https://semver.org/) for image tags:

| Tag       | When                                              |
|-----------|---------------------------------------------------|
| `vMAJOR.MINOR.PATCH` | Stable releases — what the judges should pull |
| `latest`  | Same as the latest stable tag                     |
| `sha-abc1234` | Optional — CI build of a specific commit     |

Always keep `latest` in sync with the most recent `vX.Y.Z`. The
judges will use whichever tag is listed in the submission package.

---

## 6. Built-in safety guarantees

The committed `Dockerfile` already enforces:

- **No secrets baked in.** `.env` and any `gsk_*` / `sk-*` strings are
  excluded from the build context by `.dockerignore`.
- **Unprivileged user.** Runs as `gridwise`, not `root`.
- **Bind address `0.0.0.0`** so the judging harness can reach the
  service without VPN or private networking.
- **HEALTHCHECK** on `/health` every 30 s — orchestrators can restart
  unhealthy containers automatically.
- **Minimal attack surface.** Only `curl` + `ca-certificates` are
  installed on top of `python:3.11-slim`; PuLP ships its own CBC
  solver binary so no compiler toolchain is required.

---

## 7. Troubleshooting

| Symptom                                                | Likely cause / fix                                      |
|--------------------------------------------------------|---------------------------------------------------------|
| `docker: command not found`                            | Install Docker Desktop (Win/Mac) or docker.io (Linux)   |
| `permission denied while trying to connect to docker`  | Add user to `docker` group, or use `sudo docker …`      |
| `pull access denied`                                   | Image is private — flip visibility to Public in GHCR    |
| Container exits immediately with `LLM_API_KEY … required` | You forgot `-e GROQ_API_KEY=...` (or `LLM_API_KEY`) |
| `port 8000 already in use`                             | `docker run -p 8001:8000 …` or stop the conflicting app |
| 429 / OTPM rate limits during evaluation               | Pre-warm the model or upgrade to Groq Dev Tier          |
| Container is healthy but `/optimize-energy` 500s       | Check `docker logs gridwise-api`; most often a guardrail violation from the LLM |

---

## 8. One-liner TL;DR for the judges

```bash
docker pull ghcr.io/<github-username>/gridwise:v1.0.0
docker run --rm -p 8000:8000 \
  -e GRIDWISE_LLM_PROVIDER=groq \
  -e GROQ_API_KEY="$GROQ_API_KEY" \
  ghcr.io/<github-username>/gridwise:v1.0.0
# then: POST http://localhost:8000/optimize-energy
```
