"""Build GitHub links from stored Daily records."""

from daily_pipeline.pull_requests import REPOSITORY_PATTERN


def github_run_url(repository: str | None, run_id: int | None, job_id: int | None = None) -> str | None:
    if not repository or not REPOSITORY_PATTERN.fullmatch(repository) or not run_id:
        return None
    run_url = f"https://github.com/{repository}/actions/runs/{run_id}"
    return f"{run_url}/job/{job_id}" if job_id else run_url


def github_issue_url(repository: str | None, number: int) -> str | None:
    if not repository or not REPOSITORY_PATTERN.fullmatch(repository) or not number:
        return None
    return f"https://github.com/{repository}/issues/{number}"
