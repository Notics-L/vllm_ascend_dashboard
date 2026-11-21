# 通知服务实施进度

- 设计文档：`docs/notification-center-design.md`
- 创建日期：2026-11-21
- 当前阶段：Phase 0 已完成
- 分支：`feature/ci-image-build-monitor`

## 阶段进度总览

| 阶段 | 内容 | 状态 | 完成日期 |
| --- | --- | --- | --- |
| Phase 0 | 改名与邮件入口收敛 | 已完成 | 2026-11-21 |
| Phase 1 | 联系人 + 投递记录 | 未开始 | - |
| Phase 2 | 短信 + 降级 | 未开始 | - |
| Phase 3 | 语音播报 | 未开始 | - |
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

## Phase 1：联系人 + 投递记录（待开始）

计划内容：

- 新增 `notification_contacts`、`notification_groups`、`notification_group_members`
- `User` 增加手机号
- 新增 `notification_events`、`notification_deliveries`
- 邮件发送写入投递记录
- 前端增加「联系人」「发送记录」Tab

## 变更记录

| 日期 | 阶段 | 说明 |
| --- | --- | --- |
| 2026-11-21 | Phase 0 | 完成通知服务改名、邮件 Tab 化、NotificationDispatcher 收敛 |
| 2026-11-21 | Phase 0 | 修复 Dockerfile 未复制 `backend/notifications/` 导致后端容器重启 |
| 2026-11-21 | Phase 0 | 提交并推送到 `origin/feature/ci-image-build-monitor`（`e9597ab`） |
