from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from database import bootstrap
from database.migrations import daily_pipeline_process_nodes, notifications


def result(value):
    return SimpleNamespace(scalar_one=lambda: value, scalar_one_or_none=lambda: value)


def connection(*responses):
    return SimpleNamespace(execute=AsyncMock(side_effect=responses))


def sql_from(conn):
    return [str(call.args[0]) for call in conn.execute.await_args_list]


@pytest.mark.asyncio
async def test_split_schema_contacts_reference_physical_users():
    conn = connection(
        result(0), result("VIEW"), SimpleNamespace(all=lambda: [("control_db", "users")]), None
    )

    assert await notifications.ensure_notification_contacts(conn, view_only=True)
    assert "REFERENCES `control_db`.`users`(id) ON DELETE SET NULL" in sql_from(conn)[-1]


@pytest.mark.asyncio
async def test_single_schema_contacts_keep_local_users_reference():
    conn = connection(result(0), result("BASE TABLE"), None)

    assert await notifications.ensure_notification_contacts(conn)
    assert "REFERENCES `users`(id) ON DELETE SET NULL" in sql_from(conn)[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("users_kind", [None, "BASE TABLE"])
async def test_regular_bootstrap_leaves_contacts_to_metadata_when_users_is_not_a_view(users_kind):
    conn = connection(result(0), result(users_kind))

    assert not await notifications.ensure_notification_contacts(conn, view_only=True)
    assert conn.execute.await_count == 2


@pytest.mark.asyncio
async def test_existing_contact_table_is_left_intact():
    conn = connection(result(1))

    assert not await notifications.ensure_notification_contacts(conn, view_only=True)
    assert conn.execute.await_count == 1


@pytest.mark.asyncio
async def test_ambiguous_users_view_does_not_create_contact_table():
    conn = connection(
        result(0), result("VIEW"), SimpleNamespace(all=lambda: [("one", "users"), ("two", "users")])
    )

    with pytest.raises(RuntimeError, match="Cannot resolve"):
        await notifications.ensure_notification_contacts(conn, view_only=True)
    assert all("CREATE TABLE" not in sql for sql in sql_from(conn))


@pytest.mark.asyncio
@pytest.mark.parametrize("base_has_phone", [False, True])
async def test_split_schema_phone_updates_base_and_refreshes_view(base_has_phone):
    responses = [
        result(1),  # users exists
        result(0),  # users view does not expose phone
        result("vllm_dashboard"),
        result("VIEW"),
        result("select `control_db`.`users`.`id` AS `id` from `control_db`.`users`"),
        result("BASE TABLE"),
        result(int(base_has_phone)),
    ]
    if not base_has_phone:
        responses.append(None)  # ALTER TABLE
    responses += [result("vllm_dashboard"), None]  # refresh the view
    conn = connection(*responses)

    assert await notifications._ensure_user_phone(conn)
    statements = sql_from(conn)
    alters = [sql for sql in statements if sql.startswith("ALTER TABLE")]
    assert alters == ([] if base_has_phone else [
        "ALTER TABLE `control_db`.`users` ADD COLUMN phone VARCHAR(32) DEFAULT NULL"
    ])
    assert statements[-1] == (
        "CREATE OR REPLACE VIEW `vllm_dashboard`.`users` AS SELECT * FROM `control_db`.`users`"
    )


@pytest.mark.asyncio
async def test_bootstrap_creates_split_notifications_before_metadata(monkeypatch):
    events = []
    conn = SimpleNamespace(run_sync=AsyncMock(side_effect=lambda _: events.append("metadata")))
    transaction = MagicMock()
    transaction.__aenter__.return_value = conn
    engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"), begin=lambda: transaction)

    async def ensure_contacts(db, *, view_only):
        assert db is conn
        assert view_only
        events.append("contacts")

    monkeypatch.setattr(bootstrap, "engine", engine)
    monkeypatch.setattr(bootstrap, "ensure_notification_contacts", ensure_contacts)
    monkeypatch.setattr(bootstrap, "_ensure_github_cache", AsyncMock())
    monkeypatch.setattr(daily_pipeline_process_nodes, "run", AsyncMock())
    from database.migrations import view_backed_user_permissions

    monkeypatch.setattr(view_backed_user_permissions, "ensure_view_backed_user_permissions", AsyncMock())

    await bootstrap.create_tables_with_latest_schema()

    assert events == ["contacts", "metadata"]
