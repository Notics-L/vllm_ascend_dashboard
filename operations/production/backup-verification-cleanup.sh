#!/bin/bash
# Sourced by backup.sh; mysql_root_exec executes a single MySQL session.
cleanup_verify() {
    local verify_db sql="SET SESSION FOREIGN_KEY_CHECKS=0;" names="" remaining
    [[ "$verify_prefix" =~ ^vllm_dashboard_verify_[0-9]{8}_[0-9]{6}$ ]] || return 1
    for verify_db in $verify_dbs; do
        [[ "$verify_db" == "${verify_prefix}_"* && "$verify_db" =~ ^[a-zA-Z0-9_]+$ ]] || return 1
        sql+=" DROP DATABASE IF EXISTS \`$verify_db\`;"
        names+="'${verify_db}',"
    done
    [[ -n "$names" ]] || return 0
    # Verification control/collection databases can reference each other.
    # Disable FK checks only in this cleanup session, never globally.
    sql+=" SET SESSION FOREIGN_KEY_CHECKS=1;"
    mysql_root_exec "$sql" || return 1
    remaining="$(mysql_root_exec "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name IN (${names%,})")" || return 1
    [[ "$remaining" == 0 ]]
}

cleanup_verify_on_exit() {
    local status=$?
    trap - EXIT
    if ! cleanup_verify; then
        echo "[ERROR] verification database cleanup failed: $verify_dbs" >&2
        status=1
    fi
    exit "$status"
}
