"""`sumcli chats ...`"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

import typer

from sum_cli.output import action, emit, emit_error, err, ok, param, truncate_list
from sum_cli.commands import (
    ProfileOption,
    api_client,
    extract_list,
    require_project,
    unwrap_data,
)
from sum_cli.stream_options import (
    FollowOption,
    WaitOption,
    post_with_wait_follow,
)
from sum_cli.streaming import exit_if_stream_failed, stream_sse_response

app = typer.Typer(no_args_is_help=True)

# ``details`` max length from ConversationFeedbackRequest in the sum-api OpenAPI
# snapshot; checked client-side so an overlong value fails before the request.
_DETAILS_MAX_LEN = 4000


# Mirrors ConversationFeedbackRequest in the sum-api OpenAPI snapshot
# (FeedbackRating / FeedbackReason). Typer maps a ``str, Enum`` option to a Click
# Choice, so an unsupported value is rejected at parse time instead of 422-ing.
class FeedbackRating(str, Enum):
    thumbs_up = "thumbs_up"
    thumbs_down = "thumbs_down"


class FeedbackReason(str, Enum):
    incorrect_info = "incorrect_info"
    instructions_ignored = "instructions_ignored"
    unsafe_or_problematic = "unsafe_or_problematic"
    bad_response = "bad_response"
    dont_like_style = "dont_like_style"
    other = "other"


def _msg_body(message: str, title: str | None = None) -> dict:
    body: dict = {"message": message}
    if title:
        body["title"] = title
    return body


@app.command("list")
def list_chats(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option("--project")] = None,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/conversations")
    data = unwrap_data(body or {}, "data")
    items = extract_list(data, "chats", "conversations")
    listed = truncate_list(items, count=count)
    emit(
        ok(
            {
                "chats": listed["items"],
                "project_id": pid,
                **{k: v for k, v in listed.items() if k != "items"},
            },
            next_actions=[
                action(
                    "Start chat",
                    "sumcli chats create --message <message>",
                    params={"message": param("Opening message")},
                )
            ],
        )
    )


@app.command("show")
def show_chat(
    ctx: typer.Context,
    chat_id: Annotated[str, typer.Option("--chat", "-c")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/conversations/{chat_id}")
    emit(ok({"chat": unwrap_data(body or {}, "data") or body, "project_id": pid}))


@app.command("create")
def create_chat(
    ctx: typer.Context,
    message: Annotated[str, typer.Option("--message", "-m")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    title: Annotated[str | None, typer.Option("--title")] = None,
    wait: WaitOption = True,
    follow: FollowOption = False,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    path = f"/v1/projects/{pid}/conversations"
    payload = _msg_body(message, title)
    with api_client(ctx, profile) as c:
        outcome = post_with_wait_follow(
            c,
            "POST",
            path,
            wait=wait,
            follow=follow,
            json=payload,
            result_builder=lambda p, t: {"text": t, "payload": p},
        )
        if outcome.streamed:
            return
        body = outcome.body
    result = unwrap_data(body or {}, "data") or body
    next_actions = [
        action(
            "Reply",
            "sumcli chats reply --chat <chat-id> --message <message>",
            params={
                "chat-id": param(
                    "Chat ID",
                    value=result.get("chat_id") if isinstance(result, dict) else None,
                ),
                "message": param("Message"),
            },
        )
    ]
    if not wait and isinstance(result, dict) and result.get("message_id"):
        next_actions.append(
            action(
                "Stream events",
                "sumcli chats events --chat <chat-id> --message <message-id>",
                params={
                    "chat-id": param("Chat ID", value=result.get("chat_id")),
                    "message-id": param("Message ID", value=result.get("message_id")),
                },
            )
        )
    emit(ok({"chat": result, "project_id": pid}, next_actions=next_actions))


@app.command("reply")
def reply_chat(
    ctx: typer.Context,
    chat_id: Annotated[str, typer.Option("--chat", "-c")],
    message: Annotated[str, typer.Option("--message", "-m")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    wait: WaitOption = True,
    follow: FollowOption = False,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    path = f"/v1/projects/{pid}/conversations/{chat_id}/messages"
    with api_client(ctx, profile) as c:
        outcome = post_with_wait_follow(
            c,
            "POST",
            path,
            wait=wait,
            follow=follow,
            json=_msg_body(message),
            result_builder=lambda p, t: {"text": t, "payload": p},
        )
        if outcome.streamed:
            return
        body = outcome.body
    result = unwrap_data(body or {}, "data") or body
    next_actions: list = []
    if not wait and isinstance(result, dict) and result.get("message_id"):
        next_actions.append(
            action(
                "Stream events",
                "sumcli chats events --chat <chat-id> --message <message-id>",
                params={
                    "chat-id": param("Chat ID", value=chat_id),
                    "message-id": param("Message ID", value=result.get("message_id")),
                },
            )
        )
    emit(ok({"message": result, "project_id": pid}, next_actions=next_actions))


@app.command("events")
def stream_events(
    ctx: typer.Context,
    chat_id: Annotated[str, typer.Option("--chat", "-c")],
    message_id: Annotated[str, typer.Option("--message")],
    project: Annotated[str | None, typer.Option("--project")] = None,
    raw_sse: Annotated[bool, typer.Option("--raw-sse")] = False,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    path = f"/v1/projects/{pid}/conversations/{chat_id}/messages/{message_id}/events"
    with api_client(ctx, profile) as c:
        with c.stream("GET", path) as resp:
            terminal = stream_sse_response(
                resp, raw_sse=raw_sse, result_builder=lambda p, t: {"text": t}
            )
        exit_if_stream_failed(terminal)


@app.command("feedback")
def submit_feedback(
    ctx: typer.Context,
    chat_id: Annotated[str, typer.Option("--chat", "-c", help="Chat the message belongs to.")],
    message_id: Annotated[
        str, typer.Option("--message", help="Assistant message the feedback is about.")
    ],
    rating: Annotated[
        FeedbackRating, typer.Option("--rating", help="Coarse rating for the assistant message.")
    ],
    reason: Annotated[
        FeedbackReason | None, typer.Option("--reason", help="Optional reason for the rating.")
    ] = None,
    details: Annotated[
        str | None,
        typer.Option("--details", help=f"Free-form details (max {_DETAILS_MAX_LEN} characters)."),
    ] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    """Send feedback on an assistant message back to Summation."""
    # Pure input validation first, so an overlong value does not need a resolved
    # project to report — matches files.py / tables.py flag-check ordering.
    if details is not None and len(details) > _DETAILS_MAX_LEN:
        emit_error(
            err(
                "DETAILS_TOO_LONG",
                f"--details is {len(details)} characters; the limit is {_DETAILS_MAX_LEN}.",
                f"Shorten --details to {_DETAILS_MAX_LEN} characters or fewer and re-run.",
            )
        )
    pid = require_project(ctx, project)
    payload: dict = {"rating": rating.value}
    if reason is not None:
        payload["reason"] = reason.value
    if details is not None:
        payload["details"] = details
    path = f"/v1/projects/{pid}/conversations/{chat_id}/messages/{message_id}/feedback"
    with api_client(ctx, profile) as c:
        body = c.request("POST", path, json=payload)
    emit(
        ok(
            {
                "feedback": unwrap_data(body or {}, "data") or body,
                "chat_id": chat_id,
                "message_id": message_id,
                "project_id": pid,
            },
            next_actions=[
                action(
                    "Show chat",
                    "sumcli chats show --chat <chat-id>",
                    params={"chat-id": param("Chat ID", value=chat_id)},
                )
            ],
        )
    )
