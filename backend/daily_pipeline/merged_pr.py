"""Collect merged PRs and add their links to the associated Daily Issue."""

import html
import logging
import re

from infrastructure.clients.daily_pipeline_collection import CollectionSourceError, DailyCollectionClient
from infrastructure.persistence.daily_pipeline_merged_pr import DailyMergedPRRepository

logger = logging.getLogger(__name__)
SECTION_START = "<!-- daily-merged-prs:start -->"
SECTION_END = "<!-- daily-merged-prs:end -->"
MARKDOWN_SPECIAL = re.compile(r"([\\\[\]_*~\x60])")


def render_merged_pr_section(body, pulls):
    lines = [SECTION_START, "## Merged PRs", ""]
    for number, title, url in pulls:
        title = html.escape(" ".join(title.split()), quote=False).replace("@", "@\u200b")
        label = MARKDOWN_SPECIAL.sub(r"\\\1", title)
        lines.append(f"- [#{number} {label}]({url})")
    lines.append(SECTION_END)
    section = "\n".join(lines)
    start, end = body.find(SECTION_START), body.find(SECTION_END)
    if start == -1 and end == -1:
        return body.rstrip() + "\n\n" + section
    if start < 0 or end < start:
        raise CollectionSourceError("Daily Issue 的合入 PR 小节标记不完整")
    return body[:start] + section + body[end + len(SECTION_END):]


class DailyMergedPRService:
    def __init__(self, db):
        self.db = db
        self.repository = DailyMergedPRRepository(db)

    async def collect_window(self, history, token):
        history_id = history["id"]
        run_id = history["run_id"]
        synced = await self.repository.synced(history_id)
        pulls = await self.repository.pulls(history_id)
        previous = await self.repository.previous_trigger_at(history) if synced is not True and not pulls else None
        if synced is None:
            await self.repository.set_synced(history_id, False)
        else:
            await self.db.rollback()

        if previous is not None and previous < history["triggered_at"]:
            async with DailyCollectionClient(history["repository"], token) as client:
                collected = await client.merged_pull_requests(
                    history["source_branch"], previous, history["triggered_at"]
                )
            if collected:
                await self.repository.save_pulls(history, collected)
                logger.info("Daily merged PRs saved run_id=%s count=%s", run_id, len(collected))
                return

        if not pulls and synced is not True:
            await self.repository.set_synced(history_id, True)
            logger.info("Daily merged PR collection completed run_id=%s count=0", run_id)
            return

        missing_metadata = [pull.pr_number for pull in pulls if pull.url is None]
        if missing_metadata:
            if synced is True:
                await self.repository.set_synced(history_id, False)
            async with DailyCollectionClient(history["repository"], token) as client:
                metadata = {number: await client.pull_request_metadata(number) for number in missing_metadata}
            await self.repository.fill_pull_metadata(history_id, metadata)

    async def publish_linked_issue(
        self, history, token, *, awaiting_issue: bool, verify_existing: bool,
    ) -> bool:
        history_id = history["id"]
        synced = await self.repository.synced(history_id)
        if synced is True and not verify_existing:
            await self.db.rollback()
            return True

        pulls = await self.repository.pulls(history_id)
        issue_number = await self.repository.issue_number(history) if pulls else None
        await self.db.rollback()
        if pulls and issue_number is None and awaiting_issue:
            return False

        if issue_number is not None:
            repository, run_id = history["repository"], history["run_id"]
            async with DailyCollectionClient(repository, token) as client:
                issue = await client.issue(issue_number)
                body = issue.get("body") or ""
                marker = rf"(?m)^<!-- vllm-ascend-daily:{run_id}(?::[1-9][0-9]*)? -->\r?$"
                if not re.search(marker, body):
                    raise CollectionSourceError("GitHub Issue 缺少对应的 Daily Run 标记")
                updated = render_merged_pr_section(body, pulls)
                if updated != body:
                    if synced is True:
                        await self.repository.set_synced(history_id, False)
                        synced = False
                    await client.update_issue_body(issue_number, updated)
                    logger.info(
                        "Daily Issue merged PR section updated run_id=%s issue=%s count=%s",
                        run_id, issue_number, len(pulls),
                    )

        if synced is not True:
            await self.repository.set_synced(history_id, True)
        return True
