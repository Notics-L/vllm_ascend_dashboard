"""Workflow Actions 请求模型。"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_WORKFLOW_PATH_RE = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")


def normalize_workflow_path(value: str) -> str:
    value = value.strip()
    if not _WORKFLOW_PATH_RE.fullmatch(value):
        raise ValueError("工作流路径必须类似 .github/workflows/example.yaml")
    return value


def normalize_workflow_paths(values: list[str]) -> list[str]:
    paths: list[str] = []
    for value in values:
        path = normalize_workflow_path(value)
        if path not in paths:
            paths.append(path)
    return paths


WorkflowAction = Literal["dispatch", "rerun", "rerun-failed", "cancel", "force-cancel"]


class WorkflowActionRequest(BaseModel):
    action: WorkflowAction
    workflow_path: str = Field(min_length=1, max_length=500)
    ref: str = Field(default="main", min_length=1, max_length=200)
    inputs_yaml: str = ""
    run_id: int | None = Field(default=None, gt=0)

    @field_validator("workflow_path")
    @classmethod
    def validate_workflow_path(cls, value: str) -> str:
        return normalize_workflow_path(value)

    @field_validator("ref")
    @classmethod
    def strip_ref(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("ref 不能为空")
        return value

    @model_validator(mode="after")
    def validate_run_id(self):
        if self.action != "dispatch" and self.run_id is None:
            raise ValueError("重跑和取消操作必须提供 run_id")
        return self


class WorkflowActionCredentialUpdate(BaseModel):
    token: str = Field(min_length=10, max_length=500)


class WorkflowActionPermissionCreate(BaseModel):
    usernames: list[str] = Field(min_length=1, max_length=50)
    workflow_paths: list[str] = Field(default_factory=list, max_length=50)
    schedule_permission: bool = False

    @field_validator("usernames")
    @classmethod
    def validate_usernames(cls, values: list[str]) -> list[str]:
        usernames: list[str] = []
        for value in values:
            username = value.strip()
            if not username:
                raise ValueError("用户名不能为空")
            if len(username) > 50:
                raise ValueError("用户名不能超过 50 个字符")
            if username not in usernames:
                usernames.append(username)
        return usernames

    @field_validator("workflow_paths")
    @classmethod
    def validate_workflow_paths(cls, values: list[str]) -> list[str]:
        return normalize_workflow_paths(values)

    @model_validator(mode="after")
    def validate_permission_present(self):
        if not self.workflow_paths and not self.schedule_permission:
            raise ValueError("请至少添加一个工作流路径或开启定时任务权限")
        return self


class WorkflowActionPermissionUpdate(BaseModel):
    workflow_paths: list[str] = Field(default_factory=list, max_length=50)
    schedule_permission: bool | None = None

    @field_validator("workflow_paths")
    @classmethod
    def validate_workflow_paths(cls, values: list[str]) -> list[str]:
        return normalize_workflow_paths(values)
