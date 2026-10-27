# 《发布件管理》模块技术设计文档

## 1. 概述

### 1.1 背景

项目现有的 `analysis_reports/` 目录下有一组独立脚本（`analyze_image_build.py`、`analyze_nightly_with_base.py`、`analyze_dashboard.py`），用于从 GitHub Actions API 拉取镜像构建工作流的运行数据，进行层级化耗时分析，并输出 Excel 报告和 HTML 看板。

这些脚本存在以下问题：
- **硬编码配置**：GitHub Token、Owner/Repo 直接写在脚本中
- **单一工作流**：每次只能针对固定的工作流进行分析
- **独立运行**：未纳入看板系统，无用户权限、无缓存、无定时刷新
- **输出格式有限**：Excel 手动生成，HTML 静态页面，无交互式图表

本设计将这批脚本逻辑重构成看板系统中"研发交付"下的一个新模块——**发布件管理**，实现：
1. 多仓库/多工作流的动态选择与分析
2. 构建流程全链路可视化（ECharts 图表）
3. 三方件版本信息管理与监控
4. Excel/PDF 报告导出
5. AI 驱动的数据可信度判定与生产级评估建议
6. 复用项目已有的 GitHub Client、LLM Client、设置管理等基础设施

### 1.2 目标

| 目标 | 说明 |
|------|------|
| 工作流发现 | 用户可选择目标仓库，系统自动列出所有 Actions workflow |
| 多维度分析 | 支持按 workflow、时间范围、分支、状态等维度筛选 |
| 层级化耗时 | 保留原有的 Workflow → Job Group → Phase → Sub-Job → Step 层级分析 |
| 三方件管理 | 监控镜像构建中使用的三方依赖版本（CANN、torch-npu、Python 包等） |
| AI 可信评估 | 结合算法统计和 AI 分析，输出数据可信度判定和生产级建议 |
| PDF 导出 | 将 HTML 看板替换为可打印的 PDF 报告（含图表） |
| Excel 导出 | 保留原有 Excel 导出能力，作为数据底稿 |

## 2. 系统架构

### 2.1 模块位置

```
vllm_ascend_dashboard/
├── backend/
│   ├── release_management/          # 新增模块目录
│   │   ├── __init__.py
│   │   ├── service.py               # 核心业务逻辑
│   │   ├── collector.py             # GitHub Actions 数据采集
│   │   ├── parser.py                # 工作流/Job 名称解析器
│   │   ├── analyzer.py              # 耗时统计与聚合分析
│   │   ├── third_party_service.py   # 三方件版本管理
│   │   ├── ai_evaluator.py          # AI 可信度评估
│   │   ├── report_generator.py      # PDF/Excel 报告生成
│   │   └── schemas.py               # Pydantic schema（如需要模块级 schema）
│   ├── api/v1/
│   │   └── release_management.py    # REST API 路由
│   ├── contracts/schemas/
│   │   └── (在 __init__.py 中新增相关 schema)
│   └── infrastructure/persistence/models/
│       └── release_management.py    # 数据库模型
│
├── frontend/
│   ├── src/pages/
│   │   └── ReleaseManagement.tsx    # 主看板页面
│   ├── src/components/release/
│   │   ├── WorkflowSelector.tsx      # 工作流选择器
│   │   ├── BuildTimelineChart.tsx    # 构建时间线图表
│   │   ├── PhaseBreakdownChart.tsx   # Phase 耗时分解
│   │   ├── StepRankingChart.tsx      # Step 排名图表
│   │   ├── ThirdPartyTable.tsx       # 三方件版本表格
│   │   ├── AIEvaluationPanel.tsx     # AI 评估面板
│   │   └── ExportButtons.tsx         # 导出按钮组
│   └── src/services/
│       └── releaseManagement.ts      # API 调用层
```

### 2.2 数据流

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  用户前端     │────→│  Backend API     │────→│  GitHub API     │
│  (选择仓库/   │     │  (service layer) │     │  (workflows,    │
│   workflow)  │     │                  │     │   runs, jobs)   │
└──────────────┘     └────────┬─────────┘     └─────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   解析器 (parser)  │  Job 名称 → 层级结构
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │  分析器 (analyzer) │  聚合统计 + 趋势
                    └─────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────▼──────┐ ┌─────▼────────┐ ┌────▼──────────┐
    │  数据库缓存     │ │ AI 评估服务   │ │ 报告生成器     │
    │  (持久化结果)   │ │ (可信度判定)  │ │ (PDF/Excel)   │
    └────────────────┘ └──────────────┘ └───────────────┘
              │               │               │
              └───────────────┼───────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  ECharts 前端渲染  │
                    └───────────────────┘
```

## 3. 基础设施复用

### 3.1 GitHub API 客户端

复用已有的 `backend/infrastructure/clients/github_client.py` 中的 `GitHubClient`：

- `get_workflow_runs()` → 获取指定 workflow 的运行记录
- `list_jobs_for_run()` → 获取单次运行的 Job 列表
- `get_releases()` / `get_tags()` → 获取 Release 和 Tag 信息
- 新增 `list_workflows()` → 列出仓库所有 workflow（用于工作流选择器）

**配置来源**：从系统设置中读取 `GITHUB_TOKEN`，支持多仓库配置（复用 `GitHubAPIService` 中已有的 `settings.GITHUB_OWNER` / `settings.GITHUB_REPO` 机制）。

### 3.2 LLM 客户端

复用已有的 `backend/infrastructure/clients/llm_client.py` 中的 `LLMClient`：

- 调用方式与贡献者分析的 `AIReportService` 一致
- 从数据库 `LLMProviderConfig` 表中获取启用的 provider 配置
- API 调用前检测配置是否完整（provider、model、api_key、api_base）

### 3.3 配置校验

在进入分析流程前，进行配置检测，逻辑参考贡献者分析模块：

```python
async def check_prerequisites():
    """检查 GitHub Token 和 LLM 配置"""
    result = {
        "github_configured": bool(settings.GITHUB_TOKEN),
        "llm_configured": False,
        "llm_providers": [],
    }
    
    # 检查 LLM 配置
    async with async_session() as db:
        stmt = select(LLMProviderConfig).where(LLMProviderConfig.enabled)
        result = await db.execute(stmt)
        providers = result.scalars().all()
        result["llm_providers"] = [
            {"provider": p.provider, "display_name": p.display_name, "is_active": p.is_active}
            for p in providers
        ]
        result["llm_configured"] = any(p.is_active for p in providers)
    
    return result
```

前端在页面加载时调用 `/release-management/config-check`，若 GitHub Token 未配置则显示引导提示，若 LLM 未配置则标记"AI 评估"区域为不可用状态（算法统计部分仍可使用）。

## 4. 数据库设计

### 4.1 核心表

```sql
-- 工作流配置表（用户可分析的工作流列表）
CREATE TABLE release_workflows (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    repo_owner VARCHAR(255) NOT NULL,
    repo_name VARCHAR(255) NOT NULL,
    workflow_id BIGINT NOT NULL COMMENT 'GitHub workflow ID',
    workflow_name VARCHAR(500) NOT NULL COMMENT '工作流显示名称',
    workflow_path VARCHAR(500) NOT NULL COMMENT '.github/workflows/xxx.yaml',
    workflow_type VARCHAR(50) DEFAULT 'image_build' COMMENT 'image_build / test / deploy / other',
    enabled TINYINT(1) DEFAULT 1,
    last_sync_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_workflow (repo_owner, repo_name, workflow_path)
);

-- 构建运行记录表（每次 workflow run 的聚合数据）
CREATE TABLE release_build_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workflow_id BIGINT NOT NULL COMMENT '关联 release_workflows.id',
    github_run_id BIGINT NOT NULL COMMENT 'GitHub run ID',
    status VARCHAR(50) NOT NULL COMMENT 'success / failure / cancelled / skipped',
    event VARCHAR(50) COMMENT 'schedule / push / pull_request / workflow_dispatch',
    branch VARCHAR(255) COMMENT 'head_branch 或 head_tag',
    commit_sha VARCHAR(40),
    created_at_remote DATETIME COMMENT 'GitHub created_at',
    started_at_remote DATETIME COMMENT 'GitHub run_started_at',
    completed_at_remote DATETIME COMMENT 'GitHub updated_at',
    wait_duration FLOAT DEFAULT 0 COMMENT '排队时间 (秒)',
    exec_duration FLOAT DEFAULT 0 COMMENT '执行时间 (秒)',
    wall_duration FLOAT DEFAULT 0 COMMENT '墙钟总时间 (秒)',
    num_jobs INT DEFAULT 0,
    conclusion_summary JSON COMMENT '{"total":10,"success":8,"failure":1,"skipped":1}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_github_run (workflow_id, github_run_id),
    INDEX idx_workflow_status (workflow_id, status),
    INDEX idx_created (created_at_remote)
);

-- Job 层级数据表（单次运行中的 Job Group 聚合）
CREATE TABLE release_build_groups (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id BIGINT NOT NULL COMMENT '关联 release_build_runs.id',
    group_name VARCHAR(500) NOT NULL,
    target VARCHAR(100) COMMENT '如 a2, 310p',
    os_name VARCHAR(50) COMMENT '如 ubuntu, openeuler',
    phase VARCHAR(50) COMMENT '如 base, nightly, build, merge',
    status VARCHAR(50),
    total_duration FLOAT DEFAULT 0,
    runner_name VARCHAR(255),
    architecture VARCHAR(20) COMMENT 'arm64 / amd64',
    sub_job_name VARCHAR(500),
    steps_json JSON COMMENT 'step 明细数组',
    INDEX idx_run (run_id),
    INDEX idx_target_phase (target, phase)
);

-- 三方件版本记录表
CREATE TABLE release_third_party_versions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id BIGINT NOT NULL COMMENT '关联 release_build_runs.id',
    component_name VARCHAR(255) NOT NULL COMMENT '如 CANN, torch-npu, numpy',
    component_version VARCHAR(100) NOT NULL COMMENT '如 8.0.RC1, 2.4.0',
    component_category VARCHAR(50) COMMENT 'framework / runtime / library / tool',
    source_type VARCHAR(50) COMMENT 'dockerfile / requirements / build_log',
    source_line TEXT COMMENT '原始来源行/上下文',
    detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_run (run_id),
    INDEX idx_component (component_name, component_version)
);

-- 第三方组件基线表（用户定义的可信版本基线）
CREATE TABLE release_third_party_baselines (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    component_name VARCHAR(255) NOT NULL,
    component_category VARCHAR(50),
    approved_versions JSON COMMENT '["8.0.RC1", "8.0.0"] 批准版本列表',
    min_version VARCHAR(100) COMMENT '最低允许版本',
    max_version VARCHAR(100) COMMENT '最高允许版本',
    notes TEXT COMMENT '备注：来源、测试记录链接等',
    is_active TINYINT(1) DEFAULT 1,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_component (component_name)
);

-- AI 评估报告表
CREATE TABLE release_ai_evaluations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workflow_id BIGINT NOT NULL,
    evaluation_type VARCHAR(50) COMMENT 'trustworthiness / production_ready / trend',
    period_start DATE,
    period_end DATE,
    run_count INT DEFAULT 0,
    trust_score FLOAT COMMENT '整体可信度分数 0-100',
    trust_level VARCHAR(20) COMMENT 'high / medium / low',
    production_ready BOOLEAN DEFAULT FALSE,
    llm_model VARCHAR(100),
    executive_summary TEXT,
    findings JSON COMMENT '核心发现列表',
    recommendations JSON COMMENT '建议列表',
    risk_factors JSON COMMENT '风险因子列表',
    data_quality_issues JSON COMMENT '数据质量问题',
    report_content TEXT,
    generated_by VARCHAR(255),
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_workflow (workflow_id),
    INDEX idx_generated (generated_at)
);

-- 分析任务调度表
CREATE TABLE release_analysis_tasks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workflow_id BIGINT NOT NULL,
    task_type VARCHAR(50) COMMENT 'sync_runs / analyze / generate_report / evaluate',
    status VARCHAR(50) DEFAULT 'pending' COMMENT 'pending / running / completed / failed',
    progress INT DEFAULT 0 COMMENT '0-100',
    total_items INT DEFAULT 0,
    processed_items INT DEFAULT 0,
    error_message TEXT,
    started_at DATETIME,
    completed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_workflow_status (workflow_id, status)
);
```

### 4.2 数据同步策略

| 数据类型 | 同步频率 | 策略 |
|----------|----------|------|
| workflow 列表 | 首次 + 手动刷新 | 全量拉取，upsert 到 `release_workflows` |
| build runs | 定时 30min / 手动触发 |增量同步（按 `created_at_remote` 去重）|
| Job/Step 数据 | 随 run 同步 | 一次拉取，缓存到 `release_build_groups` |
| 三方件版本 | 随 run 同步 | 从 Dockerfile / 构建日志中解析 |
| AI 评估 | 手动触发 / 每日定时 | 基于缓存数据，不重复查 GitHub |

## 5. 后端 API 设计

所有接口前缀：`/api/v1/release-management`

### 5.1 配置与发现

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/config-check` | 检查 GitHub Token 和 LLM 配置状态 |
| GET | `/workflows` | 列出所有可分析的工作流（从数据库 + 可选实时发现） |
| POST | `/workflows/discover` | 从 GitHub 拉取最新 workflow 列表并注册 |
| PUT | `/workflows/{id}/toggle` | 启用/禁用某个工作流的分析 |

### 5.2 数据采集

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/sync` | 手动触发数据同步（指定 workflow_id, days_back） |
| GET | `/sync/status` | 查询同步任务状态 |
| GET | `/sync/tasks` | 查询同步任务列表 |

### 5.3 看板数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/overview` | 获取看板概览数据（KPI + 各图表数据） |
| GET | `/runs` | 获取运行记录列表（分页、筛选） |
| GET | `/runs/{runId}` | 获取单次运行详情（Job Group 层级 + steps） |
| GET | `/runs/{runId}/third-party` | 获取某次运行的三方件版本信息 |
| GET | `/trends/duration` | 耗时趋势数据（按天/周聚合） |
| GET | `/trends/phases` | Phase 耗时对比 |
| GET | `/trends/steps` | Top Step 排名 |
| GET | `/trends/architecture` | 架构对比 (arm64 vs amd64) |

### 5.4 三方件管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/third-party` | 三方件版本总览（按组件聚合） |
| GET | `/third-party/components` | 组件列表（去重） |
| GET | `/third-party/version-history` | 指定组件的版本变迁历史 |
| GET | `/third-party/baselines` | 获取基线配置列表 |
| POST | `/third-party/baselines` | 创建/更新基线配置 |
| DELETE | `/third-party/baselines/{id}` | 删除基线配置 |
| GET | `/third-party/compliance` | 合规检查结果（当前版本 vs 基线） |

### 5.5 AI 评估

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/evaluate` | 触发 AI 评估（trustworthiness / production_ready） |
| GET | `/evaluations` | AI 评估报告列表 |
| GET | `/evaluations/{id}` | AI 评估报告详情 |
| GET | `/evaluations/latest` | 获取最新评估报告 |

### 5.6 报告导出

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/export/pdf` | 生成 PDF 报告 |
| POST | `/export/excel` | 生成 Excel 报告（保留原有能力） |
| GET | `/export/status/{taskId}` | 查询导出任务状态 |
| GET | `/export/download/{taskId}` | 下载导出的文件 |

## 6. 前端页面设计

### 6.1 页面结构

```
发布件管理
├── Tab: 构建分析（默认）
│   ├── 顶部工具栏
│   │   ├── 仓库选择器（下拉）
│   │   ├── 工作流选择器（多选）
│   │   ├── 时间范围选择器
│   │   ├── 分支筛选
│   │   ├── 状态筛选
│   │   └── 同步/导出 按钮组
│   ├── KPI 卡片行
│   │   ├── 总运行次数 | 成功率 | 平均耗时 | 波动系数 | 异常构建数
│   ├── 图表区（2×2 网格）
│   │   ├── [1] 构建耗时趋势图（折线图 + 面积填充）
│   │   ├── [2] 排队 vs 执行分解图（堆叠柱状图）
│   │   ├── [3] Phase 耗时饼图/环形图
│   │   ├── [4] 架构对比图（arm64 vs amd64 分组柱状图）
│   ├── 数据表区（1×2 网格）
│   │   ├── [5] Target/OS 组合耗时表
│   │   └── [6] Top 最耗时 Step 排名表
│   └── 运行记录列表
│       └── 点击行 → 右侧展开详情抽屉（Job Group 层级树 + Step 耗时条）
│
├── Tab: 三方件监控
│   ├── 组件概览卡片
│   │   ├── 组件总数 | 变更组件数 | 基线不合规数
│   ├── 三方件版本表（支持搜索/筛选/排序）
│   │   ├── 列：组件名 | 最新版本 | 历史版本数 | 变化趋势 | 基线状态
│   ├── 组件版本变迁时间线
│   │   └── 横向时间轴，展示每次构建中组件版本的变化
│   └── 基线管理面板
│       └── 可配置每个组件的批准版本范围
│
├── Tab: AI 评估
│   ├── 评估触发区
│   │   ├── 选择评估类型（数据可信度 / 生产级评估）
│   │   ├── 选择时间范围
│   │   ├── 选择 LLM 模型
│   │   └── 生成按钮
│   ├── 最新评估报告
│   │   ├── 可信度评分（仪表盘）
│   │   ├── 核心发现（按严重程度分类）
│   │   ├── 风险因子
│   │   ├── 数据质量问题
│   │   └── 生产级建议
│   └── 历史评估报告列表
```

### 6.2 ECharts 图表类型

| 图表 | 类型 | 交互 |
|------|------|------|
| 构建耗时趋势 | Line + Area | 悬浮 tooltip 显示详细时间，点击下钻到单次运行 |
| 排队 vs 执行 | Stacked Bar | 堆叠显示排队和执行时间，颜色区分状态 |
| Phase 耗时 | Pie / Donut | 各 phase 占比，点击筛选 |
| 架构对比 | Grouped Bar | arm64/amd64 分组对比 |
| Step 排名 | Horizontal Bar | Top N 排名，按耗时降序 |
| 三方件版本变迁 | Timeline (自定义) | 时间轴上标注版本变化点 |
| AI 可信度 | Gauge | 0-100 分仪表盘 |
| 成功率趋势 | Line + MarkLine | 显示平均值线、目标线 |

## 7. 核心业务逻辑

### 7.1 工作流发现与注册

```python
# release_management/collector.py

class ReleaseWorkflowCollector:
    def __init__(self, github_client: GitHubClient):
        self.github = github_client
    
    async def discover_workflows(self, owner: str, repo: str) -> list[dict]:
        """列出仓库所有 workflow"""
        workflows = await self.github.list_workflows(owner, repo, per_page=100)
        return workflows.get("workflows", [])
    
    async def sync_workflow_runs(self, workflow_config, days_back: int = 30, 
                                  max_runs: int = 100) -> int:
        """同步指定 workflow 的构建记录到数据库"""
        runs = await self.github.get_workflow_runs(
            workflow_file=workflow_config.workflow_path,
            days_back=days_back,
            per_page=max_runs,
        )
        
        collected = 0
        for run in runs:
            # 检查是否已存在
            existing = await self._check_existing_run(
                workflow_config.id, run["id"]
            )
            if existing:
                continue
            
            # 获取 Job 详情
            jobs = await self.github.list_jobs_for_run(run["id"])
            
            # 构建层级数据并持久化
            await self._persist_run_data(workflow_config.id, run, jobs)
            collected += 1
        
        return collected
```

### 7.2 Job 名称解析器（复用 + 扩展原有脚本逻辑）

```python
# release_management/parser.py

class JobNameParser:
    """统一的 Job 名称解析器，支持多种工作流格式"""
    
    @staticmethod
    def parse(job_name: str, workflow_type: str) -> dict:
        """
        解析 Job 名称，返回层级结构
        
        支持的格式：
        - Image Build: 'Build and push nightly-a2'
        - Nightly with base: 'Build nightly-a2 (ubuntu) / Build base image (a2-ubuntu) / build (...)'
        - 通用带分组: 'A2 Ubuntu / build (linux-aarch64-cpu-4, arm64)'
        """
        if workflow_type == "nightly_with_base":
            return JobNameParser._parse_nightly_with_base(job_name)
        elif workflow_type == "image_build":
            return JobNameParser._parse_image_build(job_name)
        else:
            return JobNameParser._parse_generic(job_name)
    
    @staticmethod
    def _parse_nightly_with_base(name: str) -> dict:
        # 复用 analyze_nightly_with_base.py 中的 parse_job_name 逻辑
        # 返回 {group_key, target, os_name, phase, sub_name, architecture}
        ...
    
    @staticmethod
    def _parse_image_build(name: str) -> dict:
        # 复用 analyze_image_build.py 中的 parse_job_group_name 逻辑
        ...
    
    @staticmethod
    def _parse_generic(name: str) -> dict:
        # 通用解析：按 " / " 拆分
        ...
```

### 7.3 三方件版本检测

```python
# release_management/third_party_service.py

class ThirdPartyDetector:
    """从构建日志/Artifact 中检测三方件版本"""
    
    # 预定义模式用于匹配常见组件
    DETECTION_PATTERNS = {
        "CANN": r"CANN[_\s-]*(\d+\.\d+(?:\.\d+)?(?:\.[A-Za-z0-9]+)?)",
        "torch-npu": r"torch-npu[\s=]*(\d+\.\d+(?:\.\d+)?)",
        "torch": r"torch[\s=]*(\d+\.\d+(?:\.\d+)?)",
        "Python": r"python[^0-9]*([\d]+\.[\d]+\.[\d]+)",
        "ascendc": r"ascend-c[_\s-]*(\d+\.\d+(?:\.\d+)?)",
        "numpy": r"numpy[\s=]*(\d+\.\d+(?:\.\d+)?)",
        "protobuf": r"protobuf[\s=]*(\d+\.\d+(?:\.\d+)?)",
    }
    
    async def detect_from_build_logs(self, run_id: int, jobs: list[dict]) -> list[dict]:
        """从 Job 的 step 日志中检测三方件版本"""
        for job in jobs:
            for step in job.get("steps", []):
                # 下载 step 日志
                log_content = await self.github.get_job_logs(run_id, job["id"])
                
                # 对关键步骤（如 RUN pip install, RUN apt install）的日志进行匹配
                for component, pattern in self.DETECTION_PATTERNS.items():
                    matches = re.finditer(pattern, log_content, re.IGNORECASE)
                    for match in matches:
                        version = match.group(1)
                        await self._save_version(run_id, component, version, 
                                                 "build_log", match.group(0))
```

### 7.4 可信度评估算法 + AI 分析

```python
# release_management/ai_evaluator.py

class TrustworthinessEvaluator:
    """数据可信度评估：算法评分 → AI 综合分析"""
    
    @staticmethod
    def calculate_algorithm_score(build_runs: list, 
                                   third_party_records: list,
                                   baselines: list) -> dict:
        """
        基于算法的可信度评分（0-100）
        
        评分维度：
        1. 成功率维度 (25%): 构建成功率越高，可信度越高
        2. 稳定性维度 (25%): 耗时 CV 越低，越稳定，可信度越高
        3. 覆盖度维度 (20%): 多架构、多 OS、多分支覆盖
        4. 合规性维度 (15%): 三方件版本是否符合基线
        5. 趋势维度 (15%): 最近趋势是否向好
        """
        # 成功率评分
        success_rate = sum(1 for r in build_runs if r["status"] == "success") / len(build_runs)
        success_score = success_rate * 100 * 0.25  # max 25
        
        # 稳定性评分 (CV < 10% 得满分)
        durations = [r["exec_duration"] for r in build_runs if r["exec_duration"] > 0]
        if len(durations) > 1:
            cv = statistics.stdev(durations) / statistics.mean(durations)
            stability_score = max(0, (1 - cv / 0.5)) * 100 * 0.25  # max 25
        else:
            stability_score = 50 * 0.25
        
        # 覆盖度评分
        unique_archs = set()
        unique_os = set()
        for r in build_runs:
            # 从 groups 中聚合
            ...
        coverage_score = min(len(unique_archs) / 2 * 50 + len(unique_os) / 2 * 50, 100) * 0.20  # max 20
        
        # 合规性评分
        violations = count_baseline_violations(third_party_records, baselines)
        compliance_score = max(0, (1 - violations / len(third_party_records))) * 100 * 0.15  # max 15
        
        # 趋势评分（最近 10 次 vs 之前 10 次）
        trend_score = calculate_trend_score(build_runs) * 0.15  # max 15
        
        total_score = success_score + stability_score + coverage_score + compliance_score + trend_score
        
        return {
            "total_score": round(total_score, 1),
            "breakdown": {
                "success": round(success_score, 1),
                "stability": round(stability_score, 1),
                "coverage": round(coverage_score, 1),
                "compliance": round(compliance_score, 1),
                "trend": round(trend_score, 1),
            },
            "level": "high" if total_score >= 80 else ("medium" if total_score >= 60 else "low"),
        }
    
    @staticmethod
    async def ai_comprehensive_evaluation(db, algorithm_result: dict,
                                           build_runs: list, period_info: dict,
                                           model_name: str) -> dict:
        """
        调用 LLM 生成综合评估报告
        
        参考贡献者分析的 generate_ai_report 流程：
        1. 构建 Prompt 上下文（算法评分 + 关键数据 + 问题列表）
        2. 调用 LLMClient.generate()
        3. 解析返回结构，存储评估报告
        """
        system_prompt = """你是一个软件工程发布质量评估专家。
        你的任务是基于提供的构建数据和算法评分，对发布件进行可信度评估，
        并给出是否达到生产级的判断和建议。
        
        请以 JSON 格式返回，包含以下字段：
        - executive_summary: 100 字以内的执行摘要
        - findings: 核心发现列表 [{id, title, description, severity: high|medium|low}]
        - risk_factors: 风险因子列表 [{name, description, impact}]
        - data_quality_issues: 数据质量问题列表
        - recommendations: 改进建议列表 [{title, description, priority: P0|P1|P2}]
        - production_ready: boolean（是否达到生产级）
        - production_ready_reason: 判断理由
        """
        
        user_prompt = build_evaluation_prompt(algorithm_result, build_runs, period_info)
        
        # 获取当前活跃的 LLM provider
        active_provider = await get_active_llm_provider(db)
        
        llm = LLMClient()
        result = await llm.generate(
            provider=active_provider.provider,
            model=model_name,
            api_key=active_provider.api_key,
            api_base=active_provider.api_base_url,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
            max_tokens=4096,
        )
        
        evaluation = json.loads(result.content)
        evaluation["algorithm_score"] = algorithm_result
        return evaluation
```

### 7.5 报告生成器

```python
# release_management/report_generator.py

class ReportGenerator:
    """PDF 和 Excel 报告生成"""
    
    async def generate_pdf(self, workflow_config, build_runs: list, 
                           stats: dict, evaluation: dict) -> bytes:
        """
        生成 PDF 报告
        
        使用 weasyprint 或 reportlab：
        1. 将 ECharts 图表序列化为 SVG/PNG（后端通过 render 引擎）
        2. 组合文本、表格、图表为 HTML 模板
        3. HTML → PDF 转换
        """
        html_content = render_report_template(
            workflow=workflow_config,
            stats=stats,
            runs=build_runs,
            evaluation=evaluation,
        )
        
        # 使用 weasyprint 生成 PDF
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_content).write_pdf()
        return pdf_bytes
    
    async def generate_excel(self, workflow_config, build_runs: list,
                              group_data: list) -> bytes:
        """
        生成 Excel 报告（保留原有分析脚本的层级化 Excel 能力）
        
        Sheet 结构：
        1. 综合概览 — KPI + 统计
        2. 构建记录明细 — 按时间排序的所有 run
        3. Job Group 层级 — Group → Phase → Sub-Job → Step
        4. 三方件版本 — 按组件聚合
        5. 耗时趋势 — 按天聚合
        6. AI 评估摘要 — 如存在
        """
        from io import BytesIO
        from openpyxl import Workbook
        
        wb = Workbook()
        # ... 复用原有 analyze_image_build.py 中的 Excel 样式和布局逻辑
        ...
```

## 8. 导航与路由集成

### 8.1 前端路由 (`App.tsx`)

```tsx
// 在 "研发交付" 组下新增路由
<Route path="release-management" element={<ReleaseManagement />} />
<Route path="release-management/runs/:runId" element={<BuildRunDetail />} />
<Route path="release-management/evaluations/:id" element={<EvaluationDetail />} />
```

### 8.2 侧边栏导航 (`Layout.tsx`)

```tsx
// 在 primaryNavigation 的 "研发交付" 组中新增
{
  type: 'group',
  label: '研发交付',
  children: [
    { key: '/project', icon: <GithubOutlined />, label: '项目动态' },
    { key: '/pr-pipeline', icon: <PullRequestOutlined />, label: 'PR 流水线' },
    { key: '/ci', icon: <CheckCircleOutlined />, label: 'CI 运行' },
    { key: '/release-management', icon: <CloudUploadOutlined />, label: '发布件管理' }, // 新增
  ],
},
```

### 8.3 路由元数据

```tsx
const routeMeta = [
  // ... 现有项
  { prefix: '/release-management', title: '发布件管理', section: '研发交付' },
];
```

### 8.4 后端路由注册 (`api/main.py`)

```python
from api.v1 import release_management

app.include_router(
    release_management.router,
    prefix="/api/v1",
    tags=["发布件管理"],
)
```

## 9. 定时任务

使用现有的 `scheduler/service.py` 机制，新增以下定时任务：

| 任务 | 频率 | 说明 |
|------|------|------|
| `release_workflow_sync` | 每 30 分钟 | 增量同步所有启用 workflow 的构建记录 |
| `release_ai_daily_eval` | 每日 08:00 | 自动触发前一日的 AI 可信度评估 |
| `release_third_party_detect` | 随同步任务 | 对新同步的 run 自动检测三方件版本 |

## 10. 权限与安全

- 普通用户：可查看看板、导出报告（只读）
- 管理员：可触发同步、配置基线、触发 AI 评估
- super_admin：以上 + 管理工作流配置
- GitHub Token 从系统配置读取，不暴露给前端
- LLM API Key 从数据库读取，仅后端使用

## 11. 渐进实现计划

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| P0 | 基础架构：数据库模型 + GitHub 采集 + 解析器 + 概览 API | 高 |
| P0 | 前端看板：工作流选择 + KPI + 核心图表（ECharts） | 高 |
| P1 | 三方件版本检测 + 基线管理 + 合规检查 | 高 |
| P1 | AI 可信度评估 + 生产级判定 | 中 |
| P2 | PDF/Excel 报告导出 | 中 |
| P2 | 构建运行详情页面（Job 层级展开 + Step 明细） | 中 |
| P3 | 定时任务 + 自动评估 | 低 |
| P3 | 多仓库支持 + 工作流自定义注册 | 低 |
