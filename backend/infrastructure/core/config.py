"""
核心配置模块
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    # 应用配置
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # GitHub 配置
    # Optional bootstrap value; runtime changes are stored in MySQL.
    GITHUB_TOKEN: str = ""
    GITHUB_OWNER: str = "vllm-project"
    GITHUB_REPO: str = "vllm-ascend"

    # 数据库配置
    DATABASE_URL: str = "mysql+aiomysql://dashboard:dashboard123@localhost:3306/vllm_dashboard"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 60

    # Phase B 拆分逻辑数据库后的独立 URL（第一阶段指向同一实例）
    CONTROL_DATABASE_URL: str = ""
    COLLECTION_DATABASE_URL: str = ""
    TELEMETRY_DATABASE_URL: str = ""

    # JWT 配置
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 小时
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    KUBECONFIG_ENCRYPTION_KEY: str | None = None

    # Resource metrics can be collected through the production API when the
    # local Docker host cannot reach the Kubernetes API networks directly.
    RESOURCE_METRICS_REMOTE_URL: str = ""
    RESOURCE_METRICS_REMOTE_USERNAME: str = ""
    RESOURCE_METRICS_REMOTE_PASSWORD: str = ""
    RESOURCE_METRICS_REMOTE_TIMEOUT_SECONDS: int = 60

    # Development-only dashboard replay.  When explicitly configured, the
    # resource and NPU queue endpoints return a captured, sanitized response
    # instead of contacting Kubernetes or relying on local CI data.
    NPU_DASHBOARD_REPLAY_SNAPSHOT: str = ""
    NPU_QUEUE_SNAPSHOT_INTERVAL_MINUTES: int = 5
    NPU_QUEUE_SNAPSHOT_RETENTION_DAYS: int = 14

    NPU_OCCUPANCY_UPSTREAM_URL: str = "https://pod-history-api.test.osinfra.cn/api/v1/envs/history"
    NPU_OCCUPANCY_TIMEOUT_SECONDS: int = 180
    NPU_OCCUPANCY_VERIFY_TLS: bool = True
    NPU_OCCUPANCY_MAX_WINDOW_SECONDS: int = 2_592_000
    NPU_OCCUPANCY_SYNC_INTERVAL_MINUTES: int = 60
    NPU_OCCUPANCY_SYNC_LOOKBACK_HOURS: int = 6

    # CORS 配置
    # 生产环境应明确指定允许的域名，不要使用 "*"
    CORS_ORIGINS: list[str] = [
        "http://123.57.0.174",
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:5173",
    ]

    # 数据目录
    DATA_DIR: str = "data"  # 数据文件存储目录

    # Agent / Docker LiteLLM routing. Declaring these settings also makes
    # backend/.env work for a locally launched uvicorn process.
    LITELLM_PROXY_URL: str = ""
    LITELLM_MASTER_KEY: str = ""
    LITELLM_CONFIG_FILE: str = "/app/litellm_config.yaml"
    LITELLM_CONTAINER_NAME: str = "vllm-dashboard-litellm"
    AGENT_SERVICE_ENABLED: bool = False
    AGENT_PROXY_ONLY: bool = False
    AGENT_PROXY_ALLOWED_HOSTS: str = "litellm,vllm-dashboard-litellm,vllm-litellm-dev"

    # 数据同步配置
    CI_SYNC_INTERVAL_MINUTES: int = 30  # CI 数据同步间隔（分钟），与生产默认调度保持一致
    CI_SYNC_DAYS_BACK: int = 7  # 自动同步时采集最近 N 天的数据
    CI_SYNC_MAX_RUNS_PER_WORKFLOW: int = 100  # 每个 workflow 最多采集多少条记录
    CI_SYNC_FORCE_FULL_REFRESH: bool = False  # 是否强制全量覆盖刷新
    # Failure analysis invokes log retrieval and an LLM. Keep automatic
    # Queueing is unbounded; this caps concurrently leased analysis tasks
    # across all Collector processes.
    CI_AUTO_FAILURE_ANALYSIS_ENABLED: bool = True
    CI_AUTO_FAILURE_ANALYSIS_MAX_CONCURRENT: int = 3
    NIGHTLY_DATA_SYNC_INTERVAL_MINUTES: int = 120
    DATA_RETENTION_DAYS: int = 365

    # 测试用例退出阈值：超过此天数未运行的用例标记为「已退出」，从监控看板隐藏
    # 与 CI 夜间测试周期对齐——超过一个周期未运行说明用例已不在 vllm-ascend CI 中
    TEST_CASE_STALE_DAYS: int = 7

    # 模型同步配置
    MODEL_SYNC_INTERVAL_MINUTES: int = 60  # 模型报告同步间隔（分钟），默认 1 小时
    MODEL_SYNC_RUNS_LIMIT: int = 100
    MODEL_SYNC_DAYS_BACK: int = 3

    PR_PIPELINE_SYNC_INTERVAL_MINUTES: int = 30
    PR_PIPELINE_DAYS_BACK: int = 7
    # Scheduled PR sync uses an updated_at watermark.  The first run falls
    # back to PR_PIPELINE_DAYS_BACK, then each run is bounded by this cap.
    PR_PIPELINE_MAX_ITEMS_PER_SYNC: int = 50
    PR_PIPELINE_INCREMENTAL_LOOKBACK_MINUTES: int = 15

    # Project Dashboard 配置
    # Git repositories are persistent analysis inputs. Refreshing them once a
    # day is sufficient; individual analyses must use the local snapshot.
    PROJECT_DASHBOARD_CACHE_INTERVAL_MINUTES: int = 1440
    GITHUB_CACHE_DIR: str = ""  # Git 镜像/worktree 存储根目录，默认使用 DATA_DIR

    # External coverage.py artifact synchronization. The upstream CI format
    # remains unchanged; the collector reads coverage.tar and writes the
    # normalized result to MySQL.
    COVERAGE_SYNC_INTERVAL_MINUTES: int = 60
    COVERAGE_DAILY_SYNC_HOUR: int = 3
    COVERAGE_DAILY_SYNC_MINUTE: int = 15
    COVERAGE_ARCHIVE_RETENTION_DAYS: int = 7
    PR_COVERAGE_TAR_URL: str = "https://vllm-ascend.obs.cn-north-4.myhuaweicloud.com/ci/precision-test/coverage.tar"
    PR_COVERAGE_DOWNLOAD_TIMEOUT_SECONDS: int = 300
    PR_COVERAGE_DOWNLOAD_RETRIES: int = 3
    PR_COVERAGE_LINE_ENABLED: bool = True

    # 每日总结配置
    DAILY_SUMMARY_ENABLED: bool = True  # 是否启用每日总结生成任务
    DAILY_SUMMARY_CRON_HOUR: int = 8  # 每日总结生成时间（小时），默认早上 8 点
    DAILY_SUMMARY_CRON_MINUTE: int = 0  # 每日总结生成时间（分钟）

    # 每日运行报告邮件推送配置（调度相关）
    REPORT_ENABLED: bool = True
    REPORT_SCHEDULE_HOUR: int = 8
    REPORT_SCHEDULE_MINUTE: int = 30
    REPORT_SUBJECT_TEMPLATE: str = "vLLM Ascend 运行报告 - {date}"
    # SMTP 配置存储在数据库（ProjectDashboardConfig 表），不放在 .env 文件中

    # 上游支持矩阵同步配置
    SUPPORT_MATRIX_SYNC_ENABLED: bool = True
    SUPPORT_MATRIX_SYNC_CRON_HOUR: int = 6
    SUPPORT_MATRIX_SYNC_CRON_MINUTE: int = 0
    SUPPORT_MATRIX_MODELS_PATH: str = "docs/source/user_guide/support_matrix/supported_models.md"
    SUPPORT_MATRIX_FEATURES_PATH: str = "docs/source/user_guide/support_matrix/supported_features.md"
    SUPPORT_MATRIX_COMPAT_PATH: str = "docs/source/user_guide/support_matrix/feature_matrix.md"
    TEST_BOARD_CASE_MATRIX_PATH: str = "resources/test-board/e2e_feature_matrix.csv"

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        """验证 JWT 密钥长度"""
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters long")
        return v

    @field_validator("GITHUB_TOKEN")
    @classmethod
    def validate_github_token(cls, v: str) -> str:
        """验证 GitHub Token 格式"""
        if not v:
            return v
        if not (v.startswith("ghp_") or v.startswith("github_pat_") or len(v) >= 10):
            raise ValueError("GITHUB_TOKEN must be a valid GitHub token (ghp_*, github_pat_*, or valid length)")
        return v

    @property
    def is_production(self) -> bool:
        """是否为生产环境"""
        return self.ENVIRONMENT == "production"


# 全局配置实例
settings = Settings()
