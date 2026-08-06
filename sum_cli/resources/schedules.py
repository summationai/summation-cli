"""`sumcli schedules ...`"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from sum_cli.commands import (
    ProfileOption,
    api_client,
    api_confirm_params,
    extract_list,
    require_confirm,
    require_project,
    unwrap_data,
)
from sum_cli.output import emit, emit_error, err, ok, truncate_list

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


def _invalid(message: str, fix: str) -> None:
    emit_error(err("INVALID_REQUEST", message, fix))


def _parse_recipient(raw: str) -> dict:
    """Parse ``--email`` as ``address[:type[:name]]`` (type defaults to ``to``)."""
    parts = raw.split(":", 2)
    email = parts[0].strip()
    if not email:
        _invalid(
            f"Invalid --email value {raw!r}: missing address.",
            "Use --email address[:type[:name]], e.g. --email ceo@acme.com:cc:Dana.",
        )
    recipient: dict = {"email": email}
    if len(parts) > 1 and parts[1].strip():
        rtype = parts[1].strip().lower()
        if rtype not in _RECIPIENT_TYPES:
            _invalid(
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
        _invalid(
            f"Invalid --param value {raw!r}: expected key=value.",
            "Use --param key=value, e.g. --param region=emea.",
        )
    return key.strip(), value


def _load_json_object(path: Path, flag: str) -> dict:
    try:
        parsed = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        _invalid(f"Invalid JSON in {flag}: {exc}", f"Provide a valid JSON object in {flag}.")
    except OSError as exc:
        _invalid(
            f"Cannot read {flag}: {exc}", f"Check that the {flag} path exists and is readable."
        )
    if not isinstance(parsed, dict):
        _invalid(f"{flag} must contain a JSON object.", 'Use an object, e.g. {"subject": "..."}.')
    return parsed


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
        _invalid(
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
                _invalid(
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


def _build_config(
    *,
    params: list[str] | None,
    output_folder: str | None,
    output_config_file: Path | None,
    emails: list[str] | None,
    max_concurrent_runs: int | None,
    paused: bool,
) -> dict:
    config: dict = {}
    if params:
        config["params"] = dict(_parse_param(raw) for raw in params)
    if output_folder is not None:
        config["output_folder"] = output_folder
    if output_config_file is not None:
        config["output_config"] = _load_json_object(output_config_file, "--output-config-file")
    if emails:
        config["email_recipients"] = [_parse_recipient(raw) for raw in emails]
    if max_concurrent_runs is not None:
        config["max_concurrent_runs"] = max_concurrent_runs
    if paused:
        config["paused"] = True
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
    typer.Option("--email", help="Recipient as address[:type[:name]]; repeatable (max 50)."),
]
MaxConcurrentRunsOption = Annotated[
    int | None,
    typer.Option("--max-concurrent-runs", min=1, max=5, help="Max concurrent runs (default 1)."),
]
PausedOption = Annotated[bool, typer.Option("--paused", help="Create/update in a paused state.")]


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
    paused: PausedOption = False,
    profile: ProfileOption = None,
) -> None:
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
    paused: PausedOption = False,
    profile: ProfileOption = None,
) -> None:
    """PUT replaces the schedule, so every field must be supplied again.

    The target must match the currently scheduled playbook; sum-api rejects a
    change of target on update.
    """
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
        body = c.request("PUT", f"/v1/schedules/{schedule_id}", json=payload)
    emit(ok({"schedule": unwrap_data(body or {}, "data") or body, "project_id": pid}))


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
    profile: ProfileOption = None,
) -> None:
    payload: dict = {}
    if reason is not None:
        payload["reason"] = reason
    if effective_run_at is not None:
        payload["effective_run_at"] = effective_run_at
    with api_client(ctx, profile) as c:
        body = c.request("POST", f"/v1/schedules/{schedule_id}/runs", json=payload)
    emit(ok({"run": unwrap_data(body or {}, "data") or body, "schedule_id": schedule_id}))
