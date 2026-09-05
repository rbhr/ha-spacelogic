# SpaceLogic C-Gate: project instructions

## Start here

- Read [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) before planning or changing this project. It records the complete numbered audit, decisions, releases, and remaining work. Read [README.md](README.md) for user-facing behavior.
- Check the working tree, recent commits, tags, and relevant PRs before reporting current state. The status document is dated evidence; check for subsequent changes.
- Keep all audit items 1–9 visible when discussing the audit. Distinguish implemented, merged/released, and validated on physical hardware. Do not infer that completed items are unfinished because a chat summary omitted them.
- Update the status document when scope, implementation, validation, releases, or next steps change. Keep these instructions concise; put session history in the status document. Do not store credentials or installation secrets.
- `CLAUDE.md` points to these same documents. The old local `plan.md` is superseded and is not an active implementation plan.

## Project and invariants

- Home Assistant custom integration, domain `spacelogic_cgate`, under `custom_components/spacelogic_cgate/`; Python >=3.13. It uses asyncio TCP with no additional runtime pip requirements.
- `cgate.py` owns command (20023), event (20024), and status/SCP (20025) connections. `__init__.py` owns entry lifecycle, shared discovery, and measurement seeding. Platform modules consume the client in `entry.runtime_data`.
- Preserve config entry IDs and existing entity/device identifiers when reconfiguring connections. Group IDs use network/application/group; measurement IDs use network/application/device/channel. Host and project name are not part of entity IDs.
- Accept events and replies only for the configured project and appropriate application: lighting 56, measurements 228. Validate full reply addresses before updating state.
- Unknown group levels are `None`, not zero. Failed/skipped reads preserve known state. Only C-Gate error 401 marks a group virtual. An unknown lock must not appear locked.
- Serialize command exchanges. Invalidate interrupted exchanges before another command can consume a late reply; do not automatically replay actuator commands. Preserve cancellation-safe cleanup and reconnect supervision.
- Quiet event/SCP sockets are normal: use TCP keepalive, not idle read timeouts. Keep reconnect resync, availability callbacks, and measurement polling intact.
- Discovery runs once per entry setup; platforms share its cache. Measurement polling includes persisted and newly observed channels without overlapping refreshes.

## Validation

Install development dependencies with `pip install -e ".[dev]"`. Use the existing virtual environment when available:

```sh
.venv/bin/pytest tests/ -q
.venv/bin/ruff check .
.venv/bin/mypy custom_components/spacelogic_cgate/
git diff --check
```

Use meaningful regression tests for behavior changes, including real Home Assistant lifecycle/service tests where dispatch or cleanup matters. Documentation-only changes need a diff and link/content review, not another software release. Consult the status document for the tested HA version and the unverified advertised compatibility floor.

## Working and release preferences

- The maintainer reported being the only user as of 2026-09-05. HACS beta releases are their practical route to testing on the actual installation.
- When asked to bump the beta and merge, finish the version bump, validation, PR merge, tag, and GitHub prerelease without repeatedly asking for the same authorization. This preference does not request a release on every change.
- Bump both `pyproject.toml` and `custom_components/spacelogic_cgate/manifest.json` to the same next beta, such as `1.2.2bN`; tag it `v1.2.2bN`. Check existing releases first and never move an existing tag.
- Follow the established release path: pass local checks; push a PR; wait for Tests, HACS Validation, and Hassfest Validation; merge the tested head (use `--match-head-commit`); update local main; verify the merge tree matches the tested tree; tag the merge commit and push the tag; create a GitHub prerelease with `--verify-tag --prerelease --latest=false`.
- Record the PR, merge commit, tag, test results, and pending hardware validation. Do not present automated tests as physical-device validation or promote a beta to stable without a request to do so.
