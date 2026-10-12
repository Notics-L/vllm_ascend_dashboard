"""Contributor board tables: metrics, classifications, errors, builds, snapshots."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_board_migration")
MIGRATION_VERSION = "20261009_01_contributor_board"


async def run() -> dict[str, int]:
    created: dict[str, int] = {}
    async with SessionLocal() as db:
        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS contributors (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    github_id BIGINT NOT NULL UNIQUE,
                    login VARCHAR(100) NOT NULL UNIQUE,
                    avatar_url VARCHAR(500),
                    email VARCHAR(200),
                    name VARCHAR(200),
                    is_community BOOLEAN DEFAULT TRUE,
                    role_types JSON,
                    company VARCHAR(200),
                    first_contribution_at TIMESTAMP NULL,
                    last_contribution_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_github_id (github_id),
                    INDEX idx_login (login),
                    INDEX idx_is_community (is_community)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            created["contributors"] = 1
        except Exception as exc:
            logger.error("Failed to create contributors: %s", exc)

        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS contributor_metrics (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    contributor_id INT NOT NULL,
                    contributor_login VARCHAR(100) NOT NULL,
                    period_start DATE NOT NULL,
                    period_end DATE NOT NULL,
                    merged_pr_count INT DEFAULT 0,
                    code_additions INT DEFAULT 0,
                    code_deletions INT DEFAULT 0,
                    review_count INT DEFAULT 0,
                    approval_count INT DEFAULT 0,
                    merge_count INT DEFAULT 0,
                    core_module_count INT DEFAULT 0,
                    closed_pr_count INT DEFAULT 0,
                    contribution_score FLOAT DEFAULT 0,
                    error_score FLOAT DEFAULT 0,
                    quadrant VARCHAR(20),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_contributor_login (contributor_login),
                    INDEX idx_contributor_id (contributor_id),
                    INDEX idx_period (period_start, period_end),
                    UNIQUE KEY uq_contributor_metrics_period (contributor_login, period_start, period_end),
                    FOREIGN KEY (contributor_id) REFERENCES contributors(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            created["contributor_metrics"] = 1
        except Exception as exc:
            logger.error("Failed to create contributor_metrics: %s", exc)

        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS commit_classifications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    commit_sha VARCHAR(40) NOT NULL UNIQUE,
                    commit_message TEXT,
                    commit_type VARCHAR(20) NOT NULL,
                    confidence FLOAT DEFAULT 1.0,
                    modules_touched JSON,
                    files_changed_count INT DEFAULT 0,
                    is_chip_specific BOOLEAN DEFAULT FALSE,
                    chip_target VARCHAR(50),
                    contributor_login VARCHAR(100),
                    pr_number INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_commit_sha (commit_sha),
                    INDEX idx_commit_type (commit_type),
                    INDEX idx_contributor_login (contributor_login),
                    INDEX idx_pr_number (pr_number)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            created["commit_classifications"] = 1
        except Exception as exc:
            logger.error("Failed to create commit_classifications: %s", exc)

        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS error_records (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    contributor_login VARCHAR(100) NOT NULL,
                    error_type VARCHAR(50) NOT NULL,
                    related_pr_number INT,
                    related_commit_sha VARCHAR(40),
                    revert_count INT DEFAULT 0,
                    bugfix_linked_count INT DEFAULT 0,
                    ci_failure_count INT DEFAULT 0,
                    nightly_failure_count INT DEFAULT 0,
                    weight_score FLOAT DEFAULT 0,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_contributor_login (contributor_login),
                    INDEX idx_error_type (error_type),
                    INDEX idx_pr_number (related_pr_number)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            created["error_records"] = 1
        except Exception as exc:
            logger.error("Failed to create error_records: %s", exc)

        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS image_build_plans (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    pr_number INT NOT NULL,
                    commit_sha VARCHAR(40) NOT NULL,
                    commit_type VARCHAR(20),
                    build_type VARCHAR(50) NOT NULL,
                    chips JSON,
                    os_list JSON,
                    cache_strategy VARCHAR(50),
                    estimated_saving_percent FLOAT DEFAULT 0,
                    actual_execution BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_pr_number (pr_number),
                    INDEX idx_build_type (build_type)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            created["image_build_plans"] = 1
        except Exception as exc:
            logger.error("Failed to create image_build_plans: %s", exc)

        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS quadrant_snapshots (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    contributor_id INT NOT NULL,
                    contributor_login VARCHAR(100) NOT NULL,
                    snapshot_date DATE NOT NULL,
                    quadrant VARCHAR(20) NOT NULL,
                    contribution_score FLOAT DEFAULT 0,
                    error_score FLOAT DEFAULT 0,
                    meta JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_contributor_login (contributor_login),
                    INDEX idx_contributor_id (contributor_id),
                    INDEX idx_snapshot_date (snapshot_date),
                    UNIQUE KEY uq_quadrant_snapshot_date (contributor_login, snapshot_date),
                    FOREIGN KEY (contributor_id) REFERENCES contributors(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            created["quadrant_snapshots"] = 1
        except Exception as exc:
            logger.error("Failed to create quadrant_snapshots: %s", exc)

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied contributor board migration %s: %s", MIGRATION_VERSION, created)
    return created
