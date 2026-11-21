from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from database.migrations.view_backed_user_permissions import ensure_view_backed_user_permissions


@pytest.mark.asyncio
async def test_physical_users_database_needs_no_compatibility_ddl():
    conn = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: "BASE TABLE")))
    await ensure_view_backed_user_permissions(conn)
    assert conn.execute.await_count == 1


@pytest.mark.asyncio
async def test_view_permissions_reference_physical_users_without_changing_view():
    conn = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: "VIEW"),
        SimpleNamespace(all=lambda: [("control_db", "users")]), None, None,
    ]))
    await ensure_view_backed_user_permissions(conn)
    ddl = [str(call.args[0]) for call in conn.execute.await_args_list[2:]]
    assert len(ddl) == 2
    assert all("REFERENCES `control_db`.`users`(id)" in statement for statement in ddl)
    assert all("CREATE TABLE IF NOT EXISTS" in statement for statement in ddl)


@pytest.mark.asyncio
async def test_ambiguous_view_is_rejected_before_creating_tables():
    conn = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: "VIEW"),
        SimpleNamespace(all=lambda: [("one", "users"), ("two", "users")]),
    ]))
    with pytest.raises(RuntimeError, match="Cannot resolve"):
        await ensure_view_backed_user_permissions(conn)
    assert conn.execute.await_count == 2
