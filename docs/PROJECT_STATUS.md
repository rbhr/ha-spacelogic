# Project status and durable context

Last updated: **2026-09-05**. Repository: [rbhr/ha-spacelogic](https://github.com/rbhr/ha-spacelogic).

This is the maintained handoff for future sessions. It preserves decisions and evidence from the project audit and implementation work; it is not a verbatim chat transcript. Verify newer commits and releases before treating this snapshot as current.

## Current state

**All nine numbered audit findings have implemented fixes, merged and released across `v1.2.2b1` and `v1.2.2b2`. Physical C-Gate validation is still pending.** No results from installing these betas on the maintainer's hardware have been reported yet. This does not establish that every possible project defect is fixed.

- Latest beta: [v1.2.2b2](https://github.com/rbhr/ha-spacelogic/releases/tag/v1.2.2b2), package/manifest version `1.2.2b2`.
- Beta 2 code merge: `246f409aa730ca29ad8bb707393a71a0f5182e7a`. Local and remote main matched this commit before the documentation handoff was added.
- Latest stable: [v1.2.1](https://github.com/rbhr/ha-spacelogic/releases/tag/v1.2.1). Both 1.2.2 betas are GitHub prereleases, not the latest stable release.
- Latest recorded code validation: **183 tests passed**, Ruff passed, mypy passed, and `git diff --check` passed. GitHub Tests, HACS Validation, and Hassfest Validation passed on PR #7.
- Next step: the maintainer tests beta 2 through HACS and reports behavior. No additional implementation from the numbered audit is waiting to be started.

## Complete audit ledger

The initial review had nine findings despite a passing baseline of 123 tests. The maintainer first requested items 1–3 and 9, then requested 4–8. Preserve those numbers when discussing scope.

| Item | Finding | Implemented result | Released in |
| --- | --- | --- | --- |
| 1 | Keepalive/task failure could stop recovery permanently and abort teardown. | Handle keepalive connection errors, signal unexpected listener completion, and finish cleanup even when a task has failed. | Beta 1, PR #6 |
| 2 | Cancelling a transmitted command could let its late reply satisfy the next command. | Bind exchanges to their original sockets, invalidate interrupted exchanges before releasing the command lock, isolate the project handshake, and validate level reply addresses. No automatic command replay. | Beta 1, PR #6 |
| 3 | Partial connection, entry setup, and config-flow failures leaked sockets/tasks. | Clean up failed and cancelled setup paths, use idempotent/shielded teardown, close every socket despite task failures, and abort transports when socket closure exceeds five seconds. | Beta 1, PR #6 |
| 4 | Events, measurement replies, and database discovery could mix projects/applications. | Filter by configured project and supported application before cache mutation/callbacks; validate the full measurement reply address and scope XML discovery to the project. | Beta 2, PR #7 |
| 5 | Virtual-group polls erased known levels; unread states appeared off or locked. | Represent unread levels with `None`; retain known values after skipped/failed reads, including virtual groups; publish unknown entity state until a read, event, or successful command establishes it. | Beta 2, PR #7 |
| 6 | Missing entity feature flags prevented expected HA service dispatch. | Advertise dimmer transition support and fan on/off plus speed support; relay lights remain on/off without transitions. Test actual HA service dispatch. | Beta 2, PR #7 |
| 7 | Measurement channels discovered after setup never joined the polling set. | Recompute a deduplicated union of persisted and live channels each cycle and coalesce overlapping refresh/reconnect polls. | Beta 2, PR #7 |
| 8 | Each actuator platform repeated discovery; first-run measurement probes could use the wrong network. | Discover/seed groups once before platform forwarding; retain all discovered networks, including measurement-only ones; avoid polling successful probes twice; retry setup on failed database discovery. | Beta 2, PR #7 |
| 9 | Recovery behavior did not meet the documented resync/availability/retry semantics. | Resync known nonvirtual groups after reconnect, publish only successful reads, notify actuator availability immediately, retain sensor refresh, and apply jittered retries capped at 120 seconds. | Beta 1, PR #6 |

Every row is **implemented, merged, and beta-released**. Hardware validation remains pending for the combined result; items 4–8 were not dropped or left deferred.

## Release evidence

| Release | Scope | PR / branch | Tested head | Merge commit / tag target | Validation |
| --- | --- | --- | --- | --- | --- |
| [v1.2.2b1](https://github.com/rbhr/ha-spacelogic/releases/tag/v1.2.2b1) | 1–3 and 9 | [#6](https://github.com/rbhr/ha-spacelogic/pull/6), `fix/cgate-recovery-beta` | `d43450f5ba96f8b71f5be0d7112905bb8a1e1384` | `4929e30e643e26272a31e7e6683e09b9fd9f1d28` | 163 tests (40 added); Ruff, mypy, diff check and all three CI checks passed. |
| [v1.2.2b2](https://github.com/rbhr/ha-spacelogic/releases/tag/v1.2.2b2) | 4–8, retaining beta 1 | [#7](https://github.com/rbhr/ha-spacelogic/pull/7), `fix/cgate-state-and-discovery` | `3f5d61134193149fe360a97f20ba7119f3b612ba` | `246f409aa730ca29ad8bb707393a71a0f5182e7a` | 183 tests (20 added); Ruff, mypy, diff check and all three CI checks passed. |

Beta 1 merged at 20:22:07 UTC and was published at 20:22:38 UTC on 2026-09-05. Beta 2 merged at 20:53:48 UTC and was published at 20:54:18 UTC that day. Both tags are annotated and point to their merge commits. Existing configuration and entity/device identifiers were retained; no migration was introduced.

The validated local environment used Python 3.13, Home Assistant **2026.2.3**, pytest **9.0.0**, and pytest-homeassistant-custom-component **0.13.316**. The recorded test commands are in [AGENTS.md](../AGENTS.md). These are historical code-validation results; a documentation-only update does not imply they were rerun.

## Why `fix/cgate-connection-recovery` was never merged

The old remote branch ends at `32df72fb6421fed72669f1b1b61132d8b8af132a`. Its [PR #3](https://github.com/rbhr/ha-spacelogic/pull/3) was closed unmerged because it started from stale main. Main commit `920c8bcb7e9b015feb980f0deba2bd2aaa1f54af` had already introduced a supervisor, `ConfigEntryNotReady`, TCP keepalive, connection callbacks, and measurement polling in v1.2.0. The old branch added a conflicting second recovery implementation. The [closing comment](https://github.com/rbhr/ha-spacelogic/pull/3#issuecomment-5466189034) explains that only missing pieces should be carried forward.

[PR #5](https://github.com/rbhr/ha-spacelogic/pull/5), merged as `a6ac8d6` and released in v1.2.1, subsequently addressed command-port EOF and restricting virtual-group latching to error 401. Useful gaps still remained: keepalive error handling, post-reconnect group resync, actuator availability callbacks, retry jitter, and recovery tests. PR #6 selectively implemented and hardened those gaps against current main.

**Do not merge or cherry-pick the whole old branch as unfinished recovery work.** It conflicts with the current implementation and predates later measurement work. Its useful missing behavior is now covered by beta 1.

## Implementation map and details worth preserving

| Area | Files / behavior |
| --- | --- |
| TCP protocol and shared state | `custom_components/spacelogic_cgate/cgate.py`: `CGateClient`, `CGateGroup`, `CGateMeasurement`; command 20023, event 20024, SCP/status 20025. |
| Entry lifecycle | `__init__.py`: early cleanup registration, connection, one shared group discovery, measurement seeding, platform forwarding, unload. Client lives in `entry.runtime_data`. |
| Configuration | `config_flow.py`: UI connection setup, per-group type selection/options, and reconfigure while retaining entry/registry IDs. No YAML setup. |
| Actuator platforms | `light.py`, `switch.py`, `cover.py`, `fan.py`, `lock.py`, `valve.py`; all use lighting application 56 with ON/OFF/RAMP as appropriate. Default type is dimmer; `group_overrides` maps the group's ID to its selected type. |
| Measurement platform | `sensor.py`: application 228, event-driven creation plus startup, interval, and reconnect reads. Unit codes determine measurement kind. |
| Supporting files | `const.py`, `strings.json`, `translations/en.json`, `manifest.json`, `hacs.json`; public behavior in `README.md`. Pure asyncio TCP, no additional runtime pip dependencies; GPL-3.0-only license. |
| Regression coverage | Existing `test_cgate.py`, `test_entities.py`, `test_light.py`, `test_config_flow.py`, plus `test_recovery.py`, `test_lifecycle.py`, and `test_state_discovery.py`. CI workflows: `tests.yml`, `hacs.yml`, `hassfest.yml`. |

Connection and state details:

- `connect()` attempts once; entry setup translates failure into `ConfigEntryNotReady` so HA retries. After connection, one supervisor owns recovery. User commands cannot run during the project handshake.
- A cancelled command waiting for the command lock does not invalidate a healthy connection. An interrupted exchange after transmission does invalidate it before another command can consume its reply.
- Teardown is idempotent, serialized, and protected by a shared shielded cleanup task. Socket closure is bounded to five seconds, followed by transport abort if needed.
- Keepalive sends `NOOP` every 60 seconds on the command port. Event/SCP ports can legitimately remain quiet and have no idle read timeout; TCP keepalive detects dead peers there.
- Recovery tries immediately after an outage. Failed attempts wait from 15 seconds, doubling with up to 20% positive jitter and an actual maximum wait of 120 seconds. Reconnect sequentially resyncs known nonvirtual groups; a second outage stops that resync. Failed reads never substitute zero.
- `CGateGroup.level` starts as `None`. Light/switch/fan on-state, lock state, cover/valve closed-state, cover position, and fan percentage remain unknown as appropriate until established. Known lock level 0 means locked; known positive level means unlocked.
- Only 401 marks `is_virtual=True`; transient errors such as 408 do not retire a group. Virtual groups retain pushed/commanded levels when their reads are skipped. Virtual flags persist across reconnect on the same client; reloading the entry creates a new client. A C-Gate restart alone does not reset those flags.
- Project names are compared case-sensitively. Lighting and measurement events are filtered before creating cache objects. XML project matching uses Address, with TagName fallback. `read_measurement()` validates project/network/application/device/channel.

Discovery and identity details:

- Group address: `//PROJECT/NETWORK/56/GROUP`. Group unique ID: `network_application_group`; entity unique ID prefix: `entry_id_`.
- Measurement address: `//PROJECT/NETWORK/228/DEVICE/CHANNEL`. Measurement unique ID: `network_application_device_channel`; entity unique ID prefix: `entry_id_meas_`. Value is `raw_value × 10^exponent`; the `units` field determines kind, not the last address component.
- `_parse_xml_project()` returns groups plus network addresses. Scan networks come from that list, then group networks, then a fallback of 254. First-run probing remains bounded to devices 0–15 and channels 0–7 per network; it does not exhaustively probe all possible addresses.
- `_known_channels(..., include_live=False)` checks registry-only membership to decide whether first-run probing is needed. This prevents an early broadcast from suppressing the broader first probe. Subsequent reads use the registry/live union, sorted and deduplicated, recomputed each cycle.
- The first probe already stores successful readings; do not immediately seed them a second time. One polling lock skips overlapping refreshes. HA's existing entity update behavior was retained, including its role in sensor staleness updates.
- Empty, malformed, or error database discovery fails explicitly and triggers entry setup retry. A link loss during group seeding also fails discovery; a transient individual group read leaves that group unknown.
- Existing light area matching uses the longest matching substring of a group tag name against HA areas. Connection reconfigure preserves the config entry and registry history; deleting/recreating the entry changes that identity.

## Remaining physical validation

The maintainer said HACS beta publication is the only practical way to test right now and that no other people are using the integration. Both release requests were carried through version bump, checked PR merge, annotated tag, and GitHub prerelease. Continue that workflow when a beta release is requested; do not request the same authorization again.

No physical C-Gate/device testing was performed during the audit implementation. Suggested checks for the maintainer after installing `v1.2.2b2` through HACS and restarting Home Assistant:

- [ ] C-Gate unavailable during HA startup: entry retries and eventually loads.
- [ ] C-Gate outage after setup: entities become unavailable, then recover automatically without reload.
- [ ] Levels changed during an outage are refreshed after reconnect; failed reads preserve previous known state.
- [ ] Measurements refresh after startup/reconnect and newly observed channels join periodic polling.
- [ ] Dimmer transitions and fan on/off/speed services reach the installation correctly.
- [ ] Unread groups remain unknown, especially locks; virtual groups retain known values.
- [ ] Entry unload/reload stops old connections/tasks and reconnects cleanly.

Record the installed beta, HA and C-Gate versions, observed behavior, and relevant sanitized logs when results arrive. A successful automated suite is not a substitute for these installation checks. Handle regressions in the next beta if needed; stable promotion remains a separate release decision.

## Additional follow-ups outside items 1–9

These were identified as potential later work, not included in the two implemented batches and not authorization to start them automatically:

- Validate the actual minimum HA version. README and `hacs.json` advertise **2024.1.0**, but the test environment was 2026.2.3 and the project requires Python >=3.13. The advertised floor remains unverified.
- Bound total command duration, in addition to timeouts on individual reads.
- Validate numeric protocol fields, including measurement exponent bounds.
- Preserve group type options for groups temporarily absent from discovery.
- Consider a broader refactor of duplicated platform setup/subscription logic after behavior is stable.

The legacy Claude notes also asserted that repeated zero-level SCP events do not arrive until a C-Gate restart. That is an **unverified historical observation**, not an established protocol guarantee. Verify against actual logs before relying on it. The old claim that virtual detection resets itself on C-Gate restart was corrected above.

## How context is kept across sessions

Codex discovers project instructions through `AGENTS.md` at session startup. That file directs future sessions to this status record; `CLAUDE.md` points to the same canonical documents. See the [official AGENTS.md documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

Codex also supports optional local memories generated from eligible previous chats. They update in the background and are a supplementary recall mechanism; required project guidance belongs in checked-in files. See [official memory documentation](https://learn.chatgpt.com/docs/customization/memories). This handoff uses repository files and does not depend on enabling global memory or editing its generated store.

The previous ignored `plan.md` described adding platforms that already exist and contained obsolete measurement address/feature assumptions. It is historical only, not the audit plan. Keep the complete ledger above as the source of audit status, and update this document after future changes or validation reports.
