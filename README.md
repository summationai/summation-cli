# summation-cli (`sumcli`)

Public Summation CLI — a first-party client of [sum-api](https://github.com/summationai/summation-skill). Talks to stable `/v1` routes on the public gateway, plus optional direct provider APIs for external storage (provider-specific).

## Naming

| Context | Name |
|---------|------|
| PyPI / package | `summation-cli` |
| Python import | `sum_cli` |
| Binary | `sumcli` |
| Entry point | `sum_cli.cli.main:main` |

## summation skill vs sumcli

Use **`sumcli`** for scripted automation and agent workflows. The summation skill (`~/.agents/skills/summation`) helper scripts may coexist until fully deprecated; prefer `sumcli` for new work.

Output uses JSON envelopes on stdout with contextual `next_actions`, and NDJSON for streaming commands. Output is **agent-first JSON whenever stdout is not a TTY** (piped, captured, or run by an agent), so the contract holds in scripted use — pipe through `jq`. At an interactive terminal it renders a human plain-text view instead. Force it with `SUMCLI_OUTPUT=json|human` (any position) or the root option `--output json|human`, which must precede the subcommand (`sumcli --output human projects list`). The human view is lossy (wide tables drop columns, noted inline) and not meant for parsing.

## Install

Requires [uv](https://docs.astral.sh/uv/) and **Python 3.11+** (uv can install Python for you).

```bash
uv tool install summation-cli
# or pin a release:
uv tool install summation-cli==X.Y.Z
```

Then:

```bash
sumcli --help
sumcli update                   # upgrade a uv-managed install to the latest PyPI release
```

Commands print a stderr notice when a newer PyPI version exists. Lookups are
cached (a day on success, 15 minutes after a failed fetch). Stdout is unchanged,
so JSON/`jq` still parse. Disable with `SUMCLI_NO_UPDATE_CHECK=1`.
`sumcli update` upgrades a uv-managed install only; other origins (pip, pipx,
brew) get a targeted error instead of a second copy on PATH.

**Bootstrap** (installs uv if needed, then `uv tool install summation-cli`):

```bash
curl -fsSL https://install.summation.com/sumcli | sh
```

```powershell
irm https://install.summation.com/sumcli.ps1 | iex
```

From **cmd.exe** (Windows Shell), launch the same PowerShell installer:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://install.summation.com/sumcli.ps1 | iex"
```

## Plugin compatibility

The Summation plugin requires **sumcli ≥ 0.1.5**. Newer CLI releases are always compatible — `sumcli update` (PyPI latest) is the upgrade path. A plugin release that needs a higher floor will bump its own `minVersion`; this CLI does not pin an upper bound.

`sumcli --version` prints a JSON envelope with `result.version` when stdout is not a TTY (or when `SUMCLI_OUTPUT=json`). That is the version string plugins should parse.

## Resources

| Resource | Description |
|----------|-------------|
| `auth` | Inspect authentication state (`whoami`, `status`, `token`, `login`) |
| `config` | Profiles, active session, and `~/.summation/summation-config` (`use`, `set-project`, `import-env`, …) |
| `tenant` | Organization and tenant metadata |
| `projects` | Project CRUD and `current` |
| `chats` | Addison conversations; SSE → NDJSON with `--follow` on create/reply |
| `reports` | Generate and verify reports (`.sdoc`); file ops via `files` |
| `playbooks` | Playbook discovery |
| `schedules` | Recurring playbook runs (CRUD, `pause`/`resume`, `run`, `runs`); create may require workflows |
| `workflows` | Multi-step automations (typed graphs: create/update/activate/run, versions, node-types) |
| `files` | Project-scoped files (`upload`, `download`, `list`, `delete`) |
| `filesystem` | Connected filesystem roots such as SharePoint |
| `catalog` | Project catalog entries (tables/views attached to a project) |
| `connections` | Data source connections (CRUD, `test`, `browse`, `datasets`, `attach-datasets`, `detach-dataset`, `snapshot`, `snapshots`) and app connectors (`app-*`) |
| `tables` | Grid tables and CSV import (`tables import`); row loads via `append` or `upsert`; also `data`, `import-status`, catalog helpers |
| `views` | Summation views |
| `grid` | Grid status, sync, lineage, and table creation (`create --kind calc` or `data`) |
| `queries` | Read-only SQL execution (`queries run`) |
| `verification-tests` | Validate, upload, attach, preview, and detach custom verification tests |

Run `sumcli | jq '.result.resources'` for the live command tree with action blurbs, or `sumcli <resource> --help` for flags.

### Custom verification tests

Validate bundles entirely offline, then use the active profile and normal bearer authentication for the managed lifecycle:

```bash
sumcli verification-tests validate --bundle ./tests.yaml
sumcli verification-tests upload --bundle ./tests.yaml
sumcli verification-tests list --subject-type deck
sumcli verification-tests attach --scope project --subject-type deck \
  --op add --custom-test-id vtd-...
sumcli verification-tests list-attachments --scope project --subject-type deck
sumcli verification-tests preview --scope project --subject-type deck
sumcli verification-tests detach vta-... --scope project --confirm
```

Project scope uses the profile's default project when `--project` is omitted. Cross-org calls use `--target-org ORG` and require an explicit project for project scope; identity always comes from the bearer token. Add `--dry-run` to `upload`, `attach`, or `detach` to validate and print the exact request without authentication or network access. A removal overlay (`attach --op remove --target-ref ...`) suppresses a test in resolution; `detach ... --confirm` soft-removes the attachment record itself.

### Developers

Editable install from this repo:

```bash
uv tool install .
# or: uv pip install -e .
```

**Releases are tag-driven.** `main` is the next unreleased line of development.
Merging without a version bump does not publish anything. To ship:

1. Bump `__version__` in `sum_cli/__init__.py` (and merge that to `main`).
2. Tag the release commit and push the tag:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
3. `.github/workflows/release.yml` re-runs tests, checks the tag matches
   `__version__`, builds, publishes to PyPI via Trusted Publishing (OIDC),
   and creates a GitHub Release with the wheel/sdist.

The tagged commit must be on `main`. A `v*` tag can point at any commit, so the
workflow also checks that the commit is an ancestor of `origin/main` and fails
if it is not. Tag after the version bump merges, not before.

One-time setup: add a PyPI Trusted Publisher for this repo
(`workflow: release.yml`, `environment: pypi`) and create a GitHub Environment
named `pypi`. Add required reviewers to that environment — the approval is the
last human gate before a publish.

Local/emergency publishes (and TestPyPI dry runs) still work with
`./scripts/publish.sh`:

```bash
export UV_PUBLISH_PASSWORD_TEST='pypi-...'   # TestPyPI
export UV_PUBLISH_PASSWORD='pypi-...'        # production (prefer CI)

./scripts/publish.sh                # TestPyPI
./scripts/publish.sh --production    # real PyPI (type the version to confirm)
```

A published version is permanent and can never be replaced.

## Quickstart

### Device login (interactive)

```bash
sumcli config set-profile onboard2 \
  --base-url https://api-<tenant>.summation.com

sumcli config use onboard2
sumcli --profile onboard2 auth login
sumcli --profile onboard2 auth whoami | jq .
```

### M2M login

```bash
sumcli config set-profile onboard2 \
  --base-url https://api-<tenant>.summation.com \
  --client-id "$SUM_API_CLIENT_ID" \
  --client-secret "$SUM_API_CLIENT_SECRET"

sumcli config use onboard2
sumcli --profile onboard2 auth login --m2m
sumcli auth whoami | jq .
sumcli projects list | jq '.result.projects'
```

## Common workflows

### Local CSV → grid table

Two ways: a one-shot direct ingest, or an explicit two-step that goes through the project file tree first.

**One-shot (recommended when you don't need the file in the project tree):**

```bash
sumcli tables import --local --path ./Customers.csv --table customers
```

Uploads bytes → previews schema → materializes a new grid table. Outputs NDJSON ending with `importStatus: SUCCESS` and a `tbl-...` ID. Server auto-detects column types.

**Two-step (when you want the CSV in the project file tree too):**

```bash
# 1. Upload the CSV into the project at /Customers.csv.
sumcli files upload ./Customers.csv

# 2. Promote it from the project tree into the grid.
sumcli tables import --remote --path /Customers.csv --table customers
```

Step 2 also accepts `--file-id file-...` if you have the ID directly. Internally, `--remote` mode downloads the file's bytes to a temp file, then runs the same upload+import flow as `--local` (sum-api has no direct project-file → grid endpoint today).

### Agent-owned data table (`grid create --kind data`)

`tables import` and `grid create --kind calc` both derive a table from data that already
exists. When your code owns the rows instead — app state, an operator log, a
suppression list — create an empty **data** table from a column schema, then load rows
with **`tables upsert`**:

```bash
sumcli grid create ops_log --kind data \
  --column event_id:uuid:notnull \
  --column op:string \
  --column count:integer \
  --column noted_at:datetime \
  --key-column event_id
```

Each `--column` is `name:type[:null|notnull]`, and order is kept. Types: `string`,
`integer`, `decimal`, `big_decimal`, `boolean`, `date`, `datetime`, `json`, `uuid`.
Columns are nullable unless you pass `:notnull`. For a longer schema, use
`--columns-file cols.json` with a JSON array instead:

```json
[{"name": "event_id", "type": "uuid", "nullable": false},
 {"name": "op", "type": "string"}]
```

The table accepts rows as soon as the create returns:

```bash
sumcli tables upsert tbl-... --rows '[{"event_id": "...", "op": "suppress", "count": 1}]'
```

**`tables append` vs `tables upsert`:** both hit `/v1/tables/{id}/rows`, different methods.

| Command | API | Rows must include |
|---------|-----|-------------------|
| `tables upsert` | `PUT` | Business-key columns only (`event_id`, …) — **use for `kind=data` tables** |
| `tables append` | `POST` | Primary key `s_id` (caller-assigned, append-only) |

`--key-column` on create names the **business key** matched on upsert, not the physical
primary key. Every data table already has an integer `s_id` primary key and an
`s_created_at` timestamp, added for you — declaring either in `--column` is refused.
A single create takes at most 50 columns.

### Existing project file → grid table

If the file is already in the project (uploaded by someone else, dropped via the UI, etc.):

```bash
sumcli files list | jq '.result.files[] | select(.fileName | endswith(".csv"))'
sumcli tables import --remote --path "/Order_Details.csv" --table order_details
```

### Inspect, attach, query, clean up

After `tables import` succeeds, the new table lives in the tenant grid but is **not** attached to the project catalog. Attach it to make it visible in `catalog list` and queryable as a project resource:

```bash
sumcli tables show tbl-...                                      # schema + columns
sumcli tables data tbl-... | jq '.result.data.rows[:5]'         # sample rows
sumcli catalog attach --source-type table --source-id tbl-...   # link to current project
sumcli catalog list                                             # confirm linkage
sumcli catalog detach --confirm file-...                        # remove the catalog entry
sumcli tables delete --confirm tbl-...                          # remove from grid
```

> **Note:** `tables delete` removes the underlying grid table but does **not** auto-cascade the project catalog entry that referenced it. Detach the entry separately with `catalog detach <file_id> --confirm`.

### Scheduled playbook runs

Schedules target **playbooks only** — `kind` is `playbook` in the API. Playbook ids come back as `fileId` from `playbooks list`. On tenants with workflows enabled, `schedules create` may return `403 use_workflows` — use `workflows` instead (existing schedules remain usable).

```bash
sumcli schedules create --project prj-... --playbook file-... \
  --type daily --time-of-day 09:30 --zone America/Los_Angeles \
  --email you@example.com

sumcli schedules list --project prj-...
sumcli schedules pause schedule_...      # stop without deleting
sumcli schedules run schedule_... --confirm   # trigger one off-cadence run (sends email)
sumcli schedules runs schedule_...       # run history
sumcli schedules delete schedule_... --confirm
```

`--type` accepts `cron`, `interval`, `one_time`, `daily`, `weekly`, `biweekly`, `monthly`, `month_end`, and `yearly`. Supply the fields each type needs: `--cron`, `--every-minutes`, `--run-date`, `--day`, `--day-of-month`, `--month`.

> **Note:** `PUT /v1/schedules/{id}` replaces the whole schedule, so `schedules update` re-sends every **cadence** flag. **Config is preserved**: the command reads the schedule first and carries over `--email`, `--param`, `--output-folder`, `--max-concurrent-runs`, and `--paused` when you omit them. This merge is deliberate — `email_recipients`, `params`, and `output_config` have no server-side default, so a cadence-only update would otherwise stop all email delivery.

### Workflows

Typed-graph automations under `/v1/workflows` (feature-gated). Author `graph.json` / `triggers.json` from `workflows node-types`, then create → activate → run.

```bash
sumcli workflows node-types
sumcli workflows create --project prj-... --title "Weekly" \
  --graph-file graph.json --triggers-file triggers.json
sumcli workflows activate wf_... --expected-revision N --confirm
sumcli workflows run wf_... --confirm   # --version from activeVersionId when omitted
sumcli workflows runs wf_...
```

### Long-running operations

`chats create`, `chats reply`, `reports generate`, `reports verify`, `grid push`, and `tables import` all support `--wait`/`--no-wait` (and `--follow` where applicable). See **Long-running commands** below.

## Multi-profile credentials

Most users should use device login; admin-managed accounts can use M2M. Power users can define several **environment accounts** in the config file. Each profile is a tenant + API host + credentials/session state (not a Ramp-style `--env` toggle on one identity). Name profiles `{tenant}_{env}` when you have multiple deployments (e.g. tenant sandbox, staging, production).

**Config file:** `~/.summation/summation-config` (TOML), overridable with `SUMMATION_CONFIG_FILE`.

```toml
[_meta]
active_profile = "tenant_staging"

[tenant_sandbox]
base_url = "https://sandbox-api-tenant.summation.com"
client_id = "..."
client_secret = "..."

[tenant_staging]
base_url = "https://staging-api-tenant.summation.com"
client_id = "..."
client_secret = "..."
default_project = "prj-..."  # optional; see project resolution below

[tenant_production]
base_url = "https://api-tenant.summation.com"
client_id = "..."
client_secret = "..."
```

Optional per-profile fields: `device_login_credential`, `access_token`, `token_expires_at`, `m2m_scope`, `default_project`.

### `config` — credential storage

| Command | Description |
|---------|-------------|
| `config path` | Print config file path |
| `config list` | List profiles (secrets not shown) |
| `config show [profile]` | Show one profile from file (secrets redacted) |
| `config active` | Resolved effective config: active profile, account, default project, credentials |
| `config import-env` | Import `SUM_API_*` from an env file into `~/.summation/summation-config` |
| `config set-profile` | Create or replace a profile (`--confirm` not required) |
| `config copy-profile` | Clone a profile |
| `config delete-profile` | Remove a profile (**`--confirm`**) |

`set-profile` options: `--base-url`, optional `--client-id` + `--client-secret`, `--default-project`, `--m2m-scope`, `--login/--no-login`.

- With only `--base-url`, `set-profile` creates a device-login-ready profile. Then run `sumcli --profile <name> auth login`.
- With both `--client-id` and `--client-secret`, the profile can also use `sumcli --profile <name> auth login --m2m`.
- `--login` only performs the M2M exchange path when M2M credentials are present.

### `config` — working session

Profile switching and the active default project live under `config` (there is no separate `context` resource):

| Command | Description |
|---------|-------------|
| `config use <profile>` | Set `_meta.active_profile`; optional `--project` writes `default_project` |
| `config set-project <id>` | Set `default_project` for the active (or `--profile`) profile |
| `config active` | Active profile, account, base URL, default project, resolved credentials |
| `config clear-project` | Clear `default_project` for the active (or `--profile`) profile |

Switch environment account:

- `sumcli config use tenant_staging` — updates shared config (interactive)
- `sumcli config use tenant_staging --project prj-...` — profile + default project in one step
- `sumcli --profile tenant_staging projects list` — one-off; **`--profile` / `--base-url` must come before the subcommand**
- `export SUMMATION_PROFILE=tenant_staging` — per-process (safe for parallel agents)

**Parallel agents:** do not call `config use` on a shared `~/.summation/summation-config`. Pass `--profile`, and/or set `SUMMATION_PROFILE` / `SUMMATION_PROJECT` in each subprocess. Prefer profiles **without** a file `default_project` when using `SUMMATION_PROJECT`, or pass `--project` on each command (see precedence below).

## Configuration precedence

Precedence is **field-specific** (there is no single global env-beats-file rule).

### Profile name

1. CLI `--profile`
2. `SUMMATION_PROFILE`
3. `[_meta].active_profile` in the config file
4. `"default"`

### API base URL

1. CLI `--base-url`
2. `SUM_API_BASE_URL`
3. Profile section `base_url` in the config file
4. `https://api.summation.com`

### Credentials (`client_id`, `client_secret`, `access_token`, `m2m_scope`)

1. Matching `SUM_API_*` environment variable
2. Profile section in the config file

**Auth behavior** (`auth.py`):

1. Profile `device_login_credential` — used as-is as the bearer token
2. `SUM_API_ACCESS_TOKEN` / profile `access_token` — used as-is (no M2M exchange)
3. `SUM_API_CLIENT_ID` + `SUM_API_CLIENT_SECRET` / profile `client_id` + `client_secret` — exchanged at `POST /v1/auth/m2m/token` (optional `scope` from `SUM_API_M2M_SCOPE` / profile `m2m_scope`)

Identity comes from the bearer token only — never `x-org-id` / `x-user-id`.

### Default project (for commands that need a project)

1. CLI `--project` on the command (highest)
2. Profile `default_project` in the config file
3. `SUMMATION_PROJECT` environment variable

Explicit `--project` always wins. When both file and env set a default, **the file value wins** over `SUMMATION_PROJECT`.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `SUMMATION_CONFIG_FILE` | Path to TOML config (default `~/.summation/summation-config`) |
| `SUMMATION_PROFILE` | Active profile name |
| `SUMMATION_PROJECT` | Default project ID when the profile has no `default_project` |
| `SUM_API_BASE_URL` | API host (no trailing slash required; stripped) |
| `SUM_API_CLIENT_ID` | M2M client ID |
| `SUM_API_CLIENT_SECRET` | M2M client secret |
| `SUM_API_ACCESS_TOKEN` | Static bearer token (skips M2M exchange) |
| `SUM_API_M2M_SCOPE` | Optional scope on M2M token request |
| `SUMCLI_INTENT` | Default `--intent` (human's request, their words when possible) |
| `SUMCLI_NO_INTENT` | Do not send `X-Summation-Intent`, even if `--intent` / `SUMCLI_INTENT` is set |
| `SHAREPOINT_TENANT_ID` | Azure AD tenant for SharePoint app-only auth |
| `SHAREPOINT_CLIENT_ID` | SharePoint app client id (falls back to `CLIENT_ID`) |
| `SHAREPOINT_CLIENT_SECRET` | SharePoint app secret (falls back to `CLIENT_SECRET`) |
| `SHAREPOINT_SITE_URL` | SharePoint site, e.g. `host.sharepoint.com:/sites/Name` |
| `SHAREPOINT_ROOT` | Default drive id (quote in shell/.env if it contains `!`) |
| `SHAREPOINT_PATH` | Default folder item id |

### SharePoint external storage (provider-specific)

Unlike the rest of sumcli, external storage commands talk **directly** to external storage providers (SharePoint via Microsoft Graph today). They do not use sum-api or the active M2M profile.

> Provider-specific external storage commands exist for SharePoint, but are omitted from this public README to keep it platform-agnostic.

Provider credentials and default root/path are stored in `~/.summation/summation-config` (provider-specific config).

## Discovery

Bare invocation prints the full command tree as JSON. Resource names and actions come from the live Typer app; action blurbs for API-backed commands are derived from the bundled OpenAPI snapshot (`sum_cli/data/openapi_snapshot.json`).

```bash
sumcli | jq '.result.resources | keys'
sumcli | jq '.result.resources.projects'
sumcli | jq '.result.resources.projects.actions'
sumcli projects --help   # per-command flags and Typer help strings
```

## Command shape

```text
sumcli [--intent TEXT] [--profile NAME] [--base-url URL] <resource> <action> [--options]
```

`--intent` is the human's request **in their own words** when possible — not a summary of the command. It is sent to sum-api as `X-Summation-Intent`. It is optional: omitting it in machine mode (piped, or `--output json`) prints a warning on stderr and the command still runs, so unattended callers such as Dagster ops keep working. Agents should always pass it — without it a run cannot be joined to a goal. `SUMCLI_INTENT` sets the string for a session. `--intent` is a root option and must precede the subcommand. `SUMCLI_NO_INTENT=1` is an org-level kill switch: the header is not attached, the missing-intent warning is skipped, and an oversized value is not refused.

No warning at all for: discovery (`sumcli` with no args), `--help`, `--version`, `update`, and the `auth`, `config`, and `filesystem` groups. `auth` and `config` set up the session before there is a goal to state; `filesystem` talks to the external storage provider with that provider's credentials and never reaches sum-api. The value is normalized to one line, control characters are removed, and it is limited to 500 bytes after encoding — so non-ASCII text gets fewer than 500 characters. An oversized intent is refused with `INTENT_TOO_LONG`, since that value would go on the wire.

Project-scoped commands accept `--project` when no default project is configured.

## Tests

```bash
python -m pytest -q
# equivalent:
PYTHONPATH=. pytest -q
```

OpenAPI drift is guarded offline against the bundled snapshot at `sum_cli/data/openapi_snapshot.json`:

```bash
python -m pytest tests/test_openapi_contract.py tests/test_load_spec.py -q
# refresh snapshot after sum-api ships new routes:
python scripts/refresh_openapi.py
# verify bundled snapshot matches production (nightly automation + manual pre-release):
python scripts/refresh_openapi.py --check
```

Per-PR CI gates on the offline contract tests above only. Production reconciliation runs on a schedule via `.github/workflows/sumcli-openapi-snapshot.yaml` so unrelated backend PRs are not reddened when sum-api deploys ahead of the snapshot.

Command-tree action blurbs for API-backed commands are derived from the snapshot at runtime via `sum_cli/openapi_doc.py`; `config` and other local-only actions stay hand-written there. Composite commands (`tables import`, `reports verify`) have known doc/route alignment gaps — see comments in `openapi_doc.py`.

## Design rules

# Resource group titles and command one-line summaries: sum_cli/openapi_doc.py
# (_RESOURCE_DESCRIPTIONS, _LOCAL_ACTION_BLURBS, apply_openapi_help). Do not duplicate
# Typer group help= or command docstrings in resource modules.
- OpenAPI at `${SUM_API_BASE_URL}/openapi.json` is the contract source of truth; `sum_cli/data/openapi_snapshot.json` is the offline copy shipped in the wheel and reconciled by `tests/test_openapi_contract.py` (CLI call sites must exist in the spec; uncovered spec operations must be allow-listed in `sum_cli/openapi_doc.py`).
- No imports from sum-api service code or gRPC clients.
- Destructive commands require **`--confirm`**: `projects delete`, `files delete`, `views delete`, `tables delete`, `connections delete`, `connections detach-dataset`, `connections app-delete`, `schedules delete`, `schedules run`, `workflows activate`, `workflows run`, `catalog detach`, `verification-tests detach`, `filesystem delete`, `config delete-profile`. `filesystem upload` requires `--confirm` only when it overwrites an existing file. `schedules run` / `workflows run` / `workflows activate` are gated because they can deliver real email/Slack immediately.
- `sumcli auth status` calls `GET /v1/auth/status` only (not an alias for `whoami`).
- `sumcli auth token` exchanges credentials if needed and prints a **redacted** token plus length.
- List commands default to **50** items unless `--count` is set (`showing`, `total`, `truncated` in the result).

### Network boundary

Commands talk **only** to the Summation API `/v1` routes, with these exceptions:

- `tables import` PUTs file bytes directly to a pre-signed URL that the API returns. The CLI never constructs that URL itself.
- `filesystem` (SharePoint) sends credentials to `login.microsoftonline.com` and file bytes to `graph.microsoft.com`; it does not go through sum-api.

### Long-running commands (`--wait` / `--follow`)

| Flag | Meaning |
|------|---------|
| `--wait` (default) | Drain the server's SSE stream to completion, then return the final envelope |
| `--no-wait` | Drain the same stream, but never print progress lines |
| `--follow` | Stream NDJSON progress to stdout while the operation runs (**requires `--wait`**) |

**Uses `--wait` / `--follow`:** `chats create`, `chats reply`, `reports generate`, `reports verify`, `grid push`.

**Defaults:** `--wait` on everywhere above. `reports generate` and `reports verify` default **`--follow` on** (SSE from sum-api), so they stream NDJSON; pass `--no-follow` to wait for completion and print one final envelope. `chats create`, `chats reply`, and `grid push` default `--follow` off and print one JSON envelope unless you pass `--follow`.

`--follow` requires `--wait`. Passing `--no-wait --follow` together is rejected with an `INVALID_FLAGS` error and **exit 1** on every command above. Passing `--no-wait` on its own is always valid, including on the commands that follow by default.

> **Caveat:** today `--no-wait` and `--wait` produce the same network behavior — both consume the server's SSE stream to completion, since sum-api has no fire-and-forget mode. The only difference is whether intermediate `--follow` progress is printed. If a true async response is added server-side, `--no-wait` will switch to it without a CLI change.

Examples:

```bash
sumcli reports generate -m "Q4 summary"              # wait, NDJSON stream (default follow)
sumcli reports generate -m "Q4 summary" --no-follow  # wait, single JSON response
sumcli reports generate -m "Q4 summary" --no-wait    # silent drain, final envelope only
sumcli chats create -m "hello"                       # wait, single JSON response (follow off by default)
sumcli chats create -m "hello" --follow              # wait, NDJSON stream
```

**`tables import`** uses `--wait/--no-wait` only (no `--follow`). With `--wait`, stdout is NDJSON (`step`, `progress`, then a terminal `result` or `error` line). Failed uploads or import statuses `FAILED` / `ERROR` emit an `error` terminal and **exit code 1**. On success the `result` carries `import_id` and the resolved **`table_id`** (looked up from `/v1/tables` by name, since `/v1/table-imports` returns only the import status), so you can pipe straight into `tables show <table_id>`.

**`chats events`** always streams NDJSON (`--raw-sse` optional); stream errors exit **1**.

### Streaming and exit codes

- Success and validation errors print one JSON envelope; failures use **`exit 1`** (`emit_error` or `SystemExit` after a stream error).
- With `--follow`, intermediate lines are NDJSON (`type`: `start`, `text`, `step`, `progress`, `log`, …). The **last line** is a terminal envelope: `type` `result` or `error`, spreading the same `ok` / `error` / `fix` fields as non-streaming output.
- SSE `error` events and transport failures produce a terminal `error` envelope and **exit 1** (without printing a second JSON blob).

### Errors

- `AuthError` and `ApiError` are caught in `main()` and emitted as structured envelopes.
- Resource commands call `emit_error()` for validation (`NO_PROJECT`, `CONFIRM_REQUIRED`, `INVALID_FLAGS`, `IMPORT_FAILED`, etc.).
- Unexpected exceptions become `INTERNAL_ERROR` envelopes.

### M2M token cache

M2M tokens from `/v1/auth/m2m/token` use the Stytch OAuth shape (`expires_in` seconds, typically 3600). The CLI caches in-process until `expires_in - 60s` skew (`TOKEN_CACHE_SKEW_SECONDS`), then refreshes.

Cache key includes **profile, base URL, client_id, client_secret, and m2m_scope** for M2M (so rotated secrets or scopes fetch a new token). Static `access_token` credentials are cached separately by token value.

If the response omits `expires_in`, TTL falls back to **300 seconds** (`DEFAULT_M2M_TTL_SECONDS`) after JWT `exp` parsing when possible.
