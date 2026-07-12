# Git, PR, and changelog conventions

All conventions below are existing practice, inferred from git history and
`.github/` — none are proposals.

## Branches

Branch = target version number, branched off `main`:

```
0.9.0        # feature release in flight
0.8.4        # patch release
```

Never `fix/…` or `feat/…` (one legacy `feat/nas-external-libraries` exists;
it's the exception, not the pattern). Semver while 0.x: patch (0.x.Y) = fixes
only; features bump the minor.

## Commits

Conventional commits with a scope; imperative, lower-case subject. Real
examples from history:

```
fix(printers): dedupe print-job history import case-insensitively
feat(auth): rate-limit login and refresh (P3-02)
perf(rbac): resolve accessible collections in one query
test(storage): MinIO integration fixture; fix S3 bucket auto-create
refactor(ui): migrate connect/config cards to shadcn semantic tokens
docs(roadmap): drop "a public cloud service" from Not Planned
chore(release): bump to 0.8.5
ci: pin QEMU binfmt image to fix arm64 build hangs
```

Backlog IDs from `reports/05-master-triage-backlog.md` (e.g. `(P3-02)`) go in
parentheses at the end when the commit closes one.

Commit as the repo's configured identity (`git config user.email`) — never an
address from session context; GitHub attributes by verified email.

## Pull requests

- One PR per bug/feature; small enough to review in one sitting
  (`CONTRIBUTING.md`).
- Fill `.github/PULL_REQUEST_TEMPLATE.md`: Summary bullets, the three Testing
  checkboxes (check them truthfully), and Notes for any schema, API, storage,
  or printer-provider behavior change.
- Title reads like a commit subject: `fix(backup): quiesce background loops
  during restore`.
- Behavior changes, larger features, and data-model/API changes should have an
  issue first (`.github/ISSUE_TEMPLATE/` has bug_report and feature_request
  forms).

## Changelog

`CHANGELOG.md`, newest release first. Entry format used by recent releases
(0.8.x+):

```markdown
## X.Y.Z

### Security
### Fixed
### Performance
### Added
```

Only include sections that apply. Bullets are user-facing sentences (bold
lead-in for headline items), not commit subjects. Write the entry on the
version branch before tagging. Upgrade-behavior warnings go in a bold callout
line at the top of the entry (also surfaced in the GitHub release body — see
[release.md](release.md)).

## Versioning

The version lives in a triple that must always match:

- `backend/pyproject.toml` → `version`
- `backend/app/core/config.py` → `app_version`
- `frontend/package.json` → `version`

Plus the git tag `vX.Y.Z`. Bump all three in one `chore(release): bump to
X.Y.Z` commit; never bump outside a release.

## Handling breaking changes

Avoid them while self-hosters auto-upgrade. If unavoidable:

- Migration path required (Alembic migration + data backfill — see
  [backend.md](backend.md)).
- Document in the changelog's bold callout line and `UPGRADE.md`.
- Never silently change API response shapes; additive changes only within a
  0.x line where possible, and note anything else in the PR's Notes section.
