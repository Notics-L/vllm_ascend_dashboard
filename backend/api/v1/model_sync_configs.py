"""
模型报告同步配置 API 路由
管理模型报告的自动同步规则
"""
import json

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from api.deps import CurrentAdminUser, DbSession
from contracts.schemas import (
    Message,
    ModelSyncConfigCreate,
    ModelSyncConfigResponse,
    ModelSyncConfigUpdate,
)
from infrastructure.clients.github_client import GitHubClient
from infrastructure.persistence.models import ModelSyncConfig
from model_sync.model_sync_service import ModelSyncService

router = APIRouter()


@router.get("", response_model=list[ModelSyncConfigResponse])
async def list_sync_configs(
    db: DbSession,
    enabled: bool | None = None
):
    """获取同步配置列表"""
    stmt = select(ModelSyncConfig)

    if enabled is not None:
        stmt = stmt.where(ModelSyncConfig.enabled == enabled)

    stmt = stmt.order_by(ModelSyncConfig.workflow_name.asc())
    result = await db.execute(stmt)
    configs = result.scalars().all()

    return list(configs)


@router.get("/{config_id}", response_model=ModelSyncConfigResponse)
async def get_sync_config(db: DbSession, config_id: int):
    """获取同步配置详情"""
    stmt = select(ModelSyncConfig).where(ModelSyncConfig.id == config_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="配置不存在",
        )

    return config


@router.post("", response_model=ModelSyncConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_sync_config(
    db: DbSession,
    config_data: ModelSyncConfigCreate,
    current_user: CurrentAdminUser
):
    """创建同步配置"""
    # 检查是否已存在
    stmt = select(ModelSyncConfig).where(
        ModelSyncConfig.workflow_file == config_data.workflow_file
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该 workflow 配置已存在",
        )

    # 准备数据，处理 JSON 字段
    config_dict = config_data.model_dump()

    # 将 file_patterns 列表转为 JSON 字符串
    if config_dict.get("file_patterns") is not None:
        config_dict["file_patterns"] = json.dumps(config_dict["file_patterns"])

    # 创建配置
    config = ModelSyncConfig(**config_dict)
    db.add(config)
    await db.commit()
    await db.refresh(config)

    return config


@router.put("/{config_id}", response_model=ModelSyncConfigResponse)
async def update_sync_config(
    db: DbSession,
    config_id: int,
    config_data: ModelSyncConfigUpdate,
    current_user: CurrentAdminUser
):
    """更新同步配置"""
    stmt = select(ModelSyncConfig).where(ModelSyncConfig.id == config_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="配置不存在",
        )

    # 更新字段
    update_data = config_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        # 将 file_patterns 列表转为 JSON 字符串
        if field == "file_patterns" and value is not None:
            value = json.dumps(value)
        setattr(config, field, value)

    await db.commit()
    await db.refresh(config)

    return config


@router.delete("/{config_id}", response_model=Message)
async def delete_sync_config(
    db: DbSession,
    config_id: int,
    current_user: CurrentAdminUser
):
    """删除同步配置"""
    stmt = select(ModelSyncConfig).where(ModelSyncConfig.id == config_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="配置不存在",
        )

    db.delete(config)
    await db.commit()

    return {"message": "配置已成功删除"}


@router.post("/{config_id}/sync", response_model=Message)
async def trigger_sync(
    db: DbSession,
    config_id: int,
    current_user: CurrentAdminUser
):
    """手动触发同步"""
    stmt = select(ModelSyncConfig).where(ModelSyncConfig.id == config_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="配置不存在",
        )

    # 创建 GitHub 客户端和同步服务
    github_client = GitHubClient()
    sync_service = ModelSyncService(db, github_client)

    try:
        # 执行同步
        collected = await sync_service.sync_from_workflow(config)
        return {
            "message": f"同步完成，采集到 {collected} 条报告"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"同步失败：{str(e)}",
        )


@router.post("/sync-all", response_model=Message)
async def sync_all_configs(
    db: DbSession,
    current_user: CurrentAdminUser
):
    """同步所有启用的配置"""
    github_client = GitHubClient()
    sync_service = ModelSyncService(db, github_client)

    try:
        total, collected = await sync_service.sync_all_enabled_configs()
        return {
            "message": f"同步完成，{total} 个配置，采集到 {collected} 条报告"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"同步失败：{str(e)}",
        )
