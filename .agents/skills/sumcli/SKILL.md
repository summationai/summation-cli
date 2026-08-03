---
name: sumcli
description: Use the sumcli CLI to authenticate, manage profiles/projects, and operate Summation via sum-api (/v1) — projects, catalog, tables, views, queries, chats, reports, files, connections, and grid. Use when running sumcli, scripting Summation automation, or when the user mentions summation-cli, sumcli, or prefers the CLI over raw API/helper scripts.
---

# sumcli

First-party public CLI for [sum-api](https://github.com/summationai/summation-skill). Prefer **`sumcli`** for scripted automation and agent workflows over older summation skill helper scripts.

| Context | Name |
|---------|------|
| PyPI / package | `summation-cli` |
| Python import | `sum_cli` |
| Binary | `sumcli` |

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv tool install summation-cli
# or from this repo (editable):
uv tool install .
curl -fsSL https://install.summation.com/sumcli | sh   # bootstrap
```

## Agent rules

1. **Discover live commands** — do not hardcode the full tree from memory:
   ```bash
   sumcli | jq '.result.resources'
   sumcli <resource> --help
   ```
2. **Parse JSON** — when stdout is not a TTY (piped/agent), output is JSON envelopes. Pipe through `jq`. Force with `SUMCLI_OUTPUT=json` or `sumcli --output json <resource> ...` (`--output` must precede the subcommand).
3. **Root options before subcommand**: `--profile`, `--base-url`, `--output`, `--project` (where applicable).
4. **Destructive ops need `--confirm`**: `projects delete`, `files delete`, `views delete`, `tables delete`, `connections delete`, `config delete-profile`, `catalog detach`.
5. **Never put secrets** in commits, logs, or skill files. Config lives in `~/.summation/summation-config`.
6. **Parallel agents**: do not call `config use` on a shared config. Pass `--profile` and/or set `SUMMATION_PROFILE` / `SUMMATION_PROJECT` per process.

## Command shape

```text
sumcli [--profile NAME] [--base-url URL] [--output json|human] <resource> <action> [options]
```

Project-scoped commands accept `--project` when no default is set.

## Auth & profiles

Config file: `~/.summation/summation-config` (override with `SUMMATION_CONFIG_FILE`).

**Base URL is tenant-specific.** Each tenant has its own API host (e.g. `https://sandbox-api-<tenant>.summation.com`, `https://api-<tenant>.summation.com`). Do **not** assume `https://sandbox-api.summation.com` — that is only the CLI's built-in fallback when nothing else is set. Ask the user for their tenant API URL, or reuse `SUM_API_BASE_URL` / an existing profile (`sumcli config active`, `sumcli config list`).

**Device login (typical):**

```bash
sumcli config set-profile myenv --base-url "$SUM_API_BASE_URL"   # or https://<tenant-api-host>
sumcli config use myenv
sumcli --profile myenv auth login
sumcli auth whoami | jq .
```

**M2M:**

```bash
sumcli config set-profile myenv \
  --base-url "$SUM_API_BASE_URL" \
  --client-id "$SUM_API_CLIENT_ID" \
  --client-secret "$SUM_API_CLIENT_SECRET"
sumcli config use myenv
sumcli --profile myenv auth login --m2m
```

Useful: `auth whoami`, `auth status`, `auth token` (redacted), `config active`, `config list`, `config set-project <id>`.

### Precedence (field-specific)

| Field | Order (highest first) |
|-------|------------------------|
| Profile | `--profile` → `SUMMATION_PROFILE` → `[_meta].active_profile` → `default` |
| Base URL | `--base-url` → `SUM_API_BASE_URL` → profile `base_url` → built-in fallback (not a real tenant host) |
| Credentials | `SUM_API_*` env → profile section |
| Project | `--project` → profile `default_project` → `SUMMATION_PROJECT` |

Auth resolution: `device_login_credential` → static `access_token` → M2M client id/secret exchange. Identity comes from the bearer token only.

## Resources

| Resource | Use for |
|----------|---------|
| `auth` | login, whoami, status, token, logout |
| `config` | profiles, `use`, `set-project`, `import-env` |
| `tenant` | org/tenant metadata |
| `projects` | CRUD, `current` |
| `catalog` | attach/detach/list project tables & views |
| `tables` | grid tables, CSV `import`, data |
| `views` | Summation views |
| `queries` | read-only SQL (`queries run --sql` / `--file`); cap rows with SQL `LIMIT` or `--limit` (API default 100, max 10000/request; higher auto-paginates) |
| `chats` | Addison; `--follow` streams NDJSON; `feedback` rates a message |
| `reports` | generate/verify (`.sdoc`); default `--follow` on |
| `playbooks` | discovery |
| `files` | project file upload/download/list/delete |
| `filesystem` | connected roots (e.g. SharePoint; provider APIs, not sum-api) |
| `connections` | external data sources (CRUD, test, browse) |
| `grid` | status, sync, lineage, push |

## Common workflows

### CSV → queryable table

```bash
# One-shot local ingest (recommended if file need not stay in project tree)
sumcli tables import --local --path ./Customers.csv --table customers
# NDJSON ends with importStatus SUCCESS + table_id (tbl-...)

sumcli tables show tbl-...
sumcli catalog attach --source-type table --source-id tbl-...
sumcli catalog list
# Cap rows either in SQL or with --limit (same idea; --limit default 100)
sumcli queries run --sql 'SELECT * FROM customers LIMIT 5'
sumcli queries run --sql 'SELECT * FROM customers' --limit 5
```

Two-step (keep CSV in project files): `files upload` then `tables import --remote --path /Customers.csv --table customers`.

After import, **attach** before the table appears in `catalog list` / project queries. `tables delete` does not auto-detach — run `catalog detach` separately.

### Report / chat (long-running)

```bash
sumcli reports generate -m "Q4 summary"              # wait + NDJSON (follow default on)
sumcli reports generate -m "Q4 summary" --no-follow  # single JSON envelope
sumcli chats create -m "hello" --follow              # NDJSON stream
```

`--wait` (default) drains SSE; `--follow` streams progress NDJSON (requires `--wait`). `--no-wait --follow` → `INVALID_FLAGS`, exit 1.

### Chat feedback

Rate one assistant message so Summation can review answer quality. Requires `agent:write` scope.

```bash
sumcli chats feedback --chat chat-... --message msg-... --rating thumbs_down \
  --reason incorrect_info \
  --details "Cited 2023 revenue; the question asked for Q4 2024."
```

`--rating` is required (`thumbs_up`, `thumbs_down`). `--reason` is optional (`incorrect_info`, `instructions_ignored`, `unsafe_or_problematic`, `bad_response`, `dont_like_style`, `other`). `--details` is optional free text, max **4000** characters — the CLI rejects longer input as `DETAILS_TOO_LONG` (exit 1) without calling the API. Bad `--rating`/`--reason` values fail at parse time (exit **2**).

**Best practices**

1. **Feedback is append-only, not an upsert.** Every call creates a new record. The API also accepts opposite ratings on the same message. There is no edit, no delete, and no "current rating". You cannot retract a mistake. A new record does not remove or hide the old one. Get it right the first time.
2. **Never file test or placeholder feedback against a real message.** These records go to human quality review. To exercise the command, use a throwaway chat in a sandbox project and say so in `--details`. Better: rely on the test suite (`tests/test_chats_feedback.py`), which covers the payload without writing to any tenant.
3. **Rate only what the user judged.** An agent must not invent a rating. File feedback when the user says the answer was wrong or good — pass on their verdict, do not substitute your own.
4. **Always send `--details` with `thumbs_down`.** A bare negative rating tells a reviewer nothing. State what was wrong and what was expected. Cite the specific claim, number, or instruction that failed.
5. **Pick the narrowest `--reason`.** Reach for `other` only when nothing else fits, and then `--details` is mandatory to be useful at all.
6. **Do not put secrets, PII, or raw customer rows in `--details`.** It is stored and read by humans. Describe the defect, do not paste the data.

**Getting the IDs.** `chats create` and `chats reply` return `messageId` in the terminal payload. They do **not** return the chat ID. The emitted "Reply" next action shows `chat-id: null`. Recover the chat ID with `chats list`:

```bash
CHAT=$(sumcli chats list --count 1 | jq -r '.result.chats[0].id')
```

Track the chat ID from the `chats list` / `chats show` response rather than expecting it in a `create` result.

## Output & errors

- Success/validation: one JSON envelope (`ok`, `result` / `error`, often `next_actions`).
- `--follow` / import streams: NDJSON lines; **last line** is terminal `result` or `error`.
- Failures: exit **1**. Codes include `NO_PROJECT`, `CONFIRM_REQUIRED`, `INVALID_FLAGS`, `IMPORT_FAILED`, `INTERNAL_ERROR`.
- Human TTY view is lossy — never parse it; use JSON mode.

## Env vars (quick)

`SUMMATION_CONFIG_FILE`, `SUMMATION_PROFILE`, `SUMMATION_PROJECT`, `SUM_API_BASE_URL`, `SUM_API_CLIENT_ID`, `SUM_API_CLIENT_SECRET`, `SUM_API_ACCESS_TOKEN`, `SUM_API_M2M_SCOPE`, `SUMCLI_OUTPUT`.

## More detail

- Full workflows, SharePoint notes, and design rules: repository `README.md`
- Live flags: `sumcli <resource> --help`
