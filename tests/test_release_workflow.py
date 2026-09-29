import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
README = REPO_ROOT / "README.md"


def _release_workflow() -> str:
    return RELEASE_WORKFLOW.read_text()


def _readme() -> str:
    return README.read_text()


def _dispatch_job(workflow: str) -> str:
    match = re.search(r"(?ms)^  dispatch-sumcli-pin-bump:\n(?P<body>.*)\Z", workflow)
    assert match, "release workflow must end with a dispatch-sumcli-pin-bump job"
    return match.group("body")


def _dispatch_script() -> str:
    dispatch_job = _dispatch_job(_release_workflow())
    match = re.search(r"(?ms)^        run: \|\n(?P<script>(?:          .*\n?)*)\Z", dispatch_job)
    assert match, "expected final dispatch step run script"
    lines = match.group("script").splitlines()
    return "\n".join(line[10:] if line.startswith("          ") else line for line in lines)


def _run_dispatch_script(tmp_path: Path, version: str) -> subprocess.CompletedProcess[str]:
    gh = tmp_path / "gh"
    gh.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > gh-args.txt\n")
    gh.chmod(0o755)
    env = {
        **os.environ,
        "GH_TOKEN": "fake-token",
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "TAG": f"v{version}",
        "VERSION": version,
    }
    return subprocess.run(
        ["bash", "-c", _dispatch_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_has_final_dispatch_job_after_publish() -> None:
    workflow = _release_workflow()
    dispatch_job = _dispatch_job(workflow)

    assert "needs: [test-and-build, publish]" in dispatch_job
    assert re.search(r"(?m)^    runs-on: ubuntu-latest$", dispatch_job)
    assert workflow.index("          --check-url https://pypi.org/simple/") < workflow.index(
        "  dispatch-sumcli-pin-bump:"
    )


def test_dispatch_job_uses_cross_repo_app_token_for_code_actions_write() -> None:
    dispatch_job = _dispatch_job(_release_workflow())

    pinned_app_token_action = (
        "uses: actions/create-github-app-token@d72941d797fd3113feb6b93fd0dec494b13a2547"
    )
    assert pinned_app_token_action in dispatch_job
    assert "app-id: ${{ vars.TS_PROTO_GEN_APP_ID }}" in dispatch_job
    assert "private-key: ${{ secrets.TS_PROTO_GEN_APP_KEY }}" in dispatch_job
    assert re.search(r"(?m)^          owner: summationai$", dispatch_job)
    assert re.search(r"(?m)^          repositories: code$", dispatch_job)
    assert re.search(r"(?m)^          permission-actions: write$", dispatch_job)
    assert "GH_TOKEN: ${{ steps.app-token.outputs.token }}" in dispatch_job


def test_dispatch_job_uses_exact_validated_release_version() -> None:
    dispatch_job = _dispatch_job(_release_workflow())

    assert re.search(r"(?m)^          TAG: \$\{\{ github\.ref_name \}\}$", dispatch_job)
    assert re.search(
        r"(?m)^          VERSION: \$\{\{ needs\.test-and-build\.outputs\.version \}\}$",
        dispatch_job,
    )
    assert re.search(r"(?m)^          version=\"\$VERSION\"$", dispatch_job)
    version_guard = 'if [[ ! "$version" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+$ ]]; then'
    assert version_guard in dispatch_job
    assert "[0-9]*.[0-9]*.[0-9]*" not in dispatch_job
    assert re.search(
        r"(?m)^          if \[ \"\$TAG\" != \"v\$\{version\}\" \]; then$",
        dispatch_job,
    )
    assert re.search(
        r"(?m)^          gh workflow run sumcli-pin-bump\.yaml --repo summationai/code "
        r"--ref main --field \"version=\$version\"$",
        dispatch_job,
    )


def test_dispatch_script_rejects_trailing_garbage_and_newline_versions(tmp_path: Path) -> None:
    for bad_version in ("0.1.6garbage", "0.1.6\n0.1.7"):
        case_dir = tmp_path / re.sub(r"\W+", "_", bad_version)
        case_dir.mkdir()
        result = _run_dispatch_script(case_dir, bad_version)

        assert result.returncode != 0, result.stdout + result.stderr
        assert "Refusing to dispatch invalid summation-cli version" in result.stderr
        assert not (case_dir / "gh-args.txt").exists()


def test_dispatch_script_runs_manual_bump_for_valid_version(tmp_path: Path) -> None:
    result = _run_dispatch_script(tmp_path, "0.1.6")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "gh-args.txt").read_text().strip() == (
        "workflow run sumcli-pin-bump.yaml --repo summationai/code "
        "--ref main --field version=0.1.6"
    )


def test_release_docs_document_admin_prereq_and_manual_recovery() -> None:
    workflow = _release_workflow()
    readme = _readme()

    for text in (workflow, readme):
        assert "TS_PROTO_GEN_APP_ID" in text
        assert "TS_PROTO_GEN_APP_KEY" in text
        assert "summationai/code" in text
        assert "Actions: write" in text
        assert "gh workflow run sumcli-pin-bump.yaml -R summationai/code -f version=X.Y.Z" in text
        assert "fail rather than silently skip" in text
        assert "sumcli-pin-bump.yaml" in text
        normalized = " ".join(text.replace("`", "").split())
        assert "main before this CLI hook is merged" in normalized
