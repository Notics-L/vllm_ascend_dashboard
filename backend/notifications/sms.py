"""短信通道配置与阿里云短信发送实现。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select

from infrastructure.core.security import decrypt_api_key, encrypt_api_key
from infrastructure.persistence.models import ProjectDashboardConfig

logger = logging.getLogger(__name__)

SMS_CONFIG_KEY = "sms_config"

DEFAULT_SMS_CONFIG: dict[str, Any] = {
    "enabled": False,
    "provider": "aliyun",
    "access_key_id": "",
    "access_key_secret": "",
    "sign_name": "",
    "template_code": "",
    "template_param_key": "content",
    "region_id": "cn-hangzhou",
    "fallback_enabled": True,
    "apply_to": "critical",
    "test_phones": [],
}

_PUBLIC_FIELDS = {
    "enabled",
    "provider",
    "access_key_id",
    "sign_name",
    "template_code",
    "template_param_key",
    "region_id",
    "fallback_enabled",
    "apply_to",
    "test_phones",
}


async def get_sms_config(db) -> dict[str, Any]:
    """读取短信配置，并将加密的 AccessKey Secret 解密为内部字段。"""
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == SMS_CONFIG_KEY
            )
        )
    ).scalar_one_or_none()
    stored = dict(row.config_value) if (row and row.config_value) else {}
    config = {**DEFAULT_SMS_CONFIG, **stored}
    config.pop("access_key_secret_encrypted", None)
    encrypted = stored.get("access_key_secret_encrypted") or ""
    config["access_key_secret"] = decrypt_api_key(encrypted) if encrypted else ""
    return config


async def get_public_sms_config(db) -> dict[str, Any]:
    """返回可展示给前端的配置，不包含明文 Secret。"""
    config = await get_sms_config(db)
    public = {key: config.get(key) for key in _PUBLIC_FIELDS}
    public["access_key_secret_set"] = bool(config.get("access_key_secret"))
    return public


async def save_sms_config(db, payload: dict[str, Any]) -> dict[str, Any]:
    """保存短信配置；secret 为空时保留原值，避免前端回显。"""
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == SMS_CONFIG_KEY
            )
        )
    ).scalar_one_or_none()
    stored = dict(row.config_value) if (row and row.config_value) else {}
    for key in _PUBLIC_FIELDS:
        if key in payload:
            stored[key] = payload[key]
    secret = (payload.get("access_key_secret") or "").strip()
    if secret:
        stored["access_key_secret_encrypted"] = encrypt_api_key(secret)
    stored.pop("access_key_secret", None)
    if row is None:
        row = ProjectDashboardConfig(
            config_key=SMS_CONFIG_KEY,
            config_value=stored,
            description="短信通道配置",
        )
        db.add(row)
    else:
        row.config_value = stored
    await db.flush()
    return await get_public_sms_config(db)


def _percent_encode(value: str) -> str:
    return quote(value, safe="~")


def _aliyun_signature(params: dict[str, str], secret: str) -> str:
    canonical = "&".join(
        f"{_percent_encode(key)}={_percent_encode(params[key])}" for key in sorted(params)
    )
    string_to_sign = f"POST&%2F&{_percent_encode(canonical)}"
    digest = hmac.new(
        f"{secret}&".encode(), string_to_sign.encode(), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode()


async def send_sms(config: dict[str, Any], phone: str, text: str) -> dict[str, Any]:
    """发送单条短信。provider=mock 时直接返回成功，便于本地验证链路。"""
    if not config.get("enabled"):
        return {"success": False, "error": "短信通道未启用"}
    provider = (config.get("provider") or "aliyun").lower()
    if provider == "mock":
        return {
            "success": True,
            "provider": "mock",
            "provider_message_id": f"mock-{uuid.uuid4().hex[:12]}",
        }
    if provider != "aliyun":
        return {"success": False, "error": f"不支持的短信服务商: {provider}"}

    access_key_id = (config.get("access_key_id") or "").strip()
    access_key_secret = (config.get("access_key_secret") or "").strip()
    sign_name = (config.get("sign_name") or "").strip()
    template_code = (config.get("template_code") or "").strip()
    if not all((access_key_id, access_key_secret, sign_name, template_code)):
        return {"success": False, "error": "短信配置不完整，请填写 AccessKey、签名和模板"}

    param_key = (config.get("template_param_key") or "content").strip() or "content"
    params: dict[str, str] = {
        "AccessKeyId": access_key_id,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": config.get("region_id") or "cn-hangzhou",
        "SignName": sign_name,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "TemplateCode": template_code,
        "TemplateParam": json.dumps({param_key: text}, ensure_ascii=False),
        "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }
    params["Signature"] = _aliyun_signature(params, access_key_secret)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post("https://dysmsapi.aliyuncs.com/", data=params)
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("SMS send failed for %s: %s", phone, exc)
        return {"success": False, "provider": "aliyun", "error": str(exc)}
    if data.get("Code") == "OK":
        return {
            "success": True,
            "provider": "aliyun",
            "provider_message_id": data.get("BizId") or data.get("RequestId"),
        }
    return {
        "success": False,
        "provider": "aliyun",
        "error": data.get("Message") or f"Aliyun SMS error: {data.get('Code')}",
    }
