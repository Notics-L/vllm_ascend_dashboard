#!/usr/bin/env python3
"""
测试 contributor sync 接口修复

使用方法:
1. 设置环境变量: export DASHBOARD_API_KEY=your_admin_api_key
2. 运行: python tests/test_contributor_sync.py
"""
import asyncio
import httpx
import os
import sys
from datetime import datetime

# 配置
BASE_URL = os.getenv("DASHBOARD_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("DASHBOARD_API_KEY", "")


async def test_sync_endpoint():
    """测试同步接口"""
    print("=" * 80)
    print("测试贡献者同步接口")
    print("=" * 80)
    
    if not API_KEY:
        print("❌ 请设置 DASHBOARD_API_KEY 环境变量")
        return False
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 测试数据
    test_cases = [
        {
            "name": "全量同步 - 指定仓库",
            "data": {
                "full_resync": True,
                "repo_name": "vllm-project/vllm-ascend"
            }
        },
        {
            "name": "增量同步 - 所有仓库",
            "data": {
                "full_resync": False,
                "repo_name": None
            }
        }
    ]
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for test_case in test_cases:
            print(f"\n测试: {test_case['name']}")
            print(f"请求数据: {test_case['data']}")
            
            try:
                response = await client.post(
                    f"{BASE_URL}/api/v1/contributor-board/sync/contributors",
                    json=test_case["data"],
                    headers=headers
                )
                
                print(f"状态码: {response.status_code}")
                print(f"响应: {response.json()}")
                
                if response.status_code == 200:
                    print("✅ 测试通过")
                    return True
                elif response.status_code == 409:
                    print("⚠️ 任务已在运行中 (这是预期的防重复行为)")
                    return True
                else:
                    print(f"❌ 测试失败: {response.status_code}")
                    return False
                    
            except httpx.ConnectError as e:
                print(f"❌ 连接失败: {e}")
                print("   请确保后端服务正在运行")
                return False
            except Exception as e:
                print(f"❌ 测试异常: {e}")
                return False


async def test_sync_status(record_id: int):
    """测试查询同步状态"""
    print(f"\n查询同步任务状态 (record_id={record_id})")
    
    headers = {
        "Authorization": f"Bearer {API_KEY}"
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(
                f"{BASE_URL}/api/v1/contributor-board/sync/status/{record_id}",
                headers=headers
            )
            
            print(f"状态码: {response.status_code}")
            print(f"响应: {response.json()}")
            
        except Exception as e:
            print(f"❌ 查询失败: {e}")


async def check_database_state():
    """检查数据库状态"""
    print("\n检查数据库状态...")
    
    try:
        from infrastructure.db.base import SessionLocal
        from sqlalchemy import text
        
        async with SessionLocal() as db:
            # 检查 background_task_records 表
            result = await db.execute(text("""
                SELECT COUNT(*) FROM background_task_records 
                WHERE task_type = 'contributor_sync'
            """))
            count = result.scalar()
            print(f"background_task_records 中 contributor_sync 任务数: {count}")
            
            # 检查 contributor_sync_records 表
            result = await db.execute(text("""
                SELECT COUNT(*) FROM contributor_sync_records
            """))
            count = result.scalar()
            print(f"contributor_sync_records 记录数: {count}")
            
            # 检查是否有正在运行的任务
            result = await db.execute(text("""
                SELECT id, status, created_at 
                FROM background_task_records 
                WHERE task_type = 'contributor_sync' 
                AND status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 5
            """))
            rows = result.fetchall()
            if rows:
                print(f"正在运行的任务:")
                for row in rows:
                    print(f"  ID={row[0]}, 状态={row[1]}, 创建时间={row[2]}")
            else:
                print("没有正在运行的任务")
    
    except Exception as e:
        print(f"❌ 数据库检查失败: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """主测试流程"""
    print(f"测试时间: {datetime.now().isoformat()}")
    print(f"API 地址: {BASE_URL}")
    
    # 1. 检查数据库状态
    await check_database_state()
    
    # 2. 测试同步接口
    success = await test_sync_endpoint()
    
    print("\n" + "=" * 80)
    if success:
        print("✅ 所有测试通过！")
    else:
        print("❌ 测试失败，请查看上述错误信息")
    print("=" * 80)
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
