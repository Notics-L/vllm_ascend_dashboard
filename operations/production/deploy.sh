#!/bin/bash
# Safe production deployment for Docker Compose + MySQL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MIGRATE_SCRIPT="$SCRIPT_DIR/migrate.sh"
COMPOSE_FILE="${DASHBOARD_COMPOSE_FILE:-$PROJECT_ROOT/deploy/compose/production/compose.yml}"
ENV_FILE="${DASHBOARD_ENV_FILE:-/etc/vllm-ascend-dashboard/production.env}"
BACKUP_DIR="${DASHBOARD_BACKUP_DIR:-$PROJECT_ROOT/backups}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
MAX_WAIT=120
DO_PULL=true
DRY_RUN=false
RECOVER_FAILED_MIGRATION=false
FAST=false
FAST_BACKUP_MAX_AGE_HOURS="${DASHBOARD_FAST_BACKUP_MAX_AGE_HOURS:-24}"
LOCK_DRAIN_SECONDS="${DASHBOARD_SCHEMA_LOCK_DRAIN_SECONDS:-30}"
PRUNE_RELEASE_IMAGES="${DASHBOARD_PRUNE_RELEASE_IMAGES:-true}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-pull) DO_PULL=false; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --recover-failed-migration) RECOVER_FAILED_MIGRATION=true; shift ;;
        --fast) FAST=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if $FAST && $RECOVER_FAILED_MIGRATION; then
    echo "[ERROR] --fast cannot be combined with --recover-failed-migration" >&2
    exit 2
fi

step() { echo; echo "=== $1 ==="; }
ok() { echo "[OK] $1"; }
warn() { echo "[WARN] $1"; }
die() { echo "[ERROR] $1" >&2; exit 1; }
compose() {
    DASHBOARD_RUNTIME_ENV_FILE="$ENV_FILE" \
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" --profile full "$@"
}

runtime_file_path() {
    local path="$1"
    if [[ "$path" = /* ]]; then
        printf '%s\n' "$path"
    else
        printf '%s/%s\n' "$PROJECT_ROOT" "$path"
    fi
}

validate_runtime_files() {
    [[ "${DASHBOARD_REQUIRE_EXTERNAL_CONFIG:-true}" == "true" ]] || return 0
    local litellm_file mysql_file
    litellm_file="$(runtime_file_path "${DASHBOARD_LITELLM_CONFIG_FILE:-}")"
    mysql_file="$(runtime_file_path "${DASHBOARD_MYSQL_CONFIG_FILE:-}")"
    [[ "$litellm_file" = /* && -f "$litellm_file" ]] \
        || die "external LiteLLM config is missing: $litellm_file"
    [[ "$mysql_file" = /* && -f "$mysql_file" ]] \
        || die "external MySQL config is missing: $mysql_file"
}

validate_external_volumes() {
    local volume
    for volume in \
        "${DASHBOARD_BACKEND_VOLUME:-vllm_ascend_dashboard_backend_data}" \
        "${DASHBOARD_BACKEND_LOG_VOLUME:-vllm_ascend_dashboard_backend_logs}" \
        "${DASHBOARD_MYSQL_VOLUME:-vllm_ascend_dashboard_mysql_data}"; do
        docker volume inspect "$volume" >/dev/null 2>&1 \
            || die "required external volume is missing: $volume"
    done
}
service_container() { compose ps -q "$1"; }
service_container_any() { compose ps -q --all "$1"; }
service_image() {
    local container
    container="$(service_container "$1")"
    [[ -n "$container" ]] || return 1
    docker inspect --format '{{.Config.Image}}' "$container"
}
image_repository() {
    local image="$1"
    # Immutable production references are normally repo:tag.  Digest-pinned
    # references are already immutable and do not have older local tags to
    # prune.
    image="${image%@*}"
    if [[ "$image" =~ ^(.+):[^/:]+$ ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
    else
        printf '%s\n' "$image"
    fi
}
image_id_is_active() {
    local image_id="$1" active_id
    shift
    for active_id in "$@"; do
        [[ "$image_id" == "$active_id" ]] && return 0
    done
    return 1
}
prune_superseded_dashboard_images() {
    [[ "$PRUNE_RELEASE_IMAGES" == "true" ]] || {
        warn "release image cleanup skipped (DASHBOARD_PRUNE_RELEASE_IMAGES=$PRUNE_RELEASE_IMAGES)"
        return 0
    }

    local service image image_id repo candidate_repo candidate_tag candidate_id
    local -a active_ids=() repositories=(
        "vllm_ascend_dashboard-backend"
        "vllm_ascend_dashboard-frontend"
    )
    for service in backend frontend scheduler collector; do
        image="$(service_image "$service" 2>/dev/null || true)"
        [[ -n "$image" ]] || continue
        image_id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
        [[ -n "$image_id" ]] && active_ids+=("$image_id")
        repositories+=("$(image_repository "$image")")
    done

    # Only consider Dashboard backend/frontend repositories.  Other products
    # sharing this Docker host are deliberately outside deployment scope.
    while IFS=$'\t' read -r candidate_repo candidate_tag candidate_id; do
        [[ "$candidate_repo" != "<none>" && "$candidate_tag" != "<none>" ]] || continue
        case " ${repositories[*]} " in
            *" $candidate_repo "*) ;;
            *) continue ;;
        esac
        image_id_is_active "$candidate_id" "${active_ids[@]}" && continue
        if docker ps -aq --filter "ancestor=$candidate_id" | grep -q .; then
            warn "keeping old image in use by an existing container: $candidate_repo:$candidate_tag"
            continue
        fi
        # No --force: Docker refuses deletion if a container still depends on
        # the image, which is the required safety guard for rollback/runtime.
        if docker image rm "$candidate_id" >/dev/null 2>&1; then
            ok "removed superseded release image: $candidate_repo:$candidate_tag"
        else
            warn "could not remove superseded release image: $candidate_repo:$candidate_tag"
        fi
    done < <(docker image ls --format '{{.Repository}}\t{{.Tag}}\t{{.ID}}')
}
service_is_healthy() {
    local container
    container="$(service_container "$1")"
    [[ -n "$container" ]] || return 1
    [[ "$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null)" == "healthy" ]]
}
mysql_root() {
    compose exec -T mysql sh -c \
        'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -N -e "$1"' sh "$1"
}
get_user_count() { mysql_root 'SELECT COUNT(*) FROM users'; }
get_table_count() { mysql_root 'SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()'; }
get_user_list() { mysql_root 'SELECT id, username, role FROM users ORDER BY id'; }

stream_backup_without_gtid() {
    local backup_file="$1"
    if [[ "$backup_file" == *.zst ]]; then
        zstd -dc "$backup_file"
    else
        cat "$backup_file"
    fi | sed '/^SET @@GLOBAL.GTID_PURGED=/d'
}

latest_verified_backup() {
    local output
    output="$(bash "$SCRIPT_DIR/backup.sh" --check-latest 2>&1)" || die "no verified backup is available: $output"
    printf '%s\n' "$output" | tail -1
}

backup_metadata_value() {
    local backup_file="$1" key="$2"
    awk -F= -v key="$key" '$1 == key { print $2; exit }' "$backup_file.meta"
}

schema_lock_rows() {
    mysql_root "
        SELECT DISTINCT p.ID, p.USER, p.HOST, p.TIME, LEFT(p.INFO, 160)
        FROM performance_schema.metadata_locks AS ml
        JOIN performance_schema.threads AS th ON th.THREAD_ID = ml.OWNER_THREAD_ID
        JOIN information_schema.PROCESSLIST AS p ON p.ID = th.PROCESSLIST_ID
        WHERE ml.OBJECT_SCHEMA = '$DATABASE_NAME'
          AND ml.LOCK_STATUS = 'GRANTED'
          AND p.ID <> CONNECTION_ID()
        ORDER BY p.TIME DESC"
}

show_schema_lock_diagnostics() {
    local rows
    rows="$(schema_lock_rows)" || die "unable to inspect schema metadata locks"
    if [[ -n "$rows" ]]; then
        warn "schema metadata locks are still held for database $DATABASE_NAME:"
        echo "$rows" | sed 's/^/  /'
        return 1
    fi
    return 0
}

wait_for_schema_lock_drain() {
    local elapsed=0
    [[ "$LOCK_DRAIN_SECONDS" =~ ^[0-9]+$ ]] || die "DASHBOARD_SCHEMA_LOCK_DRAIN_SECONDS must be a non-negative integer"
    while (( elapsed <= LOCK_DRAIN_SECONDS )); do
        if show_schema_lock_diagnostics; then
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    return 1
}

stop_database_writers() {
    step "Stop database writers"
    compose stop backend scheduler collector || die "failed to stop database writer services"
    ok "backend, scheduler, and collector stopped"
}

database_writer_ips() {
    local service container
    for service in backend scheduler collector; do
        container="$(service_container_any "$service")"
        [[ -n "$container" ]] || continue
        docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' "$container"
    done | awk 'NF && !seen[$0]++'
}

is_dashboard_writer_host() {
    local host="$1" ip candidate
    candidate="${host%%:*}"
    for ip in "${@:2}"; do
        [[ "$candidate" == "$ip" ]] && return 0
    done
    return 1
}

terminate_owned_schema_lock_holders() {
    local rows pid _user host _time _statement
    local -a writer_ips=("$@")
    (( ${#writer_ips[@]} > 0 )) || return 0

    rows="$(schema_lock_rows)" || die "unable to inspect schema metadata locks"
    [[ -n "$rows" ]] || return 0
    while IFS=$'\t' read -r pid _user host _time _statement; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        if is_dashboard_writer_host "$host" "${writer_ips[@]}"; then
            warn "terminating stale dashboard schema-lock holder: id=$pid host=$host"
            mysql_root "KILL $pid" || die "failed to terminate dashboard schema-lock holder $pid"
        else
            warn "leaving non-dashboard schema-lock holder untouched: id=$pid user=$_user host=$host"
        fi
    done <<< "$rows"
}

confirm_failed_migration_recovery() {
    [[ "${DASHBOARD_CONFIRM_RECOVER_FAILED_MIGRATION:-}" == "YES" ]] && return 0
    [[ -t 0 ]] || die "set DASHBOARD_CONFIRM_RECOVER_FAILED_MIGRATION=YES for non-interactive recovery"
    echo "[WARN] Recovery will replace $DATABASE_NAME with the selected verified backup."
    read -r -p "Type RECOVER to continue: " confirmation
    [[ "$confirmation" == "RECOVER" ]] || die "failed-migration recovery cancelled"
}

wait_for_health() {
    local elapsed=0
    while (( elapsed < MAX_WAIT )); do
        local backend_container
        backend_container="$(service_container backend)"
        if curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/health" >/dev/null 2>&1 \
            && [[ -n "$backend_container" ]] \
            && docker inspect --format '{{.State.Health.Status}}' "$backend_container" 2>/dev/null | grep -q '^healthy$'; then
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    return 1
}

restore_database() {
    local backup_file="$1"
    [[ -s "$backup_file" ]] || die "restore backup is missing: $backup_file"
    compose exec -T mysql sh -c \
        'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DROP DATABASE IF EXISTS \`$1\`; CREATE DATABASE \`$1\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"' sh "$DATABASE_NAME"
    # Every restore targets an already-running MySQL instance.  Strip the
    # bootstrap-only GTID_PURGED statement before importing either .sql or .zst.
    stream_backup_without_gtid "$backup_file" | compose exec -T mysql sh -c \
        'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$1"' sh "$DATABASE_NAME"
}

recover_failed_migration() {
    local backup_file="$1"
    local backup_users backup_tables post_users post_tables
    local -a writer_ips=()
    backup_users="$(backup_metadata_value "$backup_file" users)"
    backup_tables="$(backup_metadata_value "$backup_file" tables)"
    [[ "$backup_users" =~ ^[1-9][0-9]*$ && "$backup_tables" =~ ^[1-9][0-9]*$ ]] \
        || die "selected backup metadata is incomplete: $backup_file.meta"

    step "FAILED MIGRATION RECOVERY"
    echo "[RECOVERY] verified backup: $backup_file"
    echo "[RECOVERY] backup metadata: users=$backup_users tables=$backup_tables"
    confirm_failed_migration_recovery
    mapfile -t writer_ips < <(database_writer_ips)
    stop_database_writers
    terminate_owned_schema_lock_holders "${writer_ips[@]}"
    if ! wait_for_schema_lock_drain; then
        die "schema metadata locks remain after writer shutdown; no database was replaced"
    fi

    restore_database "$backup_file"
    post_users="$(get_user_count)"
    post_tables="$(get_table_count)"
    (( post_users >= backup_users && post_tables >= backup_tables )) \
        || die "restored database validation failed: users=$post_users/$backup_users tables=$post_tables/$backup_tables"

    compose up -d mysql litellm backend frontend scheduler collector
    wait_for_health || die "database restored but services are unhealthy"
    ok "recovery complete: users=$post_users tables=$post_tables"
    get_user_list | sed 's/^/  /'
}

command -v docker >/dev/null 2>&1 || die "docker is not installed"
[[ -f "$COMPOSE_FILE" ]] || die "compose file is missing: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || die "production environment file is missing: $ENV_FILE"

# Runtime configuration is intentionally external to the Git checkout.
set -a
source "$ENV_FILE"
set +a
validate_runtime_files
validate_external_volumes

mysql_container="$(service_container mysql)"
[[ -n "$mysql_container" ]] || die "MySQL service is not running"
DATABASE_NAME="$(compose exec -T mysql sh -c 'printf %s "$MYSQL_DATABASE"')"
[[ "$DATABASE_NAME" =~ ^[a-zA-Z0-9_]+$ ]] || die "unsafe MySQL database name"

if $RECOVER_FAILED_MIGRATION; then
    latest_backup="$(latest_verified_backup)"
    [[ -s "$latest_backup" && -s "$latest_backup.meta" ]] || die "verified recovery backup artifacts are missing"
    grep -q '^restore_verified=true$' "$latest_backup.meta" || die "selected recovery backup is not restore-verified"
    recover_failed_migration "$latest_backup"
    exit 0
fi

if ! $DRY_RUN; then
    [[ -n "${DEPLOY_ADMIN_USERNAME:-}" && -n "${DEPLOY_ADMIN_PASSWORD:-}" ]] \
        || die "DEPLOY_ADMIN_USERNAME and DEPLOY_ADMIN_PASSWORD are required for login verification"
fi

step "1/9 Backup and restore verification"
if $FAST; then
    backup_output="$(DASHBOARD_FAST_BACKUP_MAX_AGE_HOURS="$FAST_BACKUP_MAX_AGE_HOURS" \
        bash "$SCRIPT_DIR/backup.sh" --check-latest 2>&1)" \
        || die "fast backup precondition failed: $backup_output"
    warn "fast mode: no new dump created; using the latest verified backup"
else
    backup_output="$(bash "$SCRIPT_DIR/backup.sh" --verify-restore 2>&1)" || die "backup failed: $backup_output"
fi
backup_file="$(echo "$backup_output" | tail -1)"
[[ -s "$backup_file" && -s "$backup_file.meta" ]] || die "verified backup artifacts are missing"
grep -q '^restore_verified=true$' "$backup_file.meta" || die "backup restore verification did not pass"
ok "verified backup: $backup_file"

step "2/9 Record pre-deployment state"
pre_users="$(get_user_count)"
pre_tables="$(get_table_count)"
pre_git_full="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
pre_git="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD)"
(( pre_users > 0 && pre_tables > 0 )) || die "invalid pre-deployment database state"
if $FAST; then
    service_is_healthy mysql || die "fast mode requires a healthy MySQL container"
    service_is_healthy litellm || die "fast mode requires a healthy LiteLLM container"
fi
ok "commit=$pre_git users=$pre_users tables=$pre_tables"
get_user_list | sed 's/^/  /'

if $DRY_RUN; then
    ok "dry run complete; no code, schema, or service changes were made"
    exit 0
fi

if $DO_PULL && [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain)" ]]; then
    die "production checkout has local changes; archive and clear them before pulling upstream/main"
fi
if $FAST && ! $DO_PULL && [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain)" ]]; then
    die "fast mode requires a clean production checkout when --no-pull is used"
fi

step "3/9 Update source"
if $DO_PULL; then
    git -C "$PROJECT_ROOT" pull --ff-only origin main || die "git pull --ff-only failed"
else
    warn "source pull skipped"
fi
new_git_full="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
new_git="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD)"
if $FAST && [[ "$pre_git_full" != "$new_git_full" ]]; then
    # Demo seed data is never used by the production migration or runtime.
    # It is safe to ship with an application-only release, unlike every
    # other change under database/ (including migrations and bootstrap code).
    database_changes="$(git -C "$PROJECT_ROOT" diff --name-only "$pre_git_full" "$new_git_full" -- \
        database/ backend/infrastructure/persistence/ operations/production/migrate.sh | \
        sed '/^database\/seed_local_demo\.py$/d')"
    if [[ -n "$database_changes" ]]; then
        echo "[ERROR] fast mode detected database-related changes; rerun without --fast:" >&2
        echo "$database_changes" >&2
        exit 1
    fi
fi
litellm_runtime_changed=false
if [[ "$pre_git_full" != "$new_git_full" ]]; then
    litellm_runtime_changes="$(git -C "$PROJECT_ROOT" diff --name-only "$pre_git_full" "$new_git_full" -- \
        deploy/compose/production/compose.yml \
        deploy/compose/production/litellm-entrypoint.sh)"
    [[ -n "$litellm_runtime_changes" ]] && litellm_runtime_changed=true
fi
ok "$pre_git -> $new_git"

step "4/9 Pull immutable release images"
if $DO_PULL; then
    if $FAST; then
        compose pull backend frontend || die "application image pull failed; running services were not changed"
    else
        compose pull backend frontend litellm || die "image pull failed; running services were not changed"
    fi
else
    warn "image pull skipped (--no-pull); using images already available on the host"
fi

step "5/9 Run explicit MySQL migration"
if $FAST; then
    warn "fast mode: database migrations skipped (use the standard mode for schema changes)"
else
    stop_database_writers
    if ! wait_for_schema_lock_drain; then
        die "schema metadata locks remain; deployment stopped before migration without restoring the database"
    fi
    if ! bash "$MIGRATE_SCRIPT"; then
        warn "migration failed; database was not restored automatically"
        die "inspect the migration error, then run '$0 --recover-failed-migration' only if an explicit restore is required"
    fi

    post_migration_users="$(get_user_count)"
    post_migration_tables="$(get_table_count)"
    if (( post_migration_users < pre_users || post_migration_tables < pre_tables )); then
        die "database counts decreased during migration; database was not restored automatically"
    fi
    ok "migration verified: users=$post_migration_users tables=$post_migration_tables"
fi

step "6/9 Start updated containers"
if $FAST; then
    start_services=(backend frontend scheduler collector)
    # Most fast deployments leave the proxy untouched.  A changed LiteLLM
    # runtime wrapper/config mount must be recreated once to take effect.
    if $litellm_runtime_changed; then
        start_services=(litellm "${start_services[@]}")
    fi
else
    start_services=(mysql litellm backend frontend scheduler collector)
fi
if ! compose up -d "${start_services[@]}"; then
    die "container startup failed; database was not restored automatically"
fi

step "7/9 Health checks"
if ! wait_for_health; then
    die "services failed health checks; database was not restored automatically"
fi
curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/api/v1/daily-report/latest" >/dev/null 2>&1 \
    && warn "daily report endpoint unexpectedly allowed anonymous access" || true
ok "frontend and backend containers are healthy"

step "8/9 Login and database preservation"
login_payload="$(DEPLOY_ADMIN_USERNAME="$DEPLOY_ADMIN_USERNAME" DEPLOY_ADMIN_PASSWORD="$DEPLOY_ADMIN_PASSWORD" python3 -c 'import json,os; print(json.dumps({"username":os.environ["DEPLOY_ADMIN_USERNAME"],"password":os.environ["DEPLOY_ADMIN_PASSWORD"]}))')"
login_response="$(curl -fsS -X POST "http://127.0.0.1:${FRONTEND_PORT}/api/v1/auth/login" -H 'Content-Type: application/json' --data-binary "$login_payload")" \
    || die "admin login failed; database was not restored automatically"
echo "$login_response" | grep -q 'access_token' \
    || die "admin login response is invalid; database was not restored automatically"
post_users="$(get_user_count)"
post_tables="$(get_table_count)"
if (( post_users < pre_users || post_tables < pre_tables )); then
    die "post-deployment database counts decreased; database was not restored automatically"
fi
ok "login passed; users=$pre_users->$post_users tables=$pre_tables->$post_tables"
get_user_list | sed 's/^/  /'

step "9/9 Complete"
ok "deployment complete: $pre_git -> $new_git"
if $FAST; then
    ok "fast code-only deployment; verified backup retained at: $backup_file"
else
    ok "verified backup retained at: $backup_file"
fi

step "Post-deploy release image cleanup"
prune_superseded_dashboard_images
