from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NpuOccupancyDimension = Literal["pool", "project"]
NpuOccupancyGroupBy = Literal["pool", "project"]


class NpuOccupancyMetrics(BaseModel):
    peak: int = 0
    average: float = 0
    latest: int = 0
    capacity: int | None = None


class NpuOccupancySeriesPoint(BaseModel):
    timestamp: datetime
    occupied_cards: int


class NpuOccupancyTrendResponse(BaseModel):
    dimension: NpuOccupancyDimension
    names: list[str] = Field(default_factory=list)
    start: datetime
    end: datetime
    sample_interval_seconds: int = 120
    metrics: NpuOccupancyMetrics
    series: list[NpuOccupancySeriesPoint] = Field(default_factory=list)
    snapshot_at: datetime
    data_status: Literal["ready"] = "ready"


class NpuOccupancyOption(BaseModel):
    name: str
    label: str
    capacity: int | None = None
    configured: bool = True


class NpuOccupancyOptionsResponse(BaseModel):
    dimension: NpuOccupancyDimension
    options: list[NpuOccupancyOption] = Field(default_factory=list)


class NpuOccupancyTask(BaseModel):
    env_id: str
    organization: str | None = None
    repository: str | None = None
    project: str
    pool: str
    workflow: str
    job_id: str | None = None
    job_name: str
    workflow_run_id: int | None = None
    workflow_name: str | None = None
    workflow_file: str | None = None
    run_display_title: str | None = None
    run_number: int | None = None
    run_display_name: str = "未知 Run"
    workflow_ref: str | None = None
    pr_number: int | None = None
    event: str | None = None
    trigger_scope: str | None = None
    run_url: str | None = None
    github_url: str | None = None
    workflow_config_match: bool = False
    cards: int
    node_ip: str | None = None
    npu_list: str | None = None
    status: str
    created_at: datetime
    effective_end: datetime


class NpuOccupancyRun(BaseModel):
    key: str
    workflow_run_id: int | None = None
    workflow_name: str | None = None
    workflow_file: str | None = None
    run_display_title: str | None = None
    run_number: int | None = None
    run_display_name: str
    workflow_ref: str | None = None
    pr_number: int | None = None
    event: str | None = None
    trigger_scope: str | None = None
    run_url: str | None = None
    github_url: str | None = None
    workflow_config_match: bool = False
    cards: int
    task_count: int
    created_at: datetime
    effective_end: datetime
    tasks: list[NpuOccupancyTask] = Field(default_factory=list)


class NpuOccupancyDetailGroup(BaseModel):
    name: str
    occupied_cards: int
    task_count: int
    runs: list[NpuOccupancyRun] = Field(default_factory=list)


class NpuOccupancyDetailsResponse(BaseModel):
    timestamp: datetime
    dimension: NpuOccupancyDimension
    names: list[str] = Field(default_factory=list)
    group_by: NpuOccupancyGroupBy
    groups: list[NpuOccupancyDetailGroup] = Field(default_factory=list)
    snapshot_at: datetime


class NpuOccupancyAnalysisGroup(BaseModel):
    name: str
    peak_cards: int
    average_cards: float
    task_count: int
    runs: list[NpuOccupancyRun] = Field(default_factory=list)


class NpuOccupancyAnalysisResponse(BaseModel):
    dimension: NpuOccupancyDimension
    names: list[str] = Field(default_factory=list)
    start: datetime
    end: datetime
    sample_interval_seconds: int = 120
    groups: list[NpuOccupancyAnalysisGroup] = Field(default_factory=list)
    snapshot_at: datetime
