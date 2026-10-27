#!/usr/bin/env python3
"""
贡献者同步接口 500 错误诊断脚本

使用方法:
1. 在 production 服务器上运行:
   python diagnostics/contributor_sync_diagnostic.py

2. 或远程执行:
   ssh root@123.57.0.174 "cd /root/vllm_ascend_dashboard && python diagnostics/contributor_sync_diagnostic.py"
"""
import asyncio
import json
import sys
import os
from datetime import datetime

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


async def check_database_tables():
    """检查必需的数据库表是否存在"""
    print("\n=== 检查数据库表 ===")
    try:
        from infrastructure.db.base import SessionLocal
        from infrastructure.tasks.background_task import BackgroundTaskTracker
        from contributor_board.models import ContributorSyncRecord
        from sqlalchemy import text
        
        async with SessionLocal() as db:
            # 检查 background_task_records 表
            try:
                result = await db.execute(text("SHOW TABLES LIKE 'background_task_records'"))
                exists = result.fetchone() is not None
                print(f"background_task_records 表存在: {exists}")
                
                if not exists:
                    print("  尝试创建表...")
                    await BackgroundTaskTracker.ensure_table(db)
                    print("  表创建成功")
                else:
                    # 检查表结构
                    result = await db.execute(text("DESCRIBE background_task_records"))
                    columns = [row[0] for row in result.fetchall()]
                    print(f"  表字段: {columns}")
            except Exception as e:
                print(f"❌ 检查 background_task_records 表失败: {e}")
                return False
            
            # 检查 contributor_sync_records 表
            try:
                result = await db.execute(text("SHOW TABLES LIKE 'contributor_sync_records'"))
                exists = result.fetchone() is not None
                print(f"contributor_sync_records 表存在: {exists}")
                
                if not exists:
                    print("  ⚠️ contributor_sync_records 表不存在，需要运行数据库迁移")
                else:
                    result = await db.execute(text("SELECT COUNT(*) FROM contributor_sync_records"))
                    count = result.scalar()
                    print(f"  当前记录数: {count}")
                    
                    result = await db.execute(text("SELECT * FROM contributor_sync_records ORDER BY created_at DESC LIMIT 1"))
                    last_row = result.fetchone()
                    if last_row:
                        print(f"  最后一条记录: {last_row}")
            except Exception as e:
                print(f"❌ 检查 contributor_sync_records 表失败: {e}")
                return False
            
            # 检查是否有正在运行的任务
            try:
                result = await db.execute(text("""
                    SELECT id, task_type, status, repo_name, created_at 
                    FROM background_task_records 
                    WHERE task_type = 'contributor_sync' AND status IN ('pending', 'running')
                    ORDER BY created_at DESC
                """))
                active_tasks = result.fetchall()
                print(f"\n正在运行的 contributor_sync 任务数: {len(active_tasks)}")
                for task in active_tasks:
                    print(f"  任务 {task[0]}: 状态={task[2]}, 仓库={task[3]}, 创建时间={task[4]}")
            except Exception as e:
                print(f"❌ 检查运行中的任务失败: {e}")
            
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


async def check_environment_config():
    """检查环境配置"""
    print("\n=== 检查环境配置 ===")
    try:
        from infrastructure.core.config import settings
        
        print(f"GITHUB_TOKEN 已配置: {bool(settings.GITHUB_TOKEN)}")
        if settings.GITHUB_TOKEN:
            print(f"  Token 前缀: {settings.GITHUB_TOKEN[:8]}...")
        
        print(f"GITHUB_OWNER: {settings.GITHUB_OWNER}")
        print(f"GITHUB_REPO: {settings.GITHUB_REPO}")
        print(f"CONTRIBUTOR_REPOS: {settings.CONTRIBUTOR_REPOS or '(未设置，使用默认值)'}")
        print(f"DATABASE_URL: {settings.DATABASE_URL[:50]}...")
        
    except Exception as e:
        print(f"❌ 检查配置失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


async def test_github_connection():
    """测试 GitHub API 连接"""
    print("\n=== 测试 GitHub API 连接 ===")
    try:
        from infrastructure.core.config import settings
        from infrastructure.clients.github_client import GitHubClient
        
        if not settings.GITHUB_TOKEN:
            print("⚠️ GITHUB_TOKEN 未配置，跳过测试")
            return True
        
        async with GitHubClient(
            token=settings.GITHUB_TOKEN,
            owner=settings.GITHUB_OWNER,
            repo=settings.GITHUB_REPO
        ) as client:
            # 尝试获取仓库信息
            repo_info = await client.get_repo(settings.GITHUB_OWNER, settings.GITHUB_REPO)
            print(f"✅ GitHub API 连接成功")
            print(f"  仓库: {repo_info.get('full_name')}")
            print(f"  Star 数: {repo_info.get('stargazers_count')}")
            
    except Exception as e:
        print(f"❌ GitHub API 连接测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


async def test_sync_trigger():
    """测试触发同步（模拟 API 调用）"""
    print("\n=== 测试触发同步 ===")
    try:
        from infrastructure.db.base import SessionLocal
        from contributor_board.service import ContributorBoardService
        from infrastructure.tasks.background_task import BackgroundTaskTracker
        
        async with SessionLocal() as db:
            # 确保表存在
            await BackgroundTaskTracker.ensure_table(db)
            
            service = ContributorBoardService()
            
            # 模拟 API 请求参数
            print("尝试触发同步 (repo_name=vllm-project/vllm-ascend, full_resync=True)...")
            response = await service.trigger_sync(
                db=db,
                since_date=None,
                until_date=None,
                full_resync=True,
                repo_name="vllm-project/vllm-ascend",
                trigger_source="manual",
            )
            
            print(f"✅ 同步触发成功")
            print(f"  record_id: {response.record_id}")
            
            # 等待一段时间检查任务状态
            await asyncio.sleep(2)
            
            # 检查任务状态
            from sqlalchemy import text
            result = await db.execute(text(f"""
                SELECT btr.status, btr.progress_percent, btr.progress_message,
                       csr.status as sync_status, csr.progress_percent as sync_progress
                FROM background_task_records btr
                LEFT JOIN contributor_sync_records csr ON csr.id = :record_id
                WHERE btr.id IN (
                    SELECT task_id FROM background_task_records 
                    WHERE task_params LIKE :record_id_pattern
                    LIMIT 1
                )
            """), {
                "record_id": response.record_id,
                "record_id_pattern": f"%{response.record_id}%"
            })
            row = result.fetchone()
            if row:
                print(f"  任务状态: {row[0]}")
                print(f"  进度: {row[1]}%")
                print(f"  消息: {row[2]}")
            
    except Exception as e:
        print(f"❌ 触发同步失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


async def check_database_permissions():
    """检查数据库权限"""
    print("\n=== 检查数据库权限 ===")
    try:
        from infrastructure.db.base import SessionLocal
        from sqlalchemy import text
        
        async with SessionLocal() as db:
            # 检查当前用户
            result = await db.execute(text("SELECT USER(), CURRENT_USER()"))
            users = result.fetchall()
            print(f"当前数据库用户: {users}")
            
            # 检查 CREATE TABLE 权限
            try:
                await db.execute(text("SHOW DATABASES"))
                print("✅ 有 SHOW DATABASES 权限")
            except Exception as e:
                print(f"❌ 缺少 SHOW DATABASES 权限: {e}")
            
            # 检查当前数据库
            result = await db.execute(text("SELECT DATABASE()"))
            db_name = result.scalar()
            print(f"当前数据库: {db_name}")
            
    except Exception as e:
        print(f"❌ 检查数据库权限失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


async def main():
    """主诊断流程"""
    print("=" * 80)
    print("贡献者同步接口 500 错误诊断工具")
    print(f"诊断时间: {datetime.now().isoformat()}")
    print("=" * 80)
    
    results = {'config': await check_environment_config(), 'tables': await check_database_tables(),
               'permissions': await check_database_permissions(), 'github': await test_github_connection()}
    
    # 1. 检查环境配置

    # 2. 检查数据库表

    # 3. 检查数据库权限

    # 4. 测试 GitHub 连接

    # 5. 测试触发同步（可选，可能长时间运行）
    test_sync = input("\n是否测试触发同步？(y/N): ").strip().lower()
    if test_sync == 'y':
        results['sync_trigger'] = await test_sync_trigger()
    
    # 总结
    print("\n" + "=" * 80)
    print("诊断结果总结:")
    print("=" * 80)
    for check_name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{check_name}: {status}")
    
    all_passed = all(results.values())
    if all_passed:
        print("\n✅ 所有检查通过！如果仍有问题，请查看后端日志获取详细错误信息")
    else:
        print("\n❌ 部分检查失败，请根据上述信息进行修复")
        print("\n常见解决方案:")
        print("1. 运行数据库迁移: bash operations/production/migrate.sh")
        print("2. 检查 .env 文件中的 GITHUB_TOKEN 配置")
        print("3. 重启后端服务: systemctl restart dashboard-backend")
        print("4. 查看详细日志: journalctl -u dashboard-backend -f --no-pager")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
