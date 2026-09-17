"""`sumcli` — the public Summation CLI."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import typer

from sum_cli import __version__, debug_log
from sum_cli.auth import AuthError
from sum_cli.client import ApiError
from sum_cli.config import Config, load
from sum_cli.intent import resolve_intent
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
    schedules,
    tables,
    tenant,
    verification_tests,
    views,
    workflows,
)
from sum_cli.update_check import run_upgrade, warn_if_outdated


@dataclass
class CliContext:
    profile: str | None
    base_url: str | None
    verbose: bool = False
    intent: str | None = None

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
app.add_typer(schedules.app, name="schedules")
app.add_typer(workflows.app, name="workflows")
app.add_typer(files.app, name="files")
app.add_typer(filesystem.app, name="filesystem")
app.add_typer(catalog.app, name="catalog")
app.add_typer(connections.app, name="connections")
app.add_typer(tables.app, name="tables")
app.add_typer(views.app, name="views")
app.add_typer(grid.app, name="grid")
app.add_typer(queries.app, name="queries")
app.add_typer(verification_tests.app, name="verification-tests")

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
        warn_if_outdated()
        emit(
            {
                "ok": True,
                "command": "sumcli --version",
                "result": {"version": __version__},
                "next_actions": [],
            }
        )
        raise typer.Exit()


# Structured members sum-api adds to a problem+json body so a caller can recover:
# the message that is still running, the queue receipt that already started, the
# cap that was hit. They are IDs and numbers, never prose — carry them through as
# `error.data` instead of letting the envelope reduce them to a sentence.
_RECOVERY_MEMBERS = (
    "activeMessageId",
    "queuedMessageId",
    "messageId",
    "userMessageId",
    "limit",
)

# Per-code recovery guidance for the durable conversation queue. Without these a
# queue conflict falls through to the generic auth/params advice, which sends the
# caller to `auth whoami` for a problem that has nothing to do with credentials.
_QUEUE_ERROR_FIXES: dict[str, str] = {
    "conversation_busy": (
        "The chat is still answering error.data.activeMessageId. Re-send with "
        "--on-busy queue to wait your turn, or stop it with `sumcli chats cancel "
        "--message <id> --confirm`."
    ),
    "conversation_queue_full": (
        "The chat already has the maximum number of waiting messages. Inspect them with "
        "`sumcli chats queue-list --chat <chat-id>` and withdraw one, or wait."
    ),
    "conversation_queue_held": (
        "The queue is paused by a Stop. Release it with `sumcli chats queue-resume "
        "--chat <chat-id>`, then re-send."
    ),
    "queued_message_already_dispatched": (
        "The queued message already started, so it cannot be withdrawn. Read its reply "
        "with `sumcli chats events --message <error.data.messageId>`."
    ),
    "queued_message_dispatching": (
        "The queued message is starting right now. Re-read it with `sumcli chats "
        "queue-show --chat <chat-id> --queued-message <id>` and retry."
    ),
}


def _api_error_data(body: object) -> dict | None:
    """Recovery IDs from a problem+json body, or None when it carries none."""
    if not isinstance(body, dict):
        return None
    data = {key: body[key] for key in _RECOVERY_MEMBERS if body.get(key) is not None}
    return data or None


def _api_error_fields(body: object) -> tuple[str, str]:
    message = str(body)
    code = "API_ERROR"
    if isinstance(body, dict):
        err_obj = body.get("error") or body
        if isinstance(err_obj, dict):
            message = err_obj.get("message") or err_obj.get("detail") or message
            code = err_obj.get("code") or code
    return code, message


def _api_error_guidance(*, status: int, code: str, message: str) -> tuple[str, list]:
    queue_fix = _QUEUE_ERROR_FIXES.get(code)
    if queue_fix is not None:
        return (
            queue_fix,
            [
                action(
                    "List queued messages",
                    "sumcli chats queue-list --chat <chat-id>",
                    params={"chat-id": param("Chat ID")},
                )
            ],
        )
    if "service principal not found" in message.casefold():
        return (
            "Token exchange worked, but sum-api cannot resolve this M2M client in Stytch "
            "(the service principal may have been deleted). Create new sandbox M2M credentials "
            "in Summation, then update your profile and run sumcli auth login --m2m.",
            [
                action("Show config", "sumcli config active"),
                action(
                    "Update credentials",
                    "sumcli config set-profile <name> --client-id <id> "
                    "--client-secret <secret> --login",
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
    return err(code, message, fix, next_actions=next_actions, data=_api_error_data(exc.body))


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
    intent: str = typer.Option(  # noqa: B008
        None,
        "--intent",
        envvar="SUMCLI_INTENT",
        help="The human's request, using their words when possible (not a "
        "command summary). Optional, but strongly recommended for agents: "
        "without it a run cannot be joined to a goal, and sumcli warns on "
        'stderr. Example: --intent "convert my weekly recap".',
    ),
) -> None:
    debug_log.set_verbose(verbose)
    # `output` is resolved by its eager callback (_output_callback) before this body
    # runs, so the mode is already set here; nothing more to do with it.
    del output
    # Normalize only. commands.checked_intent warns about a missing intent at the
    # point a command actually calls sum-api — this callback also runs for
    # discovery and --help, which must never be refused.
    ctx.obj = CliContext(
        profile=profile, base_url=base_url, verbose=verbose, intent=resolve_intent(intent)
    )
    # Verification bundle validation and mutation dry-runs are deliberately
    # network-free. Skip the opportunistic PyPI update check for this resource
    # so that promise applies to the complete invocation, not only its API call.
    if ctx.invoked_subcommand not in {"update", "verification-tests"}:
        warn_if_outdated()
    if ctx.invoked_subcommand is None:
        try:
            emit(build_command_tree_envelope())
        except OpenApiSpecError as exc:
            emit_error(
                err(
                    "OPENAPI_SPEC_MISSING",
                    str(exc),
                    "Reinstall summation-cli (pipx install --force …) or run from a "
                    "source checkout.",
                    next_actions=[action("Show version", "sumcli --version")],
                )
            )
        raise typer.Exit()


@app.command("update")
def update_cli() -> None:
    """Install the latest PyPI release of a uv-managed sumcli, including over a version pin."""
    run_upgrade()


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
                "Configure a profile, then run sumcli auth login. Use sumcli auth login "
                "--m2m for machine credentials.",
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
    except httpx.HTTPError as e:
        emit_error(
            err(
                "NETWORK_ERROR",
                str(e),
                "Check that you are online and that the profile base URL is reachable.",
                next_actions=[
                    action("Show config", "sumcli config active"),
                    action("Show version", "sumcli --version"),
                ],
            )
        )
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
