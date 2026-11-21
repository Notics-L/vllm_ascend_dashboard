# 通知服务实施进度

- 设计文档：`docs/notification-center-design.md`
- 创建日期：2026-11-21
- 当前阶段：Phase 3 已完成
- 分支：`feature/ci-image-build-monitor`

## 阶段进度总览

| 阶段 | 内容 | 状态 | 完成日期 |
| --- | --- | --- | --- |
| Phase 0 | 改名与邮件入口收敛 | 已完成 | 2026-11-21 |
| Phase 1 | 联系人 + 投递记录 | 已完成 | 2026-11-21 |
| Phase 2 | 短信 + 降级 | 已完成 | 2026-11-21 |
| Phase 3 | 语音播报 | 已完成 | 2026-11-21 |
| Phase 4 | IVR 按键动作 | 未开始 | - |
| Phase 5 | 升级与运营 | 未开始 | - |

## Phase 0：改名与邮件入口收敛

### 目标

- 模块从「邮件服务」升级为「通知服务」
- 邮件作为「通知服务」下的一个 Tab
- 业务模块统一通过 `NotificationDispatcher` 发送邮件
- 保持现有邮件行为不变

### 完成内容

1. 新增统一通知调度器：
   - `backend/notifications/__init__.py`
   - `backend/notifications/service.py`
   - `NotificationDispatcher.get_email_config()`
   - `NotificationDispatcher.send_email()`

2. 业务侧邮件发送统一接入调度器：
   - `backend/collector/alert_evaluator.py`
   - `backend/image_build_monitor/service.py`
   - `backend/reporting/daily_report.py`（定时报告与草稿发送）
   - 顺带修复资源告警原先误用 `report_from_email` 作为发件人的问题

3. 前端模块改名：
   - `frontend/src/components/Layout.tsx`
     - 菜单「邮件服务」改为「通知服务」
     - 菜单路由 `/admin/smtp-config` 改为 `/admin/notifications`
   - `frontend/src/App.tsx`
     - 新增 `/admin/notifications` 路由
     - 旧 `/admin/smtp-config` 重定向到新路由
   - `frontend/src/pages/SmtpConfig.tsx`
     - 页面标题改为「通知服务」
     - 增加「邮件 / 短信 / 语音电话」Tabs
     - 邮件 Tab 保留原 SMTP 配置能力
     - 短信、语音 Tab 显示规划中占位
   - `frontend/src/pages/ImageBuildMonitor.tsx`
   - `frontend/src/pages/DailyReportConfig.tsx`
     - 相关「邮件服务」链接与文案更新为「通知服务」

### 变更文件清单

后端：

- `backend/notifications/__init__.py`（新增）
- `backend/notifications/service.py`（新增）
- `backend/collector/alert_evaluator.py`
- `backend/image_build_monitor/service.py`
- `backend/reporting/daily_report.py`
- `backend/api/Dockerfile`（补充 `COPY backend/notifications/ ./notifications/`，修复容器内 ModuleNotFoundError）

前端：

- `frontend/src/components/Layout.tsx`
- `frontend/src/App.tsx`
- `frontend/src/pages/SmtpConfig.tsx`
- `frontend/src/pages/ImageBuildMonitor.tsx`
- `frontend/src/pages/DailyReportConfig.tsx`

文档：

- `docs/notification-center-design.md`（新增）
- `docs/notification-center-progress.md`（新增）

### 验证记录

- 后端语法检查：对以下文件执行 `python -m py_compile` 通过：
  - `backend/notifications/service.py`
  - `backend/collector/alert_evaluator.py`
  - `backend/image_build_monitor/service.py`
  - `backend/reporting/daily_report.py`
- 代码检索确认：业务模块已无 `await send_email(...)` 直接调用，邮件实现仅保留在 `backend/notifications/service.py` 与配置读取接口中。
- 导入验证：`from notifications import NotificationDispatcher` 可正常导入。
- 容器验证：重建 dev 镜像后 `dev-backend-1` 状态为 healthy，`/health` 返回 200。
- 数据验证：`python database/seed_local_demo.py` 重新执行成功。
- 前端依赖未安装（无 `node_modules`），未执行 `tsc/lint`；改动为路由、文案、Tabs 和组件复用。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 菜单显示「通知服务」 | 已完成 |
| `/admin/notifications` 可访问 | 已完成 |
| 旧 `/admin/smtp-config` 可重定向 | 已完成 |
| 邮件作为通知服务下的 Tab | 已完成 |
| 业务统一通过 NotificationDispatcher 发邮件 | 已完成 |
| 现有邮件行为保持不变 | 已完成（仅入口收敛，SMTP 配置键未变） |

### 溯源信息

- 实施批次：`notifications-phase0`
- 实施日期：2026-11-21
- 关联需求：统一通知服务、邮件渠道收敛
- Git 提交：`e9597ab`（feat(notifications): phase 0 unify email dispatch under notification service）
- 远程分支：`origin/feature/ci-image-build-monitor`

## Phase 1：联系人 + 投递记录

### 目标

- 新增联系人、联系人组及成员关系
- 用户模型增加手机号
- 新增通知事件与投递记录
- 邮件发送自动写入事件与投递记录
- 前端增加「联系人」「发送记录」Tab

### 完成内容

1. 数据模型与迁移：
   - `backend/infrastructure/persistence/models/notifications.py`
     - `NotificationContact`
     - `NotificationGroup`
     - `NotificationGroupMember`
     - `NotificationEvent`
     - `NotificationDelivery`
   - `backend/infrastructure/persistence/models/__init__.py` 注册模型，`User` 增加 `phone`
   - `database/migrations/notifications.py` 创建通知表并为 `users` 增加 `phone`
   - `database/migrations/migrate.py` 注册迁移入口

2. 通知服务层：
   - `backend/notifications/service.py`
     - 邮件发送创建/复用 `notification_events`
     - 每个收件人写入 `notification_deliveries`
     - 记录发送成功/失败、错误原因、尝试次数
     - 联系人、联系人组 CRUD
     - 事件与投递记录分页查询
     - 邮件投递重试

3. 业务事件上下文：
   - 镜像构建告警传入 `source/event_type/severity/title/summary/payload/dedupe_key`
   - 资源告警传入规则、历史、集群、节点信息
   - 每日报告传入报告 ID 与报告日期
   - 修复资源告警历史未先落库导致去重键为 `None` 的问题

4. API：
   - `backend/api/v1/notifications.py`
     - `GET/POST/PUT/DELETE /api/v1/notifications/contacts`
     - `GET/POST/PUT/DELETE /api/v1/notifications/groups`
     - `GET /api/v1/notifications/events`
     - `GET /api/v1/notifications/deliveries`
     - `POST /api/v1/notifications/deliveries/{id}/retry`
   - `backend/contracts/schemas/notifications.py`
   - `backend/api/main.py` 注册通知中心路由
   - 用户 Schema 增加 `phone`

5. 前端：
   - `frontend/src/services/notifications.ts`
   - `frontend/src/hooks/useNotifications.ts`
   - `frontend/src/components/notifications/ContactsPanel.tsx`
     - 联系人增删改查
     - 联系人组增删改查与成员维护
   - `frontend/src/components/notifications/DeliveriesPanel.tsx`
     - 发送记录列表、来源/渠道/状态过滤、分页、失败重试
   - `frontend/src/pages/SmtpConfig.tsx`
     - 新增「联系人」「发送记录」Tab

### 变更文件清单

后端：

- `backend/infrastructure/persistence/models/notifications.py`（新增）
- `backend/infrastructure/persistence/models/__init__.py`
- `backend/notifications/service.py`
- `backend/collector/alert_evaluator.py`
- `backend/image_build_monitor/service.py`
- `backend/reporting/daily_report.py`
- `backend/contracts/schemas/notifications.py`（新增）
- `backend/contracts/schemas/__init__.py`
- `backend/api/v1/notifications.py`（新增）
- `backend/api/main.py`
- `database/migrations/notifications.py`（新增）
- `database/migrations/migrate.py`

前端：

- `frontend/src/services/notifications.ts`（新增）
- `frontend/src/hooks/useNotifications.ts`（新增）
- `frontend/src/components/notifications/ContactsPanel.tsx`（新增）
- `frontend/src/components/notifications/DeliveriesPanel.tsx`（新增）
- `frontend/src/pages/SmtpConfig.tsx`

文档：

- `docs/notification-center-progress.md`

### 验证记录

- 后端语法检查：相关文件 `python -m py_compile` 通过。
- 迁移验证：开发库成功创建 `notification_contacts`、`notification_groups`、`notification_group_members`、`notification_events`、`notification_deliveries`，并为 `users` 增加 `phone`。
- API 验证：
  - 登录 admin 后成功创建/查询/更新/删除联系人和联系人组
  - 联系人组与联系人双向关联成功
  - `/api/v1/notifications/deliveries` 可查询投递记录
- 投递落库验证：使用不可达 SMTP 配置发送测试邮件，成功生成 `notification_events` 与 `notification_deliveries`，状态为 `failed`，错误原因正确记录，随后清理测试数据。
- 前端构建：`pnpm build`（含 `tsc`）通过。
- 容器状态：`dev-backend-1`、`dev-frontend-1` 均为 healthy，`/health` 返回 200。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 可维护联系人邮箱与手机号 | 已完成 |
| 可维护联系人组及成员 | 已完成 |
| 每次通知可查渠道、接收人、状态、失败原因 | 已完成 |
| 邮件发送写入投递记录 | 已完成 |
| 旧邮件发送行为保持不变 | 已完成 |
| 前端新增联系人与发送记录 Tab | 已完成 |

### 溯源信息

- 实施批次：`notifications-phase1`
- 实施日期：2026-11-21
- 关联需求：联系人体系、投递记录、发送可追溯
- Git 提交：`9db375e`（feat(notifications): phase 1 add contacts and delivery records）
- 远程分支：`origin/feature/ci-image-build-monitor`

## Phase 2：短信 + 降级

### 目标

- 接入短信服务商，支持配置、测试发送
- 短信配置支持加密存储
- critical 告警优先短信，失败后自动降级邮件
- 投递记录支持短信渠道

### 完成内容

1. 短信通道：
   - `backend/notifications/sms.py`
     - `sms_config` 读取/保存
     - AccessKey Secret 使用 `encrypt_api_key/decrypt_api_key` 加密
     - 阿里云短信签名算法与 SendSms 调用
     - `provider=mock` 本地联调模式
   - 支持启用开关、服务商、签名、模板 CODE、模板变量名、Region、测试手机号
   - 支持 `apply_to=critical/all` 与 `fallback_enabled`

2. 调度器与降级：
   - `backend/notifications/service.py`
     - 新增 `dispatch()`：按渠道顺序发送，成功即停止
     - 新增 `send_sms()` 与短信投递记录
     - 新增 `resolve_channels()`：根据短信配置返回 `["sms","email"]` 或 `["email"]`
     - 通过联系人邮箱匹配手机号，自动解析短信接收人
     - 短信失败后继续尝试邮件
     - 事件去重键改为渠道无关，避免同一告警重复建事件

3. 业务接入：
   - 镜像构建告警：critical 时走短信优先、邮件降级
   - 资源告警：critical 时走短信优先、邮件降级
   - 每日报告保持邮件通道

4. API：
   - `GET /api/v1/notifications/channels/sms`
   - `PUT /api/v1/notifications/channels/sms`
   - `POST /api/v1/notifications/channels/sms/test`

5. 前端：
   - `frontend/src/components/notifications/SmsPanel.tsx`
     - 短信配置、降级策略、测试发送
   - 「通知服务 → 短信」Tab 由占位替换为可用面板
   - 服务与 hooks 增加短信配置和测试能力

### 变更文件清单

后端：

- `backend/notifications/sms.py`（新增）
- `backend/notifications/service.py`
- `backend/api/v1/notifications.py`
- `backend/contracts/schemas/notifications.py`
- `backend/collector/alert_evaluator.py`
- `backend/image_build_monitor/service.py`

前端：

- `frontend/src/components/notifications/SmsPanel.tsx`（新增）
- `frontend/src/hooks/useNotifications.ts`
- `frontend/src/services/notifications.ts`
- `frontend/src/pages/SmtpConfig.tsx`

文档：

- `docs/notification-center-progress.md`

### 验证记录

- 后端相关文件 `python -m py_compile` 通过。
- 前端 `pnpm build`（含 `tsc`）通过。
- API 验证：
  - 保存短信配置成功，Secret 不回显
  - `provider=mock` 测试短信发送成功，生成短信投递记录
- 降级验证：
  - 联系人邮箱与手机号匹配成功，critical 渠道解析为 `["sms","email"]`
  - mock 短信发送成功后停止，不重复发送邮件
  - 短信失败（配置不完整）后继续尝试邮件，投递记录包含两个渠道结果
- 测试数据已清理。
- 容器状态：`dev-backend-1` healthy，前端已使用新镜像启动。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 可配置短信服务商与模板 | 已完成 |
| 可发送测试短信 | 已完成 |
| critical 告警短信优先 | 已完成 |
| 短信失败自动降级邮件 | 已完成 |
| 短信投递可追溯 | 已完成 |
| 同一告警不重复建事件 | 已完成 |

### 溯源信息

- 实施批次：`notifications-phase2`
- 实施日期：2026-11-21
- 关联需求：短信主动通知、失败降级、投递可追溯
- Git 提交：`2eeda84`（feat(notifications): phase 2 add sms channel and fallback）
- 远程分支：`origin/feature/ci-image-build-monitor`

## Phase 3：语音播报

### 目标

- 接入语音电话服务商，支持配置与测试呼叫
- critical 告警优先语音播报
- 语音失败后自动降级短信/邮件
- 投递记录支持语音渠道

### 完成内容

1. 语音通道：
   - `backend/notifications/voice.py`
     - `voice_config` 读取/保存
     - AccessKey Secret 加密存储
     - 阿里云语音通知 `SingleCallByTts` 调用
     - `provider=mock` 本地联调模式
   - 支持启用开关、主叫号码、TTS 模板、模板变量、Region、重复播报次数
   - 支持 `apply_to=critical/all` 与 `fallback_enabled`

2. 调度器与降级：
   - `backend/notifications/service.py`
     - 渠道顺序升级为 `语音 → 短信 → 邮件`
     - 新增 `send_voice()` 与语音投递记录
     - 语音接收人复用联系人手机号解析
     - 语音成功即停止；语音失败继续短信/邮件
     - 未启用的渠道自动跳过

3. 业务接入：
   - 镜像构建告警、资源告警的 critical 事件自动使用语音优先链路
   - 每日报告仍保持邮件通道

4. API：
   - `GET /api/v1/notifications/channels/voice`
   - `PUT /api/v1/notifications/channels/voice`
   - `POST /api/v1/notifications/channels/voice/test`

5. 前端：
   - `frontend/src/components/notifications/VoicePanel.tsx`
     - 语音配置、降级策略、测试呼叫
   - 「通知服务 → 语音电话」Tab 由占位替换为可用面板
   - 服务与 hooks 增加语音配置和测试能力

### 变更文件清单

后端：

- `backend/notifications/voice.py`（新增）
- `backend/notifications/service.py`
- `backend/api/v1/notifications.py`
- `backend/contracts/schemas/notifications.py`

前端：

- `frontend/src/components/notifications/VoicePanel.tsx`（新增）
- `frontend/src/hooks/useNotifications.ts`
- `frontend/src/services/notifications.ts`
- `frontend/src/pages/SmtpConfig.tsx`

文档：

- `docs/notification-center-progress.md`

### 验证记录

- 后端相关文件 `python -m py_compile` 通过。
- 前端 `pnpm build`（含 `tsc`）通过。
- API 验证：
  - 保存语音配置成功，Secret 不回显
  - `provider=mock` 测试语音发起成功，生成语音投递记录
- 链路验证：
  - critical 渠道解析为 `["voice","email"]`（短信未启用时）
  - mock 语音成功后停止，不再发送邮件
  - 语音配置失败后继续尝试邮件，投递记录包含两个渠道结果
- 测试数据已清理。
- 容器状态：`dev-backend-1`、`dev-frontend-1` 均为 healthy。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 可配置语音服务商与 TTS 模板 | 已完成 |
| 可发起测试语音呼叫 | 已完成 |
| critical 告警语音优先 | 已完成 |
| 语音失败自动降级短信/邮件 | 已完成 |
| 语音投递可追溯 | 已完成 |

### 溯源信息

- 实施批次：`notifications-phase3`
- 实施日期：2026-11-21
- 关联需求：语音播报、主动触达、多级降级
- Git 提交：`869a409`（feat(notifications): phase 3 add voice channel and fallback）
- 远程分支：`origin/feature/ci-image-build-monitor`

## 变更记录

| 日期 | 阶段 | 说明 |
| --- | --- | --- |
| 2026-11-21 | Phase 0 | 完成通知服务改名、邮件 Tab 化、NotificationDispatcher 收敛 |
| 2026-11-21 | Phase 0 | 修复 Dockerfile 未复制 `backend/notifications/` 导致后端容器重启 |
| 2026-11-21 | Phase 0 | 提交并推送到 `origin/feature/ci-image-build-monitor`（`e9597ab`） |
| 2026-11-21 | Phase 1 | 完成联系人、联系人组、通知事件与投递记录 |
| 2026-11-21 | Phase 2 | 完成短信通道、配置测试与短信失败降级邮件 |
| 2026-11-21 | Phase 3 | 完成语音通道、测试呼叫与语音失败降级 |
