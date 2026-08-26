# summation-cli (`sumcli`)

Public Summation CLI — a first-party client of the Summation API. Talks to stable `/v1` routes on the public gateway.

## Naming


| Context        | Name            |
| -------------- | --------------- |
| PyPI / package | `summation-cli` |
| Python import  | `sum_cli`       |
| Binary         | `sumcli`        |


## Install

Requires [uv](https://docs.astral.sh/uv/) and **Python 3.11+** (uv can install Python for you).

```bash
uv tool install summation-cli          # latest
uv tool install summation-cli==X.Y.Z   # or pin a release
```

Or bootstrap uv + install in one shot:

```bash
curl -fsSL https://install.summation.com/sumcli | sh
```

```powershell
irm https://install.summation.com/sumcli.ps1 | iex
```

From cmd.exe:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://install.summation.com/sumcli.ps1 | iex"
```

Then:

```bash
sumcli --help
sumcli update                   # upgrade a uv-managed install to the latest PyPI release
```

A stderr notice appears when a newer version is on PyPI. Lookups are cached
(a day on success, 15 minutes after a failed fetch).
Disable with `SUMCLI_NO_UPDATE_CHECK=1`.
`sumcli update` upgrades a uv-managed install only.

The Summation plugin requires **sumcli ≥ 0.1.4**. Newer releases are always compatible; `sumcli update` installs PyPI latest.

## Resources


| Resource      | Description                                                                                                                                          |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `auth`        | Inspect authentication state (`whoami`, `status`, `token`, `login`, `logout`)                                                                        |
| `config`      | Profiles, active session, and `~/.summation/summation-config` (`use`, `set-project`, `import-env`, …)                                                |
| `tenant`      | Organization and tenant metadata                                                                                                                     |
| `projects`    | Project CRUD and `current`                                                                                                                           |
| `chats`       | Addison conversations; SSE → NDJSON with `--follow` on create/reply                                                                                  |
| `reports`     | Generate and verify reports (`.sdoc`); file ops via `files`                                                                                          |
| `playbooks`   | Playbook discovery                                                                                                                                   |
| `schedules`   | Recurring playbook runs (`list`, `show`, `create`, `update`, `delete`, `pause`, `resume`, `run`, `runs`); create may require workflows                |
| `workflows`   | Multi-step automations (typed graphs: create/update/activate/run, versions, node-types)                                                              |
| `files`       | Project-scoped files (`upload`, `download`, `list`, `show`, `import`, `delete`)                                                                      |
| `filesystem`  | Connected filesystem roots such as SharePoint (`roots`, `list`, `upload`, `download`, `mkdir`, `delete`, `import-env`, `set-defaults`)               |
| `catalog`     | Project catalog entries (`list`, `show`, `attach`, `detach`, `refresh`)                                                                              |
| `connections` | Data source connections (CRUD, `test`, `browse`, `datasets`, `attach-datasets`, `snapshot`, `snapshots`) and app connectors (`app-*`)                |
| `tables`      | Grid tables and CSV import (`tables import`); row loads via `append` or `upsert`; also `data`, `import-status`, `catalog-show`, `catalog-update` |
| `views`       | Summation views (`list`, `show`, `data`, `delete`, `catalog-show`, `catalog-update`)                                                                 |
| `grid`        | Grid `status`, `create` (`--kind calc`/`data`), `push`, `diff`, `validate`, `materialize`, `lineage`                                                                        |
| `queries`     | Read-only SQL execution (`queries run`)                                                                                                              |


Run `sumcli | jq '.result.resources'` for the live command tree with action blurbs, or `sumcli <resource> --help` for flags.

## Quickstart

### Device login (interactive)

```bash
sumcli auth login
sumcli auth whoami | jq .
```

`auth login` prints a device code and a URL. Approve it in the browser, and the CLI stores the session in `~/.summation/summation-config`.

Existing `~/.summation/config` users should rename that file to
`~/.summation/summation-config`; the TOML format is unchanged.

**Optional — named profile for a non-default host:**

```bash
sumcli config set-profile my-org \
  --base-url https://sandbox-api.summation.com

sumcli config use my-org                       # make it the active profile
sumcli auth login                              # log in against that profile
sumcli --profile my-org auth whoami | jq .     # or target a profile per command
```

Pin a default project per org so project-scoped commands need no `--project`:

```bash
sumcli config set-project --profile my-org --project prj-...
sumcli config clear-project --profile my-org       # undo
```

### M2M login

```bash
sumcli config set-profile my-org \
  --base-url https://sandbox-api.summation.com \
  --client-id "$CLIENT_ID" \
  --client-secret "$CLIENT_SECRET"

sumcli config use my-org
sumcli auth whoami | jq .
sumcli projects list | jq '.result.projects'
```

## Output

**JSON when piped, human-readable at a terminal.** JSON is a stable envelope with contextual `next_actions` (NDJSON for streaming commands); the human view is lossy — wide tables drop columns — so never parse it.

Override the default two ways:

```bash
SUMCLI_OUTPUT=human sumcli projects list   # any position
sumcli --output human projects list        # root option, before the subcommand
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

Step 2 also accepts `--file-id file-...` if you have the ID directly.

### Agent-owned data table (`grid create --kind data`)

When your code owns the rows — app state, an operator log, a suppression list — create
an empty **data** table from a column schema instead of deriving one from existing data:

```bash
sumcli grid create ops_log --kind data \
  --column event_id:uuid:notnull \
  --column op:string \
  --column count:integer \
  --key-column event_id
```

Each `--column` is `name:type[:null|notnull]`, order kept. Types: `string`, `integer`,
`decimal`, `big_decimal`, `boolean`, `date`, `datetime`, `json`, `uuid`. Nullable unless
`:notnull`. For a longer schema use `--columns-file cols.json` (a JSON array of
`{"name", "type", "nullable"}` objects). The table accepts rows immediately:

```bash
sumcli tables upsert tbl-... --rows '[{"event_id": "...", "op": "suppress", "count": 1}]'
```

**`tables append` vs `tables upsert`:** `upsert` (`PUT`) matches on business keys — the
usual path after `grid create --kind data`. `append` (`POST`) is append-only and requires
you to supply `s_id` in each row.

`--key-column` names the business key used to match rows on upsert, not the physical
primary key: every data table already has an integer `s_id` primary key and an
`s_created_at` timestamp added for you. Max 50 columns per create.

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

Schedules target **playbooks only**. Playbook ids come back as `fileId` from `playbooks list`. On tenants with workflows enabled, `schedules create` may return `403 use_workflows` — use `workflows` instead (existing schedules remain usable).

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

> **Note:** `schedules update` replaces the cadence, so re-send every cadence flag. Config is preserved — the command reads the schedule first and carries over `--email`, `--param`, `--output-folder`, `--max-concurrent-runs`, and `--paused` when you omit them.

### Workflows

Typed-graph automations under `/v1/workflows` (feature-gated). Author `graph.json` / `triggers.json` from `workflows node-types`, then create → activate → run.

```bash
sumcli workflows node-types
sumcli workflows create --project prj-... --title "Weekly" \
  --graph-file graph.json --triggers-file triggers.json
sumcli workflows activate wf_... --expected-revision N --confirm
sumcli workflows run wf_... --confirm
sumcli workflows runs wf_...
```

### Long-running operations

`chats create`, `chats reply`, `reports generate`, `reports verify`, `grid push`, and `tables import` all support `--wait`/`--no-wait` (and `--follow` where applicable). See **Long-running commands** below.

## Discovery

Bare invocation prints the full command tree as JSON. Resource names and actions come from the installed CLI; action blurbs for API-backed commands are derived from an OpenAPI snapshot bundled in the wheel.

```bash
sumcli | jq '.result.resources | keys'
sumcli | jq '.result.resources.projects'
sumcli | jq '.result.resources.projects.actions'
sumcli projects --help   # per-command flags and help strings
```

## Command shape

```text
sumcli [--intent TEXT] [--profile NAME] [--base-url URL] <resource> <action> [--options]
```

`--intent` is the human's request, using their words when possible (not a command summary). Optional — omitting it in machine mode warns on stderr but still runs, so scripted and scheduled callers are unaffected. Agents should always pass it. Discovery, `--help`, `--version`, `update`, and the `auth`, `config`, and `filesystem` groups never warn. `SUMCLI_INTENT` sets the string for a session. `SUMCLI_NO_INTENT=1` suppresses the header even when an intent is set.

Project-scoped commands accept `--project` when no default project is configured.

## Behavior

- Destructive commands require **`--confirm`**: `projects delete`, `files delete`, `views delete`, `tables delete`, `connections delete`, `connections app-delete`, `schedules delete`, `schedules run`, `workflows activate`, `workflows run`, `catalog detach`, `filesystem delete`, `config delete-profile`. `filesystem upload` requires `--confirm` only when it overwrites an existing file. `schedules run` / `workflows run` / `workflows activate` are gated because they can deliver real email/Slack immediately.
- `sumcli auth status` calls `GET /v1/auth/status` only (not an alias for `whoami`).
- `sumcli auth token` exchanges credentials if needed and prints a **redacted** token plus length.
- List commands default to **50** items unless `--count` is set (`showing`, `total`, `truncated` in the result).

### Network boundary

Commands talk **only** to the Summation API `/v1` routes, with these exceptions:

- `tables import` PUTs file bytes directly to a pre-signed URL that the API returns. The CLI never constructs that URL itself.
- `filesystem` (SharePoint) sends credentials to `login.microsoftonline.com` and file bytes to `graph.microsoft.com`; it does not go through sum-api.

### Long-running commands (`--wait` / `--follow`)


| Flag               | Meaning                                                                           |
| ------------------ | --------------------------------------------------------------------------------- |
| `--wait` (default) | Run to completion, then print the final envelope                                  |
| `--no-wait`        | Run to completion without printing progress                                       |
| `--follow`         | Print NDJSON progress while the operation runs (**requires `--wait`**)            |


**Uses `--wait` / `--follow`:** `chats create`, `chats reply`, `reports generate`, `reports verify`, `grid push`. All default to `--wait`. `reports generate` and `reports verify` also default to `--follow`; the rest default to `--no-follow`.

`--no-wait --follow` is rejected (`INVALID_FLAGS`, exit 1).

Examples:

```bash
sumcli reports generate -m "Q4 summary"              # wait, NDJSON stream (default follow)
sumcli reports generate -m "Q4 summary" --no-follow  # wait, single JSON response
sumcli reports generate -m "Q4 summary" --no-wait    # no progress, final envelope only
sumcli chats create -m "hello"                       # wait, single JSON response (follow off by default)
sumcli chats create -m "hello" --follow              # wait, NDJSON stream
```

`tables import` takes `--wait`/`--no-wait` but has no `--follow`; it streams NDJSON whenever it waits. `chats events` always streams NDJSON (`--raw-sse` optional).

### Streaming and exit codes

- Success and validation errors print one JSON envelope. Failures **exit 1**.
- With `--follow`, intermediate lines are NDJSON (`type`: `start`, `text`, `step`, `progress`, `log`, …). The **last line** is a terminal envelope: `type` `result` or `error`, carrying the same `ok` / `error` / `fix` fields as non-streaming output.
- Stream errors print one terminal `error` envelope and **exit 1**.

### Errors

Every failure is a JSON envelope with `ok: false`, an `error.code`, and a `fix` hint. Common codes: `NO_PROJECT`, `CONFIRM_REQUIRED`, `INVALID_FLAGS`, `IMPORT_FAILED`, `INTERNAL_ERROR`.

### M2M token cache

M2M tokens are cached for the life of the process and refreshed shortly before they expire, so repeated commands reuse one token.

Rotating a `client_secret` or changing `m2m_scope` fetches a new token automatically — no cache to clear.