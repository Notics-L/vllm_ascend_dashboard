import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = "C:/Program Files/Git/bin/bash.exe" if Path("C:/Program Files/Git/bin/bash.exe").exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="bash is required")
HELPER = (ROOT / "operations/production/backup-verification-cleanup.sh").read_text(encoding="utf-8")
NAMES = """
verify_prefix=vllm_dashboard_verify_20261119_030002
verify_dbs='vllm_dashboard_verify_20261119_030002_control_db vllm_dashboard_verify_20261119_030002_collection_db'
"""


def run_shell(body):
    return subprocess.run([BASH, "--noprofile", "--norc"], input=body, text=True,
                          capture_output=True, timeout=15)


def test_cleanup_uses_one_session_for_cross_database_foreign_keys():
    result = run_shell(HELPER + NAMES + """
mysql_root_exec() { echo "$1" >&2; [[ "$1" == SELECT* ]] && echo 0; return 0; }
cleanup_verify
""")
    assert result.returncode == 0, result.stderr
    statements = result.stderr.splitlines()[0]
    assert statements.startswith("SET SESSION FOREIGN_KEY_CHECKS=0;")
    assert statements.count("DROP DATABASE IF EXISTS") == 2
    assert statements.endswith("SET SESSION FOREIGN_KEY_CHECKS=1;")
    assert "SELECT COUNT(*)" in result.stderr


@pytest.mark.parametrize("status", [0, 17])
def test_exit_trap_always_cleans_and_preserves_original_failure(status):
    result = run_shell(HELPER + NAMES + f"""
mysql_root_exec() {{ echo CLEANUP >&2; [[ "$1" == SELECT* ]] && echo 0; return 0; }}
trap cleanup_verify_on_exit EXIT
exit {status}
""")
    assert result.returncode == status
    assert "CLEANUP" in result.stderr


def test_cleanup_failure_is_not_silenced():
    result = run_shell(HELPER + NAMES + """
mysql_root_exec() { return 1; }
trap cleanup_verify_on_exit EXIT
exit 0
""")
    assert result.returncode == 1
    assert "cleanup failed" in result.stderr


def test_cleanup_rejects_formal_database_name():
    result = run_shell(HELPER + NAMES + """
verify_dbs='vllm_dashboard'
mysql_root_exec() { echo MUST_NOT_RUN >&2; }
cleanup_verify
""")
    assert result.returncode != 0
    assert "MUST_NOT_RUN" not in result.stderr


def test_image_cleanup_preserves_container_images_and_other_repositories():
    source = (ROOT / "operations/production/cleanup-downloads.sh").read_text(encoding="utf-8")
    image_cleanup = source[source.index("# Remove obsolete"):]
    result = run_shell("""
set -eu
docker() {
    case "$1 $2" in
        'image ls') printf 'vllm_ascend_dashboard-backend\\told\\toldid\\nvllm_ascend_dashboard-backend\\tactive\\tactiveid\\nother-product\\told\\totherid\\n' ;;
        'ps -aq') [[ "$4" != ancestor=activeid ]] || echo container1; return 0 ;;
        'image rm') echo "REMOVE $3" ;;
        'image prune') echo 'PRUNE DANGLING' ;;
    esac
}
df() { :; }
""" + image_cleanup)
    assert result.returncode == 0, result.stderr
    assert "REMOVE vllm_ascend_dashboard-backend:old" in result.stdout
    assert "REMOVE vllm_ascend_dashboard-backend:active" not in result.stdout
    assert "REMOVE other-product" not in result.stdout
