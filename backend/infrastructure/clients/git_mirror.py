"""Bare Git object cache with explicit-ref reads and isolated worktrees."""
from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path

from infrastructure.clients.github_http import github_git_environment

logger = logging.getLogger(__name__)


class GitMirrorRepository:
    """Persist Git objects without exposing a mutable shared checkout."""

    def __init__(
        self,
        *,
        storage_root: Path,
        owner: str,
        repo: str,
        clone_url: str,
        legacy_worktree: Path | None = None,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.repo_name = f"{owner}_{repo}"
        self.clone_url = clone_url
        self.mirror_dir = storage_root / "git-mirrors" / f"{self.repo_name}.git"
        self.worktrees_dir = storage_root / "worktrees" / self.repo_name
        self.main_worktree = self.worktrees_dir / "main"
        self.legacy_worktree = legacy_worktree
        self._lock = threading.RLock()
        self.mirror_dir.parent.mkdir(parents=True, exist_ok=True)
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _env() -> dict[str, str]:
        return github_git_environment()

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        timeout: int = 180,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            args,
            check=check,
            capture_output=True,
            timeout=timeout,
            env=self._env(),
        )

    def is_ready(self) -> bool:
        return (self.mirror_dir / "HEAD").is_file() and (
            self.mirror_dir / "objects"
        ).is_dir()

    def _configure_remote(self) -> None:
        self._run(
            ["git", "--git-dir", str(self.mirror_dir), "remote", "set-url", "origin", self.clone_url]
        )

    def _seed_remote_refs_from_heads(self) -> None:
        """Make refs from a bare/local clone available as origin/* offline."""
        result = self._run(
            [
                "git", "--git-dir", str(self.mirror_dir), "for-each-ref",
                "--format=%(refname:strip=2)%00%(objectname)", "refs/heads/",
            ],
            check=False,
            timeout=30,
        )
        for line in result.stdout.decode().splitlines():
            if "\x00" not in line:
                continue
            branch, commit = line.split("\x00", 1)
            self._run(
                [
                    "git", "--git-dir", str(self.mirror_dir), "update-ref",
                    f"refs/remotes/origin/{branch}", commit,
                ]
            )
        self._run(
            [
                "git", "--git-dir", str(self.mirror_dir), "config", "--replace-all",
                "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*",
            ]
        )

    def bootstrap(self) -> bool:
        """Create the bare cache, seeding it from the legacy clone when possible."""
        with self._lock:
            if self.is_ready():
                return self.ensure_main_worktree(refresh=False) is not None
            try:
                source = self.clone_url
                if self.legacy_worktree and (self.legacy_worktree / ".git").exists():
                    source = str(self.legacy_worktree.resolve())
                    logger.info("Seeding Git mirror from legacy cache %s", source)
                self._run(
                    ["git", "clone", "--bare", "--filter=blob:none", source, str(self.mirror_dir)],
                    timeout=900,
                )
                self._configure_remote()
                self._seed_remote_refs_from_heads()
                # Materialize remote-tracking refs consistently whether the
                # mirror came from GitHub or a legacy local checkout.
                try:
                    self._fetch_all()
                except Exception as exc:
                    logger.warning(
                        "Initial remote refresh failed for %s; using locally seeded refs: %s",
                        self.repo_name,
                        exc,
                    )
                return self.ensure_main_worktree(refresh=True) is not None
            except Exception as exc:
                logger.error("Failed to bootstrap Git mirror %s: %s", self.repo_name, exc)
                return False

    def _fetch_all(self) -> None:
        self._run(
            [
                "git", "--git-dir", str(self.mirror_dir), "fetch", "origin",
                "+refs/heads/*:refs/remotes/origin/*", "--tags", "--force", "--prune",
                "--filter=blob:none",
            ],
            timeout=900,
        )

    def refresh(self) -> bool:
        """Refresh all branches/tags once; existing worktrees remain isolated."""
        with self._lock:
            if not self.is_ready() and not self.bootstrap():
                return False
            try:
                self._configure_remote()
                self._fetch_all()
                return self.ensure_main_worktree(refresh=True) is not None
            except Exception as exc:
                logger.error("Failed to refresh Git mirror %s: %s", self.repo_name, exc)
                return False

    @staticmethod
    def normalize_ref(ref: str) -> str:
        value = str(ref or "").strip()
        if value == "main":
            return "refs/remotes/origin/main"
        if value.startswith("origin/"):
            return f"refs/remotes/{value}"
        if value.startswith("releases/"):
            return f"refs/remotes/origin/{value}"
        return value

    def resolve_ref(self, ref: str) -> str | None:
        if not self.is_ready():
            return None
        normalized = self.normalize_ref(ref)
        result = self._run(
            ["git", "--git-dir", str(self.mirror_dir), "rev-parse", "--verify", f"{normalized}^{{commit}}"],
            check=False,
            timeout=30,
        )
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def ensure_ref(self, ref: str, *, branch: str | None = None) -> bool:
        """Hydrate only a missing ref/SHA instead of refreshing the whole mirror."""
        if self.resolve_ref(ref):
            return True
        if not self.is_ready() and not self.bootstrap():
            return False
        with self._lock:
            try:
                if branch and re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
                    self._run(
                        [
                            "git", "--git-dir", str(self.mirror_dir), "fetch", "origin",
                            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
                            "--filter=blob:none",
                        ],
                        timeout=300,
                    )
                if self.resolve_ref(ref):
                    return True
                # GitHub permits this for reachable commits; failure remains a
                # normal logs-only degradation for the analysis caller.
                if re.fullmatch(r"[0-9a-fA-F]{7,64}", str(ref or "")):
                    self._run(
                        ["git", "--git-dir", str(self.mirror_dir), "fetch", "origin", str(ref)],
                        timeout=300,
                    )
                return self.resolve_ref(ref) is not None
            except Exception as exc:
                logger.warning("Unable to hydrate Git ref %s from %s: %s", ref, self.repo_name, exc)
                return False

    def read_file(self, ref: str, path: str) -> str | None:
        commit = self.resolve_ref(ref)
        if not commit:
            return None
        result = self._run(
            ["git", "--git-dir", str(self.mirror_dir), "show", f"{commit}:{path}"],
            check=False,
            timeout=60,
        )
        return result.stdout.decode("utf-8", errors="replace") if result.returncode == 0 else None

    def remote_branches(self, prefix: str = "") -> list[str]:
        result = self._run(
            [
                "git", "--git-dir", str(self.mirror_dir), "for-each-ref",
                "--format=%(refname:strip=3)", f"refs/remotes/origin/{prefix}",
            ],
            check=False,
            timeout=30,
        )
        return sorted(line.strip() for line in result.stdout.decode().splitlines() if line.strip())

    def ensure_main_worktree(self, *, refresh: bool) -> Path | None:
        commit = self.resolve_ref("main")
        if not commit:
            return None
        with self._lock:
            try:
                git_marker = self.main_worktree / ".git"
                if not git_marker.exists():
                    if self.main_worktree.exists() and any(self.main_worktree.iterdir()):
                        raise RuntimeError(f"worktree path is not empty: {self.main_worktree}")
                    self._run(
                        [
                            "git", "--git-dir", str(self.mirror_dir), "worktree", "add",
                            "--detach", str(self.main_worktree), commit,
                        ],
                        timeout=600,
                    )
                elif refresh:
                    self._run(["git", "-C", str(self.main_worktree), "reset", "--hard", commit])
                    self._run(["git", "-C", str(self.main_worktree), "clean", "-fd"])
                return self.main_worktree
            except Exception as exc:
                logger.error("Failed to materialize main worktree for %s: %s", self.repo_name, exc)
                return None

    def isolated_worktree(self, ref: str, purpose: str) -> Path | None:
        """Return a detached, immutable-by-convention worktree for one ref."""
        commit = self.resolve_ref(ref)
        if not commit:
            return None
        safe_purpose = re.sub(r"[^A-Za-z0-9_.-]+", "-", purpose).strip("-") or "task"
        path = self.worktrees_dir / safe_purpose
        with self._lock:
            if (path / ".git").exists():
                try:
                    self._run(["git", "-C", str(path), "reset", "--hard", commit])
                    self._run(["git", "-C", str(path), "clean", "-fd"])
                except Exception as exc:
                    logger.error("Failed to reset worktree %s to %s: %s", purpose, ref, exc)
                    return None
                return path
            try:
                self._run(
                    [
                        "git", "--git-dir", str(self.mirror_dir), "worktree", "add",
                        "--detach", str(path), commit,
                    ],
                    timeout=600,
                )
                return path
            except Exception as exc:
                logger.error("Failed to create worktree %s at %s: %s", purpose, ref, exc)
                return None
