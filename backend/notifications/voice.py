"""语音电话通道配置与阿里云语音通知实现。"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from infrastructure.core.security import decrypt_api_key, encrypt_api_key
from infrastructure.persistence.models import ProjectDashboardConfig
from notifications.sms import _aliyun_signature

logger = logging.getLogger(__name__)

VOICE_CONFIG_KEY = "voice_config"

DEFAULT_VOICE_CONFIG: dict[str, Any] = {
    "enabled": False,
    "provider": "aliyun",
    "access_key_id": "",
    "access_key_secret": "",
    "called_show_number": "",
    "tts_code": "",
    "tts_param_key": "content",
    "region_id": "cn-hangzhou",
    "play_times": 2,
    "fallback_enabled": True,
    "apply_to": "critical",
    "test_phones": [],
}

_PUBLIC_FIELDS = {
    "enabled",
    "provider",
    "access_key_id",
    "called_show_number",
    "tts_code",
    "tts_param_key",
    "region_id",
    "play_times",
    "fallback_enabled",
    "apply_to",
    "test_phones",
}


async def get_voice_config(db) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == VOICE_CONFIG_KEY
            )
        )
    ).scalar_one_or_none()
    stored = dict(row.config_value) if (row and row.config_value) else {}
    config = {**DEFAULT_VOICE_CONFIG, **stored}
    config.pop("access_key_secret_encrypted", None)
    encrypted = stored.get("access_key_secret_encrypted") or ""
    config["access_key_secret"] = decrypt_api_key(encrypted) if encrypted else ""
    return config


async def get_public_voice_config(db) -> dict[str, Any]:
    config = await get_voice_config(db)
    public = {key: config.get(key) for key in _PUBLIC_FIELDS}
    public["access_key_secret_set"] = bool(config.get("access_key_secret"))
    return public


async def save_voice_config(db, payload: dict[str, Any]) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == VOICE_CONFIG_KEY
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
            config_key=VOICE_CONFIG_KEY,
            config_value=stored,
            description="语音电话通道配置",
        )
        db.add(row)
    else:
        row.config_value = stored
    await db.flush()
    return await get_public_voice_config(db)


async def send_voice(config: dict[str, Any], phone: str, text: str) -> dict[str, Any]:
    """发起语音播报。provider=mock 时直接返回成功。"""
    if not config.get("enabled"):
        return {"success": False, "error": "语音通道未启用"}
    provider = (config.get("provider") or "aliyun").lower()
    if provider == "mock":
        return {
            "success": True,
            "provider": "mock",
            "provider_message_id": f"mock-call-{uuid.uuid4().hex[:12]}",
        }
    if provider != "aliyun":
        return {"success": False, "error": f"不支持的语音服务商: {provider}"}

    access_key_id = (config.get("access_key_id") or "").strip()
    access_key_secret = (config.get("access_key_secret") or "").strip()
    show_number = (config.get("called_show_number") or "").strip()
    tts_code = (config.get("tts_code") or "").strip()
    if not all((access_key_id, access_key_secret, show_number, tts_code)):
        return {"success": False, "error": "语音配置不完整，请填写 AccessKey、主叫号码和 TTS 模板"}

    param_key = (config.get("tts_param_key") or "content").strip() or "content"
    params: dict[str, str] = {
        "AccessKeyId": access_key_id,
        "Action": "SingleCallByTts",
        "CalledNumber": phone,
        "CalledShowNumber": show_number,
        "Format": "JSON",
        "PlayTimes": str(int(config.get("play_times") or 2)),
        "RegionId": config.get("region_id") or "cn-hangzhou",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "TtsCode": tts_code,
        "TtsParam": json.dumps({param_key: text}, ensure_ascii=False),
        "Version": "2017-05-25",
    }
    params["Signature"] = _aliyun_signature(params, access_key_secret)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post("https://dyvmsapi.aliyuncs.com/", data=params)
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Voice call failed for %s: %s", phone, exc)
        return {"success": False, "provider": "aliyun", "error": str(exc)}
    if data.get("Code") == "OK":
        return {
            "success": True,
            "provider": "aliyun",
            "provider_message_id": data.get("CallId") or data.get("RequestId"),
        }
    return {
        "success": False,
        "provider": "aliyun",
        "error": data.get("Message") or f"Aliyun voice error: {data.get('Code')}",
    }
