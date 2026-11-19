#!/bin/bash
# Canonical production database migration entrypoint.
# This script is intentionally explicit: it never creates or resets users.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="${DASHBOARD_COMPOSE_FILE:-$PROJECT_ROOT/deploy/compose/production/compose.yml}"
ENV_FILE="${DASHBOARD_ENV_FILE:-/etc/vllm-ascend-dashboard/production.env}"

[[ -f "$COMPOSE_FILE" ]] || { echo "[ERROR] compose file is missing: $COMPOSE_FILE" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "[ERROR] production env file is missing: $ENV_FILE" >&2; exit 1; }

# Load only for this short-lived migration process. The root password is not
# written to the repository or passed as a command-line argument.
set -a
source "$ENV_FILE"
set +a
[[ -n "${MYSQL_ROOT_PASSWORD:-}" ]] || {
    echo "[ERROR] MYSQL_ROOT_PASSWORD is required for schema migration" >&2
    exit 1
}

build_migration_database_url() {
    command -v python3 >/dev/null 2>&1 || {
        echo "[ERROR] python3 is required to encode the migration database URL" >&2
        exit 1
    }
    MIGRATION_DB_PASSWORD="$MYSQL_ROOT_PASSWORD" \
    MIGRATION_DB_NAME="${MYSQL_DATABASE:-vllm_dashboard}" \
    python3 - <<'PY'
import os
from urllib.parse import quote

password = quote(os.environ["MIGRATION_DB_PASSWORD"], safe="")
database = os.environ["MIGRATION_DB_NAME"]
print(f"mysql+aiomysql://root:{password}@mysql:3306/{database}")
PY
}

migration_database_url="${DASHBOARD_MIGRATION_DATABASE_URL:-}"
if [[ -z "$migration_database_url" ]]; then
    migration_database_url="$(build_migration_database_url)"
fi

compose_run() {
    DASHBOARD_RUNTIME_ENV_FILE="$ENV_FILE" \
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" --profile full \
        run --rm --no-deps -v "$PROJECT_ROOT/database:/app/database:ro" \
        -e "DATABASE_URL=$migration_database_url" \
        --entrypoint /opt/venv/bin/python backend "$@"
}

echo "[MIGRATE] Running canonical MySQL migration"
compose_run database/migrations/migrate.py

echo "[MIGRATE] Production database migration completed"
