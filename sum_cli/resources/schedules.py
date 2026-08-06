"""`sumcli schedules ...`"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from sum_cli.commands import (
    ProfileOption,
    api_client,
    api_confirm_params,
    extract_list,
    load_json_object,
    require_confirm,
    require_project,
    unwrap_data,
)
from sum_cli.output import emit, emit_error, err, invalid_request, ok, truncate_list

app = typer.Typer(no_args_is_help=True)

# sum-api models schedules as a discriminated union on ``kind``; only "playbook"
# exists today, so the CLI sends it implicitly rather than exposing a one-value flag.
_KIND = "playbook"

_SCHEDULE_TYPES = (
    "cron",
    "interval",
    "one_time",
    "daily",
    "weekly",
    "biweekly",
    "monthly",
    "month_end",
    "yearly",
)
_DAYS_OF_WEEK = (
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
)
_RECIPIENT_TYPES = ("to", "cc", "bcc")
# PlaybookScheduleConfigRequest.email_recipients maxItems; checked client-side so an
# over-long list fails before the request (matches chats._DETAILS_MAX_LEN).
_MAX_RECIPIENTS = 50


def _check_recipient_limit(emails: list[str] | None) -> None:
    """Pure input validation, called before require_project so an over-long list
    does not need a resolved project to report — matches the chats.py ordering."""
    if emails and len(emails) > _MAX_RECIPIENTS:
        invalid_request(
            f"--email was given {len(emails)} times; the limit is {_MAX_RECIPIENTS}.",
            f"Send to {_MAX_RECIPIENTS} recipients or fewer.",
        )


def _parse_recipient(raw: str) -> dict:
    """Parse ``--email`` as ``address[:type[:name]]`` (type defaults to ``to``)."""
    parts = raw.split(":", 2)
    email = parts[0].strip()
    if not email:
        invalid_request(
            f"Invalid --email value {raw!r}: missing address.",
            "Use --email address[:type[:name]], e.g. --email ceo@acme.com:cc:Dana.",
        )
    recipient: dict = {"email": email}
    if len(parts) > 1 and parts[1].strip():
        rtype = parts[1].strip().lower()
        if rtype not in _RECIPIENT_TYPES:
            invalid_request(
                f"Invalid recipient type {rtype!r} in --email {raw!r}.",
                f"Use one of: {', '.join(_RECIPIENT_TYPES)}.",
            )
        recipient["type"] = rtype
    if len(parts) > 2 and parts[2].strip():
        recipient["name"] = parts[2].strip()
    return recipient


def _parse_param(raw: str) -> tuple[str, str]:
    """Parse ``--param key=value``. Playbook params are strings per the contract."""
    key, sep, value = raw.partition("=")
    if not sep or not key.strip():
        invalid_request(
            f"Invalid --param value {raw!r}: expected key=value.",
            "Use --param key=value, e.g. --param region=emea.",
        )
    return key.strip(), value


def _build_schedule_expression(
    *,
    type: str,
    time_of_day: str | None,
    zone_id: str | None,
    cron_expression: str | None,
    every_minutes: int | None,
    interval: int | None,
    days_of_week: list[str] | None,
    day_of_month: int | None,
    month: int | None,
    run_date: str | None,
    anchor_date: str | None,
) -> dict:
    if type not in _SCHEDULE_TYPES:
        invalid_request(
            f"Invalid --type {type!r}.",
            f"Use one of: {', '.join(_SCHEDULE_TYPES)}.",
        )
    expression: dict = {"type": type}
    if time_of_day is not None:
        expression["time_of_day"] = time_of_day
    if zone_id is not None:
        expression["zone_id"] = zone_id
    if cron_expression is not None:
        expression["cron_expression"] = cron_expression
    if every_minutes is not None:
        expression["every_minutes"] = every_minutes
    if interval is not None:
        expression["interval"] = interval
    if days_of_week:
        normalized = []
        for day in days_of_week:
            upper = day.strip().upper()
            if upper not in _DAYS_OF_WEEK:
                invalid_request(
                    f"Invalid --day {day!r}.",
                    f"Use one of: {', '.join(_DAYS_OF_WEEK)}.",
                )
            normalized.append(upper)
        expression["days_of_week"] = normalized
    if day_of_month is not None:
        expression["day_of_month"] = day_of_month
    if month is not None:
        expression["month"] = month
    if run_date is not None:
        expression["run_date"] = run_date
    if anchor_date is not None:
        expression["anchor_date"] = anchor_date
    return expression


# Response config is camelCase; request config is snake_case. Only these fields are
# writable — the response also carries read-only keys (e.g. targetAvailable) that must
# not be echoed back into a PUT.
_CONFIG_RESPONSE_TO_REQUEST = {
    "params": "params",
    "outputFolder": "output_folder",
    "outputConfig": "output_config",
    "emailRecipients": "email_recipients",
    "maxConcurrentRuns": "max_concurrent_runs",
    "paused": "paused",
}

# Read-only response keys that are correctly absent from a PUT body. Anything outside
# both this set and _CONFIG_RESPONSE_TO_REQUEST is unrecognized and gets reported.
_CONFIG_RESPONSE_READ_ONLY = frozenset({"targetAvailable"})


def _existing_config(schedule: object) -> dict:
    """Writable config fields from a GET response, renamed for a PUT body.

    ``PUT /v1/schedules/{id}`` fully replaces the schedule, and ``email_recipients``,
    ``params``, and ``output_config`` have no server-side default. Without this merge,
    updating only the cadence would silently drop the recipient list.
    """
    if not isinstance(schedule, dict):
        return {}
    current = schedule.get("config")
    if not isinstance(current, dict):
        return {}
    # Membership uses ==, and False/0/"" equal none of these, so falsy-but-real values
    # survive. Do not rewrite this as a truthiness check: dropping paused=False would
    # let the server default silently re-pause an unpaused schedule.
    return {
        request_key: current[response_key]
        for response_key, request_key in _CONFIG_RESPONSE_TO_REQUEST.items()
        if current.get(response_key) not in (None, {}, [])
    }


def _recipient_addresses(schedule: object) -> list[str]:
    """Email addresses a manual run would deliver to, for the confirm prompt."""
    if not isinstance(schedule, dict):
        return []
    config = schedule.get("config")
    if not isinstance(config, dict):
        return []
    recipients = config.get("emailRecipients")
    if not isinstance(recipients, list):
        return []
    return [
        entry["email"]
        for entry in recipients
        if isinstance(entry, dict) and isinstance(entry.get("email"), str) and entry["email"]
    ]


def _refuse_unconfirmed_run(schedule_id: str, schedule: object) -> NoReturn:
    """Refuse an unconfirmed manual run, naming the recipients it would email."""
    addresses = _recipient_addresses(schedule)
    if addresses:
        shown = ", ".join(addresses[:5])
        if len(addresses) > 5:
            shown += f", and {len(addresses) - 5} more"
        detail = f" It emails {len(addresses)} recipient(s): {shown}."
    else:
        # No recipients in the response is not proof none exist — the config may be
        # withheld or the mapping stale — so do not promise the run sends nothing.
        detail = " No recipients were listed on the schedule; check `schedules show`."
    emit_error(
        err(
            "CONFIRM_REQUIRED",
            f"Running schedule {schedule_id} now sends real output immediately.{detail}",
            "Re-run with --confirm to send now.",
        )
    )


def _existing_playbook_id(schedule: object) -> str | None:
    """Playbook id from a GET response target, for defaulting ``update --playbook``."""
    if not isinstance(schedule, dict):
        return None
    target = schedule.get("target")
    if not isinstance(target, dict):
        return None
    # Response is camelCase; accept snake_case too so a contract change does not
    # silently turn the default off.
    value = target.get("playbookId") or target.get("playbook_id")
    return value if isinstance(value, str) and value else None


def _unmapped_config_keys(schedule: object) -> list[str]:
    """Response config keys this CLI does not know how to carry into a PUT.

    The schedule config response type is ``"schema": {}``, so the camelCase mapping
    above was derived from an observed payload, not the contract. If sum-api adds a
    writable config field, ``_existing_config`` drops it and the full-replace PUT
    silently discards it — the same data loss the merge exists to prevent, but
    invisible. Surfacing the key names turns that into something the caller can see.
    """
    if not isinstance(schedule, dict):
        return []
    current = schedule.get("config")
    if not isinstance(current, dict):
        return []
    known = set(_CONFIG_RESPONSE_TO_REQUEST) | _CONFIG_RESPONSE_READ_ONLY
    return sorted(key for key in current if key not in known)


def _build_config(
    *,
    params: list[str] | None,
    output_folder: str | None,
    output_config_file: Path | None,
    emails: list[str] | None,
    max_concurrent_runs: int | None,
    paused: bool | None,
) -> dict:
    config: dict = {}
    if params:
        config["params"] = dict(_parse_param(raw) for raw in params)
    if output_folder is not None:
        config["output_folder"] = output_folder
    if output_config_file is not None:
        config["output_config"] = load_json_object(
            output_config_file, "--output-config-file", shape_hint='{"subject": "..."}'
        )
    if emails:
        config["email_recipients"] = [_parse_recipient(raw) for raw in emails]
    if max_concurrent_runs is not None:
        config["max_concurrent_runs"] = max_concurrent_runs
    if paused is not None:
        config["paused"] = paused
    return config


TypeOption = Annotated[
    str,
    typer.Option(
        "--type",
        help=f"Cadence type. One of: {', '.join(_SCHEDULE_TYPES)}.",
    ),
]
PlaybookOption = Annotated[
    str, typer.Option("--playbook", help="Project playbook file id to schedule.")
]
UpdatePlaybookOption = Annotated[
    str | None,
    typer.Option(
        "--playbook",
        help="Project playbook file id. Defaults to the one already scheduled; sum-api "
        "rejects a change of target, so there is normally no reason to pass it.",
    ),
]
DescriptionOption = Annotated[
    str | None, typer.Option("--description", help="Human-readable schedule description.")
]
TimeOfDayOption = Annotated[
    str | None, typer.Option("--time-of-day", help="Local HH:mm or HH:mm:ss time (default 09:00).")
]
ZoneOption = Annotated[
    str | None,
    typer.Option("--zone", help="IANA timezone ID, e.g. America/Los_Angeles (default UTC)."),
]
CronOption = Annotated[
    str | None, typer.Option("--cron", help="Cron expression (with --type cron).")
]
EveryMinutesOption = Annotated[
    int | None,
    typer.Option(
        "--every-minutes", min=1, max=1440, help="Interval minutes (with --type interval)."
    ),
]
IntervalOption = Annotated[
    int | None, typer.Option("--interval", min=1, max=12, help="Cadence multiplier (default 1).")
]
DayOption = Annotated[
    list[str] | None,
    typer.Option("--day", help="Day of week for weekly cadences; repeatable (e.g. MONDAY)."),
]
DayOfMonthOption = Annotated[
    int | None, typer.Option("--day-of-month", min=1, max=31, help="Day of month (1-31).")
]
MonthOption = Annotated[int | None, typer.Option("--month", min=1, max=12, help="Month (1-12).")]
RunDateOption = Annotated[
    str | None, typer.Option("--run-date", help="YYYY-MM-DD run date (with --type one_time).")
]
AnchorDateOption = Annotated[
    str | None, typer.Option("--anchor-date", help="YYYY-MM-DD anchor date for recurring cadences.")
]
ParamOption = Annotated[
    list[str] | None,
    typer.Option("--param", help="Playbook string parameter as key=value; repeatable."),
]
OutputFolderOption = Annotated[
    str | None,
    typer.Option("--output-folder", help="Project folder for outputs (default /Reports)."),
]
OutputConfigFileOption = Annotated[
    Path | None,
    typer.Option("--output-config-file", help="JSON file of non-sensitive output config."),
]
EmailOption = Annotated[
    list[str] | None,
    typer.Option(
        "--email",
        help=f"Recipient as address[:type[:name]]; repeatable (max {_MAX_RECIPIENTS}).",
    ),
]
MaxConcurrentRunsOption = Annotated[
    int | None,
    typer.Option("--max-concurrent-runs", min=1, max=5, help="Max concurrent runs (default 1)."),
]
PausedOption = Annotated[
    bool | None,
    typer.Option(
        "--paused/--no-paused",
        help="Pause or unpause the schedule. Omit to leave the server default (create) "
        "or the current state unspecified (update).",
    ),
]


@app.command("list")
def list_schedules(
    ctx: typer.Context,
    project: Annotated[
        str | None, typer.Option("--project", help="Filter to project-scoped schedules.")
    ] = None,
    target: Annotated[
        str | None,
        typer.Option("--target", help="Filter to a scheduled target, e.g. a playbook id."),
    ] = None,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    params: dict = {"kind": _KIND}
    if project:
        params["project_id"] = project
    if target:
        params["target_id"] = target
    with api_client(ctx, profile) as c:
        body = c.request("GET", "/v1/schedules", params=params)
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "schedules")
    listed = truncate_list(items, count=count)
    emit(
        ok(
            {
                "schedules": listed["items"],
                **{k: v for k, v in listed.items() if k != "items"},
            }
        )
    )


@app.command("show")
def show_schedule(
    ctx: typer.Context,
    schedule_id: Annotated[str, typer.Argument(help="Schedule id.")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/schedules/{schedule_id}")
    emit(ok({"schedule": unwrap_data(body or {}, "data") or body}))


@app.command("create")
def create_schedule(
    ctx: typer.Context,
    playbook: PlaybookOption,
    type: TypeOption,
    project: Annotated[str | None, typer.Option("--project")] = None,
    description: DescriptionOption = None,
    time_of_day: TimeOfDayOption = None,
    zone: ZoneOption = None,
    cron: CronOption = None,
    every_minutes: EveryMinutesOption = None,
    interval: IntervalOption = None,
    day: DayOption = None,
    day_of_month: DayOfMonthOption = None,
    month: MonthOption = None,
    run_date: RunDateOption = None,
    anchor_date: AnchorDateOption = None,
    param: ParamOption = None,
    output_folder: OutputFolderOption = None,
    output_config_file: OutputConfigFileOption = None,
    email: EmailOption = None,
    max_concurrent_runs: MaxConcurrentRunsOption = None,
    paused: PausedOption = None,
    profile: ProfileOption = None,
) -> None:
    _check_recipient_limit(email)
    pid = require_project(ctx, project)
    payload: dict = {
        "kind": _KIND,
        "target": {"project_id": pid, "playbook_id": playbook},
        "schedule": _build_schedule_expression(
            type=type,
            time_of_day=time_of_day,
            zone_id=zone,
            cron_expression=cron,
            every_minutes=every_minutes,
            interval=interval,
            days_of_week=day,
            day_of_month=day_of_month,
            month=month,
            run_date=run_date,
            anchor_date=anchor_date,
        ),
    }
    if description is not None:
        payload["description"] = description
    config = _build_config(
        params=param,
        output_folder=output_folder,
        output_config_file=output_config_file,
        emails=email,
        max_concurrent_runs=max_concurrent_runs,
        paused=paused,
    )
    if config:
        payload["config"] = config
    with api_client(ctx, profile) as c:
        body = c.request("POST", "/v1/schedules", json=payload)
    emit(ok({"schedule": unwrap_data(body or {}, "data") or body, "project_id": pid}))


@app.command("update")
def update_schedule(
    ctx: typer.Context,
    schedule_id: Annotated[str, typer.Argument(help="Schedule id.")],
    type: TypeOption,
    playbook: UpdatePlaybookOption = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    description: DescriptionOption = None,
    time_of_day: TimeOfDayOption = None,
    zone: ZoneOption = None,
    cron: CronOption = None,
    every_minutes: EveryMinutesOption = None,
    interval: IntervalOption = None,
    day: DayOption = None,
    day_of_month: DayOfMonthOption = None,
    month: MonthOption = None,
    run_date: RunDateOption = None,
    anchor_date: AnchorDateOption = None,
    param: ParamOption = None,
    output_folder: OutputFolderOption = None,
    output_config_file: OutputConfigFileOption = None,
    email: EmailOption = None,
    max_concurrent_runs: MaxConcurrentRunsOption = None,
    paused: PausedOption = None,
    profile: ProfileOption = None,
) -> None:
    """PUT replaces the schedule, so every cadence field must be supplied again.

    Two exceptions, both read from the current schedule this command fetches first:
    config fields you do not pass are carried over, so changing the cadence does not
    silently drop the recipient list, and ``--playbook`` defaults to the playbook
    already scheduled. Pass a flag to override its field.

    The target must match the currently scheduled playbook; sum-api rejects a
    change of target on update.
    """
    _check_recipient_limit(email)
    pid = require_project(ctx, project)
    payload: dict = {
        "kind": _KIND,
        "schedule": _build_schedule_expression(
            type=type,
            time_of_day=time_of_day,
            zone_id=zone,
            cron_expression=cron,
            every_minutes=every_minutes,
            interval=interval,
            days_of_week=day,
            day_of_month=day_of_month,
            month=month,
            run_date=run_date,
            anchor_date=anchor_date,
        ),
    }
    if description is not None:
        payload["description"] = description
    overrides = _build_config(
        params=param,
        output_folder=output_folder,
        output_config_file=output_config_file,
        emails=email,
        max_concurrent_runs=max_concurrent_runs,
        paused=paused,
    )
    with api_client(ctx, profile) as c:
        existing = c.request("GET", f"/v1/schedules/{schedule_id}")
        current = unwrap_data(existing or {}, "data") or existing
        # sum-api rejects a target change, so the stored playbook is the only valid
        # value. Default it rather than making the user run `show` to copy it back.
        target_playbook = playbook or _existing_playbook_id(current)
        if not target_playbook:
            invalid_request(
                f"Schedule {schedule_id} has no playbook id to reuse.",
                "Pass --playbook with the id from `sumcli schedules show`.",
            )
        payload["target"] = {"project_id": pid, "playbook_id": target_playbook}
        config = {**_existing_config(current), **overrides}
        if config:
            payload["config"] = config
        body = c.request("PUT", f"/v1/schedules/{schedule_id}", json=payload)
    result: dict = {"schedule": unwrap_data(body or {}, "data") or body, "project_id": pid}
    # A config key this CLI cannot map is dropped by the full-replace PUT. Name it
    # rather than losing it quietly.
    unmapped = _unmapped_config_keys(current)
    if unmapped:
        result["unmapped_config_keys"] = unmapped
        result["warning"] = (
            f"Dropped unrecognized config on update: {', '.join(unmapped)}. "
            "This sumcli is older than the API; upgrade and re-apply those fields."
        )
    emit(ok(result))


@app.command("delete")
def delete_schedule(
    ctx: typer.Context,
    schedule_id: Annotated[str, typer.Argument(help="Schedule id.")],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="schedules delete")
    with api_client(ctx, profile) as c:
        c.request("DELETE", f"/v1/schedules/{schedule_id}", params=api_confirm_params())
    emit(ok({"deleted": schedule_id}))


@app.command("pause")
def pause_schedule(
    ctx: typer.Context,
    schedule_id: Annotated[str, typer.Argument(help="Schedule id.")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("POST", f"/v1/schedules/{schedule_id}/pause")
    emit(ok({"schedule": unwrap_data(body or {}, "data") or body}))


@app.command("resume")
def resume_schedule(
    ctx: typer.Context,
    schedule_id: Annotated[str, typer.Argument(help="Schedule id.")],
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("POST", f"/v1/schedules/{schedule_id}/resume")
    emit(ok({"schedule": unwrap_data(body or {}, "data") or body}))


@app.command("runs")
def list_schedule_runs(
    ctx: typer.Context,
    schedule_id: Annotated[str, typer.Argument(help="Schedule id.")],
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/schedules/{schedule_id}/runs")
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "runs")
    listed = truncate_list(items, count=count)
    emit(
        ok(
            {
                "runs": listed["items"],
                "schedule_id": schedule_id,
                **{k: v for k, v in listed.items() if k != "items"},
            }
        )
    )


@app.command("run")
def run_schedule_now(
    ctx: typer.Context,
    schedule_id: Annotated[str, typer.Argument(help="Schedule id.")],
    reason: Annotated[
        str | None, typer.Option("--reason", help="Reason recorded for the manual run.")
    ] = None,
    effective_run_at: Annotated[
        str | None,
        typer.Option("--effective-run-at", help="ISO-8601 effective run time for the run record."),
    ] = None,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    """Run the schedule now. Requires --confirm: this delivers real email immediately.

    A manual run is not recoverable once the mail is sent, so it is gated the same
    way `delete` is. The confirm prompt names the recipients first, since a typo'd
    but valid schedule id is the easy mistake here.
    """
    payload: dict = {}
    if reason is not None:
        payload["reason"] = reason
    if effective_run_at is not None:
        payload["effective_run_at"] = effective_run_at
    with api_client(ctx, profile) as c:
        if not confirm:
            # Fetch before refusing so the message can name who would be emailed.
            existing = c.request("GET", f"/v1/schedules/{schedule_id}")
            _refuse_unconfirmed_run(schedule_id, unwrap_data(existing or {}, "data") or existing)
        body = c.request("POST", f"/v1/schedules/{schedule_id}/runs", json=payload)
    emit(ok({"run": unwrap_data(body or {}, "data") or body, "schedule_id": schedule_id}))
