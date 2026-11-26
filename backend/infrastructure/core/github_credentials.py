"""Load credentials as handles for the audited GitHub clients."""

import os
from dataclasses import dataclass, field

from infrastructure.core.config import settings
from infrastructure.core.github_audit import CredentialSource
from infrastructure.core.security import decrypt_api_key

_CREDENTIAL_ENV_NAMES = {
    "GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_ACCESS_TOKEN", "GITHUB_API_TOKEN",
    "GITHUB_ACTIONS_REPORT_TOKEN",
}


def environment_without_github_credentials() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key.upper() not in _CREDENTIAL_ENV_NAMES}


@dataclass(frozen=True, slots=True)
class GitHubCredential:
    source: CredentialSource
    _github_token: str = field(repr=False)

    def __bool__(self) -> bool:
        return bool(self._github_token)

    @property
    def fine_grained(self) -> bool:
        return self._github_token.startswith("github_pat_")

    def validate(self) -> None:
        if not self._github_token.isascii() or not self._github_token.isprintable() or any(
            char.isspace() for char in self._github_token
        ):
            raise ValueError("GitHub Token 格式无效")


def global_github_credential(
    candidate: str | None = None, *, prefer_environment: bool = False,
) -> GitHubCredential:
    if candidate is None:
        candidate = (os.getenv("GITHUB_TOKEN", "") if prefer_environment else "") or settings.GITHUB_TOKEN
    return GitHubCredential("global", candidate)


def global_github_configured(*, prefer_environment: bool = False) -> bool:
    return bool(global_github_credential(prefer_environment=prefer_environment))


def daily_github_credential(
    config: dict[str, str | None], *, candidate: str | None = None,
) -> GitHubCredential:
    return GitHubCredential("daily", candidate or decrypt_api_key(config.get("github_token") or ""))
