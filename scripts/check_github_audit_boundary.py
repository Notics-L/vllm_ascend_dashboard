"""Keep GitHub credentials and network calls inside the audited clients."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = "backend/infrastructure/clients/github_http.py"
CREDENTIALS = "backend/infrastructure/core/github_credentials.py"
CREDENTIAL_LOADERS = {CREDENTIALS, "backend/workflow_actions/credentials.py"}
REPORT_CLI = "scripts/github_actions_exceptions.py"
REPORT_TOKEN = "GITHUB_ACTIONS_REPORT_TOKEN"
ENV_KEYS = {
    "GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_ACCESS_TOKEN", "GITHUB_API_TOKEN", REPORT_TOKEN,
}
ENV_STORAGE = {
    CREDENTIALS, "backend/infrastructure/core/config.py", "backend/infrastructure/core/github_config.py",
}
ENV_NAMES = ENV_STORAGE | {
    "backend/infrastructure/clients/claude_code_cli.py",
    "backend/daily_pipeline/failure_analysis/agent.py",
}
API_ADDRESS = re.compile(r"(?:api|uploads)\.github\.com|github\.com/api/v3", re.IGNORECASE)
TOKEN_LITERAL = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{48,})\b")
ENV_ACCESS = re.compile(
    r"(?:\$(?:\{|env:)?|\b(?:process\.env|import\.meta\.env)\.(?:VITE_)?)"
    + r"(?:" + "|".join(sorted(ENV_KEYS)) + r")\b", re.IGNORECASE,
)
AUTH_ARGUMENT = re.compile(
    r"x-access-token:|\.extraheader|\bgh(?:\.exe)?\s+(?:api|auth|pr|run|workflow)\b",
    re.IGNORECASE,
)
GIT_COMMANDS = {"clone", "fetch", "ls-remote", "pull", "push"}
RAW_GIT_COMMAND = re.compile(r"\bgit(?:\.exe)?\s+(?:clone|fetch|ls-remote|pull|push)\b", re.IGNORECASE)
PUBLIC_GIT_CLIENTS = {
    TRANSPORT,
    "backend/infrastructure/clients/github_cache.py",
    "backend/infrastructure/clients/git_mirror.py",
}
STANDALONE_DAILY = "daily_pipeline.failure_analysis"
STANDALONE_DAILY_PATH = "backend/daily_pipeline/failure_analysis/"
SOURCE_ROOTS = ("backend", "operations", "frontend/src", "deploy", "scripts")
SOURCE_SUFFIXES = {".py", ".sh", ".ps1", ".js", ".jsx", ".ts", ".tsx", ".yml", ".yaml"}
CONFIG_SUFFIXES = {".env", ".example", ".json", ".md", ".toml"}

# These literals describe or validate URLs, or belong to the standalone Daily CLI.
API_LITERALS = {
    "backend/agent/agent_tools.py": {"api.github.com", "Error: 仅允许 api.github.com 域名，不支持: "},
    "backend/collector/ci.py": {"https://api.github.com/repos/"},
    "backend/daily_pipeline/logging.py": {"api.github.com"},
    "backend/infrastructure/clients/daily_pipeline_collection.py": {"api.github.com"},
    "backend/daily_pipeline/failure_analysis/daily_tools.py": {"https://api.github.com"},
    REPORT_CLI: {"api.github.com", "https://api.github.com/repos/"},
}
LOCAL_TOKEN_STATUS = (
    'if [ -z "$GITHUB_TOKEN" ] || [[ "$GITHUB_TOKEN" == *"your-"* ]]; then'
)
ALLOWED_ENV_REFERENCES = {
    "operations/development/start-local.sh": {LOCAL_TOKEN_STATUS},
    f"{STANDALONE_DAILY_PATH}harness.patch.yml": {
        "GITHUB_PERSONAL_ACCESS_TOKEN: !!js process.env.GITHUB_PERSONAL_ACCESS_TOKEN",
    },
}
SECRET_FIELDS = {
    "github_token": {CREDENTIALS},
    "token_encrypted": {
        "backend/infrastructure/core/github_config.py",
        "backend/workflow_actions/credentials.py",
    },
}


def _string(node: ast.AST) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def check_python(path: str, source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source, filename=path)
    problems = set()
    forbidden_env_keys = ENV_KEYS - ({REPORT_TOKEN} if path == REPORT_CLI else set())
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr) and _string(node.body[0].value)
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "infrastructure.core.github_audit"
                and path != TRANSPORT
                and any(
                    item.name in {"begin_github_operation", "finish_github_operation", "wait_for_github_audit"}
                    for item in node.names
                )
            ):
                problems.add((node.lineno, "Use the audited transport to write GitHub operation records"))
            imports = [item.name for item in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.extend(f"{node.module}.{item.name}" for item in node.names)
                imports.append(node.module)
            for name in imports:
                if name.split(".")[0] in {"github", "github3", "ghapi"}:
                    problems.add((node.lineno, "Use the audited GitHub clients instead of a third-party GitHub SDK"))
                relative_daily = (
                    isinstance(node, ast.ImportFrom) and node.level == 1
                    and path.startswith("backend/daily_pipeline/") and name == "failure_analysis"
                )
                if (STANDALONE_DAILY in name or relative_daily) and not path.startswith(STANDALONE_DAILY_PATH):
                    problems.add((node.lineno, "Keep the standalone Daily analysis CLI out of dashboard services"))
        if isinstance(node, ast.Attribute):
            if node.attr in forbidden_env_keys and path not in ENV_STORAGE:
                problems.add((node.lineno, "Access GitHub settings through the credential loader"))
            if node.attr == "_github_token" and path not in {CREDENTIALS, TRANSPORT}:
                problems.add((node.lineno, "Keep the raw GitHub token inside the audited transport"))
        if isinstance(node, ast.Subscript):
            key = _string(node.slice)
            if key in forbidden_env_keys and path not in ENV_STORAGE:
                problems.add((node.lineno, "Access GitHub settings through the credential loader"))
        if isinstance(node, ast.Call):
            name = _name(node.func)
            args = tuple(map(_string, node.args))
            keys = set(args)
            env_keys = {key.upper() for key in keys} if name == "getenv" else keys
            if name in {"get", "getenv", "getattr", "setattr", "setdefault", "pop", "environ"} and (
                env_keys & forbidden_env_keys and path not in ENV_STORAGE
            ):
                problems.add((node.lineno, "Access GitHub settings through the credential loader"))
            if name in {"run", "Popen", "system", "create_subprocess_shell"} and (
                args and RAW_GIT_COMMAND.search(args[0]) and path not in PUBLIC_GIT_CLIENTS
            ):
                problems.add((node.lineno, "Use the audited or public Git client for remote Git operations"))
            if name in {"getattr", "setattr"} and "_github_token" in keys:
                problems.add((node.lineno, "Keep the raw GitHub token inside the audited transport"))
            if name == "GitHubCredential" and path not in CREDENTIAL_LOADERS:
                problems.add((node.lineno, "Create GitHub credentials through the approved credential loaders"))
            if name in {"GitHubClient", "GitHubAsyncClient"} and any(
                kw.arg == "verify" and not (isinstance(kw.value, ast.Constant) and kw.value.value is True)
                for kw in node.keywords
            ):
                problems.add((node.lineno, "Keep TLS certificate verification enabled for GitHub"))
            if name in {"get", "getattr", "setdefault", "pop"}:
                for key in keys & SECRET_FIELDS.keys():
                    if path not in SECRET_FIELDS[key]:
                        problems.add((node.lineno, "Access stored GitHub tokens through the credential loader"))
        if isinstance(node, ast.Subscript):
            key = _string(node.slice)
            if key in SECRET_FIELDS and path not in SECRET_FIELDS[key]:
                problems.add((node.lineno, "Access stored GitHub tokens through the credential loader"))
        if isinstance(node, (ast.Tuple, ast.List)) and node.elts:
            command = _string(node.elts[0])
            args = {_string(arg) for arg in node.elts[1:]}
            if command in {"gh", "gh.exe"} and args & {"api", "auth", "pr", "run", "workflow"}:
                problems.add((node.lineno, "Use the audited client instead of the GitHub CLI"))
            if command in {"git", "git.exe"} and args & GIT_COMMANDS and path not in PUBLIC_GIT_CLIENTS:
                problems.add((node.lineno, "Use the audited or public Git client for remote Git operations"))
        literal = _string(node)
        if literal and node not in docstrings and path != TRANSPORT:
            if API_ADDRESS.search(literal) and literal not in API_LITERALS.get(path, set()):
                problems.add((node.lineno, "Use GitHubClient or GitHubAsyncClient for GitHub API calls"))
            if AUTH_ARGUMENT.search(literal):
                problems.add((node.lineno, "Use run_github_git or GitHubAsyncClient for authentication"))
            if literal in forbidden_env_keys and path not in ENV_NAMES:
                problems.add((node.lineno, "Keep GitHub credential environment names inside the credential loader"))
            if STANDALONE_DAILY in literal and not path.startswith(STANDALONE_DAILY_PATH):
                problems.add((node.lineno, "Keep the standalone Daily analysis CLI out of dashboard services"))
    return sorted(problems)


def source_paths() -> list[Path]:
    """Check project code without reading ignored runtime files or dependencies."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, check=True, capture_output=True,
    )
    return sorted({Path(name) for name in result.stdout.decode("utf-8").split("\0") if name})


def main() -> int:
    failures = 0
    for path in source_paths():
        name = path.as_posix()
        is_config = path.suffix in CONFIG_SUFFIXES or path.name.startswith(".env")
        if not (is_config or path.suffix in SOURCE_SUFFIXES) or not (ROOT / path).is_file():
            continue
        try:
            source = (ROOT / path).read_text(encoding="utf-8-sig")
            violations = [
                (line, "Possible hard-coded GitHub token")
                for line, content in enumerate(source.splitlines(), 1)
                if TOKEN_LITERAL.search(content)
            ]
            if name != "scripts/check_github_audit_boundary.py" and name.startswith(
                tuple(f"{root}/" for root in SOURCE_ROOTS)
            ):
                if path.suffix == ".py":
                    violations.extend(check_python(name, source))
                elif path.suffix in SOURCE_SUFFIXES:
                    violations.extend(
                        (line, "Route GitHub operations through the audited backend")
                        for line, content in enumerate(source.splitlines(), 1)
                        if API_ADDRESS.search(content) or AUTH_ARGUMENT.search(content)
                    )
                    violations.extend(
                        (line, "Keep GitHub tokens inside the backend credential loader")
                        for line, content in enumerate(source.splitlines(), 1)
                        if ENV_ACCESS.search(content) and content.strip() not in ALLOWED_ENV_REFERENCES.get(name, set())
                    )
        except SyntaxError as exc:
            violations = [(exc.lineno or 1, exc.msg)]
        except (OSError, UnicodeError) as exc:
            violations = [(1, f"Could not read source for audit check ({type(exc).__name__})")]
        failures += len(violations)
        for line, message in violations:
            print(f"{path.as_posix()}:{line}: {message}", file=sys.stderr)
    if failures:
        return 1
    print("GitHub audit boundary check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
