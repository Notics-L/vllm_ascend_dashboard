from pathlib import Path

import pytest
from scripts import check_github_audit_boundary as boundary
from scripts import github_actions_exceptions as report


@pytest.mark.parametrize(
    "source",
    [
        "token = settings.GITHUB_TOKEN",
        'TOKEN_VAR = "GH_TOKEN"\ntoken = os.environ[TOKEN_VAR]',
        'token = os.getenv("GITHUB_TOKEN")',
        'token = settings.get("github_token")',
        'token = config["token_encrypted"]',
        "token = credential._github_token",
        'credential = GitHubCredential("global", value)',
        "from infrastructure.core.github_audit import begin_github_operation",
        'client.get("https://api.github.com/user")',
        'subprocess.run(["gh", "api", "/user"])',
        'subprocess.run(["git", "push", "origin", "main"])',
        'subprocess.run("git push origin main", shell=True)',
        'GitHubAsyncClient(audit_source="example", credential=credential, verify=False)',
        'subprocess.run("git -c http.extraheader=AUTH fetch")',
        "from github import Github",
        "from daily_pipeline.failure_analysis import DailyFailureAgent",
        "from daily_pipeline import failure_analysis",
    ],
)
def test_new_backend_module_cannot_bypass_github_audit(source):
    assert boundary.check_python("backend/new_module.py", source)


def test_audited_clients_allow_multiple_credential_sources():
    source = '''
async def call_github(config, db):
    daily = daily_github_credential(config)
    actions = await get_github_credential(db)
    global_credential = global_github_credential()
    for credential in (daily, actions, global_credential):
        async with GitHubAsyncClient(audit_source="new_module", credential=credential) as client:
            await client.get("/user")
'''
    assert not boundary.check_python("backend/new_module.py", source)


def test_ci_command_returns_failure_and_location_on_new_bypass(
    tmp_path: Path, monkeypatch, capsys,
):
    path = tmp_path / "backend" / "new_module.py"
    path.parent.mkdir()
    path.write_text('token = os.getenv("GITHUB_TOKEN")', encoding="utf-8")
    monkeypatch.setattr(boundary, "ROOT", tmp_path)
    monkeypatch.setattr(boundary, "source_paths", lambda: [path.relative_to(tmp_path)])

    assert boundary.main() == 1
    assert "backend/new_module.py:1:" in capsys.readouterr().err


def test_config_secret_check_reports_location_without_echoing_value(tmp_path: Path, monkeypatch, capsys):
    path = tmp_path / ".env.example"
    secret = "ghp_" + "A" * 36
    path.write_text(f"GITHUB_TOKEN={secret}", encoding="utf-8")
    monkeypatch.setattr(boundary, "ROOT", tmp_path)
    monkeypatch.setattr(boundary, "source_paths", lambda: [path.relative_to(tmp_path)])

    assert boundary.main() == 1
    output = capsys.readouterr().err
    assert ".env.example:1: Possible hard-coded GitHub token" in output
    assert secret not in output


def test_ci_blocks_shell_from_reading_github_token(tmp_path: Path, monkeypatch, capsys):
    path = tmp_path / "operations" / "new_script.sh"
    path.parent.mkdir()
    path.write_text('curl -H "Authorization: Bearer $GITHUB_TOKEN" "$ENDPOINT"', encoding="utf-8")
    monkeypatch.setattr(boundary, "ROOT", tmp_path)
    monkeypatch.setattr(boundary, "source_paths", lambda: [path.relative_to(tmp_path)])

    assert boundary.main() == 1
    assert "operations/new_script.sh:1:" in capsys.readouterr().err


def test_standalone_report_uses_its_own_token_and_removes_it_on_external_redirect(monkeypatch):
    endpoint = "https://api.github.com/repos/example/project/actions/jobs/1/logs"
    monkeypatch.setenv("GITHUB_TOKEN", "dashboard-local-test-token")
    monkeypatch.delenv("GITHUB_ACTIONS_REPORT_TOKEN", raising=False)
    assert not report._github_request(endpoint).has_header("Authorization")

    monkeypatch.setenv("GITHUB_ACTIONS_REPORT_TOKEN", "standalone-local-test-token")
    request = report._github_request(endpoint)
    assert request.get_header("Authorization") == "Bearer standalone-local-test-token"
    redirect = report._GitHubReportRedirect().redirect_request(
        request, None, 302, "Found", {}, "https://artifacts.example/logs",
    )
    assert not redirect.has_header("Authorization")
