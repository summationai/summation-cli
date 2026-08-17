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
sumcli update   # later upgrades (uv tool install --force summation-cli@latest)
```

## Agent rules

1. **Discover live commands** — do not hardcode the full tree from memory:
   ```bash
   sumcli | jq '.result.resources'
   sumcli <resource> --help
   ```
2. **State intent** — every command that reads or writes data must include `--intent` as a root option before the subcommand. Use the human's request **in their own words** when possible — not a summary of the command you are running. Keep it under 500 characters; if the request is longer, use the first part of their words. Set `SUMCLI_INTENT` once to that same string to cover a whole session.
   - User said: `convert my weekly recap` → `--intent "convert my weekly recap"`
   - Wrong: `--intent "list projects"` or `--intent "attach the catalog table"`
   - **Exempt** (no `--intent` needed): discovery, `--help`, `--version`, `update`, and the `auth` and `config` groups. Setup runs before there is a goal to state, and `config` only writes the local config file.
   - The examples in this file show `--intent` only where it is necessary. Add your own value; do not copy the placeholder text.
3. **Parse JSON** — when stdout is not a TTY (piped/agent), output is JSON envelopes. Pipe through `jq`. Force with `SUMCLI_OUTPUT=json` or `sumcli --output json <resource> ...` (`--output` must precede the subcommand).
4. **Root options before subcommand**: `--intent`, `--profile`, `--base-url`, `--output`, `--project` (where applicable).
5. **Destructive ops need `--confirm`**: `projects delete`, `files delete`, `views delete`, `tables delete`, `connections delete`, `connections app-delete`, `schedules delete`, `schedules run`, `catalog detach`, `filesystem delete`, `config delete-profile`. `filesystem upload` needs it only when it overwrites an existing file. `schedules run` is included because a manual run delivers real email immediately — check the recipients the refusal lists with the user before re-running with `--confirm`.
6. **Never put secrets** in commits, logs, or skill files. Config lives in `~/.summation/summation-config`.
7. **Parallel agents**: do not call `config use` on a shared config. Pass `--profile` and/or set `SUMMATION_PROFILE` / `SUMMATION_PROJECT` per process.

## Command shape

```text
sumcli --intent "human's request" [--profile NAME] [--base-url URL] [--output json|human] <resource> <action> [options]
sumcli update    # root command: upgrade to the latest PyPI release
```

`--intent` is required when stdout is not a TTY (agents, pipes). Use the human's request **in their own words** when possible, not a command summary. `SUMCLI_INTENT` satisfies the same requirement for a session. Humans at an interactive terminal may omit it.

Discovery, `--help`, `--version`, `update`, and the `auth` and `config` groups do not need it, so the setup sequence below runs as written.

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
| `playbooks` | discovery only (`list`, `show`) — **read-only**; author and edit via `chats` |
| `schedules` | recurring playbook runs (CRUD, pause/resume, run now, run history) |
| `files` | project file upload/download/list/delete |
| `filesystem` | connected roots (e.g. SharePoint; provider APIs, not sum-api) |
| `connections` | data sources (CRUD, test, browse, datasets, snapshots) and app connectors (`app-*`) |
| `grid` | status, sync, lineage, push |

## Common workflows

### CSV → queryable table

```bash
# Set the intent once for the session, in the human's own words.
export SUMCLI_INTENT="get my customer spreadsheet into a table I can query"

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

Without `SUMCLI_INTENT`, pass `--intent "<the human's request>"` before the subcommand on each of these commands.

Two-step (keep CSV in project files): `files upload` then `tables import --remote --path /Customers.csv --table customers`.

After import, **attach** before the table appears in `catalog list` / project queries. `tables delete` does not auto-detach — run `catalog detach` separately.

### Data connections (add, verify, remove)

A connection is not usable until **three** things are true: the record exists, its
credentials pass a test, and datasets are attached. Creating alone gets you none of
the last two.

```bash
# 1. CREATE — secrets go in a file, never on the command line
cat > /tmp/conn.json <<'EOF'
{
  "config":  {"snowflake_account": "myorg-acct1", "snowflake_username": "svc_user",
              "snowflake_warehouse": "WH"},
  "secrets": {"snowflake_password": "..."}
}
EOF
sumcli connections create --name prod-sf --type SNOWFLAKE --config-file /tmp/conn.json
rm -f /tmp/conn.json          # always, success or failure

# 2. TEST — the only step that proves the credentials work
CONN=con-...
sumcli connections test "$CONN"
sumcli connections show "$CONN" | jq '.result.connection.lastTestStatus'   # want "PASS"

# 3. ATTACH — browse the source, then attach what you want
sumcli connections browse "$CONN" --path-prefix DB.SCHEMA
sumcli connections attach-datasets "$CONN" --from-source DB.SCHEMA.ORDERS
sumcli connections datasets "$CONN" | jq '.result.datasets[] | {name, status, proxyTableStatus}'
```

Snapshots — copy a dataset's current source data into Summation's lakehouse so
downstream tables build on a stable copy:

```bash
sumcli connections snapshot "$CONN" ds-...        # returns 202 queued; takes no body
sumcli connections snapshots "$CONN" --limit 5    # poll: newest first, 1-50
```

Snapshotting must be enabled for the dataset first — either
`--snapshot-enabled` at attach time or the connection's snapshot policy. Otherwise
the call returns a conflict explaining what to turn on. One snapshot per dataset runs
at a time. Poll until a run's `status` is terminal, then read `snapshotTableName` for
the table to build on; failures carry `errorCode` / `errorMessage`.

Updating a connection — rotate a credential or change settings:

```bash
cat > /tmp/rotate.json <<'EOF'
{"secrets": {"snowflake_password": "..."}}
EOF
sumcli connections update "$CONN" --config-file /tmp/rotate.json
rm -f /tmp/rotate.json          # always, success or failure
sumcli connections test "$CONN"  # re-test: rotation does not verify the new credential
```

`update` takes the same three top-level keys as `create` (`config`, `secrets`,
`snapshot_config`) and sends **only the top-level keys the file contains** — an
omitted top-level key is left unchanged, so a secrets-only file does not disturb
`config`. Each key you *do* send replaces that stored object entirely: include the
full `config` or `secrets`, not a partial one. Same for `snapshot_config` (the
spec calls that out explicitly). Use `--name` / `--description` for those two
fields; they are rejected inside the file.

Removing a connection:

```bash
sumcli connections show "$CONN"                 # confirm the target first
sumcli connections datasets "$CONN"             # see what you are about to orphan
sumcli connections delete "$CONN" --confirm     # irreversible
sumcli connections list                         # verify it is gone
```

**Best practices**

1. **`status: ACTIVE` does not mean the connection works.** A brand-new connection
   reports `ACTIVE` with no `lastTestStatus` at all — `ACTIVE` only means the record
   exists. A connection with entirely fake credentials still reports `ACTIVE` *after*
   a failed test. **Gate on `lastTestStatus == "PASS"`, never on `status`.** This is
   the single most common way to hand someone a connection that returns nothing.
2. **Always `test` after `create`.** `lastTestStatus` and `lastTestedAt` are absent
   until the first test runs. Nothing tests a connection for you.
3. **A dataset needs two green lights.** It is queryable only when `status` is
   `DEPLOYED` **and** `proxyTableStatus` is `CREATED`. Attachment returns immediately
   and loads in the background, so poll `connections datasets` until both hold — one
   alone is not enough.
4. **Secrets go through `--config-file`, never a flag or heredoc in chat.** True of
   both `create` and `update` — rotation uses the same file route. The file keeps the
   credential out of argv, shell history, and the transcript. Delete it immediately
   after, on success or failure. Responses return `secretRefs` (e.g.
   `CON_PROD_SF_SNOWFLAKE_PASSWORD`), never the value.
5. **Never create a connection without its secrets.** The API accepts a secretless
   create, but the result cannot be finished in the web app — it strands an orphan
   record the user cannot fix. If one is created by accident, delete it.
6. **Snowflake wants the account identifier, not a URL.** Use `myorg-acct1`, not
   `myorg-acct1.snowflakecomputing.com` and not `https://…`. Legacy identifiers
   without a hyphen are rejected. Required: `snowflake_account`,
   `snowflake_username`, plus either `snowflake_password` or
   `snowflake_private_key` — auth type is inferred from which you supply.
7. **Pass `--from-source` values exactly as `browse` returned them.** Never retype or
   guess a source path. For request-shaped sources (HTTP APIs) omit `--from-source`
   entirely and describe the request in `--datasets-file` `params`.
8. **Use `--datasets-file` for anything beyond a plain table.** `--from-source` is
   repeatable for the simple case; connector-specific `params` need the file. Limit
   is 100 datasets per request, applied as one atomic batch.
9. **Check what a delete orphans before running it.** Deleting is irreversible and
   takes down every dataset attached to that connection. List the datasets first, and
   confirm the id with `show` — ids are easy to transpose.
10. **Do not leave test connections behind in a real tenant.** If you create one to
    exercise a flow, delete it in the same session. An inert record with dead
    credentials still shows up in the workspace UI as a broken connection.
11. **Response keys are lowerCamel; single-word enum values are not.** Fields come
    back as `lastTestStatus` / `proxyTableStatus`, while their values stay upper-case
    (`PASS`, `DEPLOYED`, `CREATED`). Only **multi-word** enum values are rewritten —
    a `SCHEDULE_STATE_ACTIVE` arrives as `scheduleStateActive`. Compare against what
    the payload actually contains rather than assuming one convention throughout.

### App connectors (`connections app-*`)

A separate resource under the same group: third-party apps (NetSuite, SharePoint,
Salesforce, Google Drive …) whose **tools the agent can call during chat** — not data
sources you query. The bare verbs (`list`, `show`, `delete`) belong to data
connections; every app command is prefixed `app-`.

```bash
sumcli connections app-catalog                      # what can be connected
sumcli connections app-tools netsuite               # what that app exposes
sumcli connections app-list                         # what IS connected
sumcli connections app-list --enabled-for-chat-only
sumcli connections app-enable-chat  app-conn-...    # let the agent use its tools
sumcli connections app-disable-chat app-conn-...
sumcli connections app-disconnect   app-conn-...    # revoke access, KEEP the record
sumcli connections app-delete       app-conn-... --confirm   # remove entirely
```

**Best practices**

1. **`disconnect` and `delete` are different.** Disconnect revokes the agent's access
   and keeps the record; delete removes it. Prefer disconnect when the user may
   reconnect later.
2. **Connecting is not a CLI operation.** The OAuth handshake happens in the web app.
   The CLI inspects, toggles chat access, disconnects, and deletes — it cannot create
   an app connection.
3. **Enabling for chat is what actually exposes the tools.** A connected app whose
   `enabledForChat` is false is inert. If the agent cannot see an app's tools, check
   this before assuming the connection is broken.
4. **Catalog keys are not provider slugs.** SharePoint's key is `share_point`, not
   `sharepoint`. Pass `app-tools` the `key` from `app-catalog` verbatim.

### Report / chat (long-running)

```bash
sumcli reports generate -m "Q4 summary"              # wait + NDJSON (follow default on)
sumcli reports generate -m "Q4 summary" --no-follow  # single JSON envelope
sumcli chats create -m "hello" --follow              # NDJSON stream
```

`--wait` (default) drains SSE; `--follow` streams progress NDJSON (requires `--wait`). `--no-wait --follow` → `INVALID_FLAGS`, exit 1.

### Creating and editing playbooks (via `chats`)

**sum-api has no playbook write endpoints.** The whole public surface is `GET /v1/projects/{project_id}/playbooks` and `GET …/{playbook_id}` — there is no create, no update, and **no run**. So `sumcli playbooks` only lists and shows. Authoring, editing, and triggering all go through `chats`, which asks Addison to do the work.

This makes playbook authoring non-deterministic: you send prose, an agent acts, and **nothing validates the result but you**. The practices below exist to make that loop reliable.

```bash
# 1. Scope first — investigate, change nothing
sumcli chats create --project prj-... --title "Scoping: refresh X" --no-follow \
  --message "Investigation only — do NOT create, modify, or delete anything.
  Report on: where the data lives, how the artifact is produced, what a playbook needs."

# 2. Build, once you agree with the assessment
CHAT=$(sumcli chats list --project prj-... --count 1 | jq -r '.result.chats[0].id')
sumcli chats reply --chat "$CHAT" --project prj-... --no-follow --message "Now create the playbook. <constraints>"

# 3. VERIFY — never trust the agent's summary
sumcli playbooks list --project prj-... | jq '.result.playbooks'   # note: fileId, NOT id
sumcli files list --project prj-... --count 100 | jq -r '.result.files[] | "\(.kind) \(.folderPath)\(.fileName)"'
```

**Best practices**

1. **Scope before you build.** Send a read-only investigation message first — "do NOT create, modify, or delete anything; report back." An agent that has inspected the data writes a far better playbook than one guessing from your description, and you get a reviewable spec before anything is written.
2. **Verify every change independently.** The agent's summary is a claim, not evidence. Download the playbook's `instructions.md` with `files download` and grep for the rule you asked for. Agents do self-correct their own earlier reports — treat a summary as a starting point for checking, not a result.
3. **Forbid mid-run questions explicitly.** An agent that stops to ask a clarifying question mid-run leaves the job unfinished, which is fatal for anything scheduled. State it directly: *"A run must never ask the operator a question. It either completes, or it fails with a specific error and leaves the target untouched. A halt is a failure, not a prompt."*
4. **Make correctness a check the playbook runs, not a judgment it escalates.** Instead of "stop if the data looks truncated," require a `COUNT(*)` anchor and an assertion that processed rows equal it. Deterministic checks survive unattended runs; judgment calls do not.
5. **Name what must not change, in bytes.** For artifact refreshes, say exactly which region is replaced and that everything else is preserved byte-for-byte. Then verify: strip the changed region from the old and new file and compare the remainder. Vague instructions invite an agent to "improve" the layout you wanted frozen.
6. **Demand a run report with counts.** Rows read, date range, per-segment counts, skipped items, output size, byte delta. Without numbers you cannot tell a correct run from a plausible-looking one.
7. **Iterate through the same chat.** Use `chats reply --chat <id>` so the agent keeps the full constraint history. A fresh `chats create` loses that context and re-litigates decisions.
8. **Remember the row cap.** `queries` caps at **10,000 rows per request** (`QueryExecutionRequest.limit` maximum); the agent's own SQL tool caps higher. Neither can be raised. If a source exceeds the cap, the playbook must paginate — say so up front rather than letting a run discover it.

**Triggering.** There is no `sumcli playbooks run`. Options: run it in the web app, ask in chat (*"run the X playbook"*), or create a schedule and call `sumcli schedules run` — see below.

### Scheduled playbook runs

Run a playbook on a cadence and email the output. **Schedules target playbooks only** — to schedule a report, first save the work as a playbook, then schedule that.

```bash
# Playbook ids come back as fileId (NOT id) and look like file-...
PB=$(sumcli playbooks list --project prj-... | jq -r '.result.playbooks[0].fileId')

# Weekdays at 07:30 Pacific, emailed to two people
sumcli schedules create --project prj-... --playbook "$PB" \
  --type weekly --day MONDAY --day FRIDAY \
  --time-of-day 07:30 --zone America/Los_Angeles \
  --email cfo@acme.com --email board@acme.com:cc:Board \
  --param region=emea --output-folder /Reports

sumcli schedules list --project prj-... | jq '.result.schedules'
sumcli schedules show schedule_...      # verify state before trusting it
sumcli schedules pause schedule_...     # stop without deleting
sumcli schedules resume schedule_...
sumcli schedules run schedule_... --reason backfill --confirm   # off-cadence run; sends email
sumcli schedules runs schedule_... --count 5          # recent run history
sumcli schedules delete schedule_... --confirm
```

Schedule ids are `schedule_<uuid>`, not `sch-...`. After `create`, confirm `state` is `SCHEDULE_STATE_ACTIVE`, `reconciliationStatus` is `reconciliationStatusSynced`, and `config.targetAvailable` is `true` — a schedule can be created against a target that is not actually runnable.

`--type` is one of `cron`, `interval`, `one_time`, `daily`, `weekly`, `biweekly`, `monthly`, `month_end`, `yearly`. Supply the fields that type needs: `--cron` for `cron`, `--every-minutes` for `interval`, `--run-date` for `one_time`, `--day` for weekly cadences, `--day-of-month` / `--month` for monthly and yearly. Defaults: `--time-of-day 09:00`, `--zone UTC`, `--output-folder /Reports`.

**Best practices**

1. **`update` replaces the cadence, but carries over config.** `PUT` overwrites the whole schedule, so resend every **cadence** flag you want to keep — an omitted `--time-of-day` or `--day` reverts to the server default. **Config is different**: `sumcli` reads the schedule first and carries over `--email`, `--param`, `--output-folder`, `--max-concurrent-runs`, and `--paused` when you do not pass them. Pass a flag to override its field. This merge exists because `email_recipients`, `params`, and `output_config` have no server-side default — without it, changing the cadence would silently stop all email delivery. `--playbook` is optional on `update` and defaults to the playbook already scheduled; sum-api rejects a change of target, so there is normally no reason to pass it. If the result carries `unmapped_config_keys`, this `sumcli` is older than the API and dropped those fields — upgrade and re-apply them.
2. **Prefer `pause` over `delete`.** Pausing keeps the schedule and its run history. Deleting is irreversible and needs `--confirm`.
3. **`schedules run` sends real email immediately and needs `--confirm`.** It is not a dry run. Without `--confirm` the command refuses and lists the recipients it would have mailed; show that list to the user before re-running. A valid but wrong schedule id is the easy mistake — it delivers to someone else's recipients, and nothing can recall it.
4. **Create paused when the cadence is uncertain.** `--paused` registers the schedule without firing it. Verify with `schedules run --confirm` for a one-off, then `resume`.
5. **`--paused` is tri-state.** `--paused` sends `true`, `--no-paused` sends `false`, and omitting the flag sends no key. On `update`, omitting it keeps the schedule's current pause state, because the config merge carries the existing value over.
6. **`--email` is not validated — a wrong address fails silently.** The contract types it as a plain string with only a length check (3–320 chars), so an id, a typo, or a dead mailbox is accepted, stored, and never delivered. Nothing bounces back. **Never guess an address**: `auth whoami` and `tenant show` return `user_id`, not email, so ask the user. If you cannot get one, create the schedule with no recipients — the run still produces its output in the project — and add email later.
7. **Do not schedule a playbook whose failure path is unproven.** A playbook that has only ever succeeded has never exercised its own error handling. Trigger it manually at least once, and prefer one that has failed at least once safely, before putting it on an unattended cadence.
8. **Match the cadence to how often the data actually changes.** Daily on a source that updates weekly means 365 near-identical emails a year, and recipients stop reading them. Confirm the intended frequency when the request is ambiguous — "every Monday morning" and "every morning" are very different asks.
9. **Always set `--zone` for business hours.** The default is UTC, so an unzoned `09:00` is early evening in Sydney and the small hours in Los Angeles. Pass an IANA ID (`America/New_York`), which tracks daylight saving; a fixed offset does not.
10. **`--param` values are strings.** The contract types them as strings, so `--param threshold=0.8` arrives as `"0.8"`. The playbook must do its own casting.
11. **Repeat `--email` per recipient**, as `address[:type[:name]]` — e.g. `--email ops@acme.com`, `--email cfo@acme.com:cc:Dana`. Type defaults to `to`; max 50 recipients.
12. **Confirm delivery through `schedules runs`**, not by assuming a create succeeded. A schedule can exist and still fail every run — for example, if the playbook errors or the output folder is wrong.

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

`SUMMATION_CONFIG_FILE`, `SUMMATION_PROFILE`, `SUMMATION_PROJECT`, `SUM_API_BASE_URL`, `SUM_API_CLIENT_ID`, `SUM_API_CLIENT_SECRET`, `SUM_API_ACCESS_TOKEN`, `SUM_API_M2M_SCOPE`, `SUMCLI_OUTPUT`, `SUMCLI_INTENT`, `SUMCLI_NO_UPDATE_CHECK`, `SUMCLI_CLIENT_CONTEXT` (calling-surface token appended to the User-Agent, e.g. `claude-plugin/0.4.0`; analytics only).

## More detail

- Full workflows, SharePoint notes, and design rules: repository `README.md`
- Live flags: `sumcli <resource> --help`
