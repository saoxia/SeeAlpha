"""Platform reads tenant Engine service health through HTTP only."""

from fastapi import HTTPException


class ServiceMonitor:
    """Engine alone validates collection, history and calculation evidence."""

    def __init__(self, manager):
        self.manager = manager

    def read(self):
        engine = getattr(self.manager, "engine", None)
        if engine is None:
            raise HTTPException(503, "行情 Engine 未配置，无法读取服务监控。")
        return engine.market_control("services")
