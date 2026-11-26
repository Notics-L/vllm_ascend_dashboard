"""
GitHub Local Cache Service
Manages a local clone of the vllm-ascend repository for efficient data access
"""
import logging
import os
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from infrastructure.clients.git_mirror import GitMirrorRepository
from infrastructure.clients.github_http import github_git_environment
from infrastructure.core.config import settings

logger = logging.getLogger(__name__)

# Docker mirror configurations
DOCKER_MIRRORS = {
    "quay.io": "quay.io/ascend/vllm-ascend",
    "m.daocloud.io": "m.daocloud.io/quay.io/ascend/vllm-ascend",
    "quay.nju.edu.cn": "quay.nju.edu.cn/ascend/vllm-ascend",
}

# GitHub mirror configurations for better network reliability
GITHUB_MIRRORS = [
    "https://github.com/{owner}/{repo}.git",
    "https://ghproxy.com/https://github.com/{owner}/{repo}.git",
    "https://github.moeyy.xyz/https://github.com/{owner}/{repo}.git",
]


class GitHubLocalCache:
    """GitHub 本地缓存服务"""

    def __init__(self, cache_dir: str | None = None, owner: str | None = None, repo: str | None = None):
        # 支持自定义仓库，默认使用配置的 vllm-ascend 仓库
        self.owner = owner or settings.GITHUB_OWNER or "vllm-project"
        self.repo = repo or settings.GITHUB_REPO or "vllm-ascend"
        self.repo_name = f"{self.owner}_{self.repo}"
        self.mirror: GitMirrorRepository | None = None
        # Explicit paths retain legacy checkout semantics for local tooling.
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            data_root = Path(settings.GITHUB_CACHE_DIR or settings.DATA_DIR)
            if not data_root.is_absolute():
                data_root = Path.cwd() / data_root
            data_root = data_root.resolve()
            self.mirror = GitMirrorRepository(
                storage_root=data_root,
                owner=self.owner,
                repo=self.repo,
                clone_url=f"https://github.com/{self.owner}/{self.repo}.git",
                legacy_worktree=data_root / "repos" / self.repo_name,
            )
            self.cache_dir = self.mirror.main_worktree
        self.clone_url = f"https://github.com/{self.owner}/{self.repo}.git"
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        self.cache_dir.parent.mkdir(parents=True, exist_ok=True)
        if self.mirror is None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _is_repo_cloned(self) -> bool:
        """检查仓库是否已克隆"""
        if self.mirror is not None:
            return self.mirror.is_ready() and (self.cache_dir / ".git").exists()
        git_dir = self.cache_dir / ".git"
        return git_dir.exists()

    def _get_git_env(self) -> dict:
        """获取 git 命令的环境变量，包括代理配置"""
        env = github_git_environment()

        # 尝试从 git config 读取代理配置
        try:
            result = subprocess.run(
                ["git", "config", "--global", "http.proxy"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                proxy_url = result.stdout.strip()
                env["http_proxy"] = proxy_url
                env["https_proxy"] = proxy_url
                logger.debug(f"Using git proxy: {proxy_url}")
        except Exception as e:
            logger.debug(f"Failed to read git proxy config: {e}")

        # 也检查系统环境变量
        for proxy_var in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            if proxy_var in os.environ:
                env[proxy_var] = os.environ[proxy_var]
                logger.debug(f"Using proxy from env: {proxy_var}")

        return env

    def clone(self) -> bool:
        """克隆仓库到本地（包含完整历史和 tags）"""
        if self.mirror is not None:
            return self.mirror.bootstrap()
        if self._is_repo_cloned():
            logger.info(f"Repository already cloned at {self.cache_dir}")
            return True

        try:
            logger.info(f"Cloning {self.clone_url} to {self.cache_dir}")
            # 完整克隆，包含所有历史和 tags
            # --no-single-branch: 获取所有分支
            # --tags: 获取所有 tags
            # --filter=blob:none: 按需获取文件内容，加快克隆速度
            env = self._get_git_env()
            result = subprocess.run(
                ["git", "clone", "--no-single-branch", "--tags", "--filter=blob:none", self.clone_url, str(self.cache_dir)],
                check=True,
                capture_output=True,
                timeout=900,  # 15 minutes timeout for full clone
                env=env,
            )
            logger.info(f"Repository cloned successfully to {self.cache_dir}")
            logger.debug(f"Clone output: {result.stdout.decode()[:500] if result.stdout else 'none'}")
            return True
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else str(e)
            stdout_msg = e.stdout.decode() if e.stdout else 'none'
            logger.error(f"Failed to clone repository: {stderr_msg}")
            logger.error(f"Clone stdout: {stdout_msg}")
            # 如果是网络问题，尝试浅克隆作为备选
            if "RPC failed" in stderr_msg or "early EOF" in stderr_msg or "disconnect" in stderr_msg or "fatal:" in stderr_msg:
                logger.warning("Network error detected, trying shallow clone as fallback...")
                return self._shallow_clone()
            return False
        except subprocess.TimeoutExpired:
            logger.error("Repository clone timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to clone repository: {str(e)}")
            return False

    def _shallow_clone(self) -> bool:
        """浅克隆作为网络问题时的备选方案"""
        try:
            logger.info(f"Attempting shallow clone: {self.clone_url}")
            env = self._get_git_env()
            # 先进行最基础的浅克隆
            subprocess.run(
                ["git", "clone", "--depth", "1", self.clone_url, str(self.cache_dir)],
                check=True,
                capture_output=True,
                timeout=300,
                env=env,
            )
            # 然后逐步获取更多信息
            logger.info("Fetching more history...")
            subprocess.run(
                ["git", "fetch", "--depth", "100", "origin", "main"],
                cwd=str(self.cache_dir),
                capture_output=True,
                timeout=300,
                env=env,
            )
            # 获取 tags
            logger.info("Fetching tags...")
            subprocess.run(
                ["git", "fetch", "--tags"],
                cwd=str(self.cache_dir),
                capture_output=True,
                timeout=300,
                env=env,
            )
            logger.info("Shallow clone with history and tags completed")
            return True
        except Exception as e:
            logger.error(f"Shallow clone also failed: {str(e)}")
            return False

    def _cleanup_git_locks(self):
        """清理 git 锁文件"""
        git_dir = self.cache_dir / ".git"
        lock_files = [
            git_dir / "index.lock",
            git_dir / "shallow.lock",
            git_dir / "HEAD.lock",
            git_dir / "config.lock",
        ]

        for lock_file in lock_files:
            if lock_file.exists():
                try:
                    lock_file.unlink()
                    logger.info(f"Cleaned up stale lock file: {lock_file}")
                except Exception as e:
                    logger.warning(f"Failed to remove lock file {lock_file}: {e}")

    def pull(self) -> bool:
        """拉取最新代码和 tags"""
        if self.mirror is not None:
            return self.mirror.refresh()
        if not self._is_repo_cloned():
            logger.info("Repository not cloned, attempting to clone")
            return self.clone()

        # 清理可能残留的锁文件
        self._cleanup_git_locks()

        try:
            env = self._get_git_env()
            logger.info(f"Pulling latest changes from {self.clone_url}")

            # 先 reset 到远程状态，避免本地修改冲突
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                cwd=str(self.cache_dir),
                capture_output=True,
                timeout=30,
                env=env,
            )

            # 清理未跟踪的文件
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(self.cache_dir),
                capture_output=True,
                timeout=30,
                env=env,
            )

            # 拉取最新代码
            # Upstream release tags are occasionally recreated. Force-sync
            # them before pulling so Git does not abort with
            # "would clobber existing tag" and invalidate the analysis cache.
            subprocess.run(
                ["git", "fetch", "origin", "--tags", "--force", "--prune"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=120,
                env=env,
            )
            subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=120,  # 2 minutes timeout
                env=env,
            )
            # 同时 fetch 新的 tags
            subprocess.run(
                ["git", "fetch", "origin", "--tags", "--force", "--prune"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=60,
                env=env,
            )
            logger.info("Repository pulled successfully")
            return True
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else str(e)
            logger.error(f"Failed to pull repository: {stderr_msg}")
            if "divergent branches" in stderr_msg or "need to specify how to reconcile" in stderr_msg.lower():
                return self._reset_to_origin_main(env)
            # 如果是锁文件问题，尝试清理后重试
            if "lock" in stderr_msg.lower() or "another git process" in stderr_msg.lower():
                logger.warning("Lock file detected, cleaning up and retrying...")
                self._cleanup_git_locks()
                try:
                    # 重试一次
                    subprocess.run(
                        ["git", "fetch", "origin", "--tags", "--force", "--prune"],
                        cwd=str(self.cache_dir),
                        check=True,
                        capture_output=True,
                        timeout=120,
                        env=env,
                    )
                    subprocess.run(
                        ["git", "pull", "origin", "main"],
                        cwd=str(self.cache_dir),
                        check=True,
                        capture_output=True,
                        timeout=120,
                        env=env,
                    )
                    subprocess.run(
                        ["git", "fetch", "origin", "--tags", "--force", "--prune"],
                        cwd=str(self.cache_dir),
                        check=True,
                        capture_output=True,
                        timeout=60,
                        env=env,
                    )
                    logger.info("Repository pulled successfully after lock cleanup")
                    return True
                except Exception as retry_err:
                    logger.error(f"Retry also failed: {retry_err}")
            return False
        except subprocess.TimeoutExpired:
            logger.error("Repository pull timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to pull repository: {str(e)}")
            return False

    def _reset_to_origin_main(self, env: dict) -> bool:
        """Reset a divergent local cache back to origin/main.

        These worktrees are disposable analysis caches. When Git refuses to
        pull because local and remote main diverged, prefer a deterministic
        reset over an interactive merge/rebase policy.
        """
        try:
            logger.info("Resetting divergent repository cache: %s", self.cache_dir)
            subprocess.run(
                ["git", "fetch", "origin", "main", "--tags", "--force", "--prune"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=180,
                env=env,
            )
            subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=60,
                env=env,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=60,
                env=env,
            )
            logger.info("Repository cache reset to origin/main successfully")
            return True
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else str(e)
            logger.error("Failed to reset repository cache: %s", stderr_msg[:500])
            return False
        except Exception as e:
            logger.error("Failed to reset repository cache: %s", e)
            return False

    def fetch_full_history(self) -> bool:
        """获取完整的 git 历史和 tags（用于修复浅克隆）"""
        if self.mirror is not None:
            return self.mirror.is_ready() and not (self.mirror.mirror_dir / "shallow").exists()
        if not self._is_repo_cloned():
            return self.clone()

        try:
            env = self._get_git_env()
            # 先清理可能的 lock 文件
            lock_file = self.cache_dir / ".git" / "shallow.lock"
            if lock_file.exists():
                logger.info(f"Removing stale lock file: {lock_file}")
                lock_file.unlink()

            # 检查是否是浅克隆
            shallow_file = self.cache_dir / ".git" / "shallow"
            if not shallow_file.exists():
                logger.info("Repository is already a full clone")
                # 只需要 fetch tags
                subprocess.run(
                    ["git", "fetch", "--tags", "--force"],
                    cwd=str(self.cache_dir),
                    capture_output=True,
                    timeout=120,
                    env=env,
                )
                return True

            logger.info("Fetching full git history and tags...")
            # 取消浅克隆限制，获取完整历史
            result = subprocess.run(
                ["git", "fetch", "--unshallow"],
                cwd=str(self.cache_dir),
                capture_output=True,
                timeout=600,
                env=env,
            )
            if result.returncode != 0:
                logger.error(f"Failed to unshallow: {result.stderr.decode() if result.stderr else 'unknown error'}")
                return False

            # 获取所有 tags
            subprocess.run(
                ["git", "fetch", "--tags", "--force"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=120,
                env=env,
            )
            logger.info("Full history and tags fetched successfully")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to fetch full history: {e.stderr.decode() if e.stderr else str(e)}")
            return False
        except subprocess.TimeoutExpired:
            logger.error("Fetch full history timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to fetch full history: {str(e)}")
            return False

    def _run_git_command(self, args: list[str]) -> str | None:
        """运行 git 命令并返回输出"""
        if not self._is_repo_cloned():
            if not self.clone():
                return None

        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=60,
            )
            return result.stdout.decode().strip()
        except Exception as e:
            logger.error(f"Git command failed: {str(e)}")
            return None

    def get_file_content(self, file_path: str, branch: str = "main") -> str | None:
        """获取指定分支的文件内容"""
        if not self._is_repo_cloned():
            if not self.clone():
                return None

        if self.mirror is not None:
            return self.mirror.read_file(branch, file_path)

        try:
            result = subprocess.run(
                ["git", "show", f"{branch}:{file_path}"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=30,
            )
            return result.stdout.decode()
        except Exception as e:
            logger.error(f"Failed to get file content: {str(e)}")
            return None

    def ensure_ref(self, ref: str, *, branch: str | None = None) -> bool:
        """Ensure one commit/ref exists without refreshing every branch."""
        if self.mirror is not None:
            return self.mirror.ensure_ref(ref, branch=branch)
        if not self._is_repo_cloned() and not self.clone():
            return False
        verify = ["git", "-C", str(self.cache_dir), "rev-parse", "--verify", f"{ref}^{{commit}}"]
        if subprocess.run(verify, capture_output=True, timeout=30).returncode == 0:
            return True
        if branch:
            subprocess.run(
                ["git", "-C", str(self.cache_dir), "fetch", "origin", branch],
                capture_output=True,
                timeout=300,
                env=self._get_git_env(),
            )
        return subprocess.run(verify, capture_output=True, timeout=30).returncode == 0

    def get_remote_branches(self, prefix: str = "") -> list[str]:
        if not self._is_repo_cloned() and not self.clone():
            return []
        if self.mirror is not None:
            return self.mirror.remote_branches(prefix)
        result = subprocess.run(
            ["git", "-C", str(self.cache_dir), "branch", "-r", "--format=%(refname:strip=3)"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return sorted(
            branch for branch in result.stdout.splitlines()
            if branch.startswith(prefix) and branch != "HEAD"
        )

    def get_worktree(self, ref: str = "main", *, purpose: str = "main") -> Path | None:
        """Materialize a stable main tree or an isolated detached worktree."""
        if not self._is_repo_cloned() and not self.clone():
            return None
        if self.mirror is None:
            return self.cache_dir
        if purpose == "main" and ref in {"main", "origin/main"}:
            return self.mirror.ensure_main_worktree(refresh=False)
        return self.mirror.isolated_worktree(ref, purpose)

    def get_all_tags(self) -> list[str]:
        """获取所有 tags 列表"""
        if not self._is_repo_cloned():
            if not self.clone():
                return []

        try:
            result = subprocess.run(
                ["git", "tag", "-l", "--sort=-creatordate"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=30,
            )
            tags = result.stdout.decode().strip().split("\n")
            return [tag for tag in tags if tag]
        except Exception as e:
            logger.error(f"Failed to get tags: {str(e)}")
            return []

    def get_releases(self, recommended_only: bool = False) -> list[dict[str, Any]]:
        """获取所有 release 标签信息

        Args:
            recommended_only: 如果为 True，只返回推荐版本（最新 2 个 stable + 最新 1 个 pre-release）
        """
        if not self._is_repo_cloned():
            if not self.clone():
                return []

        try:
            # Get all tags sorted by version
            result = subprocess.run(
                ["git", "tag", "-l", "--sort=-creatordate"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=30,
            )
            tags = result.stdout.decode().strip().split("\n")

            releases = []
            for tag in tags:
                if not tag:
                    continue

                # Get tag date
                date_result = subprocess.run(
                    ["git", "log", "-1", "--format=%ai", tag],
                    cwd=str(self.cache_dir),
                    capture_output=True,
                    timeout=30,
                )
                date_str = date_result.stdout.decode().strip() if date_result.returncode == 0 else ""

                # Determine if stable (no pre-release markers)
                is_stable = not any(marker in tag.lower() for marker in ["rc", "beta", "alpha", "dev"])

                # Generate docker commands
                docker_commands = {}
                for mirror_name, mirror_path in DOCKER_MIRRORS.items():
                    docker_commands[mirror_name] = f"docker pull {mirror_path}:{tag}"

                releases.append({
                    "version": tag,
                    "is_stable": is_stable,
                    "published_at": date_str,
                    "docker_commands": docker_commands,
                })

            # If recommended_only, filter to latest 1 stable + 1 pre-release
            if recommended_only:
                stable_releases = [r for r in releases if r["is_stable"]]
                prerelease_releases = [r for r in releases if not r["is_stable"]]

                # Take latest 1 stable and 1 pre-release
                recommended = stable_releases[:1] + prerelease_releases[:1]
                # Sort by date again
                recommended.sort(key=lambda x: x["published_at"], reverse=True)
                return recommended

            return releases
        except Exception as e:
            logger.error(f"Failed to get releases: {str(e)}")
            return []

    @staticmethod
    def _parse_mkdocs_extra(content: str) -> dict[str, str]:
        """Parse version values from the repository's top-level ``extra`` block."""
        version_keys = {
            "vllm_version",
            "vllm_ascend_version",
            "pip_vllm_ascend_version",
            "pip_vllm_version",
            "cann_image_tag",
            "main_python_version",
            "main_cann_version",
            "main_pytorch_torch_npu_version",
            "main_triton_ascend_version",
        }
        versions: dict[str, str] = {}
        in_extra = False
        extra_indent: int | None = None

        for line in content.splitlines():
            stripped = line.strip()
            if not in_extra:
                if re.fullmatch(r"extra:\s*", stripped):
                    in_extra = True
                continue
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())
            if extra_indent is None:
                extra_indent = indent
            if indent < extra_indent:
                break
            if indent != extra_indent:
                continue

            match = re.match(r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.*)$", stripped)
            if not match or match.group("key") not in version_keys:
                continue

            value = match.group("value").split(" #", 1)[0].strip()
            if value.startswith("!ENV"):
                value = value.removeprefix("!ENV").strip().strip("[]").split(",")[-1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                versions[match.group("key")] = value

        return versions

    def get_conf_py_versions(self) -> dict[str, str] | None:
        """Get main-branch version metadata from current and legacy layouts."""
        mkdocs_content = self.get_file_content("mkdocs.yml", "main")
        if mkdocs_content:
            versions = self._parse_mkdocs_extra(mkdocs_content)
            for key, path in {
                "main_vllm_commit": ".github/vllm-main-verified.commit",
                "main_vllm_tag": ".github/vllm-release-tag.commit",
            }.items():
                value = self.get_file_content(path, "main")
                if value and value.strip():
                    versions[key] = value.strip()
            if versions:
                return versions

        return self._get_legacy_conf_py_versions()

    def _get_legacy_conf_py_versions(self) -> dict[str, str] | None:
        """从 conf.py 获取 main 分支的 vllm 版本信息"""
        content = self.get_file_content("docs/source/conf.py", "main")
        if not content:
            return None

        versions = {}
        in_substitutions = False

        for line in content.split("\n"):
            if "myst_substitutions" in line:
                in_substitutions = True
                continue

            if in_substitutions:
                if line.strip().startswith("}"):
                    break

                # Parse version variables in myst_substitutions dict
                # Looking for patterns like: "vllm_version": "v0.17.0",
                # or: "main_vllm_tag": "v0.18.0",
                if ":" in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        key = parts[0].strip().strip('"\'').strip()
                        # Remove whitespace, trailing comma, then quotes
                        value = parts[1].strip().rstrip(',').strip('"\'')
                        if key and value:
                            versions[key] = value

        return versions

    def get_commits_between_tags(self, base_tag: str, head_tag: str) -> list[dict[str, Any]]:
        """获取两个 tag 之间的 commits

        Args:
            base_tag: 基准 tag（较旧的版本）
            head_tag: 目标 tag（较新的版本）
        """
        if not self._is_repo_cloned():
            if not self.clone():
                return []

        try:
            # Handle "main" branch as a special case
            base_ref = base_tag if base_tag != "main" else "origin/main"
            head_ref = head_tag if head_tag != "main" else "origin/main"

            # If either is main, fetch latest main branch
            if self.mirror is None and (base_tag == "main" or head_tag == "main"):
                env = self._get_git_env()
                subprocess.run(
                    ["git", "fetch", "origin", "main"],
                    cwd=str(self.cache_dir),
                    capture_output=True,
                    timeout=60,
                    env=env,
                )

            # Auto-detect and swap if base is newer than head
            # Check if base_ref is an ancestor of head_ref
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", base_ref, head_ref],
                cwd=str(self.cache_dir),
                capture_output=True,
                timeout=30,
            )

            # If base is NOT an ancestor of head, swap them
            if result.returncode != 0:
                logger.info(f"Swapping refs: {base_tag} is newer than {head_tag}")
                base_ref, head_ref = head_ref, base_ref
                base_tag, head_tag = head_tag, base_tag

            # Use %x00 (null byte) as commit separator and %x1f as field separator
            result = subprocess.run(
                ["git", "log", f"{base_ref}..{head_ref}", "--pretty=format:%H%x1f%s%x1f%b%x1f%an%x1f%ae%x1f%aI%x00"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=60,
            )

            commits = []
            # Split by null byte to separate commits (handles multi-line commit messages)
            output = result.stdout.decode('utf-8', errors='replace')
            for commit_block in output.strip().split("\x00"):
                if not commit_block.strip():
                    continue

                parts = commit_block.split("\x1f", 6)
                if len(parts) < 5:
                    continue

                sha = parts[0]
                title = parts[1]
                message = parts[2] if len(parts) > 2 else ""
                author_name = parts[3] if len(parts) > 3 else ""
                author_email = parts[4] if len(parts) > 4 else ""
                date = parts[5] if len(parts) > 5 else ""

                # Categorize commit
                category = self._categorize_commit(title)

                # Extract PR number from title or message
                pr_number = self._extract_pr_number(title + " " + message)

                commits.append({
                    "sha": sha,
                    "title": title,
                    "message": message,
                    "author": f"{author_name} <{author_email}>" if author_email else author_name,
                    "date": date,
                    "category": category,
                    "pr_number": pr_number,
                })

            return commits
        except Exception as e:
            logger.error(f"Failed to get commits between tags: {str(e)}")
            return []

    def _categorize_commit(self, title: str) -> str:
        """根据 commit title 分类"""
        title_lower = title.lower()

        if "[bugfix]" in title_lower or "[fix]" in title_lower:
            return "BugFix"
        elif "[feature]" in title_lower or "[feat]" in title_lower:
            return "Feature"
        elif "[performance]" in title_lower or "[perf]" in title_lower:
            return "Performance"
        elif "[refactor]" in title_lower:
            return "Refactor"
        elif "[doc]" in title_lower:
            return "Doc"
        elif "[test]" in title_lower or "[unittest]" in title_lower or "[e2e]" in title_lower:
            return "Test"
        elif "[ci]" in title_lower:
            return "CI"
        elif "[misc]" in title_lower:
            return "Misc"
        else:
            return "Misc"

    def _extract_pr_number(self, text: str) -> int | None:
        """从 commit 信息中提取 PR 号"""
        import re

        # Match patterns like #123, PR #123, p123
        patterns = [
            r"#(\d+)",
            r"PR\s*#?(\d+)",
            r"p(\d{4,})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))

        return None

    def get_latest_pr_commits(self, pr_number: int) -> list[dict[str, Any]]:
        """获取指定 PR 的最新 commits"""
        if not self._is_repo_cloned():
            if not self.clone():
                return []

        try:
            # Fetch PR refs
            subprocess.run(
                ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=60,
                env=self._get_git_env(),
            )

            result = subprocess.run(
                ["git", "log", f"pr-{pr_number}", "--pretty=format:%H|%s|%b|%an|%ae|%aI", "-5"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=30,
            )

            commits = []
            for line in result.stdout.decode().strip().split("\n"):
                if not line:
                    continue

                parts = line.split("|", 5)
                if len(parts) < 5:
                    continue

                sha, title, message, author_name, author_email, date = parts[0], parts[1], parts[2] if len(parts) > 2 else "", parts[3] if len(parts) > 3 else "", parts[4] if len(parts) > 4 else ""

                commits.append({
                    "sha": sha,
                    "title": title,
                    "message": message,
                    "author": f"{author_name} <{author_email}>" if author_email else author_name,
                    "date": date,
                })

            return commits
        except Exception as e:
            logger.error(f"Failed to get PR commits: {str(e)}")
            return []

    def get_commits_by_date_range(
        self,
        start_time: datetime,
        end_time: datetime
    ) -> list[dict[str, Any]]:
        """
        获取指定时间范围内的 commits

        Args:
            start_time: 开始时间（需要带 timezone，建议使用 UTC）
            end_time: 结束时间（需要带 timezone，建议使用 UTC）

        Returns:
            commits 列表，每个元素包含 sha、message、author、committed_at 等信息
        """
        if not self._is_repo_cloned():
            if not self.clone():
                return []

        try:
            # Convert datetime to local time string for git log
            # Git interprets --since/--until in local timezone
            # Convert UTC time to local time for correct filtering
            start_local = start_time.astimezone()
            end_local = end_time.astimezone()
            start_str = start_local.strftime('%Y-%m-%d %H:%M:%S')
            end_str = end_local.strftime('%Y-%m-%d %H:%M:%S')

            # Use git log with date string filters
            # Use %x1f (unit separator) as field separator to avoid issues with | in commit messages
            env = self._get_git_env()
            result = subprocess.run(
                [
                    "git", "log",
                    "--since", start_str,
                    "--until", end_str,
                    "--pretty=format:%H%x1f%s%x1f%an%x1f%ae%x1f%aI"
                ],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=60,
                env=env,
            )

            commits = []
            output = result.stdout.decode('utf-8', errors='replace')
            for line in output.strip().split('\n'):
                if not line.strip():
                    continue

                parts = line.split('\x1f')
                if len(parts) >= 5:
                    commits.append({
                        "sha": parts[0],
                        "message": parts[1],
                        "author": parts[2],
                        "author_email": parts[3],
                        "committed_at": parts[4],
                    })

            logger.info(f"Found {len(commits)} commits between {start_str} and {end_str}")
            return commits

        except subprocess.CalledProcessError as e:
            logger.error(f"Git command failed: {e.stderr.decode() if e.stderr else str(e)}")
            return []
        except Exception as e:
            logger.error(f"Failed to get commits by date range: {str(e)}")
            return []

    def get_latest_commit(self) -> dict[str, Any] | None:
        """获取最新 commit 信息"""
        if not self._is_repo_cloned():
            if not self.clone():
                return None

        try:
            # 获取最新 commit 的 hash、subject、author、date
            result = subprocess.run(
                ["git", "log", "-1", "--pretty=format:%H|%s|%an|%ae|%aI"],
                cwd=str(self.cache_dir),
                check=True,
                capture_output=True,
                timeout=30,
            )
            output = result.stdout.decode().strip()
            if not output:
                return None

            parts = output.split('|')
            if len(parts) >= 5:
                return {
                    "sha": parts[0],
                    "subject": parts[1],
                    "author_name": parts[2],
                    "author_email": parts[3],
                    "date": parts[4],
                }
            return None
        except Exception as e:
            logger.error(f"Failed to get latest commit: {str(e)}")
            return None


# Singleton instances for different repos
_cache_instances: dict[str, GitHubLocalCache] = {}
_analysis_repos_lock = threading.Lock()


def get_github_cache() -> GitHubLocalCache:
    """获取 GitHub 本地缓存服务单例（默认 vllm-ascend 仓库）"""
    return get_github_cache_for_repo()


def get_github_cache_for_repo(owner: str | None = None, repo: str | None = None) -> GitHubLocalCache:
    """
    获取指定仓库的 GitHub 本地缓存服务实例

    Args:
        owner: GitHub 组织名，默认使用配置的 GITHUB_OWNER
        repo: 仓库名，默认使用配置的 GITHUB_REPO

    Returns:
        GitHubLocalCache 实例
    """
    actual_owner = owner or settings.GITHUB_OWNER or "vllm-project"
    actual_repo = repo or settings.GITHUB_REPO or "vllm-ascend"
    cache_key = f"{actual_owner}_{actual_repo}"

    global _cache_instances
    if cache_key not in _cache_instances:
        _cache_instances[cache_key] = GitHubLocalCache(owner=actual_owner, repo=actual_repo)
    return _cache_instances[cache_key]


def get_vllm_cache() -> GitHubLocalCache:
    """获取 vLLM 仓库的本地缓存实例"""
    return get_github_cache_for_repo(owner="vllm-project", repo="vllm")


def ensure_analysis_repos_ready(*, update: bool = True) -> dict[str, str]:
    """Ensure both repositories required by failure analysis are usable."""
    caches = {
        "vllm_ascend": get_github_cache(),
        "vllm": get_vllm_cache(),
    }
    # Concurrent analyses share these worktrees, so repository maintenance
    # must be serialized to avoid git index/ref lock conflicts.
    with _analysis_repos_lock:
        for name, cache in caches.items():
            was_cloned = cache._is_repo_cloned()
            if update:
                ready = cache.pull()
            elif was_cloned:
                # The daily refresh owns network access. Existing snapshots
                # are intentionally accepted without fetch/pull here.
                ready = True
            else:
                # Bootstrap a clean deployment on first use; subsequent
                # analyses remain fully local until the daily refresh.
                ready = cache.clone()
            if not ready or not cache._is_repo_cloned():
                raise RuntimeError(
                    f"Required analysis repository is unavailable: {name} "
                    f"({cache.clone_url})"
                )
            if update or not was_cloned:
                history_ready = cache.fetch_full_history()
            else:
                history_ready = not (cache.cache_dir / ".git" / "shallow").exists()
            if not history_ready:
                raise RuntimeError(
                    f"Required analysis repository has incomplete history: {name} "
                    f"({cache.cache_dir})"
                )
    return {name: str(cache.cache_dir.resolve()) for name, cache in caches.items()}


def ensure_repo_cloned() -> bool:
    """Check local readiness without performing network I/O in API callers."""
    cache = get_github_cache()
    return cache._is_repo_cloned()


__all__ = [
    "GitHubLocalCache",
    "ensure_analysis_repos_ready",
    "ensure_repo_cloned",
    "get_github_cache",
    "get_github_cache_for_repo",
    "get_vllm_cache",
]
