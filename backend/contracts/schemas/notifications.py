"""通知中心相关 Pydantic Schemas。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    user_id: int | None = None
    enabled: bool = True
    group_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_reachable(self):
        if not (self.email or "").strip() and not (self.phone or "").strip():
            raise ValueError("邮箱和手机号至少填写一个")
        if self.email and "@" not in self.email:
            raise ValueError("邮箱格式不正确")
        return self


class ContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    user_id: int | None = None
    enabled: bool | None = None
    group_ids: list[int] | None = None

    @model_validator(mode="after")
    def validate_email(self):
        if self.email and "@" not in self.email:
            raise ValueError("邮箱格式不正确")
        return self


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    name: str
    email: str | None
    phone: str | None
    phone_verified_at: datetime | None
    enabled: bool
    group_ids: list[int] = Field(default_factory=list)
    created_at: datetime | None
    updated_at: datetime | None


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    contact_ids: list[int] = Field(default_factory=list)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    contact_ids: list[int] | None = None


class GroupResponse(BaseModel):
    id: int
    name: str
    description: str | None
    enabled: bool
    contact_ids: list[int] = Field(default_factory=list)
    contacts: list[dict] = Field(default_factory=list)
    created_at: datetime | None


class DeliveryResponse(BaseModel):
    id: int
    event_id: int
    source: str
    event_type: str
    severity: str
    title: str
    summary: str | None
    channel: str
    recipient: str
    attempt: int
    status: str
    provider: str | None
    error: str | None
    sent_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime | None


class EventResponse(BaseModel):
    id: int
    source: str
    event_type: str
    severity: str
    title: str
    summary: str | None
    status: str
    created_at: datetime | None


class SmsConfigUpdate(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    access_key_id: str | None = None
    access_key_secret: str | None = None
    sign_name: str | None = None
    template_code: str | None = None
    template_param_key: str | None = None
    region_id: str | None = None
    fallback_enabled: bool | None = None
    apply_to: str | None = None
    test_phones: list[str] | None = None


class SmsTestRequest(BaseModel):
    phones: list[str] = Field(min_length=1)
    text: str = "vLLM Ascend 通知服务短信测试"


class VoiceConfigUpdate(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    access_key_id: str | None = None
    access_key_secret: str | None = None
    called_show_number: str | None = None
    tts_code: str | None = None
    tts_param_key: str | None = None
    region_id: str | None = None
    play_times: int | None = None
    fallback_enabled: bool | None = None
    apply_to: str | None = None
    test_phones: list[str] | None = None


class VoiceTestRequest(BaseModel):
    phones: list[str] = Field(min_length=1)
    text: str = "vLLM Ascend 通知服务语音测试"
