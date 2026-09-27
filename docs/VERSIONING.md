# Versioning & Release Process

How SIP is versioned, changelogged, tagged and released. Adopted 2026-09-26.

## Scheme — Semantic Versioning
Versions are `MAJOR.MINOR.PATCH` ([SemVer 2.0](https://semver.org)):

| Bump | When | Examples here |
|------|------|---------------|
| **MAJOR** | Breaking change to an API contract, DB schema, or deployment model that needs coordinated action | SQL Server → Postgres cutover; removing/renaming an API route |
| **MINOR** | New backward-compatible capability | Fleet Overview (3.3.0), external partner API (3.2.0), Datadog paging (3.1.0) |
| **PATCH** | Backward-compatible bug fix, perf fix, or docs/infra-only change | cache-header fix (3.3.0), OTel opt-out (3.4.0) |

Pre-release work uses a `-rc.N` / `-dev` suffix when needed (e.g. `3.5.0-rc.1`).

## Single source of truth
The canonical version lives in **`backend/app/core/config.py`** (`app_version`).
`.env.example` (`APP_VERSION`) and the `deploy/` ConfigMap mirror it. A release PR
updates all three together; they must never drift.

## The changelog is the record
- **Every PR** adds a bullet under `[Unreleased]` in [`CHANGELOG.md`](../CHANGELOG.md),
  grouped as **Added / Changed / Fixed / Removed / Deprecated / Security**, and
  referencing its PR number.
- Keep entries user-facing (what changed and why it matters), not commit-level noise.

## Cutting a release
1. Decide the bump (table above) from what's under `[Unreleased]`.
2. In one **release PR**:
   - Move `[Unreleased]` items into a new `## [X.Y.Z] — YYYY-MM-DD — <title>` section.
   - Bump `app_version` in `config.py` (+ `.env.example`, `deploy/` ConfigMap).
   - Update the compare/link footer in `CHANGELOG.md`.
3. Merge to `main`.
4. **Tag** the merge commit: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.
5. **GitHub Release** for the tag; paste that version's changelog section as the notes.

## How releases map to environments (CI/CD)
Tracks the deploy design in the CI/CD workflows:

| Trigger | Builds | Deploys to |
|---------|--------|-----------|
| PR | test only (no publish) | — |
| merge to `main` | images `:<sha>` + `:dev` → GHCR | **dev** (AWS Rancher nonprod, auto) |
| merge to `prod` **or** tag `vX.Y.Z` | promote the **same** image `:<sha>` | **prod** (Rancher prod, gated by approval) |

Promotion re-deploys the **same artifact** dev → prod — never a rebuild. Prod
deploys require the `prod` GitHub Environment's required-reviewer approval.

## Conventions
- Branch names: `feat/…`, `fix/…`, `chore/…`, `ci/…`, `refactor/…`.
- Commits/PRs are plain and descriptive; no tool-generated attribution trailers.
- A hotfix to prod is a `PATCH` off the prod line, changelogged and tagged like any release.
