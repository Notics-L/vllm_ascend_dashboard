"""幂等修复 image_build_plans：统一 repo 口径 + 去重 + 加 (pr_number, repo_name) 唯一约束。

对应模型变更 ImageBuildPlan.__table_args__ = (UniqueConstraint("pr_number","repo_name", name="uq_build_plan_pr_repo"),)

用法（dev，连 3308）：
    python fix_image_build_plans.py
"""
import pymysql

CONN = dict(host="127.0.0.1", port=3308, user="dashboard",
            password="local-db-50700cacdceddf86ab855c1e1422f7ca",
            database="vllm_dashboard", connect_timeout=10)

CANON = "vllm-project/vllm-ascend"


def uq_exists(cur):
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema=DATABASE() AND table_name='image_build_plans' "
        "AND index_name='uq_build_plan_pr_repo'")
    return int(cur.fetchone()[0]) > 0


def main():
    conn = pymysql.connect(**CONN, autocommit=False)
    cur = conn.cursor()

    print("== 修复前 ==")
    cur.execute("SELECT COUNT(*) FROM image_build_plans")
    print("  total rows:", cur.fetchone()[0])
    cur.execute("SELECT repo_name, COUNT(*) FROM image_build_plans GROUP BY repo_name")
    print("  per-repo:", cur.fetchall())
    print("  uq exists:", uq_exists(cur))

    # 1. 统一错误口径 -> canonical
    cur.execute("UPDATE image_build_plans SET repo_name=%s WHERE repo_name=%s",
                (CANON, "vllm-ascend"))
    migrated = cur.rowcount
    print(f"\n[1] repo 口径迁移 ('vllm-ascend' -> '{CANON}'): {migrated} rows")

    # 2. 去重：每组 (pr_number, repo_name) 保留 MAX(id)，删其余
    cur.execute(
        """
        DELETE ibp FROM image_build_plans ibp
        JOIN (
            SELECT pr_number, COALESCE(repo_name,'') rn, MAX(id) keep_id
            FROM image_build_plans
            GROUP BY pr_number, COALESCE(repo_name,'')
            HAVING COUNT(*) > 1
        ) d
        ON ibp.pr_number = d.pr_number
        AND COALESCE(ibp.repo_name,'') = d.rn
        AND ibp.id <> d.keep_id
        """
    )
    dups = cur.rowcount
    print(f"[2] 去重删除（保留 max id）: {dups} 行")

    cur.execute("SELECT COUNT(*) FROM image_build_plans")
    print("  去重后 total rows:", cur.fetchone()[0])
    cur.execute(
        "SELECT pr_number, COUNT(*) c FROM image_build_plans GROUP BY pr_number, repo_name HAVING c>1 LIMIT 5")
    left = cur.fetchall()
    print("  仍重复组（应为 0）:", left)

    # 3. 加唯一约束
    if not uq_exists(cur):
        cur.execute(
            "ALTER TABLE image_build_plans "
            "ADD CONSTRAINT uq_build_plan_pr_repo UNIQUE (pr_number, repo_name)"
        )
        print("[3] 已加唯一约束 uq_build_plan_pr_repo (pr_number, repo_name)")
    else:
        print("[3] 唯一约束已存在，跳过")

    conn.commit()
    conn.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
