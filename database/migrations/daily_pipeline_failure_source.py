"""Normalize failure facts into retained collection nodes, preserving history."""
import json
import re

from infrastructure.db.base import engine
from sqlalchemy import inspect, text

OLD_FIELDS = ("repository", "run_id", "source_run_id", "job_id", "name", "hardware", "case_type", "vllm_version", "execution_started_at", "failed_at", "context")

async def run():
    async with engine.begin() as conn:
        async def columns(table):
            return {c["name"] for c in await conn.run_sync(lambda s:inspect(s).get_columns(table))}
        root_cols = await columns("daily_pipeline_collection_runs")
        for name,ddl in {"repository":"VARCHAR(255) NULL", "source_branch":"VARCHAR(255) NULL", "deleted_at":"DATETIME(6) NULL"}.items():
            if name not in root_cols:
                await conn.execute(text(f"ALTER TABLE daily_pipeline_collection_runs ADD COLUMN `{name}` {ddl}"))
        node_cols = await columns("daily_pipeline_collection_nodes")
        for name,ddl in {"is_current":"BOOLEAN NOT NULL DEFAULT 1", "source_run_id":"BIGINT NULL", "execution_started_at":"DATETIME(6) NULL", "failed_at":"DATETIME(6) NULL", "vllm_ref":"VARCHAR(255) NULL"}.items():
            if name not in node_cols:
                await conn.execute(text(f"ALTER TABLE daily_pipeline_collection_nodes ADD COLUMN `{name}` {ddl}"))
        await conn.execute(text("""UPDATE daily_pipeline_collection_runs r
            JOIN (SELECT run_id,MAX(id) id FROM daily_pipeline_trigger_history GROUP BY run_id) latest ON latest.run_id=r.run_id
            JOIN daily_pipeline_trigger_history h ON h.id=latest.id
            SET r.repository=COALESCE(r.repository,h.repository),r.source_branch=COALESCE(r.source_branch,h.source_branch)"""))
        nodes = {n["id"]:dict(n) for n in (await conn.execute(text("SELECT * FROM daily_pipeline_collection_nodes"))).mappings()}
        for node in nodes.values():
            source_run,version,parent=node["run_id"],node.get("vllm_ref") or node.get("vllm_version"),node["parent_id"]
            seen=set()
            while parent in nodes and parent not in seen:
                seen.add(parent)
                ancestor=nodes[parent]
                if ancestor["node_type"]=="workflow_run":
                    source_run=ancestor["source_id"]
                match=re.fullmatch(r"run-all-tests \(([^)]+)\)",ancestor["name"])
                if match:
                    version=match.group(1)
                parent=ancestor["parent_id"]
            await conn.execute(text("UPDATE daily_pipeline_collection_nodes SET source_run_id=COALESCE(source_run_id,:source),vllm_ref=COALESCE(vllm_ref,:version) WHERE id=:id"),{"id":node["id"],"source":source_run,"version":version})
        tables=await conn.run_sync(lambda s:inspect(s).get_table_names())
        if "daily_pipeline_failures" not in tables:
            return
        cols=await columns("daily_pipeline_failures")
        if "node_id" not in cols:
            await conn.execute(text("ALTER TABLE daily_pipeline_failures ADD COLUMN node_id BIGINT NULL"))
        if "job_id" in cols:
            failures=list((await conn.execute(text("SELECT * FROM daily_pipeline_failures"))).mappings())
            for f in failures:
                node_id=await conn.scalar(text("SELECT id FROM daily_pipeline_collection_nodes WHERE run_id=:run AND source_id=:job AND node_type='test_job'"),{"run":f["run_id"],"job":f["job_id"]})
                if node_id is None:
                    await conn.execute(text("""INSERT INTO daily_pipeline_collection_runs(run_id,repository,source_branch,workflow,result,deleted_at)
                        VALUES(:run,:repo,NULL,'schedule_daily_test.yaml','failure',UTC_TIMESTAMP(6))
                        ON DUPLICATE KEY UPDATE run_id=VALUES(run_id)"""),{"run":f["run_id"],"repo":f["repository"]})
                    inserted=await conn.execute(text("""INSERT INTO daily_pipeline_collection_nodes(run_id,parent_id,node_type,source_id,source_run_id,is_current,name,result,case_type,hardware,vllm_version,vllm_ref,execution_started_at,failed_at)
                        VALUES(:run,NULL,'test_job',:job,:source,0,:name,'failure',:type,:hardware,:display,:version,:started,:failed)"""),{"run":f["run_id"],"job":f["job_id"],"source":f["source_run_id"],"name":f["name"],"type":f["case_type"],"hardware":f["hardware"],"display":(json.loads(f["context"]) if isinstance(f["context"],str) else f["context"] or {}).get("display_version"),"version":f["vllm_version"],"started":f["execution_started_at"],"failed":f["failed_at"]})
                    node_id=inserted.lastrowid
                await conn.execute(text("UPDATE daily_pipeline_collection_nodes SET execution_started_at=COALESCE(execution_started_at,:started),failed_at=COALESCE(failed_at,:failed),vllm_ref=COALESCE(:version,vllm_ref) WHERE id=:node"),{"node":node_id,"started":f["execution_started_at"],"failed":f["failed_at"],"version":f["vllm_version"]})
                await conn.execute(text("UPDATE daily_pipeline_failures SET node_id=:node WHERE id=:id"),{"node":node_id,"id":f["id"]})
            await conn.execute(text("ALTER TABLE daily_pipeline_failures MODIFY COLUMN node_id BIGINT NOT NULL"))
        indexes=await conn.run_sync(lambda s:inspect(s).get_indexes("daily_pipeline_failures"))
        for index in indexes:
            if set(index["column_names"]) & set(OLD_FIELDS):
                name=index["name"].replace("`","``")
                await conn.execute(text(f"ALTER TABLE daily_pipeline_failures DROP INDEX `{name}`"))
        for field in OLD_FIELDS:
            if field in cols:
                await conn.execute(text(f"ALTER TABLE daily_pipeline_failures DROP COLUMN `{field}`"))
        indexes=await conn.run_sync(lambda s:inspect(s).get_indexes("daily_pipeline_failures"))
        if not any(i["unique"] and i["column_names"]==["node_id"] for i in indexes):
            await conn.execute(text("ALTER TABLE daily_pipeline_failures ADD UNIQUE INDEX uq_daily_failure_node(node_id)"))
        fks=await conn.run_sync(lambda s:inspect(s).get_foreign_keys("daily_pipeline_failures"))
        if not any(fk["constrained_columns"]==["node_id"] for fk in fks):
            await conn.execute(text("ALTER TABLE daily_pipeline_failures ADD CONSTRAINT fk_daily_failure_source FOREIGN KEY(node_id) REFERENCES daily_pipeline_collection_nodes(id) ON DELETE RESTRICT"))
