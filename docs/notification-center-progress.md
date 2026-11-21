# 通知服务实施进度

- 设计文档：`docs/notification-center-design.md`
- 创建日期：2026-11-21
- 当前阶段：Phase 6.1 已完成（生产化阶段暂停）
- 分支：`feature/ci-image-build-monitor`

## 阶段进度总览

| 阶段 | 内容 | 状态 | 完成日期 |
| --- | --- | --- | --- |
| Phase 0 | 改名与邮件入口收敛 | 已完成 | 2026-11-21 |
| Phase 1 | 联系人 + 投递记录 | 已完成 | 2026-11-21 |
| Phase 2 | 短信 + 降级 | 已完成 | 2026-11-21 |
| Phase 3 | 语音播报 | 已完成 | 2026-11-21 |
| Phase 4 | IVR 按键动作 | 已完成 | 2026-11-21 |
| Phase 5 | 升级与运营 | 已完成 | 2026-11-21 |
| Phase 6 | 通知服务页面重构 | 已完成 | 2026-11-21 |
| Phase 7 | 生产化落地 | 暂停（等待团队申请结果） | - |

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

## Phase 4：IVR 按键动作

### 目标

- 接入语音服务商回调接口
- 解析 DTMF 按键并执行动作
- 回调 token、幂等、白名单与审计
- 高危动作二次确认
- 补充 GitHub 重试/取消能力

### 完成内容

1. IVR 数据与回调：
   - 新增 `notification_actions` 表与 `NotificationAction` 模型
     - 记录 call_id、按键、动作、阶段、状态、执行结果
     - `(provider_call_id, digit, stage)` 唯一约束保证回调幂等
   - 新增 `backend/notifications/ivr.py`
     - 回调 token 校验
     - 支持 `call_id/CallId`、`digit/Dtmf`、`phone/CalledNumber` 字段映射
     - 手机号白名单校验
     - 按 `provider_message_id` 关联语音投递记录

2. 按键动作：
   - 默认映射：`1=retry`、`2=abort`、`3=ack`、`9=ignore`
   - `ack/ignore` 立即执行
   - `retry/abort` 需二次确认：按 1 确认，按 0 取消
   - 高危动作限制来源事件，默认仅 `image_build_monitor`
   - 记录动作状态、结果与执行时间

3. GitHub 能力：
   - `GitHubClient.rerun_failed_jobs(run_id)`
   - `GitHubClient.cancel_run(run_id)`
   - 修复 `_request` 对 201/204 空响应体的处理

4. API：
   - `POST /api/v1/notifications/voice/callback?token=...`（公开回调 + token 校验）
   - `GET /api/v1/notifications/actions`

5. 前端：
   - `VoicePanel` 增加 IVR 配置
     - 启用 IVR、回调 Token、允许来源、允许手机号
     - 展示回调地址
   - 增加最近 IVR 操作审计表格

### 变更文件清单

后端：

- `backend/notifications/ivr.py`（新增）
- `backend/notifications/voice.py`
- `backend/notifications/service.py`（Phase 4 未新增逻辑，沿用渠道链路）
- `backend/api/v1/notifications.py`
- `backend/contracts/schemas/notifications.py`
- `backend/infrastructure/persistence/models/notifications.py`
- `backend/infrastructure/persistence/models/__init__.py`
- `backend/infrastructure/clients/github_client.py`
- `database/migrations/notifications.py`

前端：

- `frontend/src/components/notifications/VoicePanel.tsx`
- `frontend/src/hooks/useNotifications.ts`
- `frontend/src/services/notifications.ts`

文档：

- `docs/notification-center-progress.md`

### 验证记录

- 后端相关文件 `python -m py_compile` 通过。
- 前端 `pnpm build`（含 `tsc`）通过。
- 迁移验证：`notification_actions` 表创建成功。
- 回调验证：
  - 错误 token 返回“回调 token 校验失败”
  - `digit=3` 立即执行确认动作成功
  - `digit=1` 对 `retry` 返回二次确认提示
  - `digit=0` 成功取消待确认动作
- 幂等约束：同一 `(call_id, digit, stage)` 重复回调复用记录。
- 测试事件与动作已清理。
- 前后端容器均 healthy。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 语音回调可正确落库 | 已完成 |
| 按键可触发确认/忽略 | 已完成 |
| 重试/取消需二次确认 | 已完成 |
| 回调 token 校验 | 已完成 |
| 手机号来源白名单 | 已完成 |
| 高危动作有来源限制 | 已完成 |
| 重复回调幂等 | 已完成 |
| 操作审计可查询 | 已完成 |

### 溯源信息

- 实施批次：`notifications-phase4`
- 实施日期：2026-11-21
- 关联需求：IVR 按键、二次确认、审计、GitHub 操作
- Git 提交：`702f96c`（feat(notifications): phase 4 add ivr callbacks and actions）
- 远程分支：`origin/feature/ci-image-build-monitor`

## Phase 5：升级与运营

### 目标

- 静默时段、冷却与告警风暴抑制
- 主渠道失败后升级到备用联系人组
- 值班组配置
- 通知成功率、渠道表现、IVR 动作指标
- 前端策略配置与运营指标展示

### 完成内容

1. 通知策略：
   - `backend/notifications/policy.py`
     - `notification_policy_config` 读取/保存
     - 静默时段：静默期内跳过语音/短信，保留邮件
     - 冷却时间：相同 `dedupe_key` 冷却期内跳过语音/短信
     - 风暴抑制：同来源事件数超过阈值时跳过语音/短信
     - 升级链：主渠道全部失败后使用升级联系人组继续尝试
     - 值班组配置

2. 调度器：
   - `dispatch()` 增加策略判断与抑制原因返回
   - 增加 `_group_recipients()` 解析联系人组邮箱/手机号
   - 主渠道失败后按 `requested_channels` 对升级组重试语音/短信/邮件
   - 返回 `suppressed/suppressed_reasons/escalated`
   - 新增 `get_metrics()` 统计事件、投递、渠道成功率、IVR 动作

3. API：
   - `GET/PUT /api/v1/notifications/policy`
   - `GET /api/v1/notifications/metrics?days=7`

4. 前端：
   - `PolicyPanel.tsx`：静默时段、冷却、风暴阈值、值班组、升级组
   - `MetricsPanel.tsx`：事件数、投递数、成功率、渠道表现、IVR 动作
   - 「通知策略」「运营指标」Tab

### 变更文件清单

后端：

- `backend/notifications/policy.py`（新增）
- `backend/notifications/service.py`
- `backend/api/v1/notifications.py`
- `backend/contracts/schemas/notifications.py`

前端：

- `frontend/src/components/notifications/PolicyPanel.tsx`（新增）
- `frontend/src/components/notifications/MetricsPanel.tsx`（新增）
- `frontend/src/hooks/useNotifications.ts`
- `frontend/src/services/notifications.ts`
- `frontend/src/pages/SmtpConfig.tsx`

文档：

- `docs/notification-center-progress.md`

### 验证记录

- 后端相关文件 `python -m py_compile` 通过。
- 前端 `pnpm build`（含 `tsc`）通过。
- API 验证：策略读写、指标查询正常。
- 静默抑制：`quiet_hours` 场景下语音/短信被跳过，仅保留邮件。
- 风暴抑制：同来源事件达到阈值后返回 `storm`，语音/短信被跳过。
- 升级链：主渠道失败后成功使用升级组手机号发送短信，返回 `escalated=true`。
- 修复升级链丢失短信/语音渠道的问题。
- 测试数据已清理，策略与通道配置已恢复默认关闭。
- 前后端容器均 healthy。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 静默时段生效 | 已完成 |
| 冷却与风暴抑制生效 | 已完成 |
| 失败升级到备用联系人组 | 已完成 |
| 值班组/升级组可配置 | 已完成 |
| 渠道成功率与 IVR 指标可查 | 已完成 |
| 前端策略与指标页面 | 已完成 |

### 溯源信息

- 实施批次：`notifications-phase5`
- 实施日期：2026-11-21
- 关联需求：升级链、运营指标、告警风暴抑制
- Git 提交：`77b567f`（feat(notifications): phase 5 add policies escalation and metrics）
- 远程分支：`origin/feature/ci-image-build-monitor`

## Phase 6：通知服务页面重构

### 背景

生产化落地需要等待团队完成组邮箱、短信签名、语音模板和公网回调等申请，因此暂停 Phase 7，先对通知服务模块进行页面设计重构，解决配置页面拥挤、输入框过长、层次不清晰的问题。

### 目标

- 统一通知服务页面设计语言
- 建立清晰的页面标题、状态概览、导航、面板层级
- 限制表单宽度，使用栅格布局
- 提升可读性、可操作性和信息密度

### 完成内容

1. 新增页面样式 `frontend/src/pages/NotificationCenter.css`
   - 页面最大宽度、居中布局
   - 顶部标题区、页面图标、状态标签
   - 胶囊式 Tab 导航
   - 面板标题、描述、操作区统一样式
   - 12 栅格表单布局与响应式断点
   - 分区标题、提示区统一样式

2. 重构通知服务主页面 `SmtpConfig.tsx`
   - 新增页面级标题与说明
   - 顶部显示邮件/短信/语音配置状态标签
   - Tab 增加图标与胶囊式导航
   - 邮件面板改为统一面板头 + 状态提示 + 分区表单
   - SMTP 配置改为栅格布局：地址/端口、用户名/密码、发件人/TLS
   - 表单最大宽度限制，避免输入框过长

3. 统一子面板样式与组件排布
   - 新增 `ui.tsx`：`PanelHeader`、`SectionCard`、`ToggleRow` 统一面板结构
   - 联系人、发送记录、通知策略、运营指标、短信、语音面板统一 `nc-panel`
   - 短信/语音拆分为「启用策略 / 服务商参数 / 接收人与测试 / IVR」分区卡片
   - 通知策略拆分为静默、冷却与风暴、升级链分区卡片，时间字段改为 TimePicker
   - 邮件通道拆分为连接参数、发件身份分区卡片
   - 高频开关改为左右对齐的 ToggleRow，表单采用 12 栅格紧凑排布
   - 测试手机号、回调 Token、升级组等长字段限制最大宽度
   - 表格页面保持现有信息密度

### 变更文件清单

前端：

- `frontend/src/pages/NotificationCenter.css`（新增）
- `frontend/src/components/notifications/ui.tsx`（新增）
- `frontend/src/pages/SmtpConfig.tsx`
- `frontend/src/components/notifications/ContactsPanel.tsx`
- `frontend/src/components/notifications/DeliveriesPanel.tsx`
- `frontend/src/components/notifications/SmsPanel.tsx`
- `frontend/src/components/notifications/VoicePanel.tsx`
- `frontend/src/components/notifications/PolicyPanel.tsx`
- `frontend/src/components/notifications/MetricsPanel.tsx`

文档：

- `docs/notification-center-progress.md`

### 验证记录

- 前端 `pnpm build`（含 `tsc`）通过。
- 新前端镜像已启动，`dev-frontend-1` 状态 healthy。
- 页面结构、Tab、表单栅格与样式类已完成接入。
- 未修改后端接口与数据结构。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 页面标题与状态概览清晰 | 已完成 |
| 导航层次分明 | 已完成 |
| 配置表单宽度合理 | 已完成 |
| 子面板样式统一 | 已完成 |
| 不影响已有功能 | 已完成 |

### 溯源信息

- 实施批次：`notifications-phase6-ui`
- 实施日期：2026-11-21
- 关联需求：通知服务页面设计重构
- Git 提交：`1fbe498`（文档 `4c0811b`），已推送

## Phase 7：生产化落地（暂停）

因团队需要先完成组邮箱、短信签名与模板、语音 TTS 模板、公网回调地址等申请，Phase 7 暂停，等待配置答复后继续。

## Phase 6.1：交互优化、多服务商配置与演示数据

### 目标

- 将短信/语音的测试入口统一收敛到面板顶部按钮，点击弹出测试弹窗。
- 优化开关控件的排布与视觉呈现。
- 短信与语音支持维护多套服务商配置，可选择其中一套启用。
- 为联系人、发送记录、运营指标提供演示数据，便于本地预览。

### 完成内容

- 后端 `notifications/sms.py`、`notifications/voice.py` 改造为多服务商配置结构，兼容历史单配置数据并自动迁移读取。
- 契约层为 `SmsConfigUpdate`、`VoiceConfigUpdate` 增加 `providers` 与 `active_provider_id`。
- 短信/语音面板改为服务商卡片列表，可新增、删除、切换启用配置，密钥留空保留原值。
- 测试功能移至面板顶部按钮并弹出 Modal，测试结果在弹窗内展示。
- `ToggleRow` 改为独立卡片式条目，开关右对齐并垂直居中，增加 hover 反馈。
- `database/seed_local_demo.py` 新增 `seed_notifications`，写入 4 位联系人、值班组、14 条事件与 34 条投递记录，支持幂等重跑。

### 变更文件

- `backend/notifications/sms.py`
- `backend/notifications/voice.py`
- `backend/contracts/schemas/notifications.py`
- `frontend/src/services/notifications.ts`
- `frontend/src/components/notifications/ui.tsx`
- `frontend/src/components/notifications/SmsPanel.tsx`
- `frontend/src/components/notifications/VoicePanel.tsx`
- `frontend/src/pages/NotificationCenter.css`
- `database/seed_local_demo.py`

### 验证记录

- 前端 `docker compose build frontend`（含 `tsc`）通过。
- Python 变更文件 `py_compile` 通过。
- 本地执行 `seed_local_demo.py --skip-files` 成功。
- `/channels/sms`、`/channels/voice` 返回 `providers` 列表与 `active_provider_id`。
- `/deliveries` 返回 34 条演示投递；`/metrics?days=7` 返回 14 个事件、34 次投递。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 测试功能位于顶部按钮并弹窗 | 已完成 |
| 开关位置与展示优化 | 已完成 |
| 短信支持多配置切换 | 已完成 |
| 语音支持多配置切换 | 已完成 |
| 联系人/发送记录/指标有演示数据 | 已完成 |

### 溯源信息

- 实施批次：`notifications-phase6.1-interaction`
- 实施日期：2026-11-21
- Git 提交：`1fbe498`（文档 `4c0811b`），已推送

## 变更记录

| 日期 | 阶段 | 说明 |
| --- | --- | --- |
| 2026-11-21 | Phase 0 | 完成通知服务改名、邮件 Tab 化、NotificationDispatcher 收敛 |
| 2026-11-21 | Phase 0 | 修复 Dockerfile 未复制 `backend/notifications/` 导致后端容器重启 |
| 2026-11-21 | Phase 0 | 提交并推送到 `origin/feature/ci-image-build-monitor`（`e9597ab`） |
| 2026-11-21 | Phase 1 | 完成联系人、联系人组、通知事件与投递记录 |
| 2026-11-21 | Phase 2 | 完成短信通道、配置测试与短信失败降级邮件 |
| 2026-11-21 | Phase 3 | 完成语音通道、测试呼叫与语音失败降级 |
| 2026-11-21 | Phase 4 | 完成 IVR 回调、按键动作、二次确认与审计 |
| 2026-11-21 | Phase 5 | 完成静默/冷却/风暴抑制、升级链与运营指标 |
| 2026-11-21 | Phase 6 | 完成通知服务页面布局、表单宽度与视觉层级重构 |
| 2026-11-21 | Phase 6.1 | 测试入口改为顶部按钮弹窗，开关卡片化优化 |
| 2026-11-21 | Phase 6.1 | 短信/语音支持多服务商配置新增与切换启用 |
| 2026-11-21 | Phase 6.1 | 新增联系人、投递记录、运营指标演示数据种子 |
| 2026-11-21 | Phase 7 | 生产化落地暂停，等待团队申请结果 |
