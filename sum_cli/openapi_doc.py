"""OpenAPI contract helpers for sumcli.

The public sum-api OpenAPI document is the source of truth for the CLI's API
surface. A snapshot is bundled at ``sum_cli/data/openapi_snapshot.json`` (offline,
deterministic); refresh with ``scripts/refresh_openapi.py``.

This module provides:

* drift guard (``tests/test_openapi_contract.py``) — CLI call sites must exist
  in the spec; uncovered spec operations must be allow-listed; destructive
  DELETE call sites must send ``confirm=true`` when the spec documents it.
  The guard keys on ``(method, path)`` existence — other breaking param or
  body changes require a snapshot refresh or explicit test coverage.
* command-tree discovery — bare ``sumcli`` JSON with spec-backed action blurbs.
* Typer ``--help`` — ``apply_openapi_help(app)`` patches group and subcommand help
  from the same blurbs (OpenAPI summaries + local overrides).
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
SNAPSHOT_PATH = _PACKAGE_ROOT / "data" / "openapi_snapshot.json"
RESOURCES_DIR = _PACKAGE_ROOT / "resources"
_BUNDLED_SNAPSHOT_NAME = "openapi_snapshot.json"

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_TEMPLATE_SEGMENT = re.compile(r"\{[^}]+\}")
_V1_PREFIX = "/v1/"


def normalize_path(path: str) -> str:
    """Collapse every templated segment to ``*`` so CLI f-strings and spec
    named templates compare equal: ``/v1/projects/{pid}`` == ``/v1/projects/{project_id}``."""
    normalized = _TEMPLATE_SEGMENT.sub("*", path)
    return normalized.rstrip("/") or normalized


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    operation_id: str | None
    summary: str | None
    tags: tuple[str, ...]

    @property
    def normalized_path(self) -> str:
        return normalize_path(self.path)

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.normalized_path)


@dataclass(frozen=True)
class CallSite:
    method: str
    path: str
    source: str
    # Query-param names the call site statically sends. ``None`` means the
    # ``params=`` argument was non-literal (a variable or helper call) and we
    # cannot prove which keys are sent — required-param checks skip those.
    query_params: frozenset[str] | None = None

    @property
    def normalized_path(self) -> str:
        return normalize_path(self.path)

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.normalized_path)


# Spec operations the CLI intentionally does not expose yet. Each entry must
# include a short reason so adding a route without CLI coverage is a conscious
# choice, not silent drift.
UNCOVERED_OPERATIONS_ALLOWLIST: dict[tuple[str, str], str] = {
    ("POST", "/v1/auth/logout"): "No sumcli logout; sessions are profile-scoped.",
    ("GET", "/v1/chat-models"): "Chat model listing not exposed in sumcli.",
    ("GET", "/v1/connections/app"): "App connectors not exposed in sumcli.",
    ("GET", "/v1/connections/app/catalog"): "App connector catalog not exposed in sumcli.",
    ("GET", "/v1/connections/app/catalog/*/tools"): "App connector tools not exposed in sumcli.",
    ("GET", "/v1/connections/app/*"): "App connectors not exposed in sumcli.",
    ("PATCH", "/v1/connections/app/*"): "App connectors not exposed in sumcli.",
    ("DELETE", "/v1/connections/app/*"): "App connectors not exposed in sumcli.",
    ("POST", "/v1/connections/app/*/disconnect"): "App connector disconnect not exposed in sumcli.",
    ("POST", "/v1/grid/tables"): "Grid calculation-table creation not exposed in sumcli.",
    ("POST", "/v1/grid/tables/*/materialize"): "Grid materialize not exposed in sumcli.",
    ("POST", "/v1/projects/*/files/uploads"): "Project file uploads not exposed in sumcli.",
    (
        "POST",
        "/v1/projects/*/files/uploads/*/finalize",
    ): "Project file uploads not exposed in sumcli.",
    ("GET", "/v1/projects/*/reports"): "Report listing is via files, not a dedicated command.",
    ("DELETE", "/v1/projects/*/reports/*"): "Report delete is via files delete.",
    ("GET", "/v1/projects/*/reports/*/content"): "Report content export not exposed in sumcli.",
    ("GET", "/v1/sum-apps"): "SumApp management not exposed in sumcli.",
    ("POST", "/v1/sum-apps"): "SumApp management not exposed in sumcli.",
    ("DELETE", "/v1/sum-apps/*"): "SumApp management not exposed in sumcli.",
    ("POST", "/v1/tables/*/rows"): "Row append not exposed in sumcli.",
    ("PUT", "/v1/tables/*/rows"): "Row replace not exposed in sumcli.",
    ("GET", "/v1/tables/catalog"): "Tenant-wide table catalog list not exposed in sumcli.",
    ("GET", "/v1/views/catalog"): "Tenant-wide view catalog list not exposed in sumcli.",
}


class OpenApiSpecError(RuntimeError):
    """Bundled OpenAPI snapshot missing or unreadable."""


def _read_bundled_spec_text() -> str:
    try:
        return (
            resources.files("sum_cli.data")
            .joinpath(_BUNDLED_SNAPSHOT_NAME)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, TypeError, OSError):
        if SNAPSHOT_PATH.is_file():
            return SNAPSHOT_PATH.read_text(encoding="utf-8")
        raise OpenApiSpecError(
            "OpenAPI snapshot is missing from the installed summation-cli package. "
            "Reinstall with: pipx install --force summation-cli"
        ) from None


@lru_cache(maxsize=None)
def load_spec(path: str | None = None) -> dict:
    if path is not None:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise OpenApiSpecError(f"Cannot read OpenAPI snapshot at {path}") from exc
    else:
        raw = _read_bundled_spec_text()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenApiSpecError("Bundled OpenAPI snapshot is not valid JSON.") from exc
    if not isinstance(spec, dict):
        raise OpenApiSpecError("Bundled OpenAPI snapshot must be a JSON object.")
    return spec


def iter_operations(spec: dict) -> list[Operation]:
    ops: list[Operation] = []
    for path, item in spec.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            ops.append(
                Operation(
                    method=method.upper(),
                    path=path,
                    operation_id=op.get("operationId"),
                    summary=op.get("summary"),
                    tags=tuple(op.get("tags", [])),
                )
            )
    return ops


def spec_operation_keys(spec: dict) -> set[tuple[str, str]]:
    return {op.key for op in iter_operations(spec)}


def _path_template_from_node(node: ast.AST, env: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _extract_v1_suffix(node.value)
    if isinstance(node, ast.JoinedStr):
        return _joined_str_template(node)
    if isinstance(node, ast.Name):
        return env.get(node.id)
    return None


def _extract_v1_suffix(value: str) -> str | None:
    if value.startswith(_V1_PREFIX):
        return value
    idx = value.find(_V1_PREFIX)
    if idx >= 0:
        return value[idx:]
    return None


def _joined_str_template(node: ast.JoinedStr) -> str | None:
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            parts.append("{_}")
        else:
            return None
    candidate = "".join(parts)
    return _extract_v1_suffix(candidate)


def _method_from_node(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.upper()
    return None


# Static query-param sets for helper calls the collector recognizes.
_KNOWN_PARAMS_HELPERS: dict[str, frozenset[str]] = {
    "api_confirm_params": frozenset({"confirm"}),
    "api_fs_delete_params": frozenset({"recursive", "confirm"}),
}


def _query_params_from_node(node: ast.AST | None) -> frozenset[str] | None:
    """Extract query-param keys from a literal ``params=`` value or known helper."""
    if node is None:
        return None
    if isinstance(node, ast.Dict):
        keys: set[str] = set()
        for key_node in node.keys:
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                keys.add(key_node.value)
        return frozenset(keys)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return _KNOWN_PARAMS_HELPERS.get(node.func.id)
    return None


class _CallSiteCollector(ast.NodeVisitor):
    """Collect sum-api call sites from a function body.

    Recognizes ``c.request(...)``, ``c.request_bytes(...)``, ``c.stream(...)``,
    ``post_with_wait_follow(...)``, and bare ``.post(...)``. Unrecognized client
    call patterns are silently omitted — extend this visitor when adding new
    HTTP helpers.
    """

    _CLIENT_METHODS = frozenset({"request", "request_bytes", "stream"})

    def __init__(self, source: str) -> None:
        self.source = source
        self.sites: list[CallSite] = []
        self._env: dict[str, str] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            template = _path_template_from_node(node.value, self._env)
            if template is not None:
                self._env[node.targets[0].id] = template
        self.generic_visit(node)

    def _append_site(
        self,
        *,
        method: str,
        path: str,
        query_params: frozenset[str] | None = None,
        params_node: ast.AST | None = None,
    ) -> None:
        resolved_params = query_params
        if params_node is not None:
            resolved_params = _query_params_from_node(params_node)
        self.sites.append(
            CallSite(
                method=method,
                path=path,
                source=self.source,
                query_params=resolved_params,
            )
        )

    def _params_kwarg(self, node: ast.Call) -> ast.AST | None:
        for kw in node.keywords:
            if kw.arg == "params":
                return kw.value
        return None

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in self._CLIENT_METHODS:
            if len(node.args) >= 2:
                method = _method_from_node(node.args[0])
                path = _path_template_from_node(node.args[1], self._env)
                if method and path:
                    self._append_site(
                        method=method,
                        path=path,
                        params_node=self._params_kwarg(node),
                    )
        elif isinstance(node.func, ast.Name) and node.func.id == "post_with_wait_follow":
            if len(node.args) >= 3:
                method = _method_from_node(node.args[1])
                path = _path_template_from_node(node.args[2], self._env)
                if method and path:
                    self._append_site(method=method, path=path)
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "post":
            if len(node.args) >= 1:
                path = _path_template_from_node(node.args[0], self._env)
                if path:
                    self._append_site(method="POST", path=path)
        self.generic_visit(node)


class _ResourceModuleVisitor(ast.NodeVisitor):
    """Map Typer command functions to their API call sites.

    Call sites may live in same-module helpers (e.g. ``_execute_query``). Commands
    inherit sites from the transitive closure of Name-callable helpers they invoke.
    """

    def __init__(self, module_path: Path) -> None:
        self.module_path = module_path
        self.resource = module_path.stem
        self.actions: dict[str, list[CallSite]] = {}
        self._func_nodes: dict[str, ast.FunctionDef] = {}
        self._func_sites: dict[str, list[CallSite]] = {}

    def visit_Module(self, node: ast.Module) -> None:
        for child in node.body:
            if isinstance(child, ast.FunctionDef):
                collector = _CallSiteCollector(f"{self.module_path}:{child.lineno}")
                for item in ast.iter_child_nodes(child):
                    collector.visit(item)
                self._func_nodes[child.name] = child
                self._func_sites[child.name] = collector.sites
        for name, func in self._func_nodes.items():
            action = _typer_command_name(func)
            if action is None:
                continue
            sites = self._reachable_sites(name)
            if sites:
                self.actions[action] = sites

    def _reachable_sites(self, func_name: str, seen: set[str] | None = None) -> list[CallSite]:
        if seen is None:
            seen = set()
        if func_name in seen or func_name not in self._func_nodes:
            return []
        seen.add(func_name)
        sites = list(self._func_sites.get(func_name, []))
        for callee in _name_callees(self._func_nodes[func_name]):
            sites.extend(self._reachable_sites(callee, seen))
        return sites


def _name_callees(node: ast.AST) -> set[str]:
    """Same-module helper names invoked as bare calls (``helper(...)``)."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            names.add(child.func.id)
    return names


def _typer_command_name(node: ast.FunctionDef) -> str | None:
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "app"
            and func.attr == "command"
        ):
            continue
        if (
            dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            return dec.args[0].value
        return node.name.replace("_", "-")
    return None


def _scan_module(module_path: Path) -> dict[str, list[CallSite]]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    visitor = _ResourceModuleVisitor(module_path)
    visitor.visit(tree)
    return visitor.actions


def cli_call_sites(
    resources_dir: Path | None = None,
    *,
    include_auth: bool = True,
) -> list[CallSite]:
    """Every (method, normalized path) the CLI references in resource modules."""
    target = resources_dir or RESOURCES_DIR
    sites: list[CallSite] = []
    for module in sorted(target.glob("*.py")):
        if module.name == "__init__.py":
            continue
        for action_sites in _scan_module(module).values():
            sites.extend(action_sites)
    if include_auth:
        sites.extend(_auth_m2m_call_sites())
    return sites


def _auth_m2m_call_sites() -> list[CallSite]:
    auth_module = _PACKAGE_ROOT / "auth.py"
    tree = ast.parse(auth_module.read_text(encoding="utf-8"), filename=str(auth_module))
    collector = _CallSiteCollector(f"{auth_module}:m2m")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "exchange_m2m_token":
            for child in ast.iter_child_nodes(node):
                collector.visit(child)
    return collector.sites


def cli_operation_keys(
    resources_dir: Path | None = None,
    *,
    include_auth: bool = True,
) -> set[tuple[str, str]]:
    return {site.key for site in cli_call_sites(resources_dir, include_auth=include_auth)}


def command_action_call_sites(
    resources_dir: Path | None = None,
) -> dict[tuple[str, str], list[CallSite]]:
    """Resource/action → call sites discovered from Typer command functions."""
    target = resources_dir or RESOURCES_DIR
    out: dict[tuple[str, str], list[CallSite]] = {}
    for module in sorted(target.glob("*.py")):
        if module.name == "__init__.py":
            continue
        resource = module.stem
        for action, sites in _scan_module(module).items():
            out[(resource, action)] = sites
    return out


def primary_call_site(sites: list[CallSite]) -> CallSite | None:
    if not sites:
        return None
    for site in sites:
        if site.method in _MUTATING_METHODS:
            return site
    return sites[0]


def summaries_by_operation_key(spec: dict) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for op in iter_operations(spec):
        text = op.summary or _humanize_operation_id(op.operation_id)
        if text:
            out.setdefault(op.key, text)
    return out


def summaries_by_normalized_path(spec: dict) -> dict[str, str]:
    """Best-effort path → human summary (method-agnostic, first wins)."""
    out: dict[str, str] = {}
    for op in iter_operations(spec):
        text = op.summary or _humanize_operation_id(op.operation_id)
        if text and op.normalized_path not in out:
            out[op.normalized_path] = text
    return out


def blurb_for_call_site(site: CallSite, summaries: dict[tuple[str, str], str]) -> str:
    return summaries.get(site.key) or summaries.get(
        ("GET", site.normalized_path),
        site.normalized_path,
    )


def uncovered_spec_operations(spec: dict) -> list[Operation]:
    covered = cli_operation_keys()
    allowlisted = set(UNCOVERED_OPERATIONS_ALLOWLIST)
    missing: list[Operation] = []
    for op in iter_operations(spec):
        if op.key in covered or op.key in allowlisted:
            continue
        missing.append(op)
    return sorted(missing, key=lambda o: (o.normalized_path, o.method))


def cli_paths_missing_from_spec(spec: dict) -> list[CallSite]:
    spec_keys = spec_operation_keys(spec)
    return sorted(
        {site for site in cli_call_sites() if site.key not in spec_keys},
        key=lambda s: (s.normalized_path, s.method),
    )


def _operation_query_param_names(spec: dict, method: str, path: str) -> set[str]:
    path_item = spec.get("paths", {}).get(path, {})
    op = path_item.get(method.lower(), {})
    if not isinstance(op, dict):
        return set()
    return {
        p["name"]
        for p in op.get("parameters", [])
        if isinstance(p, dict) and p.get("in") == "query" and isinstance(p.get("name"), str)
    }


def _confirm_query_operations(spec: dict) -> set[tuple[str, str]]:
    """DELETE operations whose contract documents a ``confirm`` query param."""
    out: set[tuple[str, str]] = set()
    for op in iter_operations(spec):
        if op.method != "DELETE":
            continue
        params = _operation_query_param_names(spec, op.method, op.path)
        if "confirm" in params:
            out.add(op.key)
    return out


def cli_call_sites_missing_confirm(spec: dict) -> list[CallSite]:
    """Call sites that hit a confirm-gated DELETE without sending ``confirm=true``.

    OpenAPI marks ``confirm`` as optional (default false) even though the API
    rejects destructive calls without it, so existence checks alone miss this.
    """
    confirm_ops = _confirm_query_operations(spec)
    missing: list[CallSite] = []
    for site in cli_call_sites():
        if site.method != "DELETE" or site.key not in confirm_ops:
            continue
        if site.query_params is None or "confirm" not in site.query_params:
            missing.append(site)
    return sorted(missing, key=lambda s: (s.normalized_path, s.source))


def _humanize_operation_id(operation_id: str | None) -> str | None:
    if not operation_id:
        return None
    base = re.split(r"_v1_", operation_id)[0]
    return base.replace("_", " ").strip() or None


# ---------------------------------------------------------------------------
# Command tree (bare ``sumcli`` JSON discovery)
# ---------------------------------------------------------------------------

# Resource groupings stay curated in this module (see also apply_openapi_help).
_RESOURCE_DESCRIPTIONS: dict[str, str] = {
    "auth": "Inspect authentication state.",
    "config": "Manage config profiles and the active working session (default ~/.summation/summation-config; override with SUMMATION_CONFIG_FILE).",
    "tenant": "Organization and tenant metadata.",
    "projects": "Manage projects.",
    "chats": "Chats (Addison conversations).",
    "reports": "Generate and verify reports (.sdoc). List/download/delete via files.",
    "playbooks": "Playbook discovery.",
    "schedules": "Recurring playbook schedules and their runs.",
    "files": "Project-scoped files.",
    "filesystem": "External storage providers (SharePoint).",
    "catalog": "Project catalog entries (tables/views attached to project).",
    "connections": "External data source connections.",
    "tables": "Canonical tables and imports.",
    "views": "Summation views.",
    "grid": "Grid status, sync, and lineage.",
    "queries": "Read-only SQL execution.",
}

# Hand-written action blurbs (see sum_cli/resources/__init__.py — do not duplicate in handlers).
#
# Known composite doc misalignments (intentionally deferred):
# * ``tables import`` — workflow over upload-url → preview → POST /v1/table-imports
#   (create_table_import) → poll GET /v1/table-imports/{id}. ``tables import-status`` maps
#   1:1 to show_table_import. ``primary_call_site`` would pick POST /v1/assets/upload-urls,
#   not create_table_import, so we keep a hand blurb until _COMPOSITE_PRIMARY_OPS (or a thin
#   ``tables create-import``) lands.
# * ``reports verify`` — POST …/verifications exists in spec; hand blurb adds --wait/--follow
#   (same pattern as _STREAMING_ACTION_SUFFIXES on reports.generate).
_LOCAL_ACTION_BLURBS: dict[str, dict[str, str]] = {
    "auth": {
        "login": "Start interactive device login by default; use --m2m to persist a machine session.",
        "logout": "Revoke the stored device-login credential for the profile.",
        "token": "Show redacted bearer token for the active session.",
        # ``whoami`` (GET /v1/me) and ``status`` (GET /v1/auth/status) both carry the
        # spec summary "Show current identity", so distinguish them by hand.
        "whoami": "Show the resolved identity, profile, base URL, and auth mode.",
        "status": "Report whether the profile's credentials currently authenticate.",
    },
    "config": {
        "path": "Print config file path.",
        "list": "List profiles with base_url, tenant, env, and default_project.",
        "show": "Show a profile (secrets redacted).",
        "active": "Show the active profile: account, default project, and resolved credentials.",
        "use": "Switch active profile; optional --project sets default_project.",
        "set-project": "Set default_project for the active (or --profile) profile.",
        "clear-project": "Clear default_project for the active (or --profile) profile.",
        "import-env": "Import SUM_API_* from a skill-style env file into a profile.",
        "set-profile": "Create or replace a profile.",
        "copy-profile": "Clone a profile under a new name.",
        "delete-profile": "Remove a profile (--confirm).",
    },
    "reports": {
        "verify": "Verify a report or document by file id (--wait/--no-wait, --follow).",
    },
    "schedules": {
        # PUT replaces the schedule and the target cannot change, so the bare spec
        # summary ("Update schedule") reads as a partial update. The CLI merges
        # existing config so an omitted --email does not drop the recipient list.
        "update": (
            "Replace a schedule's cadence, keeping unspecified config "
            "and the same --playbook target."
        ),
        "delete": "Delete a schedule (--confirm).",
    },
    "tables": {
        "import": "Import from local file (multi-step; --wait/--no-wait).",
    },
    "filesystem": {
        "roots": "List drives/roots for the configured site (--provider sharepoint).",
        "list": "List folder entries by root and optional parent folder id (--path).",
        "download": "Download a file by item id to --output or a temp path.",
        "upload": "Upload a local file (--file) into a root/folder.",
        "mkdir": "Create a folder under a root/folder.",
        "delete": "Delete a file or folder by item id (--confirm).",
        "set-defaults": "Persist --root/--path defaults in config (--provider required).",
        "import-env": (
            "Import SHAREPOINT_* from a skill-style env file into ~/.summation/summation-config."
        ),
    },
}

# CLI flags the OpenAPI summary does not mention.
_STREAMING_ACTION_SUFFIXES: dict[tuple[str, str], str] = {
    ("chats", "create"): " (--wait/--no-wait, --follow streams NDJSON).",
    ("chats", "reply"): " (--wait/--no-wait, --follow).",
    ("chats", "events"): " (always streams NDJSON; --raw-sse optional).",
    ("reports", "generate"): " (--wait/--no-wait, --follow).",
    ("grid", "push"): " (--wait/--no-wait, --follow).",
}


def registered_typer_actions() -> dict[str, set[str]]:
    """Return resource → action names from the live Typer app (source of truth)."""
    from typer.main import get_command

    from sum_cli.cli.main import (
        app,
    )  # gazelle:ignore sum_cli.cli.main

    root = get_command(app)
    out: dict[str, set[str]] = {}
    for resource, group in root.commands.items():
        if hasattr(group, "commands"):
            out[str(resource)] = set(group.commands.keys())
    return out


def _action_blurb(
    resource: str,
    action: str,
    *,
    summaries: dict[tuple[str, str], str],
    action_sites: dict[tuple[str, str], list[CallSite]],
) -> str:
    local = _LOCAL_ACTION_BLURBS.get(resource, {}).get(action)
    if local is not None:
        return local
    site = primary_call_site(action_sites.get((resource, action), []))
    if site is None:
        return action.replace("-", " ")
    blurb = blurb_for_call_site(site, summaries)
    suffix = _STREAMING_ACTION_SUFFIXES.get((resource, action), "")
    return f"{blurb}{suffix}"


def build_resources() -> dict[str, dict]:
    spec = load_spec()
    summaries = summaries_by_operation_key(spec)
    action_sites = command_action_call_sites()
    typer_actions = registered_typer_actions()

    resources: dict[str, dict] = {}
    for resource, description in _RESOURCE_DESCRIPTIONS.items():
        actions = {
            action: _action_blurb(
                resource,
                action,
                summaries=summaries,
                action_sites=action_sites,
            )
            for action in sorted(typer_actions[resource])
        }
        resources[resource] = {"description": description, "actions": actions}
    return resources


def apply_openapi_help(app: object) -> None:
    """Patch Typer group and subcommand help from ``build_resources()``.

    Call once from ``cli/main.py`` after every ``add_typer(...)``. Typer rebuilds
    Click commands on each invocation, so we set ``CommandInfo.help`` on the Typer
    registration metadata (not ``click.Command.help``). Handlers and CLI-only option
    help stay in resource modules; one-line summaries come from OpenAPI + local overrides.
    """
    import typer

    resources = build_resources()
    if not isinstance(app, typer.Typer):
        return
    for group_info in app.registered_groups:
        resource_name = group_info.name
        if not resource_name:
            continue
        meta = resources.get(resource_name)
        if meta is None:
            continue
        description = meta.get("description")
        if description and group_info.typer_instance is not None:
            group_info.typer_instance.info.help = description
        typer_group = group_info.typer_instance
        if typer_group is None:
            continue
        for command_info in typer_group.registered_commands:
            action_name = command_info.name
            if not action_name and command_info.callback is not None:
                from typer.main import get_command_name

                action_name = get_command_name(command_info.callback.__name__)
            blurb = meta.get("actions", {}).get(action_name or "")
            if blurb:
                command_info.help = blurb
                command_info.short_help = None


def build_command_tree_envelope() -> dict:
    from sum_cli import __version__
    from sum_cli.output import action, ok, param

    return ok(
        {
            "name": "sumcli",
            "version": __version__,
            "description": "sumcli — public Summation CLI (sum-api /v1). Pattern: sumcli <resource> <action> [--flags]",
            "resources": build_resources(),
        },
        next_actions=[
            action("Show active identity", "sumcli auth whoami"),
            action("List profiles", "sumcli config list"),
            action(
                "List projects",
                "sumcli projects list [--count <count>]",
                params={"count": param("Max projects to return", default=50)},
            ),
            action(
                "Switch profile",
                "sumcli config use <profile>",
                params={"profile": param("Profile name")},
            ),
            action(
                "Set default project",
                "sumcli config set-project --project <project-id>",
                params={"project-id": param("Project ID (proj_...)")},
            ),
        ],
    )
