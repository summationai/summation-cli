"""`sumcli chats ...`"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Annotated, Any

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
from sum_cli.output import action, emit, emit_error, err, ok, param, truncate_list
from sum_cli.stream_options import (
    FollowOption,
    WaitOption,
    post_with_wait_follow,
)
from sum_cli.streaming import QueueObservation, exit_if_stream_failed, stream_sse_response

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


# Mirrors ChatMessageRequest.on_busy in the sum-api OpenAPI snapshot. Only these
# two modes exist — steer and interrupt are deliberately not offered — so Typer's
# Click Choice rejects anything else at parse time instead of 422-ing upstream.
class OnBusy(str, Enum):
    queue = "queue"
    reject = "reject"


class FeedbackReason(str, Enum):
    incorrect_info = "incorrect_info"
    instructions_ignored = "instructions_ignored"
    unsafe_or_problematic = "unsafe_or_problematic"
    bad_response = "bad_response"
    dont_like_style = "dont_like_style"
    other = "other"


def _reply_result(payload: dict, text: str, queue_state: QueueObservation) -> dict:
    """Terminal result for a chat send, carrying the queue receipt when it waited."""
    result: dict[str, Any] = {"text": text, "payload": payload}
    if queue_state.waiting:
        result["queued_message"] = queue_state.as_result()
    return result


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
            require_terminal=True,
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
    on_busy: Annotated[
        OnBusy,
        typer.Option(
            "--on-busy",
            help=(
                "What to do when the chat is already working: 'queue' waits durably "
                "and runs next, 'reject' fails with conversation_busy."
            ),
        ),
    ] = OnBusy.queue,
    idempotency_key: Annotated[
        str | None,
        typer.Option(
            "--idempotency-key",
            help=(
                "Stable key for this logical send. Generated per invocation when "
                "omitted; pass the key from an interrupted run to resume that same "
                "message instead of sending a second one."
            ),
        ),
    ] = None,
    wait: WaitOption = True,
    follow: FollowOption = False,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    path = f"/v1/projects/{pid}/conversations/{chat_id}/messages"
    payload = _msg_body(message)
    payload["on_busy"] = on_busy.value
    # One key per invocation, generated before the request goes out, so a retry of
    # *this* run (or a re-run with the key echoed back) resumes the same message
    # rather than enqueueing a duplicate. An explicit key recovers a previous run.
    key = idempotency_key or uuid.uuid4().hex
    payload["idempotency_key"] = key
    # The observation carries the key so an interrupted wait can print it. A caller
    # that never saw the generated key cannot replay it, and re-sending without it
    # enqueues the same question twice — which is the whole point of the key.
    queue_state = QueueObservation(idempotency_key=key)
    with api_client(ctx, profile) as c:
        outcome = post_with_wait_follow(
            c,
            "POST",
            path,
            wait=wait,
            follow=follow,
            json=payload,
            # The receipt goes through the builder, not just the envelope below, so
            # it reaches the NDJSON terminal under --follow too — the docs point
            # agents at result.queued_message.idempotency_key in both modes.
            result_builder=lambda p, t: _reply_result(p, t, queue_state),
            queue_state=queue_state,
            require_terminal=True,
        )
        if outcome.streamed:
            return
        body = outcome.body
    result = unwrap_data(body or {}, "data") or body
    queued = result.pop("queued_message", None) if isinstance(result, dict) else None
    envelope: dict[str, Any] = {"message": result, "project_id": pid}
    next_actions: list = []
    if queued:
        # The send waited its turn. Report the receipt: it is the durable handle
        # for this message, and the only one if the stream is interrupted.
        envelope["queued_message"] = queued
        next_actions.append(
            action(
                "Show queued message",
                "sumcli chats queue-show --chat <chat-id> --queued-message <queued-message-id>",
                params={
                    "chat-id": param("Chat ID", value=chat_id),
                    "queued-message-id": param(
                        "Queued message ID", value=queue_state.queued_message_id
                    ),
                },
            )
        )
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
    emit(ok(envelope, next_actions=next_actions))


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
                resp,
                raw_sse=raw_sse,
                result_builder=lambda p, t: {"text": t},
                # --raw-sse dumps bytes without parsing, so it has no terminal to
                # require; the parsed path always ends in the route's done/error.
                require_terminal=not raw_sse,
            )
        exit_if_stream_failed(terminal)


# ---------------------------------------------------------------------------
# Durable turn queue.
#
# When a chat is already working, a queue-mode reply waits durably instead of
# being rejected. These commands inspect and manage that backlog. A queue receipt
# ("queued message") is addressable for the chat's lifetime, so a terminal one
# still answers `queue-show` with the assistant message it produced.
# ---------------------------------------------------------------------------

ChatOption = Annotated[str, typer.Option("--chat", "-c", help="Chat the queue belongs to.")]
QueuedMessageOption = Annotated[
    str, typer.Option("--queued-message", help="Queued message ID (qt_...).")
]

# Terminal states that are not an answer. `queue-show` exits non-zero on these so
# a polling caller can stop on failure the same way `tables import-status` does,
# instead of having to parse state out of a successful envelope.
_QUEUE_FAILURE_EXITS = {
    "failed": (
        "QUEUED_MESSAGE_FAILED",
        "The queued message failed before it ran.",
        "Read error.data.failureDetail, fix the cause, and send the message again.",
    ),
    "withdrawn": (
        "QUEUED_MESSAGE_WITHDRAWN",
        "The queued message was withdrawn before it ran.",
        "Send the message again if it is still needed.",
    ),
}


def _queue_data(body: object, *, endpoint: str) -> dict:
    """The ``data`` object from a queue response, refusing an unrecognized shape.

    Normalizing a missing wrapper to ``{}`` would report an empty, unheld queue for
    a response the CLI failed to read — the SUM-5882 failure mode ``extract_list``
    exists to prevent. For a queue that is exactly how a caller ends up re-sending a
    message that is already waiting, so this refuses instead.
    """
    data = unwrap_data(body or {}, "data")
    if not isinstance(data, dict):
        emit_error(
            err(
                "UNEXPECTED_SHAPE",
                f"{endpoint} returned no readable 'data' object.",
                "The API response shape changed. Upgrade sumcli, or report this with"
                " sumcli --version output and the command you ran.",
            )
        )
    return data


def _queue_items(data: dict, key: str) -> list:
    """Waiting messages from a queue payload.

    The key is optional in the contract (it defaults to an empty list), so an absent
    key on a payload whose sibling fields we did read is a genuine zero. Anything
    else still goes through ``extract_list``, which refuses unrecognized shapes.
    """
    if key not in data and "revision" in data:
        return []
    return extract_list(data, key)


def _resume_action(chat_id: str) -> dict:
    return action(
        "Resume the queue",
        "sumcli chats queue-resume --chat <chat-id>",
        params={"chat-id": param("Chat ID", value=chat_id)},
    )


@app.command("queue-list")
def list_queue(
    ctx: typer.Context,
    chat_id: ChatOption,
    project: Annotated[str | None, typer.Option("--project")] = None,
    count: Annotated[int | None, typer.Option("--count")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("GET", f"/v1/projects/{pid}/conversations/{chat_id}/queue")
    data = _queue_data(body, endpoint="queue-list")
    listed = truncate_list(_queue_items(data, "items"), count=count)
    held = bool(data.get("held"))
    next_actions = [
        action(
            "Show queued message",
            "sumcli chats queue-show --chat <chat-id> --queued-message <queued-message-id>",
            params={
                "chat-id": param("Chat ID", value=chat_id),
                "queued-message-id": param("Queued message ID"),
            },
        )
    ]
    if held:
        # A held queue does not drain on its own; without this the listing reports
        # waiting messages that will never start and offers no way out.
        next_actions.insert(0, _resume_action(chat_id))
    emit(
        ok(
            {
                "queued_messages": listed["items"],
                "held": held,
                "revision": data.get("revision"),
                "chat_id": chat_id,
                "project_id": pid,
                **{k: v for k, v in listed.items() if k != "items"},
            },
            next_actions=next_actions,
        )
    )


@app.command("queue-show")
def show_queued_message(
    ctx: typer.Context,
    chat_id: ChatOption,
    queued_message_id: QueuedMessageOption,
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request(
            "GET",
            f"/v1/projects/{pid}/conversations/{chat_id}/queue/{queued_message_id}",
        )
    item = _queue_data(body, endpoint="queue-show")
    state = str(item.get("state") or "")
    failure = _QUEUE_FAILURE_EXITS.get(state)
    if failure is not None:
        code, message, fix = failure
        emit_error(
            err(
                code,
                item.get("failureDetail") or message,
                fix,
                # camelCase, matching the keys sum-api puts in a problem body and the
                # ones the stream terminals use, so one generic reader works everywhere.
                data={
                    "queuedMessageId": item.get("id") or queued_message_id,
                    "state": state,
                    "failureCode": item.get("failureCode"),
                    "failureDetail": item.get("failureDetail"),
                },
            )
        )
    next_actions: list = []
    bound_message_id = item.get("messageId")
    if bound_message_id:
        # Dispatched (possibly already finished): the reply lives on the bound
        # assistant message, which is how a caller reads an answer it never saw.
        next_actions.append(
            action(
                "Stream events",
                "sumcli chats events --chat <chat-id> --message <message-id>",
                params={
                    "chat-id": param("Chat ID", value=chat_id),
                    "message-id": param("Message ID", value=bound_message_id),
                },
            )
        )
    elif state in ("dispatching", "dispatched"):
        # Claimed, but the binding is not in this projection yet (sum-api omits a
        # null message id, and guards for dispatched-without-id itself). Withdrawal
        # loses this race with a 409, so re-reading is the only useful next step.
        next_actions.append(
            action(
                "Re-read the queued message",
                "sumcli chats queue-show --chat <chat-id> --queued-message <queued-message-id>",
                params={
                    "chat-id": param("Chat ID", value=chat_id),
                    "queued-message-id": param("Queued message ID", value=queued_message_id),
                },
            )
        )
    else:
        next_actions.append(
            action(
                "Withdraw queued message",
                "sumcli chats queue-withdraw --chat <chat-id> "
                "--queued-message <queued-message-id> --confirm",
                params={
                    "chat-id": param("Chat ID", value=chat_id),
                    "queued-message-id": param("Queued message ID", value=queued_message_id),
                },
            )
        )
    emit(
        ok(
            {"queued_message": item, "chat_id": chat_id, "project_id": pid},
            next_actions=next_actions,
        )
    )


@app.command("queue-withdraw")
def withdraw_queued_message(
    ctx: typer.Context,
    chat_id: ChatOption,
    queued_message_id: QueuedMessageOption,
    project: Annotated[str | None, typer.Option("--project")] = None,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="chats queue-withdraw")
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request(
            "DELETE",
            f"/v1/projects/{pid}/conversations/{chat_id}/queue/{queued_message_id}",
            params=api_confirm_params(),
        )
    emit(
        ok(
            {
                "queued_message": _queue_data(body, endpoint="queue-withdraw"),
                "chat_id": chat_id,
                "project_id": pid,
            },
            next_actions=[
                action(
                    "List queued messages",
                    "sumcli chats queue-list --chat <chat-id>",
                    params={"chat-id": param("Chat ID", value=chat_id)},
                )
            ],
        )
    )


@app.command("queue-resume")
def resume_queue(
    ctx: typer.Context,
    chat_id: ChatOption,
    project: Annotated[str | None, typer.Option("--project")] = None,
    profile: ProfileOption = None,
) -> None:
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request("POST", f"/v1/projects/{pid}/conversations/{chat_id}/queue/resume")
    data = _queue_data(body, endpoint="queue-resume")
    emit(
        ok(
            {
                "held": bool(data.get("held")),
                "revision": data.get("revision"),
                "active_message_id": data.get("activeMessageId"),
                "queued_messages": _queue_items(data, "queuedTurns"),
                "chat_id": chat_id,
                "project_id": pid,
            },
            next_actions=[
                action(
                    "List queued messages",
                    "sumcli chats queue-list --chat <chat-id>",
                    params={"chat-id": param("Chat ID", value=chat_id)},
                )
            ],
        )
    )


@app.command("cancel")
def cancel_reply(
    ctx: typer.Context,
    chat_id: ChatOption,
    message_id: Annotated[
        str, typer.Option("--message", help="Assistant message whose reply to stop.")
    ],
    project: Annotated[str | None, typer.Option("--project")] = None,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    profile: ProfileOption = None,
) -> None:
    require_confirm(confirm, action_name="chats cancel")
    pid = require_project(ctx, project)
    with api_client(ctx, profile) as c:
        body = c.request(
            "POST",
            f"/v1/projects/{pid}/conversations/{chat_id}/messages/{message_id}/cancel",
            params=api_confirm_params(),
        )
    result = _queue_data(body, endpoint="chats cancel")
    if result.get("status") == "not_found":
        # Nothing was stopped. Reporting ok here lets an agent believe the turn is
        # over and send the next message straight into the turn it thought it
        # killed — the same reason queue-show exits non-zero on a non-answer.
        emit_error(
            err(
                "CANCEL_TARGET_NOT_FOUND",
                f"No in-progress reply for message {message_id} in this chat.",
                "Check the message id with `sumcli chats show --chat <chat-id>`; the reply may "
                "belong to a different chat, or may already be gone.",
                data={"messageId": message_id, "status": "not_found"},
            )
        )
    next_actions = [
        action(
            "Show chat",
            "sumcli chats show --chat <chat-id>",
            params={"chat-id": param("Chat ID", value=chat_id)},
        )
    ]
    # Stop holds the queue rather than draining it, so waiting messages survive the
    # cancel. Say so, and give the command that releases them.
    if result.get("queueHeld"):
        next_actions.insert(0, _resume_action(chat_id))
    emit(
        ok(
            {
                "cancel": result,
                "chat_id": chat_id,
                "message_id": message_id,
                "project_id": pid,
            },
            next_actions=next_actions,
        )
    )


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
