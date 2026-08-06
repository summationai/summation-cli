"""`sumcli` — the public Summation CLI."""

from __future__ import annotations

from dataclasses import dataclass

import typer

from sum_cli import __version__, debug_log
from sum_cli.auth import AuthError
from sum_cli.client import ApiError
from sum_cli.config import Config, load
from sum_cli.openapi_doc import (
    OpenApiSpecError,
    apply_openapi_help,
    build_command_tree_envelope,
)
from sum_cli.output import (
    OutputChoice,
    action,
    emit,
    emit_error,
    err,
    param,
    resolve_output_mode,
    set_output_mode,
)
from sum_cli.resources import (
    auth,
    catalog,
    chats,
    config,
    connections,
    files,
    filesystem,
    grid,
    playbooks,
    projects,
    queries,
    reports,
    tables,
    tenant,
    views,
)


@dataclass
class CliContext:
    profile: str | None
    base_url: str | None
    verbose: bool = False

    def config(self, *, profile: str | None = None) -> Config:
        return load(profile=profile or self.profile, base_url=self.base_url)


app = typer.Typer(
    no_args_is_help=False,
    help="sumcli — public Summation CLI (sum-api /v1).",
    add_completion=True,
)

app.add_typer(auth.app, name="auth")
app.add_typer(config.app, name="config")
app.add_typer(tenant.app, name="tenant")
app.add_typer(projects.app, name="projects")
app.add_typer(chats.app, name="chats")
app.add_typer(reports.app, name="reports")
app.add_typer(playbooks.app, name="playbooks")
app.add_typer(files.app, name="files")
app.add_typer(filesystem.app, name="filesystem")
app.add_typer(catalog.app, name="catalog")
app.add_typer(connections.app, name="connections")
app.add_typer(tables.app, name="tables")
app.add_typer(views.app, name="views")
app.add_typer(grid.app, name="grid")
app.add_typer(queries.app, name="queries")

apply_openapi_help(app)


def _output_callback(value: OutputChoice | None) -> OutputChoice | None:
    """Resolve the output mode eagerly from the --output value / env / TTY.

    Eager so the mode is set before other eager options (e.g. --version) and the
    no-subcommand command-tree dump emit their envelopes. --output is a root option
    only — it must precede the subcommand (`sumcli --output human projects list`);
    use SUMCLI_OUTPUT for after-the-fact control. We deliberately do NOT pre-scan
    argv for position independence: doing so outside click would silently misread a
    legitimate option value of `--output` (e.g. `chats send -m --output human`).
    """
    set_output_mode(resolve_output_mode(value.value if value else None))
    return value


def _version_callback(value: bool) -> None:
    if value:
        emit(
            {
                "ok": True,
                "command": "sumcli --version",
                "result": {"version": __version__},
                "next_actions": [],
            }
        )
        raise typer.Exit()


def _api_error_fields(body: object) -> tuple[str, str]:
    """Extract (code, message) from an API error body, always as strings.

    Neither field is guaranteed to be a string on the wire: FastAPI's 422 sends
    ``detail`` as a list of validation objects. Callers format and match on these,
    so coerce here rather than letting a non-string reach them.
    """
    message: object = str(body)
    code: object = "API_ERROR"
    if isinstance(body, dict):
        err_obj = body.get("error") or body
        if isinstance(err_obj, dict):
            message = err_obj.get("message") or err_obj.get("detail") or message
            code = err_obj.get("code") or code
    return _as_error_text(code, "API_ERROR"), _as_error_text(message, str(body))


def _as_error_text(value: object, fallback: str) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return fallback
    # A 422 detail list carries one entry per invalid field; join their ``msg``
    # values with the field path so the user sees what to fix, not a raw repr.
    if isinstance(value, list):
        parts = [_validation_detail_text(item) for item in value]
        joined = "; ".join(part for part in parts if part)
        return joined or fallback
    return str(value)


def _validation_detail_text(item: object) -> str:
    if not isinstance(item, dict):
        return str(item)
    msg = item.get("msg")
    if not isinstance(msg, str):
        return str(item)
    # loc is like ["body", "datasets", 0]; drop the leading location kind.
    loc = item.get("loc")
    if isinstance(loc, list) and len(loc) > 1:
        field = ".".join(str(part) for part in loc[1:])
        return f"{field}: {msg}"
    return msg


def _api_error_guidance(*, status: int, code: str, message: str) -> tuple[str, list]:
    if "service principal not found" in message.casefold():
        return (
            "Token exchange worked, but sum-api cannot resolve this M2M client in Stytch "
            "(the service principal may have been deleted). Create new sandbox M2M credentials "
            "in Summation, then update your profile and run sumcli auth login --m2m.",
            [
                action("Show config", "sumcli config active"),
                action(
                    "Update credentials",
                    "sumcli config set-profile <name> --client-id <id> --client-secret <secret> --login",
                    params={
                        "name": param("Profile name"),
                        "id": param("M2M client id"),
                        "secret": param("M2M client secret"),
                    },
                ),
            ],
        )
    if status == 401 or code == "unauthenticated":
        return (
            "Credentials are missing, expired, or rejected. Run sumcli auth login, or run "
            "sumcli auth login --m2m if this profile uses machine credentials.",
            [
                action("Start device login", "sumcli auth login"),
                action("Refresh M2M session", "sumcli auth login --m2m"),
                action("Show config", "sumcli config active"),
            ],
        )
    return (
        "Check sumcli auth whoami, profile credentials, and the request parameters.",
        [
            action("Show identity", "sumcli auth whoami"),
            action("Show config", "sumcli config active"),
        ],
    )


def _api_error_envelope(exc: ApiError) -> dict:
    code, message = _api_error_fields(exc.body)
    fix, next_actions = _api_error_guidance(status=exc.status, code=code, message=message)
    return err(code, message, fix, next_actions=next_actions)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    profile: str = typer.Option(  # noqa: B008
        None,
        "--profile",
        envvar="SUMMATION_PROFILE",
        help="Profile in ~/.summation/summation-config.",
    ),
    base_url: str = typer.Option(  # noqa: B008
        None,
        "--base-url",
        help="Override sum-api base URL for this invocation.",
    ),
    output: OutputChoice = typer.Option(  # noqa: B008
        None,
        "--output",
        # SUMCLI_OUTPUT is resolved leniently in resolve_output_mode (not via Click's
        # envvar), so a typo there still falls back to the TTY default instead of
        # erroring — only the explicit flag is strictly validated against the Choice.
        callback=_output_callback,
        is_eager=True,
        help="Output format: 'json' (agent-first) or 'human'. Place before the "
        "subcommand (e.g. `sumcli --output human projects list`), or set "
        "SUMCLI_OUTPUT. Defaults to human on a TTY, json when piped.",
    ),
    version: bool = typer.Option(  # noqa: B008
        False, "--version", callback=_version_callback, is_eager=True
    ),
    verbose: bool = typer.Option(  # noqa: B008
        False,
        "--verbose",
        "-v",
        envvar="SUMCLI_VERBOSE",
        is_flag=True,
        help="Log auth/HTTP debug details to stderr (no secrets).",
    ),
) -> None:
    debug_log.set_verbose(verbose)
    # `output` is resolved by its eager callback (_output_callback) before this body
    # runs, so the mode is already set here; nothing more to do with it.
    del output
    ctx.obj = CliContext(profile=profile, base_url=base_url, verbose=verbose)
    if ctx.invoked_subcommand is None:
        try:
            emit(build_command_tree_envelope())
        except OpenApiSpecError as exc:
            emit_error(
                err(
                    "OPENAPI_SPEC_MISSING",
                    str(exc),
                    "Reinstall summation-cli (pipx install --force …) or run from a source checkout.",
                    next_actions=[action("Show version", "sumcli --version")],
                )
            )
        raise typer.Exit()


def main() -> None:
    try:
        app()
    except typer.Exit:
        raise
    except AuthError as e:
        debug_log.log_auth_error(str(e))
        emit_error(
            err(
                "AUTH_ERROR",
                str(e),
                "Configure a profile, then run sumcli auth login. Use sumcli auth login --m2m for machine credentials.",
                next_actions=[
                    action("Show config", "sumcli config active"),
                    action(
                        "Set profile",
                        "sumcli config set-profile <name> --base-url <url> --client-id <id>",
                        params={
                            "name": param("Profile name"),
                            "url": param("sum-api base URL"),
                            "id": param("M2M client id"),
                        },
                    ),
                ],
            )
        )
    except ApiError as e:
        if e.method and e.url:
            debug_log.log_api_error(e.status, e.body, method=e.method, url=e.url)
        emit_error(_api_error_envelope(e))
    except Exception as e:
        emit_error(
            err(
                "INTERNAL_ERROR",
                str(e),
                "Retry the command. If it persists, report with sumcli --version output.",
                next_actions=[action("Show version", "sumcli --version")],
            )
        )


if __name__ == "__main__":
    main()
