"""短信通道配置（支持多服务商配置切换）与阿里云短信发送实现。"""
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

PROVIDER_FIELDS = (
    "provider",
    "access_key_id",
    "sign_name",
    "template_code",
    "template_param_key",
    "region_id",
)

DEFAULT_SMS_CONFIG: dict[str, Any] = {
    "enabled": False,
    "fallback_enabled": True,
    "apply_to": "critical",
    "test_phones": [],
    "active_provider_id": "",
    "providers": [],
    "provider": "aliyun",
    "access_key_id": "",
    "access_key_secret": "",
    "sign_name": "",
    "template_code": "",
    "template_param_key": "content",
    "region_id": "cn-hangzhou",
}

_GLOBAL_FIELDS = {
    "enabled",
    "fallback_enabled",
    "apply_to",
    "test_phones",
    "active_provider_id",
}


def _legacy_provider(stored: dict[str, Any]) -> dict[str, Any] | None:
    if not any(stored.get(field) for field in PROVIDER_FIELDS):
        return None
    return {
        "id": "default",
        "name": "默认配置",
        **{field: stored.get(field, DEFAULT_SMS_CONFIG[field]) for field in PROVIDER_FIELDS},
        "access_key_secret_encrypted": stored.get("access_key_secret_encrypted", ""),
    }


async def get_sms_config(db) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == SMS_CONFIG_KEY
            )
        )
    ).scalar_one_or_none()
    stored = dict(row.config_value) if (row and row.config_value) else {}
    config = {**DEFAULT_SMS_CONFIG, **stored}
    providers = stored.get("providers") or []
    if not providers:
        legacy = _legacy_provider(stored)
        providers = [legacy] if legacy else []
    if providers:
        active_id = stored.get("active_provider_id") or providers[0].get("id")
        active = next((p for p in providers if p.get("id") == active_id), providers[0])
        config["active_provider_id"] = active.get("id", "")
        for field in PROVIDER_FIELDS:
            config[field] = active.get(field) or DEFAULT_SMS_CONFIG[field]
        encrypted = active.get("access_key_secret_encrypted") or ""
        config["access_key_secret"] = decrypt_api_key(encrypted) if encrypted else ""
    else:
        encrypted = stored.get("access_key_secret_encrypted") or ""
        config["access_key_secret"] = decrypt_api_key(encrypted) if encrypted else ""
    config["providers"] = providers
    return config


async def get_public_sms_config(db) -> dict[str, Any]:
    config = await get_sms_config(db)
    providers = []
    for provider in config.get("providers") or []:
        item = {key: provider.get(key) for key in ("id", "name", *PROVIDER_FIELDS)}
        item["access_key_secret_set"] = bool(provider.get("access_key_secret_encrypted"))
        providers.append(item)
    if not providers:
        providers.append(
            {
                "id": "default",
                "name": "默认配置",
                "provider": config.get("provider", "aliyun"),
                "access_key_id": config.get("access_key_id", ""),
                "access_key_secret_set": bool(config.get("access_key_secret")),
                "sign_name": config.get("sign_name", ""),
                "template_code": config.get("template_code", ""),
                "template_param_key": config.get("template_param_key", "content"),
                "region_id": config.get("region_id", "cn-hangzhou"),
            }
        )
    public = {key: config.get(key) for key in _GLOBAL_FIELDS}
    public["providers"] = providers
    if not public.get("active_provider_id"):
        public["active_provider_id"] = providers[0]["id"]
    active = next(
        (p for p in providers if p["id"] == public["active_provider_id"]), providers[0]
    )
    public.update({key: active.get(key) for key in PROVIDER_FIELDS})
    public["access_key_secret_set"] = bool(active.get("access_key_secret_set"))
    return public


async def save_sms_config(db, payload: dict[str, Any]) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == SMS_CONFIG_KEY
            )
        )
    ).scalar_one_or_none()
    stored = dict(row.config_value) if (row and row.config_value) else {}
    existing = {p.get("id"): p for p in (stored.get("providers") or [])}
    legacy = _legacy_provider(stored)
    if legacy:
        existing.setdefault(legacy["id"], legacy)

    if "providers" in payload:
        providers = []
        for raw in payload.get("providers") or []:
            provider_id = raw.get("id") or uuid.uuid4().hex[:12]
            item = {"id": provider_id, "name": raw.get("name") or "未命名配置"}
            for field in PROVIDER_FIELDS:
                item[field] = raw.get(field) or DEFAULT_SMS_CONFIG[field]
            secret = (raw.get("access_key_secret") or "").strip()
            if secret:
                item["access_key_secret_encrypted"] = encrypt_api_key(secret)
            elif existing.get(provider_id, {}).get("access_key_secret_encrypted"):
                item["access_key_secret_encrypted"] = existing[provider_id][
                    "access_key_secret_encrypted"
                ]
            providers.append(item)
        stored["providers"] = providers
        if providers and not stored.get("active_provider_id"):
            stored["active_provider_id"] = providers[0]["id"]

    for key in _GLOBAL_FIELDS - {"active_provider_id"}:
        if key in payload:
            stored[key] = payload[key]
    if payload.get("active_provider_id"):
        stored["active_provider_id"] = payload["active_provider_id"]
    stored.pop("access_key_secret", None)
    stored.pop("access_key_secret_encrypted", None)
    for field in PROVIDER_FIELDS:
        stored.pop(field, None)

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
