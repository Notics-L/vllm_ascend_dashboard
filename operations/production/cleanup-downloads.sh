#!/usr/bin/env bash
# Daily download retention; never touches MySQL or analysis reports.
set -euo pipefail
volume="${DASHBOARD_BACKEND_VOLUME:-vllm_ascend_dashboard_backend_data}"
[[ "$volume" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || exit 1
root="$(docker volume inspect --format '{{.Mountpoint}}' "$volume")"
[[ "$root" == /var/lib/docker/volumes/"$volume"/_data ]] || exit 1
[[ "$(realpath -e "$root")" == "$root" ]] || exit 1
exec 9>/run/lock/dashboard-download-cleanup.lock
flock -n 9 || exit 0
evidence="$root/ci-evidence/runs"
if [[ -d "$evidence" && ! -L "$evidence" ]]; then
    exec 8>"$evidence/.analysis.lock"
    # A running analysis keeps a shared lock for both failed and last-good runs.
    if flock -n -x 8; then
    # Keep runs written today; discard all older local evidence.
    while IFS= read -r -d '' path; do
        [[ "${path##*/}" =~ ^[0-9]+$ && ! -L "$path" ]] || continue
        rm -rf -- "$path"
    done < <(find "$evidence" -mindepth 1 -maxdepth 1 -type d ! -newermt "$(date +%F)" -print0)
    flock -u 8
    fi
fi
coverage="$root/coverage"
if [[ -d "$coverage" && ! -L "$coverage" ]]; then
    latest=""
    for path in "$coverage"/*; do
        [[ -d "$path" && ! -L "$path" && "${path##*/}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || continue
        [[ "$path" > "$latest" ]] && latest="$path"
    done
    for path in "$coverage"/*; do
        [[ -d "$path" && ! -L "$path" && "${path##*/}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || continue
        [[ "$path" == "$latest" ]] || rm -rf -- "$path"
    done
fi
# Remove obsolete Dashboard releases only. Docker's no-force deletion protects
# images referenced by any running or stopped container (including rollback).
while IFS=$'\t' read -r repository tag image_id; do
    case "$repository" in
        ghcr.io/vllm-ascend/vllm_ascend_dashboard-backend|ghcr.io/vllm-ascend/vllm_ascend_dashboard-frontend|vllm_ascend_dashboard-backend|vllm_ascend_dashboard-frontend) ;;
        *) continue ;;
    esac
    [[ -n "$image_id" && "$tag" != "<none>" ]] || continue
    containers="$(docker ps -aq --filter "ancestor=$image_id")"
    [[ -z "$containers" ]] || continue
    if ! docker image rm "$repository:$tag"; then
        echo "[WARN] could not remove unused Dashboard image: $repository:$tag" >&2
    fi
done < <(docker image ls --format '{{.Repository}}\t{{.Tag}}\t{{.ID}}')
docker image prune -f
df -h /
