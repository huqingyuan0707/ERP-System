"""系统数据清理脚本示例。

此脚本展示了如何使用 MaintenanceService 清理过期数据。

使用方式：
1. 直接运行：python scripts/cleanup_data.py
2. 配置到定时任务（如cron、Celery beat、APScheduler等）

建议执行频率：
- 幂等键清理：每天凌晨执行一次
- 操作日志清理：每周执行一次
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.db.session import async_session_maker
from app.services.maintenance import MaintenanceService


async def cleanup_all():
    """执行所有清理任务。"""
    async with async_session_maker() as db:
        maintenance = MaintenanceService(db)

        # 清理幂等键
        print("=== 幂等键清理 ===")
        stats_before = await maintenance.get_idempotency_stats()
        print(f"清理前 - 总记录数: {stats_before['total_count']}, 已过期: {stats_before['expired_count']}")

        result = await maintenance.cleanup_expired_idempotency_keys()
        print(f"删除记录数: {result['deleted_count']}")

        stats_after = await maintenance.get_idempotency_stats()
        print(f"清理后 - 总记录数: {stats_after['total_count']}, 已过期: {stats_after['expired_count']}")

        # 清理操作日志（保留90天）
        print("\n=== 操作日志清理 ===")
        log_stats_before = await maintenance.get_oper_log_stats()
        print(f"清理前 - 总记录数: {log_stats_before['total_count']}, 最近90天: {log_stats_before['last_90_days']}")

        log_result = await maintenance.cleanup_old_oper_logs(retention_days=90)
        print(f"删除记录数: {log_result['deleted_count']}")
        print(f"保留天数: {log_result['retention_days']}")
        print(f"截止日期: {log_result['cutoff_date']}")

        log_stats_after = await maintenance.get_oper_log_stats()
        print(f"清理后 - 总记录数: {log_stats_after['total_count']}, 最近90天: {log_stats_after['last_90_days']}")

        # 获取所有统计信息
        print("\n=== 系统整体统计 ===")
        all_stats = await maintenance.get_all_stats()
        print(f"统计时间: {all_stats['timestamp']}")
        print(f"\n幂等键统计:")
        print(f"  总记录数: {all_stats['idempotency']['total_count']}")
        print(f"  已过期: {all_stats['idempotency']['expired_count']}")
        print(f"  未过期: {all_stats['idempotency']['active_count']}")
        print(f"\n操作日志统计:")
        print(f"  总记录数: {all_stats['oper_log']['total_count']}")
        print(f"  最近30天: {all_stats['oper_log']['last_30_days']}")
        print(f"  最近90天: {all_stats['oper_log']['last_90_days']}")

        print("\n=== 所有清理任务完成 ===")


if __name__ == "__main__":
    asyncio.run(cleanup_all())
