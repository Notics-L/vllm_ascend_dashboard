#!/bin/bash
# Online MySQL backup for the production Docker deployment.
# Phase 0 升级：三库备份 + --source-data=2 + GTID/binlog 位置记录。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKUP_DIR="${DASHBOARD_BACKUP_DIR:-$PROJECT_ROOT/backups}"
COMPOSE_FILE="${DASHBOARD_COMPOSE_FILE:-$PROJECT_ROOT/deploy/compose/production/compose.yml}"
ENV_FILE="${DASHBOARD_ENV_FILE:-/etc/vllm-ascend-dashboard/production.env}"

# 自动加载 .env.production 中的 MYSQL_ROOT_PASSWORD 等
if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
fi
RETENTION_DAYS=7
SILENT=false
VERIFY_RESTORE=false
CHECK_LATEST=false
MAX_BACKUP_AGE_HOURS="${DASHBOARD_FAST_BACKUP_MAX_AGE_HOURS:-24}"

# By default back up the database selected by MYSQL_DATABASE. Additional
# databases can be supplied explicitly through DASHBOARD_BACKUP_DATABASES.
# This keeps a single-database production installation from silently backing
# up unrelated or empty names.
DATABASES="${DASHBOARD_BACKUP_DATABASES:-${MYSQL_DATABASE:-vllm_dashboard}}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --silent) SILENT=true; shift ;;
        --verify-restore) VERIFY_RESTORE=true; shift ;;
        --check-latest) CHECK_LATEST=true; shift ;;
        --retention) RETENTION_DAYS="${2:?retention days required}"; shift 2 ;;
        --databases) DATABASES="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

log() { $SILENT || echo "[BACKUP] $1"; }
die() { echo "[ERROR] $1" >&2; exit 1; }

latest_verified_backup() {
    local latest_meta backup_file expected_checksum actual_checksum
    local metadata_users metadata_tables now_epoch modified_epoch age_seconds max_age_seconds

    [[ "$MAX_BACKUP_AGE_HOURS" =~ ^[0-9]+$ ]] \
        || die "DASHBOARD_FAST_BACKUP_MAX_AGE_HOURS must be a non-negative integer"
    max_age_seconds=$((MAX_BACKUP_AGE_HOURS * 3600))

    now_epoch="$(date +%s)"
    while IFS= read -r latest_meta; do
        [[ -n "$latest_meta" && -f "$latest_meta" ]] || continue

        backup_file="${latest_meta%.meta}"
        [[ -s "$backup_file" ]] || continue
        grep -q '^restore_verified=true$' "$latest_meta" || continue

        metadata_users="$(awk -F= '$1 == "users" { print $2; exit }' "$latest_meta")"
        metadata_tables="$(awk -F= '$1 == "tables" { print $2; exit }' "$latest_meta")"
        [[ "$metadata_users" =~ ^[1-9][0-9]*$ && "$metadata_tables" =~ ^[1-9][0-9]*$ ]] || continue

        modified_epoch="$(stat -c %Y "$latest_meta" 2>/dev/null)" || continue
        age_seconds=$((now_epoch - modified_epoch))
        (( age_seconds < 0 )) && age_seconds=0
        (( age_seconds <= max_age_seconds )) || continue

        expected_checksum="$(awk -F= '$1 == "sha256" { print $2; exit }' "$latest_meta")"
        [[ "$expected_checksum" =~ ^[[:xdigit:]]{64}$ ]] || continue
        actual_checksum="$(sha256sum "$backup_file" | awk '{print $1}')"
        [[ "$actual_checksum" == "$expected_checksum" ]] || continue

        log "reusing verified backup: $backup_file (age=${age_seconds}s users=$metadata_users tables=$metadata_tables)"
        printf '%s\n' "$backup_file"
        return 0
    done < <(find "$BACKUP_DIR" -maxdepth 1 -type f \
        \( -name 'vllm_dashboard_*.sql.meta' -o -name 'vllm_dashboard_*.sql.zst.meta' \) \
        -printf '%T@ %p\n' 2>/dev/null | sort -nr | cut -d' ' -f2-)

    die "no recent restore-verified MySQL backup with a valid checksum found in $BACKUP_DIR"
}

mysql_root_exec() {
    compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "$1"
}
compose() {
    DASHBOARD_RUNTIME_ENV_FILE="$ENV_FILE" \
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" --profile full "$@"
}

if $CHECK_LATEST; then
    mkdir -p "$BACKUP_DIR"
    latest_verified_backup
    exit 0
fi

command -v docker >/dev/null 2>&1 || die "docker is not installed"
[[ -f "$COMPOSE_FILE" ]] || die "compose file is missing: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || die "production environment file is missing: $ENV_FILE"
[[ -n "$(compose ps -q mysql)" ]] || die "MySQL service is unavailable"
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || die "retention must be a non-negative integer"

# 检测实际存在的数据库
EXISTING_DBS=""
for db in $DATABASES; do
    [[ "$db" =~ ^[a-zA-Z0-9_]+$ ]] || die "unsafe database name: $db"
    if mysql_root_exec "SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='$db'" 2>/dev/null | grep -q 1; then
        EXISTING_DBS="$EXISTING_DBS $db"
    fi
done

# 如果三个逻辑库都不存在，回退到 MySQL 容器的默认数据库
if [[ -z "${EXISTING_DBS// /}" ]]; then
    # 回退到 vllm_dashboard（Phase 0 拆分前单库名）
    default_db="vllm_dashboard"
    if mysql_root_exec "SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='$default_db'" 2>/dev/null | grep -q 1; then
        EXISTING_DBS="$default_db"
    fi
fi

[[ -n "${EXISTING_DBS// /}" ]] || die "no backup target databases found (tried: $DATABASES and MYSQL_DATABASE)"

log "backup targets: $EXISTING_DBS"

mkdir -p "$BACKUP_DIR"
timestamp="$(date +%Y%m%d_%H%M%S)"
backup_file="$BACKUP_DIR/vllm_dashboard_${timestamp}.sql.zst"
metadata_file="$BACKUP_DIR/vllm_dashboard_${timestamp}.sql.zst.meta"

# 解压工具（压缩备份的内容校验/恢复都用它）
decompress() { zstd -dc "$1"; }

# 记录备份前状态
pre_users=0
pre_tables_total=0
for db in $EXISTING_DBS; do
    count="$(mysql_root_exec "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$db'" 2>/dev/null || echo 0)"
    pre_tables_total=$((pre_tables_total + count))
    if mysql_root_exec "SELECT 1 FROM information_schema.tables WHERE table_schema='$db' AND table_name='users'" 2>/dev/null | grep -q 1; then
        users="$(mysql_root_exec "SELECT COUNT(*) FROM \`$db\`.users" 2>/dev/null || echo 0)"
        [[ "$users" =~ ^[0-9]+$ ]] || die "live database user count is invalid for $db: $users"
        pre_users=$((pre_users + users))
    fi
done
[[ "$pre_users" =~ ^[0-9]+$ ]] && (( pre_users > 0 )) || die "live database user count is invalid: $pre_users"
[[ "$pre_tables_total" -gt 0 ]] || die "live database table count is invalid: $pre_tables_total"

log "creating transaction-consistent MySQL dump for: $EXISTING_DBS"

# --source-data=2 将当时的 binlog 坐标写入 dump（注释形式），
# 使 PITR 恢复可以从备份位置继续应用 binlog。
# --no-tablespaces 避免需要 PROCESS 权限。
dump_cmd="mysqldump -uroot -p\"\$MYSQL_ROOT_PASSWORD\" \
  --databases $EXISTING_DBS \
  --single-transaction \
  --quick \
  --routines \
  --triggers \
  --events \
  --source-data=2 \
  --no-tablespaces"

if ! compose exec -T mysql sh -c "exec $dump_cmd" | zstd -q > "$backup_file"; then
    rm -f "$backup_file"
    die "mysqldump failed"
fi

[[ -s "$backup_file" ]] || die "backup is empty"
# 注意：不能用 grep -q —— 它找到匹配即退出会让上游 zstd -dc 收到 SIGPIPE，
# 在 pipefail 下整个管道被误判为失败（曾导致"backup does not contain users
# table"误报）。grep -c 会读完整个流，无此问题。
[[ "$(decompress "$backup_file" | grep -c 'CREATE TABLE .users.')" -gt 0 ]] || die "backup does not contain users table"
[[ "$(decompress "$backup_file" | grep -c 'Dump completed on')" -gt 0 ]] || die "mysqldump completion marker is missing"

# 提取 binlog 恢复坐标
binlog_file="$(decompress "$backup_file" | grep -oP 'SOURCE_LOG_FILE='\''\K[^'\'']+' 2>/dev/null || echo "")"
binlog_position="$(decompress "$backup_file" | grep -oP 'SOURCE_LOG_POS=\K[0-9]+' 2>/dev/null || echo "")"

backup_users="$pre_users"
backup_tables="$pre_tables_total"

# 恢复校验：将备份恢复到隔离的验证库
if $VERIFY_RESTORE; then
    verify_prefix="vllm_dashboard_verify_${timestamp}"
    verify_dbs=""
    sed_args=()
    for db in $EXISTING_DBS; do
        verify_db="${verify_prefix}_${db}"
        [[ "$verify_db" =~ ^[a-zA-Z0-9_]+$ ]] || die "unsafe verification database name"
        verify_dbs="$verify_dbs $verify_db"
        sed_args+=( -e "s/\`$db\`/\`$verify_db\`/g" )
    done
    source "$SCRIPT_DIR/backup-verification-cleanup.sh"
    trap cleanup_verify_on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # 去掉 GTID_PURGED 语句（verify 用独立临时库，不需要 GTID）
    decompress "$backup_file" | sed '/^SET @@GLOBAL.GTID_PURGED=/d' | sed "${sed_args[@]}" | \
    compose exec -T mysql sh -c \
        'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD"'

    # 验证用户数和表数
    backup_users=0
    backup_tables=0
    for db in $EXISTING_DBS; do
        verify_db="${verify_prefix}_${db}"
        if mysql_root_exec "SELECT 1 FROM information_schema.tables WHERE table_schema='$verify_db' AND table_name='users'" 2>/dev/null | grep -q 1; then
            db_users="$(mysql_root_exec "SELECT COUNT(*) FROM \`$verify_db\`.users" 2>/dev/null || echo 0)"
            [[ "$db_users" =~ ^[0-9]+$ ]] || die "restore verification user count is invalid for $verify_db: $db_users"
            backup_users=$((backup_users + db_users))
        fi
        db_tables="$(mysql_root_exec "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$verify_db'" 2>/dev/null || echo 0)"
        [[ "$db_tables" =~ ^[0-9]+$ ]] || die "restore verification table count is invalid for $verify_db: $db_tables"
        backup_tables=$((backup_tables + db_tables))
    done

    [[ "$backup_users" = "$pre_users" ]] || die "restore verification user count mismatch: $pre_users -> $backup_users"
    [[ "$backup_tables" = "$pre_tables_total" ]] || die "restore verification table count mismatch: $pre_tables_total -> $backup_tables"
    cleanup_verify || die "verification database cleanup failed"
    trap - EXIT INT TERM
fi

checksum="$(sha256sum "$backup_file" | awk '{print $1}')"
git_commit="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

{
    echo "created_at=$(date --iso-8601=seconds)"
    echo "git_commit=$git_commit"
    echo "sha256=$checksum"
    echo "users=$backup_users"
    echo "tables=$backup_tables"
    echo "restore_verified=$VERIFY_RESTORE"
    echo "binlog_file=${binlog_file:-unknown}"
    echo "binlog_position=${binlog_position:-unknown}"
    echo "backup_time=$(date --iso-8601=seconds)"
} > "$metadata_file"

# 清理过期备份（含压缩与未压缩两种命名）
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'vllm_dashboard_*.sql' -o -name 'vllm_dashboard_*.sql.meta' \
       -o -name 'vllm_dashboard_*.sql.zst' -o -name 'vllm_dashboard_*.sql.zst.meta' \) \
    -mtime "+$RETENTION_DAYS" -delete

log "backup verified: users=$backup_users tables=$backup_tables sha256=$checksum"
log "binlog: ${binlog_file:-unknown}:${binlog_position:-unknown}"
echo "$backup_file"
