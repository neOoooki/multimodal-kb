# Findings brief — Compose orchestration patterns (and the Qdrant/init deadlock)

Scope: Docker Compose startup ordering, one-shot init jobs, liveness vs readiness, restart/profile
semantics, deploy-wrapper design, and fresh-install CI. Sources are official docs plus Docker's own
Compose source (`docker/compose` @ main, i.e. Compose v5.x, 2026) where the docs are ambiguous.

## Root cause of the reported deadlock (the pattern, not the bug)

The causality is inverted. `/health` was made to report an **application-readiness** fact (collection
exists) but that fact can only be produced by `kb init`, and `kb init` is gated behind the very signal
that consumes it:

```
deploy.sh polls 200 ──requires──> collection exists ──requires──> kb init ──runs after──> poll succeeds
```

Two properties made it fatal rather than flaky:

1. **Docker healthchecks are binary and have no "ready but not live" concept.** Anything in the
   healthcheck feeds *both* `depends_on: service_healthy` *and* `up --wait`, so any readiness content
   you put there becomes a startup gate.
2. **`docker compose up --wait` fails fast on `unhealthy`** (verified in source: it returns
   `container X is unhealthy` immediately instead of polling until `--wait-timeout`). So a 503 from a
   missing collection doesn't degrade gracefully — it aborts bring-up.

The fix is ordering + endpoint separation, not weakening health reporting.

---

## Q1 — `docker compose up --wait`: exact semantics, exit codes, and whether it replaces curl polling

**(a) Recommended pattern.** Yes — replace the hand-rolled poll loop with
`docker compose up -d --build --wait --wait-timeout <N>`. Treat the exit code as the gate. Do *not*
use it as a substitute for a business-readiness assertion (see below).

**(b) Documented semantics.**

- `--wait`: "Wait for services to be running|healthy. Implies detached mode."
- `--wait-timeout`: "Maximum duration in seconds to wait for the project to be running|healthy".
  Default `0` = wait indefinitely. Negative values are rejected (`--wait-timeout must be a
  non-negative integer`).
- Exit codes: `1` on any error; `0` on success; `0` on SIGINT/SIGTERM after stopping containers.
- Incompatible with `--abort-on-container-exit`, `--abort-on-container-failure`, `--attach`,
  `--attach-dependencies`, `--watch`.

**Semantics verified in source** (`pkg/compose/start.go`, `pkg/compose/service_containers.go`), because
the docs don't spell these out:

- After starting everything, Compose builds a synthetic `depends_on` entry for **every** service and
  waits on it. Poll interval is 500 ms.
- Per-service condition is `service_completed_successfully` **if some other service declares that
  condition on it**, otherwise `running_or_healthy`. (This is the fix from
  [docker/compose#9572](https://github.com/docker/compose/pull/9572); the commit comment is explicit:
  *"or `--wait` will never finish waiting for one-shot containers"*.)
- `running_or_healthy` resolution: container `Exited` → error `container X exited (N)`. No healthcheck
  → falls back to requiring `running`. Health `healthy` → satisfied. Health `starting` → keep polling.
  Health **`unhealthy` → error `container X is unhealthy`, immediately**.
- `service_completed_successfully` resolution: exit 0 → satisfied; non-zero → `service "X" didn't
  complete successfully: exit N`, and the dependent never starts.
- Timeout expiry → `application not healthy after <N>s`.

**Two traps that matter for this project:**

1. A one-shot/init service that **nothing depends on with `service_completed_successfully`** is waited
   on as `running_or_healthy`; its exit (even code 0) makes `--wait` fail with
   `container X exited (0)`. The init service must be a declared dependency.
2. `--wait` cannot express "ready" for readiness that depends on data initialization. It can only
   express "healthy per the healthcheck". So the healthcheck must not encode the collection's
   existence (Q3).

**(c) Sources.**
- https://docs.docker.com/reference/cli/docker/compose/up/ (raw: `_vendor/github.com/docker/compose/v5/docs/reference/compose_up.md`)
- https://github.com/docker/compose/pull/9572
- https://github.com/docker/compose/blob/main/pkg/compose/start.go
- https://github.com/docker/compose/blob/main/pkg/compose/service_containers.go

---

## Q2 — One-shot init/migration containers in Compose

**(a) Recommended pattern.** Two options; pick by Compose version.

- **Portable (Compose v2.x+): one-shot service + `service_completed_successfully`.**
  `restart: "no"`, no ports, and the app declares the dependency. This is the pattern the ecosystem
  has used for years and it is still what Docker documents under "Control startup order".
- **New preferred (Compose ≥ v5.3.0): `pre_start` init containers.** Docker's own docs now say
  `pre_start` is preferable and explicitly title a section *"Replace the one-shot service pattern"*.
  Each step is an ephemeral container created after the service's `depends_on` conditions are
  satisfied and before the service starts; it joins the same networks and shares volumes; it must
  exit 0.

`pre_start` advantages per Docker docs: setup is a subordinate step rather than a peer exited
service; completed steps don't show as exited services in `docker compose ps`; no web of `depends_on`
edges; inherits the service image. A step is **skipped on later `up` runs if it previously succeeded
and its definition is unchanged**, and reruns on definition change, prior failure, or
`--force-recreate`.

Caveat: this is brand new. Gate it on `docker compose version`.

**Is this the recommended way to do "create collection/index if not exists"? Yes** — as an explicit,
ordered, idempotent step before the app starts. Not via health-endpoint blackmail.

**(b) Snippets.**

Portable:

```yaml
services:
  mmkb-init:
    build: .
    command: ["kb", "init"]        # MUST be idempotent
    restart: "no"
    depends_on:
      qdrant:
        condition: service_healthy

  mmkb:
    build: .
    depends_on:
      mmkb-init:
        condition: service_completed_successfully
      qdrant:
        condition: service_healthy
```

Compose ≥ v5.3.0 (`pre_start`):

```yaml
services:
  mmkb:
    build: .
    depends_on:
      qdrant:
        condition: service_healthy
    pre_start:
      - command: ["kb", "init"]    # runs in an ephemeral container, must exit 0
```

**Idempotency rules (non-negotiable either way):**

- `docker compose up` will (re)start an already-exited one-shot container, re-executing its command —
  so `kb init` runs on every `up`, not just the first. `pre_start` also reruns on definition change.
- Implement **check-then-create**, not blind create:
  1. `GET /collections/{name}` (qdrant-client: `collection_exists()`), or
  2. create and **treat HTTP 409 as success**. Qdrant maps `StorageError::AlreadyExists` →
     `409 CONFLICT` in `src/actix/helpers.rs`.
- Never use `recreate_collection()` on an existing install — it drops and recreates, i.e. data loss.
  Use it only in explicit tests/dev fixtures.
- Wrap in a short retry/backoff for the "Qdrant is up but still finishing startup" window.
- Add a guard so concurrent replicas can't race (a single init service is naturally single-instance;
  for multiple app replicas use `pre_start` with `per_replica: false` semantics or a lock).
- Don't gate the init service behind a profile the app doesn't share — Compose errors when a
  dependency is disabled by profiles.

**(c) Sources.**
- https://docs.docker.com/compose/how-tos/startup-order/
- https://docs.docker.com/compose/how-tos/init-containers/
- https://docs.docker.com/reference/compose-file/services/#pre_start
- https://github.com/compose-spec/compose-spec/blob/main/spec.md (depends_on, pre_start)
- Qdrant 409: https://github.com/qdrant/qdrant/blob/master/src/actix/helpers.rs
- qdrant-client `collection_exists` / `recreate_collection`:
  https://github.com/qdrant/qdrant-client/blob/master/qdrant_client/qdrant_client.py

---

## Q3 — Liveness vs readiness in HTTP health endpoints

**(a) Recommended pattern: split the endpoints, and point Compose's healthcheck at liveness.**

| Endpoint | Question | Success | Failure | Consumed by |
|---|---|---|---|---|
| `GET /livez` | Is the process alive and not deadlocked? | always `200` while the HTTP loop serves | `500` only on genuine non-recoverable internal failure | restart decisions; **Compose `healthcheck`** |
| `GET /readyz` | Can it serve traffic *right now*? | `200` when Qdrant reachable **and** collection exists **and** schema/version matches | `503` + JSON reasons | external LB / monitoring / orchestrators that actually gate traffic |
| `GET /health` | legacy alias | same as `/readyz` | `503` | keep for compat; don't invent a third meaning |

`/livez` must perform **zero** dependency I/O. `/readyz` may probe Qdrant and collection existence.

**(b) Why conflation causes exactly this deadlock.**

- Kubernetes: *"When your app has a strict dependency on back-end services, you can implement both a
  liveness and a readiness probe. The liveness probe passes when the app itself is healthy, but the
  readiness probe additionally checks that each required back-end service is available."* Merging them
  makes the app's own liveness hostage to a dependency.
- Kubernetes API-server health endpoints draw the operational line: *"/livez: Use this to determine if
  the API server should be restarted. If `/livez` returns a failure status code (such as 500), the API
  server is likely in a non-recoverable state, such as a deadlock... `/readyz`: Use this to determine
  if the API server is ready to accept traffic. If `/readyz` returns a failure status code, it
  indicates the server is still initializing or temporarily unable to serve requests... and traffic
  should be routed away from it."*
- Status codes: Kubernetes HTTP probes treat `200 ≤ code < 400` as success, so `503` is a correct
  readiness-failure code. Return `503` (not `500`) for "dependency not ready" and reserve `500` for
  "I am broken". A `503` on a *liveness* probe is what turns a dependency outage into a crash loop —
  K8s warns *"Incorrect implementation of liveness probes can lead to cascading failures."*
- Compose-specific amplifier: the healthcheck is the **only** readiness-ish signal Compose has, and it
  is the gate that must open before the init step can run. That is the circular wait. Also, in plain
  (non-Swarm) Compose, health status does **not** trigger restarts and does **not** unbind published
  ports — it is consumed only by `depends_on: service_healthy` and `up --wait`. So encoding
  application readiness there buys you nothing for traffic routing while costing you startup
  correctness.
- Docker HEALTHCHECK mechanics: exit `0` = healthy, `1` = unhealthy, `2` reserved. `curl -f` therefore
  converts any 4xx/5xx into `unhealthy`; `curl -f` without `-s`/`--fail` discipline is where people
  accidentally get false positives. Defaults: `interval 30s`, `timeout 30s`, `retries 3`,
  `start_period 0s`, `start_interval 5s` (Engine ≥ 25.0). `start_period` failures are not counted
  toward `retries`.
- Note the naming trap: Qdrant *itself* already exposes `/healthz`, `/livez`, `/readyz` (since
  v1.5.0), but they are "the most basic status response, returning HTTP 200 if Qdrant is started and
  ready to be used" — they say nothing about *your* collection. Qdrant's `/readyz` is the right target
  for `qdrant.healthcheck`, and the app's `/readyz` is where collection existence belongs.

**(c) Compose healthcheck block.**

```yaml
  mmkb:
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8088/livez"]
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 20s
```

**(d) Sources.**
- https://kubernetes.io/docs/concepts/workloads/pods/probes/
- https://kubernetes.io/docs/reference/using-api/health-checks/
- https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/
- https://docs.docker.com/reference/compose-file/services/#healthcheck
- https://docs.docker.com/reference/dockerfile/#healthcheck
- https://qdrant.tech/documentation/ops-monitoring/monitoring/

---

## Q4 — `depends_on` conditions, `profiles`, `restart` policies

**(a) Recommended pattern.**

| Feature | Use it for | Don't use it for |
|---|---|---|
| `condition: service_started` (also the short `depends_on: [x]` form) | pure ordering, no readiness need | anything where the dependent does I/O immediately |
| `condition: service_healthy` | `qdrant → mmkb`, `qdrant → mmkb-init` | services with no healthcheck (errors: `container X has no healthcheck configured`) |
| `condition: service_completed_successfully` | `mmkb-init → mmkb` | long-running services |
| `depends_on: {restart: true}` | re-start the dependent when the dependency is explicitly restarted | — |
| `profiles` | opt-in tooling: debug UIs, seeders, chaos toggles | gating a dependency of an always-on service |
| `restart: "no"` | one-shot init/migration job | long-running services |
| `restart: unless-stopped` / `always` | long-running services | init jobs (infinite re-run loop) |
| `restart: on-failure:N` | init job where you genuinely want bounded retries | as a substitute for `--wait` failing loudly |

Documented `restart` values: `no` (default), `always`, `on-failure[:max-retries]`,
`unless-stopped`. Docker's "Use Compose in production" guide recommends `restart: always` to avoid
downtime for long-running services. In plain Compose, restart policy triggers on **exit**, never on
`unhealthy`.

`profiles` gotcha: if a targeted service's dependency is gated behind a profile, you must ensure the
dependency is in the same profile, started separately, or unassigned. So the init service must not be
profile-gated if the app `depends_on` it.

**(b) Snippets.**

```yaml
  mmkb-init:
    build: .
    command: ["kb", "init"]
    restart: "no"                     # default; explicit is better
    depends_on:
      qdrant:
        condition: service_healthy
        restart: false

  qdrant:
    image: qdrant/qdrant:latest
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:6333/readyz || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 15s
    volumes: [qdrant_data:/qdrant/storage]

  mmkb:
    build: .
    restart: unless-stopped
    depends_on:
      mmkb-init:
        condition: service_completed_successfully
      qdrant:
        condition: service_healthy
```

**(c) Sources.**
- https://docs.docker.com/reference/compose-file/services/#depends_on
- https://docs.docker.com/reference/compose-file/services/#restart
- https://docs.docker.com/compose/how-tos/startup-order/
- https://docs.docker.com/compose/how-tos/profiles/
- https://docs.docker.com/compose/how-tos/production/

---

## Q5 — Should teams still hand-roll bash deploy scripts?

**(a) Recommended pattern (2025/2026): a thin wrapper with no orchestration logic in it.**

The wrapper's job is preflight, one command, and good failure output:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

# 0. stable, isolated project identity
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mmkb}"

# 1. preflight
docker compose version >/dev/null
docker compose config -q          # fail before touching the daemon

# 2. ONE convergence command: build, start, wait for deps + init job
if ! docker compose up -d --build --wait --wait-timeout 300; then
  echo "convergence failed; state:" >&2
  docker compose ps -a >&2
  docker compose logs --tail=200 >&2
  exit 1
fi

# 3. optional: ONE business-readiness assertion (not the orchestration gate)
for i in {1..20}; do
  curl -fsS http://localhost:8088/readyz >/dev/null && exit 0
  sleep 1
done
echo "API never became ready" >&2
docker compose logs --tail=200 mmkb >&2
exit 1
```

Everything the old script did *between* `up` and `kb init` is now expressed declaratively in the
Compose file, so it is inspectable with `docker compose config` and reused by CI.

**(b) Anti-patterns of hand-rolled polling loops.** From the reported bug and the pattern in the wild
(e.g. [pgEdge/ai-dba-workbench `ci-docker.yml`](https://github.com/pgEdge/ai-dba-workbench/blob/c84abaab0ce3544b63326aec877b193a8d6d1e14/.github/workflows/ci-docker.yml),
which polls `docker compose ps | grep healthy` and then curls `/health` in a `while` loop with a
hand-rolled timeout):

1. **Inverted causality.** Gating on an app-level readiness signal that can only be produced by a step
   the gate itself releases. This is the exact deadlock; it is structurally invisible to the author
   because each half looks reasonable.
2. **Reimplementing readiness worse.** No `start_period` (fresh containers are judged during warm-up),
   no distinction between `starting` and `unhealthy`, no fail-fast, no per-dependency conditions.
3. **Two disagreeing timeouts.** The healthcheck's `interval × retries` and the script's 120 s drift
   apart, so the script can give up while the healthcheck is still legitimately starting (or vice
   versa).
4. **No `-f` / wrong curl semantics.** `curl localhost:8088/health` exits 0 on a 500; with `-f`, exit
   7 (connection refused) and 22 (HTTP error) get conflated into "not ready".
5. **Cross-project collisions.** Polling a published port with no `COMPOSE_PROJECT_NAME` can hit a
   stale container from a different project or a leftover from a previous run.
6. **Dirty-state blindness.** Nothing in the script distinguishes a fresh install from an upgrade, so
   clean-install breakage ships (their bug class).
7. **Ordering that only exists in bash.** `docker compose config` can't validate it, CI can't reuse it,
   and `docker compose run`/`pre_start` users can't see it.

**(c) Where the community genuinely disagrees.**

- Some teams keep a readiness poll **after** `--wait`, because `--wait` has no healthy-vs-ready
  distinction and its fail-fast-on-`unhealthy` behavior has changed across Compose versions. That is a
  legitimate, narrow use (a single post-convergence assertion, as in the snippet above) — not a
  replacement for declarative ordering.
- There is no official Docker statement forbidding wrapper scripts; Docker documents `--wait` and
  lifecycle hooks but does not publish an "anti-patterns of deploy scripts" page. The strongest
  official signal is Docker's own docs calling `pre_start` preferable to one-shot services and
  documenting `--wait` as the supported way to wait for `running|healthy`.

**(d) Sources.**
- https://docs.docker.com/reference/cli/docker/compose/up/
- https://docs.docker.com/compose/how-tos/init-containers/
- https://docs.docker.com/compose/how-tos/production/
- https://github.com/docker/compose/pull/9572

---

## Q6 — Testing "fresh install" automatically in CI

**(a) Recommended pattern.**

1. **Use an ephemeral runner** so the host is genuinely clean. GitHub-hosted runners are a new VM per
   job: *"each GitHub-hosted runner is a new virtual machine (VM) hosted by GitHub."*
2. **Always tear down volumes**: `docker compose down -v --remove-orphans` in an `if: always()` step.
3. **Isolate the project** with `COMPOSE_PROJECT_NAME=mmkb-ci-${{ github.run_id }}` so parallel jobs
   never share named volumes or host ports.
4. **Make the fresh-install path itself a test target**: `docker compose up -d --build --wait
   --wait-timeout 300` on a runner that has never seen the project. If the init path is broken, this
   step fails — no extra assertions needed.
5. **Then** a fixture-based smoke ingest: rely on the init/seed step (or `docker compose run --rm
   --no-deps seed`), push a fixture, assert count/query results via the API.
6. **Test both paths, in two jobs:** *fresh install* (no volumes at all) and *upgrade* (populate, then
   `up` again **without** `down -v`, assert the init step is idempotent and data survived). The
   upgrade job is what catches non-idempotent `kb init`.
7. **Assert the negative case explicitly:** one job that starts from zero must never pre-create the
   collection. That is the regression test for this deadlock.
8. Dump `docker compose ps -a` and `docker compose logs` on failure; upload as artifacts.
9. Cache build layers (buildx `cache-from/to`), never the Qdrant volume.

**(b) Snippet.**

```yaml
name: ci
on: [push, pull_request]
jobs:
  fresh-install:
    runs-on: ubuntu-latest
    env:
      COMPOSE_PROJECT_NAME: mmkb-ci-${{ github.run_id }}
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3

      # NO cache of any kind for the DB volume: this must be a cold start.
      - name: Converge from a clean state
        run: docker compose up -d --build --wait --wait-timeout 300

      - name: Smoke ingest (fixture)
        run: ./scripts/smoke.sh            # POST one fixture doc, assert count == 1

      - name: Idempotency — run the init path again
        run: |
          docker compose run --rm --no-deps mmkb kb init
          ./scripts/smoke.sh               # must still pass, data intact

      - name: Diagnostics on failure
        if: failure()
        run: |
          docker compose ps -a
          docker compose logs --tail=300

      - name: Teardown
        if: always()
        run: docker compose down -v --remove-orphans
```

For app-level integration tests (rather than full-stack bring-up), Testcontainers is the mainstream
alternative and Docker now documents it extensively.

**(c) Sources.**
- https://docs.github.com/en/actions/concepts/runners/github-hosted-runners
- https://github.com/pgEdge/ai-dba-workbench/blob/c84abaab0ce3544b63326aec877b193a8d6d1e14/.github/workflows/ci-docker.yml
- https://docs.docker.com/reference/cli/docker/compose/down/
- https://docs.docker.com/testcontainers/
- https://docs.docker.com/guides/testcontainers-python-getting-started/

---

## Concrete recommendation for this repo

1. **Fix the API endpoints, honestly but separately.** `/livez` = process only, never 503.
   `/readyz` = Qdrant reachable + collection exists + schema version matches, 503 with reasons
   otherwise. Keep `/health` as an alias of `/readyz` for compatibility (or return 200 with a detail
   body; do not let it mean a third thing).
2. **Move collection creation out of `deploy.sh` and into the Compose dependency graph** — either a
   one-shot `mmkb-init` service with `restart: "no"` plus `depends_on: {condition:
   service_completed_successfully}`, or `pre_start: [{command: ["kb", "init"]}]` if
   `docker compose version` ≥ v5.3.0.
3. **Make `kb init` idempotent**: `collection_exists()` → create; treat 409 as success; never
   `recreate_collection`.
4. **Point the app's Compose `healthcheck` at `/livez`** with `start_period` and `retries` sized to
   actual boot time; point Qdrant's healthcheck at Qdrant's own `/readyz`.
5. **Reduce `deploy.sh` to** `docker compose up -d --build --wait --wait-timeout 300` + one
   `/readyz` assertion + log dump on failure. Delete the 120 s curl loop and the
   `docker compose exec mmkb kb init` line.
6. **Add a CI job that runs the fresh-install path with no cached volume**, plus an upgrade-path job
   that re-runs the init step against existing data.

## Open items / doc inconsistencies

- The compose-spec short-syntax text for `depends_on` says Compose "waits for dependency services to be
  'ready'", while the startup-order guide correctly says it waits only until running and that
  `service_started` is the short-syntax equivalent. Operationally the short form = **started only**.
- `pre_start`'s spec entry documents `per_replica: true`, but the how-to's Limitations section says
  per-replica execution "is not yet supported". Verify on your Compose build before relying on it.
- `--wait` fail-fast-on-`unhealthy` and the one-shot condition fix both landed via
  [PR #9572](https://github.com/docker/compose/pull/9572); pin/verify `docker compose version` in
  `deploy.sh` if you depend on either behavior.
