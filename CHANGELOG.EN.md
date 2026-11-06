# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

[中文](docs/Changelog.md)

## [Unreleased]

### Fixed

- **Token Shark base URL corrected to the official public endpoint `https://llmv1.ahau.edu.cn/v1`**: the school's announcement, the platform manual and the integration guide all state "平台固定接口地址为 https://llmv1.ahau.edu.cn/v1". The address previously used (`http://llm.ahau.edu.cn:4000/v1`, taken from `/api/me/api-base-url/`) is the platform's **internal** gateway; students cannot reach it because the campus ACL drops the SYN, and the 80/81/8080 ports answer with a custom block page echoing the client IP — which looks like a dead service but is a network policy. The public host on 443 works (uvicorn + vLLM); `/v1/models`, `/v1/chat/completions` and `/v1/responses` all returned 200, and the Responses endpoint natively supports `tools` / `parallel_tool_calls`
- **Model catalog generator now understands `max_input_tokens`**: Token Shark's `/v1/models` returns `max_input_tokens` rather than `context_window`, so the generated catalog had no context window
- **Model catalog generator skips placeholder keys**: the switch template keeps a placeholder while the real key lives in the keychain or `~/.codex/config.toml`; sending the placeholder to `/models` produced a 401 that was hard to trace
- **Third-party gateways no longer choke on `custom_tool_call`**: Codex's free-form tools (`apply_patch`, `exec`) are recorded in the Responses API as the special input item types `custom_tool_call` / `custom_tool_call_output`. OpenAI accepts them, but many third-party gateways (Token Shark's litellm proxy, for example) only accept standard `function_call` and answer HTTP 400 — `Unsupported input item type: custom_tool_call (parameter=input)` — so resuming an older conversation fails while a fresh one works. The generator no longer writes `apply_patch_tool_type` into the catalog: Codex 0.168 only accepts `freeform` for it (writing `function` breaks the whole catalog with `unknown variant 'function'`), and omitting the field stops Codex from registering the tool at all, so the model edits files through `exec_command` and only standard `function_call` items end up in the history
- **`clean-custom-tool-calls.py` rewrites existing history**: converts `custom_tool_call` / `custom_tool_call_output` items in the rollout files into standard `function_call` / `function_call_output` (`input` → `arguments`), so conversations recorded before the switch can be resumed on a third-party gateway. Affected files are backed up as a single `tar.gz` under `~/.codex/backups/` (the affected rollouts add up to gigabytes, so per-file copies would fill the disk), and the script must be followed by `remap-history-base.py` to re-map the fork byte offsets and invalidate the GUI projection cache

### Added

- **token_shark mode (campus LLM gateway)**: `./codex-switch.sh token_shark` (or double-click `切换到Token鲨.command`). Before switching it reads `codex-token-shark` from the macOS keychain (falling back to an inline key in the config), then runs a gateway preflight (`GET {base_url}/models`, requires HTTP 200 *and* a JSON body) and a protocol probe (`/responses` vs `/chat/completions`, which decides `wire_api`); on failure it aborts and leaves the current config completely untouched — an unreachable gateway is worse than an exhausted quota. The key only ever lives in the keychain, never in the repo or the template; it is injected into `config.toml` (mode 600) at switch time, and `model_catalog_json` is set automatically when `~/.codex/token-shark-models.json` exists. Session provider markers are repainted as `token_shark` through the generic provider-name logic
- **`CODEX_SWITCH_TOKEN_SHARK_BASE_URL`**: swap the gateway address without touching the repo template

### Fixed

- **strip invalid `[model_providers.X.auth]` sections on switch**: Codex's model provider has no `auth` field at all, so a "fetch the key via a command" block is silently ignored (no error) — which means "no credentials" and the platform answers 401. The script now removes the whole section line by line (the `args = [...]` array line inside it is not left behind; leaving it produces invalid TOML that stops Codex from starting)

## [1.6.1] - 2026-10-22

### Added

- **trim: 7-day safety window + over-window rescue auto-pass**: the creation date is parsed from the `rollout-YYYY-MM-DD` filename; sessions younger than 7 days are refused by default (prevents mis-trimming active recent sessions), overridable with `--force`. Exception: young sessions whose replay payload (latest compacted window + tail) is estimated to exceed the 1M-token context window are auto-approved — they cannot run at all without trimming
- **Schema shim: empty-assistant cleanup**: during codeproxy conversion, fully empty reasoning items (Codex writes one every turn, with empty summary/content) and empty assistant messages produce a bare assistant message with `content=null`, which Kimi rejects with `the message at position N with role 'assistant' must not be empty` — and it breaks auto-compaction ("compaction interrupted"). The shim now drops these empty items before forwarding (a reasoning item whose summary has text gets that text moved into content instead). The shim script lives in the repo root; the switch script's kimi branch syncs it to `~/.codex` and restarts the shim service

## [1.6.0] - 2026-10-22

### Changed

- **kimi default model upgraded to `k3` (K3 1M, 1,048,576-token context)**: after Kimi launched the K3 (1M) variant, the old `k3-256k` default made long-session continuations fail with `Your request exceeded k3-256k model token limit: 262144` — the UI model picker doesn't change a thread's pinned model, so requests still went out as 256K. The config template default, the script's `DEF_MODEL`, and the history-rewrite mapping target are all `k3` now; threads pinned to `k3-256k` flip automatically on switch, and legacy names (`k3-256k` / `kimi-*`) are normalized to `k3` by the rewrite pass

## [1.5.3] - 2026-10-21

### Fixed

- **trim now covers MCP image results**: MCP tools return images as `{type:'image', data: base64, mimeType}` (image bytes in `data`, not `image_url`), which all prior versions missed — measured: one 2026-09-26 session retained 166 such results totaling 10.2MB. They are now swapped for the 1x1 placeholder too (that session's replay payload dropped from 12.1MB to 1.8MB)

## [1.5.2] - 2026-10-21

### Fixed

- **trim now swaps base64 images inside the protected zone too**: the recent working zone (last 2MB by default) was previously left fully untouched, but large screenshots from UI-snapshot tools (measured: a single 2.3MB image) inside the zone still pushed the request body past third-party gateway nginx limits (kimi mode failed continuations with `413 Payload Too Large`). The zone now protects text only; images are always swapped for the 1x1 placeholder (measured: a 21.4MB active work session trimmed to 1.2MB, resumable in kimi mode again)

## [1.5.1] - 2026-10-21

### Fixed

- **trim now covers base64 screenshots embedded in tool-output lists**: UI-snapshot tools embed screenshots as `input_image` (data: URLs) inside the `output` list of `custom_tool_call_output` / `function_call_output` and in MCP result `content` lists — a single one can reach 1.6MB. v1.5.0 only handled images in message content and missed these. They are now swapped for the 1x1 placeholder as well (measured: an active 8.5MB work session shed another 4.2MB thanks to this)

## [1.5.0] - 2026-10-21

### Added

- **Smart trimming of giant sessions (`trim-rollout-output.py`)**: long-running sessions fail to resume with `Your input exceeds the context window of this model` — the app replays the full history to the model, and the bulk is not conversation text but accumulated tool output (image-generation base64, MCP tool output, command output, pasted base64 images, and the `guardian_history` audit copy inside compacted window records). The tool trims only old tool outputs and pasted bulk; user/assistant message text is never touched. Base64 images are swapped for a 1x1 placeholder PNG (keeps structure valid), oversized text keeps a 500-char head plus a placeholder note, and the last 2MB "recent working zone" is left untouched (compacted window records excepted). The original is backed up as `.bak-trim-<timestamp>` and the projection caches of affected threads (including forks) are invalidated. Measured: a 20.8MB session trimmed to 7.6MB, replay payload (latest window + tail) down from ~2.7MB to ~181KB. Usage: `python3 trim-rollout-output.py <rollout.jsonl> [--keep-recent-mb 2] [--max-field-chars 4000] [--max-msg-chars 20000]` — always run `remap-history-base.py` afterwards to repair fork offsets

### Fixed

- **remap now supports `.jsonl.zst` compressed fork sources**: app 0.163.x validates lineage offsets against the **compressed byte size**, while `history_base.end_byte_offset` records decompressed offsets — any source compressed by the maintenance task inevitably fails validation, showing as repeated reconnects and an "cannot open file" tab error. `.zst` candidate sources are now decompressed back to `.jsonl` before offset calculation (the original is renamed to `.zst.bak-lineage` and excluded from scans), self-healing on the next run if the app re-compresses them. Also adds reading compressed sources (`zstd -dc`, offsets measured on decompressed bytes) and a "next surviving line / end of file" fallback when the target ordinal was deleted by cleanup

## [1.4.0] - 2026-09-18

### Added

- **Default-model presets**: no need to re-pick a model in the UI after switching — openai→`gpt-5.6-terra`, kimi→`k3-256k`, deepseek→`deepseek-v4-flash`, all at medium reasoning effort. Besides the new-session defaults in the config templates, the switch script also flips the pinned `model` / `reasoning_effort` of every thread in `state_5.sqlite`'s `threads` table — otherwise reopening an old conversation still runs on the old model and fails. Adjust `DEF_MODEL` in the script and the `model` field in each config template as needed

## [1.3.0] - 2026-09-18

### Added

- **Forked-session `history_base` offset remapping (`remap-history-base.py`)**: a forked session file embeds `history_base.end_byte_offset` in its header, pointing at a byte position in the source rollout file. After history rewriting changes the source's length, the offset falls past the end and the app fails with `invalid paginated history lineage: cutoff byte offset is past the source rollout` — the conversation won't open, and projection-cache invalidation can't help because the offset lives in the jsonl, not the database. The new script relocates the same line via `end_ordinal_exclusive` in the source file and updates the cutoff to its current byte offset; supports multi-level fork chains, idempotent, auto-invalidates affected threads' projection caches. Runs automatically on every switch; standalone repair: `python3 remap-history-base.py ~/.codex`

### Changed

- **Incremental + streaming history rewrites**: the model-name rewrite and third-party residue cleanup passes each keep their own processed-timestamp (`~/.codex/.switch-rewrite-marker-model` / `-reasoning`) and only scan files modified since the last run (archived sessions are processed once and skipped forever); all I/O is line-by-line streaming — files are never loaded whole into memory, and unchanged files are read-only. Switch time no longer grows with session history (tested against a 14 GB / 448-file library with a 464 MB single file). The first switch after upgrading performs one final full scan to establish the baseline

## [1.2.0] - 2026-09-11

### Added

- **Browser control in third-party mode**: Codex's embedded browser / Computer Use is an OpenAI cloud service and always fails with `Codex auth token is unavailable` in third-party mode. Two new bridges based on Kimi WebBridge let Codex drive the user's real browser (with login sessions):
  - `skills/webbridge-browser/SKILL.md` (recommended): teaches Codex to call the WebBridge daemon via `exec_command` curl — verified end-to-end
  - `webbridge-mcp.py` (fallback): an MCP stdio bridge. Note Codex 0.153.4 hardcodes `tool_search_always_defer_mcp_tools` to true, so MCP tools are invisible in exec mode and only surfaced via the GUI's tool_suggest

## [1.1.0] - 2026-09-10

### Fixed

- **GUI projection-cache invalidation (2026-09-09)**: the Codex GUI reads conversations from a projection in `thread_history_1.sqlite` that indexes rollout files by byte offset. In-place rewrites invalidated those offsets, leaving the GUI stuck showing stale content. The script now invalidates the affected threads' projection rows after any rewrite/cleanup; the app rebuilds them from the jsonl on next launch. (`4cae38c`)
- **Four-layer official-API validation on resume (2026-09-08/09)**: history items produced by third-party direct `/responses` endpoints trip four consecutive validations — reasoning `content` array (`array_above_max_length`), locally Fernet-encrypted `encrypted_content` (`invalid_encrypted_content`), `tool_`-prefixed tool-call ids (`invalid_id_prefix`), and third-party `rs_` reasoning ids (`Item with id 'rs_...' not found`). On switching to openai, the script strips reasoning content/encrypted_content/id and rewrites tool-call ids to the `fc` prefix; resume verified working. (`15538dc`)
- **Kimi schema validation (2026-09-08)**: added the port-8788 schema shim fixing MCP tool schemas (`$ref` + sibling `type`) rejected by Kimi's validator (`moonshot flavored json schema`); both K3 and K2.7 verified returning 200. (`e6505f7`)

### Documentation

- **Bidirectional resume test results (2026-09-08)**: README now documents the full forward/reverse cross-provider resume findings, the four validation symptoms layer by layer, and the cleanup trade-offs
- **Troubleshooting table extended (2026-09-09)**: new entries for "conversation shows stale content" and "threads disappear from the list after switching", with self-check commands for config vs `state_5.sqlite` flag consistency
- **CC Switch role clarified (2026-09-07)**: verified on CC Switch v3.19.2 / v3.20.1 (including takeover mode) that clicking "enable" never writes `~/.codex/config.toml`; docs now state it is a read-only panel and real switching must go through the script
- Added English README (`3285b92`), badges (`d45f61c`), and social preview image (`fcf2b79`)

## [1.0.0] - 2026-09-08

First public release.

### Added

- **One-click three-way switching**: `codex-switch.sh openai|deepseek|kimi` plus three double-clickable `.command` entries — quit app → write config → sync session visibility flags → rewrite historical model names → relaunch
- **Automated cross-provider resume**:
  - Model-name rewriting: fixes `invalid_request_error` caused by pre-sampling compaction pinning the model name recorded in session metadata
  - Third-party residue cleanup: handles reasoning residue fields when switching back to official mode
  - First-touch backups before every rewrite/cleanup (`.bak-model` / `.bak-reasoning` / `.bak-switch`)
- **Kimi local routing**: codeproxy (port 8787) running as a launchd service
- **Split model catalogs**: separate catalogs for DeepSeek and Kimi so the GUI model picker only shows the active provider's models
- **Optional CC Switch integration**: syncs its "in use" flag when installed (panel display only, not required)

[Unreleased]: https://github.com/aipmer/codex-switch/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/aipmer/codex-switch/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/aipmer/codex-switch/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/aipmer/codex-switch/releases/tag/v1.0.0
