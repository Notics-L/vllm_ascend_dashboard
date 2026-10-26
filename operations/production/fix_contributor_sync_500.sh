#!/bin/bash
# 快速修复 contributor sync 500 错误
# 在 production 服务器上运行此脚本

set -e

echo "=========================================="
echo "贡献者同步 500 错误 - 快速修复脚本"
echo "=========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查是否在正确的服务器上
if [ ! -d "/root/vllm_ascend_dashboard" ]; then
    log_error "请在 production 服务器上运行此脚本 (/root/vllm_ascend_dashboard 不存在)"
    exit 1
fi

cd /root/vllm_ascend_dashboard

# 1. 检查后端服务状态
log_info "检查后端服务状态..."
if systemctl is-active --quiet dashboard-backend; then
    log_info "后端服务正在运行"
else
    log_warn "后端服务未运行，尝试启动..."
    systemctl start dashboard-backend
    sleep 2
fi

# 2. 检查数据库连接
log_info "检查数据库连接..."
MYSQL_CMD="mysql -u root -p'$(grep DB_PASSWORD .env | cut -d'=' -f2)'"

# 3. 创建缺失的表
log_info "创建缺失的数据库表..."
$MYSQL_CMD -e "
SELECT DATABASE();
" 2>/dev/null || {
    log_error "数据库连接失败，请检查 .env 文件中的数据库配置"
    exit 1
}

DB_NAME=$(grep DB_NAME .env | cut -d'=' -f2)
if [ -z "$DB_NAME" ]; then
    DB_NAME="vllm_ascend_dashboard"
fi

# 创建 background_task_records 表
log_info "检查 background_task_records 表..."
TABLE_EXISTS=$($MYSQL_CMD $DB_NAME -e "
SELECT COUNT(*) FROM information_schema.tables 
WHERE table_schema = '$DB_NAME' 
AND table_name = 'background_task_records';
" 2>/dev/null | tail -1)

if [ "$TABLE_EXISTS" = "0" ]; then
    log_info "创建 background_task_records 表..."
    $MYSQL_CMD $DB_NAME -e "
    CREATE TABLE IF NOT EXISTS background_task_records (
        id INT AUTO_INCREMENT PRIMARY KEY,
        task_type VARCHAR(100) NOT NULL,
        task_params JSON,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        repo_name VARCHAR(200),
        progress_percent FLOAT DEFAULT 0,
        progress_message VARCHAR(500),
        result_metadata JSON,
        error_message TEXT,
        started_at TIMESTAMP NULL,
        completed_at TIMESTAMP NULL,
        duration_seconds FLOAT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_task_type_status (task_type, status),
        INDEX idx_status_created (status, created_at),
        INDEX idx_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
" 
    if [ $? -eq 0 ]; then
        log_info "✓ background_task_records 表创建成功"
    else
        log_error "✗ 表创建失败"
        exit 1
    fi
else
    log_info "✓ background_task_records 表已存在"
fi

# 4. 清理卡住的任务
log_info "清理卡住的同步任务..."
$MYSQL_CMD $DB_NAME -e "
UPDATE background_task_records 
SET status = 'failed', 
    error_message = 'Task cleared by fix script (stale task)',
    completed_at = NOW() 
WHERE task_type = 'contributor_sync' 
AND status IN ('pending', 'running')
AND TIMESTAMPDIFF(HOUR, created_at, NOW()) > 1;
" 
log_info "✓ 已清理超过 1 小时的卡住任务"

# 5. 检查 contributor_sync_records 表
log_info "检查 contributor_sync_records 表..."
TABLE_EXISTS=$($MYSQL_CMD $DB_NAME -e "
SELECT COUNT(*) FROM information_schema.tables 
WHERE table_schema = '$DB_NAME' 
AND table_name = 'contributor_sync_records';
" 2>/dev/null | tail -1)

if [ "$TABLE_EXISTS" = "0" ]; then
    log_warn "contributor_sync_records 表不存在，需要运行数据库迁移"
    log_info "尝试运行迁移脚本..."
    if [ -f "operations/production/migrate.sh" ]; then
        bash operations/production/migrate.sh
    else
        log_error "迁移脚本不存在，请手动运行数据库迁移"
        exit 1
    fi
else
    log_info "✓ contributor_sync_records 表已存在"
    RECORD_COUNT=$($MYSQL_CMD $DB_NAME -e "SELECT COUNT(*) FROM contributor_sync_records;" 2>/dev/null | tail -1)
    log_info "  当前记录数: $RECORD_COUNT"
fi

# 6. 检查 GitHub Token
log_info "检查环境配置..."
if grep -q "GITHUB_TOKEN=" .env; then
    TOKEN_LENGTH=$(grep GITHUB_TOKEN= .env | cut -d'=' -f2 | wc -c)
    if [ $TOKEN_LENGTH -gt 10 ]; then
        log_info "✓ GITHUB_TOKEN 已配置"
    else
        log_warn "⚠ GITHUB_TOKEN 可能未正确配置"
    fi
else
    log_warn "⚠ GITHUB_TOKEN 未在 .env 中配置"
fi

# 7. 重启后端服务
log_info "重启后端服务..."
systemctl restart dashboard-backend
sleep 3

if systemctl is-active --quiet dashboard-backend; then
    log_info "✓ 后端服务重启成功"
else
    log_error "✗ 后端服务启动失败"
    log_info "查看服务日志: journalctl -u dashboard-backend -f --no-pager"
    exit 1
fi

# 8. 验证 API 健康
log_info "验证 API 健康状态..."
HEALTH=$(curl -s http://127.0.0.1:8000/api/v1/health 2>/dev/null)
if echo "$HEALTH" | grep -q "ok"; then
    log_info "✓ API 健康检查通过"
else
    log_warn "⚠ API 健康检查未通过，但服务可能仍在启动中"
fi

# 9. 打印统计信息
log_info "当前同步任务统计:"
$MYSQL_CMD $DB_NAME -e "
SELECT 
    status, 
    COUNT(*) as count 
FROM background_task_records 
WHERE task_type = 'contributor_sync' 
GROUP BY status;
" 2>/dev/null || true

echo ""
echo "=========================================="
log_info "修复完成！"
echo "=========================================="
echo ""
echo "现在可以尝试再次调用同步接口:"
echo "  curl -X POST http://127.0.0.1:8000/api/v1/contributor-board/sync/contributors \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -H 'Authorization: Bearer YOUR_TOKEN' \\"
echo "    -d '{\"full_resync\": true, \"repo_name\": \"vllm-project/vllm-ascend\"}'"
echo ""
echo "查看实时日志:"
echo "  journalctl -u dashboard-backend -f --no-pager"
echo ""
