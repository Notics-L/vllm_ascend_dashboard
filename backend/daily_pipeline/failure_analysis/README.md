# Daily failure analysis

This backend module analyzes one GitHub Actions failure. DeepSeek Harness runs the model and tool loop. The Daily module owns the classification and localization instructions, the two read-only CI evidence tools, and the typed results. The CLI and Python methods work independently of the web UI, the database, and the Daily scheduler.

## Python interface

From `backend/`, import the public models and the Agent from `daily_pipeline.failure_analysis`.

```python
from pathlib import Path

from daily_pipeline.failure_analysis import AnalysisRequest, DailyFailureAgent, HarnessOptions

agent = DailyFailureAgent(HarnessOptions(harness_home=Path("daily-harness-home")))
request = AnalysisRequest(
    failure_url="https://github.com/OWNER/REPO/actions/runs/123/job/456",
    case_name="cpu-0 card-(part 1-1)",
    success_url="https://github.com/OWNER/REPO/actions/runs/100/job/400",
)
classification = agent.classify(request)
if classification.category == "code":
    localization = agent.locate(request, classification)
```

| Method | Result |
| --- | --- |
| `classify(request) -> Classification` | Identifies the failed Job and returns `code`, `version_compatibility`, `environment`, or `insufficient_evidence`, plus evidence and missing information. |
| `locate(request, classification) -> Localization` | Accepts a code classification for the same failure. Returns tested commit comparisons when available, candidate PRs, and evidence. |
| `analyze(request) -> Analysis` | Runs classification, then localization for code failures. Retains a valid classification if the second stage fails. |

`case_name` and `success_url` are optional. The synchronous methods are intended for a CLI or a background worker. Inputs and outputs are Pydantic models. Use `model_dump(mode="json")` to persist or transmit a result. `ValidationError` indicates invalid input. `AnalysisError` indicates a runtime failure or invalid analysis output. Evidence shortages are normal results.

The module returns candidate PRs. A `diff_match` candidate cites the file changes of that PR. A `timing_only` candidate still needs diff evidence. Applications that use these results decide how to act on them.

## Tools

| Tool | Source and behavior |
| --- | --- |
| `get_daily_job_context` | Selects an exact failed Job or a unique failed Job matching the case name. Reads the requested attempt when specified. Returns the selected Job, runner labels, steps, and workflow SHA, or the candidates and reason when selection is ambiguous. |
| `search_daily_job_log` | Reads or searches the selected Job's log with numbered lines and paging. Each analysis caches logs up to 8 MiB per Job. Responses indicate truncation. |
| Repository and document tools | Available through the Harness `sdk` profile and the read-only GitHub MCP Server when configured. Repository comparisons and PR diffs support localization. |

The Daily tools take the failure and baseline links from the validated request. `target: success` selects the optional baseline.

## Run and verify

1. Install the isolated dependencies in a Python environment.

   ```sh
   python -m pip install -r backend/daily_pipeline/failure_analysis/requirements.txt
   ```

2. Set `DEEPSEEK_API_KEY` for the default route. To use another route, configure the Harness `sdk` profile or pass `--harness-patch`, then use `--provider` and `--model`.
3. Set `GITHUB_PERSONAL_ACCESS_TOKEN` with read access to Actions logs and the repositories you will analyze, or provide `HarnessOptions.github_token` from Python. Installing [GitHub MCP Server](https://github.com/github/github-mcp-server/releases) on `PATH` adds its read-only repository and PR tools. The Daily tools also support publicly accessible data without a token.

Run the following command from the repository root.

```sh
python -m backend.daily_pipeline.failure_analysis.analyze "https://github.com/OWNER/REPO/actions/runs/123/job/456" --harness-home /path/to/daily-harness
```

Use a dedicated Harness home. Pass `--case-name` to identify a matrix Job when starting from a Run link and `--success-url` to give localization a baseline. Use a trusted `--workspace` for a local checkout or a prepared log named `job-456.log`. The Daily tool verifies the Job in GitHub before choosing that file.

From `backend/`, run `uv run pytest -o addopts= ../tests/unit/test_daily_failure_agent.py ../tests/unit/test_daily_failure_tools.py`. These tests exercise the Agent contract and evidence tools without model or GitHub credentials. For a live acceptance test, run the CLI with an actual failure and check that the classification cites the failed step, and that any candidate PR is traceable to its diff.
