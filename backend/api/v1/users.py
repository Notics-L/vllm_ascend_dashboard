"""
用户管理 API 路由
Phase 1: 基础版本，提供用户管理接口
"""
import logging
import re
from io import BytesIO
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import CurrentAdminUser, CurrentSuperAdminUser, DbSession
from contracts.schemas import (
    Message,
    PasswordReset,
    UserCreate,
    UserImportExecuteResponse,
    UserImportParseResponse,
    UserImportPreviewResponse,
    UserImportPreviewRow,
    UserImportRequest,
    UserResponse,
    UserUpdate,
)
from infrastructure.core.security import hash_password
from infrastructure.persistence.models import User

logger = logging.getLogger(__name__)

router = APIRouter()

ROLE_ALIASES = {
    "user": "user",
    "普通用户": "user",
    "普通": "user",
    "admin": "admin",
    "管理员": "admin",
    "super_admin": "super_admin",
    "超级管理员": "super_admin",
}

HUAWEI_EMAIL_DOMAINS = {"huawei.com", "h-partner.com", "h-partners.com"}


def _is_huawei_email(email: str) -> bool:
    """判断邮箱是否属于华为体系（@huawei.com / @h-partners.com）"""
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].strip().lower()
    return domain in HUAWEI_EMAIL_DOMAINS


def _cell_to_text(value: object) -> str:
    """把 Excel 单元格转换为去除首尾空白的字符串"""
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value).strip()


def _normalize_username(raw: str, lowercase: bool, remove_spaces: bool, remove_special: bool) -> str:
    """根据规则生成规范化后的用户名"""
    value = raw.strip()
    if remove_spaces:
        value = re.sub(r"\s+", "", value)
    if lowercase:
        value = value.lower()
    if remove_special:
        value = re.sub(r"[^a-zA-Z0-9_-]", "", value)
    return value.strip("-_")[:45]


async def _build_import_preview(db: DbSession, payload: UserImportRequest) -> UserImportPreviewResponse:
    """根据导入规则计算最终用户名、邮箱及冲突情况"""
    result = await db.execute(select(User.id, User.username, User.email))
    existing_map: dict[str, dict[str, Any]] = {}
    existing_emails: set[str] = set()
    for row in result:
        if not row.username:
            continue
        existing_map[row.username.lower()] = {
            "id": row.id,
            "username": row.username,
            "email": (row.email or "").strip(),
        }
        if row.email:
            existing_emails.add(row.email.lower())

    existing_usernames = set(existing_map)
    used_usernames: set[str] = set()
    used_emails: set[str] = set()
    preview_rows: list[UserImportPreviewRow] = []

    default_role = ROLE_ALIASES.get(payload.default_role.strip(), "user")
    rules = payload.username_rules

    for item in payload.rows:
        base = _normalize_username(
            item.username_raw, rules.lowercase, rules.remove_spaces, rules.remove_special
        )
        email = item.email_raw.strip()
        messages: list[str] = []
        status_value = "ok"
        renamed = False
        final_username = base

        def mark_invalid(reason: str) -> None:
            nonlocal status_value
            status_value = "invalid"
            messages.append(reason)

        if not base:
            mark_invalid("用户名为空或按规则处理后为空")

        if email and "@" not in email:
            mark_invalid("邮箱格式无效")

        if base and status_value != "invalid":
            existing = existing_map.get(base.lower())
            if existing is not None:
                existing_email = existing["email"]
                final_username = existing["username"]

                if not email:
                    mark_invalid("账号已存在且导入数据未提供邮箱，已跳过")
                elif email.lower() in existing_emails and email.lower() != existing_email.lower():
                    mark_invalid("邮箱已被其他用户使用")
                elif not existing_email:
                    status_value = "update"
                    messages.append(f"账号已存在且原用户无邮箱，将更新邮箱为 {email}")
                    existing["email"] = email
                elif email.lower() == existing_email.lower():
                    mark_invalid("账号已存在且邮箱相同，已跳过")
                elif _is_huawei_email(email) and _is_huawei_email(existing_email):
                    prefix = _normalize_username(
                        email.split("@", 1)[0],
                        rules.lowercase,
                        rules.remove_spaces,
                        rules.remove_special,
                    )
                    if not prefix:
                        mark_invalid("华为邮箱前缀无法生成有效账号")
                    elif prefix.lower() in existing_usernames or prefix.lower() in used_usernames:
                        mark_invalid(f"华为邮箱前缀生成的账号 {prefix} 已存在，已跳过")
                    else:
                        final_username = prefix
                        messages.append(
                            f"账号已存在但华为邮箱不同（{existing_email} → {email}），将新建账号 {prefix}"
                        )
                else:
                    counter = 0
                    while (
                        final_username.lower() in existing_usernames
                        or final_username.lower() in used_usernames
                    ):
                        counter += 1
                        final_username = f"{base}{counter}"[:50]
                    if counter > 0:
                        renamed = True
                        status_value = "renamed"
                        messages.append(
                            f"账号已存在且邮箱非华为体系，已按原规则改名为 {final_username}"
                        )
                    else:
                        messages.append("账号已存在，按原规则保留账号")
            else:
                if email:
                    if email.lower() in existing_emails:
                        mark_invalid("邮箱已存在（数据库）")
                    elif email.lower() in used_emails:
                        mark_invalid("邮箱在本批次中重复")
                if status_value != "invalid" and base.lower() in used_usernames:
                    counter = 0
                    final_username = base
                    while (
                        final_username.lower() in existing_usernames
                        or final_username.lower() in used_usernames
                    ):
                        counter += 1
                        final_username = f"{base}{counter}"[:50]
                    if counter > 0:
                        renamed = True
                        status_value = "renamed"
                        messages.append(f"本批次账号重复，已自动改为 {final_username}")

        role_raw = (item.role or payload.default_role).strip()
        role = ROLE_ALIASES.get(role_raw)
        if role is None:
            role = default_role
            messages.append(f"未识别角色“{role_raw}”，已使用默认角色")
            if status_value == "ok":
                status_value = "invalid"

        password = (item.password or payload.default_password).strip()
        if len(password) < 6:
            mark_invalid("密码长度不足 6 位")

        if final_username and status_value != "invalid":
            used_usernames.add(final_username.lower())
        if email and "@" in email and status_value != "invalid":
            used_emails.add(email.lower())

        preview_rows.append(
            UserImportPreviewRow(
                row_number=item.row_number,
                source_username=item.username_raw,
                source_email=item.email_raw,
                username=final_username,
                email=email,
                password=password,
                role=role,
                status=status_value,
                renamed=renamed,
                messages=messages,
            )
        )

    valid_count = sum(1 for row in preview_rows if row.status != "invalid")
    return UserImportPreviewResponse(
        rows=preview_rows,
        total=len(preview_rows),
        valid_count=valid_count,
        renamed_count=sum(1 for row in preview_rows if row.renamed),
        invalid_count=len(preview_rows) - valid_count,
        updated_count=sum(1 for row in preview_rows if row.status == "update"),
    )


@router.post("/import/parse", response_model=UserImportParseResponse)
async def parse_user_import_file(
    current_user: CurrentSuperAdminUser,
    db: DbSession,
    file: UploadFile = File(...),
):
    """解析上传的 Excel 文件，返回表头与数据行"""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请上传 .xlsx 格式的 Excel 文件",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件内容为空")

    try:
        workbook = load_workbook(BytesIO(contents), data_only=True, read_only=True)
    except Exception as exc:
        logger.warning("Excel parse failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Excel 解析失败，请确认文件未加密且格式正确",
        )

    worksheet = workbook.active
    raw_rows = list(worksheet.iter_rows(values_only=True))
    if not raw_rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Excel 中没有数据")

    header_index = -1
    for index, row in enumerate(raw_rows):
        if any(_cell_to_text(cell) for cell in row):
            header_index = index
            break

    if header_index < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Excel 中没有有效表头")

    raw_header = raw_rows[header_index]
    columns: list[str] = []
    used_headers: set[str] = set()
    for index, cell in enumerate(raw_header):
        name = _cell_to_text(cell) or f"列{index + 1}"
        unique_name = name
        suffix = 1
        while unique_name in used_headers:
            suffix += 1
            unique_name = f"{name}_{suffix}"
        used_headers.add(unique_name)
        columns.append(unique_name)

    data_rows: list[dict[str, object]] = []
    for row in raw_rows[header_index + 1:]:
        values = [_cell_to_text(cell) for cell in row]
        if not any(values):
            continue
        record = {columns[index]: values[index] if index < len(values) else "" for index in range(len(columns))}
        data_rows.append(record)

    return UserImportParseResponse(
        columns=columns,
        rows=data_rows,
        total=len(data_rows),
        sheet_name=worksheet.title,
    )


@router.post("/import/preview", response_model=UserImportPreviewResponse)
async def preview_user_import(
    db: DbSession,
    payload: UserImportRequest,
    current_user: CurrentSuperAdminUser,
):
    """预览导入结果（用户名规范化、重名递增、邮箱冲突等）"""
    if not payload.rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可导入的数据")
    return await _build_import_preview(db, payload)


@router.post("/import/execute", response_model=UserImportExecuteResponse)
async def execute_user_import(
    db: DbSession,
    payload: UserImportRequest,
    current_user: CurrentSuperAdminUser,
):
    """执行批量导入，自动跳过无效或冲突行"""
    preview = await _build_import_preview(db, payload)
    existing_result = await db.execute(select(User))
    users_by_name = {
        user.username.lower(): user for user in existing_result.scalars() if user.username
    }
    created = 0
    updated = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    for row in preview.rows:
        if row.status == "invalid":
            skipped += 1
            continue
        try:
            if row.status == "update":
                existing_user = users_by_name.get(row.username.lower())
                if existing_user is None:
                    failed += 1
                    errors.append(f"第 {row.row_number} 行 {row.username} 更新失败：用户不存在")
                    continue
                async with db.begin_nested():
                    existing_user.email = row.email or None
                    await db.flush()
                updated += 1
                continue

            user = User(
                username=row.username,
                email=row.email or None,
                password_hash=hash_password(row.password),
                role=row.role,
                is_active=True,
            )
            async with db.begin_nested():
                db.add(user)
                await db.flush()
            created += 1
        except IntegrityError as exc:
            failed += 1
            errors.append(f"第 {row.row_number} 行 {row.username} 导入失败：用户名或邮箱已存在")
            logger.warning("Import row %s failed: %s", row.row_number, exc)
        except Exception as exc:
            failed += 1
            errors.append(f"第 {row.row_number} 行 {row.username} 导入失败：{exc}")
            logger.error("Import row %s error: %s", row.row_number, exc, exc_info=True)

    await db.commit()
    return UserImportExecuteResponse(
        created=created,
        updated=updated,
        skipped=skipped,
        failed=failed,
        errors=errors,
    )


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: DbSession,
    current_user: CurrentSuperAdminUser
):
    """获取用户列表（仅超级管理员）"""
    # 使用 execution_options 确保每次请求都从数据库获取最新数据
    result = await db.execute(
        select(User).execution_options(populate_existing=True)
    )
    users = result.scalars().all()
    return users


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    db: DbSession,
    user_data: UserCreate,
    current_user: CurrentSuperAdminUser
):
    """创建用户（仅超级管理员）"""
    # 检查用户名是否已存在
    stmt = select(User).where(User.username == user_data.username)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在",
        )

    # 创建用户
    user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        role=user_data.role or "user",
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    return user


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    db: DbSession,
    user_id: int,
    current_user: CurrentAdminUser
):
    """获取用户详情"""
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    return user


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    db: DbSession,
    user_id: int,
    user_data: UserUpdate,
    current_user: CurrentAdminUser
):
    """更新用户信息"""
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    # 普通管理员不能修改超级管理员
    if user.role == "super_admin" and current_user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="普通管理员不能修改超级管理员用户",
        )

    update_data = user_data.model_dump(exclude_unset=True)

    new_username = update_data.get("username")
    if new_username is not None:
        new_username = new_username.strip()
        if new_username != user.username:
            duplicate_stmt = select(User).where(
                User.username == new_username, User.id != user.id
            )
            duplicate = (await db.execute(duplicate_stmt)).scalar_one_or_none()
            if duplicate:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="用户名已存在，请更换后重试",
                )
            update_data["username"] = new_username

    if "email" in update_data and not update_data["email"]:
        update_data["email"] = None

    # 更新字段
    for field, value in update_data.items():
        setattr(user, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名或邮箱已存在，请更换后重试",
        )
    await db.refresh(user)

    return user


@router.delete("/{user_id}", response_model=Message)
async def delete_user(
    db: DbSession,
    user_id: int,
    current_user: CurrentSuperAdminUser
):
    """删除用户（仅超级管理员）"""
    try:
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="用户不存在",
            )

        # 不允许删除自己
        if user.id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不能删除自己",
            )

        logger.info(f"Deleting user: {user.username} (ID: {user_id}) by {current_user.username}")
        await db.delete(user)
        await db.commit()

        logger.info(f"User {user.username} (ID: {user_id}) deleted successfully")
        return {"message": "用户已成功删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除失败：{str(e)}",
        )


@router.put("/{user_id}/password", response_model=Message)
async def reset_user_password(
    db: DbSession,
    user_id: int,
    password_data: PasswordReset,
    current_user: CurrentAdminUser
):
    """重置用户密码（管理员权限）"""
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    # 不允许重置自己的密码（应使用修改密码接口）
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请使用修改密码接口修改自己的密码",
        )

    # 更新密码
    user.password_hash = hash_password(password_data.new_password)
    await db.commit()

    return {"message": "用户密码已成功重置"}
