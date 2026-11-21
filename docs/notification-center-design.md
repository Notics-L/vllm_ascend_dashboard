# 统一通知服务设计文档

- 文档状态：已评审，分阶段实施中
- 创建日期：2026-11-21
- 关联需求：将「邮件服务」升级为统一的「通知服务」，支持邮件、短信、语音电话、失败降级与语音 IVR 操作
- 进度追踪：见 `docs/notification-center-progress.md`

## 1. 背景与现状

当前系统只有邮件通知能力，配置入口为：

- 页面：`/admin/smtp-config`「管理中心 → 邮件服务」
- 后端配置：`project_dashboard_config.config_key='smtp_config'`
- 邮件实现：`backend/infrastructure/core/email.py`
- 配置接口：`backend/api/v1/system_config.py`
- 业务调用：
  - 资源告警：`backend/collector/alert_evaluator.py`
  - 镜像构建告警：`backend/image_build_monitor/service.py`
  - 每日运行报告：`backend/reporting/daily_report.py`

现存问题：

1. 模块命名只体现邮件，无法承载短信、语音等综合通知能力。
2. 业务模块直接依赖 SMTP 参数与 `send_email`，新增渠道需要修改所有调用方。
3. 无统一通知事件模型、渠道抽象、投递记录、重试与降级。
4. 无联系人/联系人组/值班体系，用户模型和告警规则只有邮箱。
5. 邮件发送使用个人 QQ 邮箱，缺少组账号与域名认证，不适合生产告警。
6. 无法感知“邮件是否被看到”，需要电话/短信等主动触达方式。

## 2. 目标与非目标

### 2.1 目标

- 将「邮件服务」升级为「通知服务」，邮件成为其中一个渠道 Tab。
- 统一通知事件入口与渠道适配层，业务方不感知具体渠道。
- 支持邮件、短信、语音电话，后续可扩展企业微信、钉钉、Webhook。
- 支持通知策略：渠道顺序、失败降级、重试、冷却、静默时段、升级。
- 支持联系人/联系人组/值班人，支持手机号与邮箱。
- 支持投递记录、渠道回执、失败原因与操作审计。
- 支持语音播报与 IVR 按键操作（重试/中断/确认/忽略）。
- 电话未接听/占线/失败时降级为短信，再降级为邮件。

### 2.2 非目标

- 本期不建设完整呼叫中心或人工客服系统。
- 不在第一阶段开放生产流水线的高危写操作，重试/取消需后续阶段加白名单与二次确认。
- 不替换每日报告的邮件内容生成逻辑，仅统一发送入口。

## 3. 总体架构

```text
业务告警源
  ├── alert_evaluator
  ├── image_build_monitor
  ├── daily_report
  └── 未来其他模块
        │
        ▼
NotificationEvent（统一事件）
        │
        ▼
NotificationDispatcher（通知调度器）
  ├── 解析通知策略
  ├── 解析联系人/联系人组
  ├── 渲染邮件/SMS/TTS 模板
  ├── 按渠道顺序发送
  ├── 失败降级/重试/冷却
  ├── 写投递记录
  └── 处理语音回调与按键动作
        │
        ├── EmailChannel  → SMTP / 企业邮箱 / 邮件推送
        ├── SmsChannel    → 阿里云/腾讯云短信
        └── VoiceChannel  → 语音通知 / IVR
```

### 3.1 后端目录规划

```text
backend/notifications/
  __init__.py
  service.py          # NotificationDispatcher（Phase 0 已创建）
  events.py           # NotificationEvent / NotificationResult（Phase 1）
  policies.py         # 策略解析、降级、冷却、静默（Phase 2）
  templates.py        # 邮件/SMS/语音脚本渲染（Phase 2）
  contacts.py         # 联系人解析（Phase 1）
  channels/
    base.py           # NotificationChannel 协议（Phase 1）
    email.py          # 邮件渠道（Phase 1 从 infrastructure/core/email.py 迁移）
    sms.py            # 短信渠道（Phase 2）
    voice.py          # 语音渠道（Phase 3）
  providers/
    smtp.py
    aliyun_sms.py
    aliyun_voice.py   # 或 twilio/freeswitch
  callbacks.py        # 语音回调校验与动作分发（Phase 4）
```

### 3.2 统一事件模型

```python
NotificationEvent(
    source="image_build_monitor",
    event_type="build_failure",
    severity="critical",
    title="Nightly Image Build 构建失败",
    summary="Run #44583104548，失败 Job：Build nightly-a5 (ubuntu)",
    detail_url="https://.../image-build-monitor",
    run_url="https://github.com/.../actions/runs/44583104548",
    dedupe_key="failure:workflow:run",
    metadata={"workflow": "...", "run_id": ..., "failed_jobs": [...]},
    actions=["retry", "abort", "ack"],
)
```

业务模块只产生事件，不直接关心渠道。

## 4. 数据模型设计

### 4.1 联系人

```text
notification_contacts
  id
  user_id            # 可关联系统用户，可空
  name
  email
  phone              # E.164，如 +8613800000000
  phone_verified_at
  enabled
  created_at
  updated_at

notification_groups
  id
  name
  description
  enabled

notification_group_members
  group_id
  contact_id
```

### 4.2 通知策略

```text
notification_policies
  id
  name
  source             # image_build_monitor / alert_rules / daily_report / *
  severity           # critical / warning / info / *
  channel_order      # JSON: ["voice","sms","email"]
  fallback_rules     # JSON: 未接听/失败后的降级规则
  max_attempts
  retry_interval_seconds
  quiet_hours        # JSON: {"start":"22:00","end":"08:00"}
  cooldown_minutes
  escalation         # JSON: 二次升级联系人组/时间
  enabled
```

### 4.3 事件与投递记录

```text
notification_events
  id
  source
  event_type
  severity
  title
  summary
  payload            # JSON
  dedupe_key         # 唯一，防重复
  status             # pending/sent/partial/failed/acknowledged
  created_at

notification_deliveries
  id
  event_id
  policy_id
  channel            # email/sms/voice
  recipient
  contact_id
  attempt
  status             # pending/sent/delivered/failed/no_answer/busy/answered
  provider
  provider_message_id
  error
  sent_at
  delivered_at
  answered_at

notification_actions
  id
  delivery_id
  provider_call_id
  digit
  action             # retry/abort/ack/ignore
  status             # received/executing/success/failed
  result             # JSON
  created_at
```

### 4.4 渠道配置

继续存储于 `project_dashboard_config`：

- `smtp_config`：兼容现有邮件配置
- `sms_config`：服务商、AccessKey、签名、模板、加密后的 Secret
- `voice_config`：服务商、AppId、主叫号码、TTS 模板、回调 Token、加密后的 Secret
- `notification_policy_config`：MVP 策略

敏感字段复用 `backend/infrastructure/core/security.py` 的 `encrypt_api_key/decrypt_api_key`，接口只返回 `*_set` 状态。

## 5. 关键流程

### 5.1 普通告警发送

```text
业务告警触发
→ 生成 NotificationEvent（含 dedupe_key）
→ 命中通知策略
→ 解析联系人/联系人组
→ 按 channel_order 发送
→ 写 notification_deliveries
→ 失败按 fallback_rules 降级
→ 更新事件状态
```

### 5.2 渠道降级

默认策略建议：

```text
critical：
  1. 语音电话（30 秒未接听/占线/失败）
  2. 短信
  3. 邮件
  4. 升级备用联系人组（可选）

warning：
  1. 短信
  2. 邮件

info：
  1. 邮件
```

要求：去重、冷却、静默时段、频控、电话并发限制、降级原因记录。

### 5.3 语音 IVR

```text
电话接通
→ TTS 播报告警摘要
→ 用户按键
→ 服务商回调 /api/v1/notifications/voice/callback
→ 校验签名、时间戳、call_id 幂等
→ 写 notification_actions
→ 执行动作
→ 回播结果或结束
```

按键规划：

- Phase 3：仅播报，不开放写操作。
- Phase 4：按 1 确认/按 2 忽略；后续再开放重试/取消。
- 重试/取消必须：值班白名单、二次确认、幂等、审计、限制仓库/Workflow、最小权限 Token。

需要补充 GitHub 接口：

- `POST /repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs`
- `POST /repos/{owner}/{repo}/actions/runs/{run_id}/cancel`

## 6. API 设计

```text
GET    /api/v1/notifications/channels
GET    /api/v1/notifications/channels/{channel}
PUT    /api/v1/notifications/channels/{channel}
POST   /api/v1/notifications/channels/{channel}/test

GET    /api/v1/notifications/policies
PUT    /api/v1/notifications/policies/{id}

GET    /api/v1/notifications/contacts
POST   /api/v1/notifications/contacts
PUT    /api/v1/notifications/contacts/{id}
GET    /api/v1/notifications/groups
POST   /api/v1/notifications/groups

GET    /api/v1/notifications/events
GET    /api/v1/notifications/deliveries
POST   /api/v1/notifications/deliveries/{id}/retry

POST   /api/v1/notifications/voice/callback   # 公开，需签名校验
```

旧接口 `/api/v1/system/config/smtp` 至少保留一个版本，内部委托新通知服务。

## 7. 前端设计

```text
管理中心
└── 通知服务（/admin/notifications）
    ├── 邮件
    ├── 短信
    ├── 语音电话
    ├── 通知策略
    ├── 联系人/联系人组
    └── 发送记录
```

要求：

- 渠道页显示「已配置/未配置」、脱敏密钥、测试连通性、发送测试。
- 策略页支持渠道顺序、降级、静默时段、冷却、升级。
- 发送记录支持按事件/渠道/状态过滤、失败重试、详情查看。
- 语音模板支持变量提示与 TTS 预听。
- 旧路由 `/admin/smtp-config` 重定向到 `/admin/notifications`。

## 8. 分阶段实施计划

### Phase 0：改名与邮件入口收敛（已完成）

- 菜单、路由、页面标题改为「通知服务」
- 邮件作为「通知服务」中的「邮件」Tab
- 新增 `NotificationDispatcher`，业务统一通过它发送邮件
- 保持原有邮件行为不变

验收标准：

- 菜单显示「通知服务」
- `/admin/notifications` 可访问，旧路由可重定向
- 资源告警、镜像监控、每日报告仍可正常发邮件
- 业务模块不再直接调用 `send_email`

### Phase 1：联系人 + 投递记录

- 新增联系人/联系人组表
- `User` 增加手机号
- 新增通知事件/投递记录表
- 邮件发送写入投递记录
- 前端新增「联系人」「发送记录」Tab

验收标准：

- 可为用户/联系人维护手机号与邮箱
- 每次通知可查到渠道、接收人、状态、失败原因
- 旧邮件发送行为不变

### Phase 2：短信 + 降级

- 接入短信服务商
- 支持短信签名、模板、测试发送
- 实现 `voice → sms → email` 降级
- 增加冷却、静默、频控

验收标准：

- 可发送真实短信
- 语音失败后自动降级短信
- 同一告警不会重复轰炸

### Phase 3：语音播报

- 接入语音通知服务商
- TTS 模板与测试呼叫
- 记录接通/未接/失败
- 暂不开放危险按键

验收标准：

- 可按策略拨打值班手机并播报告警摘要
- 未接听可触发短信降级
- 通话记录可追溯

### Phase 4：IVR 按键动作

- 实现语音回调与按键解析
- 支持确认/忽略，逐步开放重试/取消
- 白名单、二次确认、幂等、审计
- 补充 GitHub rerun/cancel

验收标准：

- 按键回调可正确落库并执行
- 高危动作有白名单和二次确认
- 重复回调不会重复执行

### Phase 5：升级与运营

- 值班表、升级链、备用联系人
- 通知成功率、接通率、降级率、动作成功率监控
- 告警风暴抑制与通知聚合

## 9. 服务商选型建议

- 邮件：组邮箱/企业邮箱，或阿里云邮件推送、腾讯企业邮；配置 SPF、DKIM、DMARC。
- 短信：阿里云短信或腾讯云短信，需签名与模板审核。
- 语音：优先阿里云语音通知/智能语音交互；必须确认是否支持 DTMF 按键回调。
- 若国内语音服务商不支持 IVR：评估 Twilio 或自建 FreeSWITCH + SIP 中继。
- 不建议继续使用个人 QQ 邮箱作为生产告警发件账号。

## 10. 安全、合规与可观测性

- 手机号、邮箱属于个人信息，需脱敏展示、最小权限访问、明确使用范围。
- 短信/语音模板需服务商审核，遵守拨打时段、频控与退订要求。
- 语音回调必须校验签名、时间戳，防重放，幂等处理。
- 重试/取消等写操作必须白名单、二次确认、审计，禁止任意命令执行。
- 渠道密钥加密存储，日志不输出明文。
- 指标：发送成功率、投递延迟、降级率、电话接通率、动作成功率。
- 所有通知使用统一 `event_id` / `delivery_id` 串联日志与审计。
