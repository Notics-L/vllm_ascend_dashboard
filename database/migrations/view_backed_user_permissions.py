"""Resolve the compatibility users view for schema migrations."""
from sqlalchemy import text


def qualified_table_name(schema: str | None, table: str) -> str:
    parts = (schema, table) if schema else (table,)
    return ".".join("`" + name.replace("`", "``") + "`" for name in parts)


async def resolve_view_backed_users_table(connection) -> tuple[str, str] | None:
    kind = (await connection.execute(text(
        "SELECT TABLE_TYPE FROM information_schema.tables "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users'"
    ))).scalar_one_or_none()
    if kind != "VIEW":
        return None
    targets = (await connection.execute(text(
        "SELECT u.TABLE_SCHEMA,u.TABLE_NAME FROM information_schema.VIEW_TABLE_USAGE u "
        "JOIN information_schema.tables t ON t.TABLE_SCHEMA=u.TABLE_SCHEMA "
        "AND t.TABLE_NAME=u.TABLE_NAME AND t.TABLE_TYPE='BASE TABLE' "
        "WHERE u.VIEW_SCHEMA=DATABASE() AND u.VIEW_NAME='users'"
    ))).all()
    if len(targets) != 1 or targets[0][1] != "users":
        raise RuntimeError("Cannot resolve users view to one physical users table")
    schema, table = targets[0]
    return schema, table


async def ensure_view_backed_user_permissions(connection) -> None:
    target = await resolve_view_backed_users_table(connection)
    if target is None:
        return
    reference = qualified_table_name(*target)
    from database.migrations.workflow_action_schedules import _PERMISSIONS_DDL as schedule_ddl
    from database.migrations.workflow_actions import _PERMISSIONS_DDL as action_ddl
    for ddl in (action_ddl, schedule_ddl):
        await connection.execute(text(ddl.replace("REFERENCES users(id)", f"REFERENCES {reference}(id)")))
