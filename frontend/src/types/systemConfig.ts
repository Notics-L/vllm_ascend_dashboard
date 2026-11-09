// 类型定义
export interface SystemConfig {
  app_config: {
    environment: string
    debug: boolean
    log_level: string
    timezone: string
  }
  github_config: {
    owner: string
    repo: string
    token_configured: boolean
    token_preview?: string | null
  }
  sync_config: {
    ci_sync_config: {
      sync_interval_minutes: number
      days_back: number
      max_runs_per_workflow: number
      force_full_refresh: boolean
      auto_failure_analysis_enabled: boolean
    }
    model_sync_config: {
      sync_interval_minutes: number
      days_back: number
      runs_limit: number
    }
    data_retention_days: number
    project_dashboard_cache_interval_minutes: number
    github_cache_dir: string
  }
  database_config: {
    type: string
    configured: boolean
  }
}

export interface SystemStatus {
  scheduler: {
    running: boolean
    sync_interval_minutes: number
    last_sync?: string | null  // 上次同步时间
    tasks: {
      ci_sync: {
        name: string
        next_sync: string | null
        interval_minutes: number
      }
      model_report_sync: {
        name: string
        next_sync: string | null
        interval_minutes: number
      }
      project_dashboard_cache: {
        name: string
        next_sync: string | null
        interval_minutes: number
      }
      daily_summary: {
        name: string
        next_sync: string | null
        enabled: boolean
        cron_hour: number
        cron_minute: number
      }
    }
  }
  database: {
    connected: boolean
    type: string
  }
  github: {
    configured: boolean
    owner: string
    repo: string
  }
  timestamp: string
}

export interface SyncConfigUpdate {
  ci_sync_interval_minutes?: number
  ci_sync_days_back?: number
  ci_sync_max_runs_per_workflow?: number
  ci_sync_force_full_refresh?: boolean
  ci_auto_failure_analysis_enabled?: boolean
  model_sync_interval_minutes?: number
  model_sync_days_back?: number
  model_sync_runs_limit?: number
  data_retention_days?: number
  project_dashboard_cache_interval_minutes?: number
  github_cache_dir?: string
}

export interface AppConfigUpdate {
  log_level?: string
  debug?: boolean
}

export interface GitHubConfigUpdate {
  github_token?: string
  clear_token?: boolean
}
