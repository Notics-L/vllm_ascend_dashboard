from database.migrations.mysql_schema import (
    _FORWARDING_VIEW_SOURCE,
    CREATE_TABLE_MIGRATIONS,
    INDEX_MIGRATIONS,
    MIGRATION_VERSION,
    TABLE_COLUMN_MIGRATIONS,
)


def test_mysql_migration_manifest_is_mysql_only_and_complete():
    assert MIGRATION_VERSION
    assert INDEX_MIGRATIONS["test_cases"]["ix_test_cases_is_flaky_manual"] == "is_flaky_manual"
    assert set(TABLE_COLUMN_MIGRATIONS["test_cases"]) == {
        "lifetime_runs",
        "lifetime_failures",
        "issues_found",
        "suspected_test_issue_count",
        "is_flaky_manual",
        "auto_issues_found",
        "auto_suspected_test_issue_count",
        "issues_found_override",
    }
    assert all(
        "sqlite" not in definition.lower()
        for definitions in TABLE_COLUMN_MIGRATIONS.values()
        for definition in definitions.values()
    )


def test_model_fo_mapping_table_is_part_of_the_explicit_schema_migration():
    assert any("model_fo_mappings" in ddl for ddl in CREATE_TABLE_MIGRATIONS)


def test_workflow_config_columns_are_declared_for_an_explicit_migration():
    assert set(TABLE_COLUMN_MIGRATIONS["workflow_configs"]) == {
        "materialize_name_regex",
        "auto_failure_analysis_enabled",
    }


def test_forwarding_view_source_resolves_the_production_base_table():
    definition = (
        "select `control_db`.`workflow_configs`.`id` "
        "from `control_db`.`workflow_configs`"
    )

    source = _FORWARDING_VIEW_SOURCE.search(definition)

    assert source is not None
    assert source.groupdict() == {
        "schema": "control_db",
        "table": "workflow_configs",
    }
