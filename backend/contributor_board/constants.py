"""贡献者分析模块 — 全局常量配置"""

# 四象限中文标签映射
QUADRANT_LABELS: dict[str, str] = {
    "INCENTIVE": "激励区",
    "IMPROVE": "改进区",
    "ENCOURAGE": "鼓励区",
    "REPLACE": "置换区",
    "unknown": "未知",
}

# 四象限等级排序（值越大等级越高）
QUADRANT_RANK: dict[str, int] = {
    "INCENTIVE": 4,
    "IMPROVE": 3,
    "ENCOURAGE": 2,
    "REPLACE": 1,
    "unknown": 0,
}
