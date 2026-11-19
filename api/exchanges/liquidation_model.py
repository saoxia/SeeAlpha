"""Read-only external liquidation model proxy. Never infer a market distribution from OI.

An operator must explicitly configure an HTTPS model endpoint which returns
{symbol, source, time, levels:[{price,notional,side}]}. No source configured:
no model displayed. This API is not a trading signal or risk input.
"""
import ipaddress
import os
import socket
import time
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException


def configured_provider():
    endpoint = os.getenv("SEEALPHA_LIQ_MODEL_ENDPOINT", "").strip()
    if not endpoint:
        return None
    uri = urlparse(endpoint)
    if (uri.scheme != "https" or not uri.hostname or uri.username or uri.password
            or uri.fragment or uri.port not in (None, 443)):
        raise HTTPException(503, "强平模型提供方配置无效。")
    try:
        addresses = socket.getaddrinfo(uri.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise HTTPException(503, "强平模型提供方网络地址无效。")
    except OSError:
        raise HTTPException(503, "强平模型提供方暂不可达。") from None
    return endpoint


def validate_model(raw, symbol, now=None):
    now = int(time.time() * 1000) if now is None else now
    if not isinstance(raw, dict) or raw.get("symbol") != symbol:
        raise ValueError("model symbol mismatch")
    stamp, source, rows = raw.get("time"), raw.get("source"), raw.get("levels")
    if (type(stamp) is not int or stamp > now + 5000 or now - stamp > 60_000
            or not isinstance(source, str) or not 0 < len(source.strip()) <= 80
            or not isinstance(rows, list) or not 0 < len(rows) <= 300):
        raise ValueError("model origin, freshness or size invalid")
    import math
    levels = []
    for row in rows:
        if not isinstance(row, dict) or row.get("side") not in {"long", "short"}:
            raise ValueError("model level invalid")
        price, notional = row.get("price"), row.get("notional")
        if (type(price) not in {float, int} or type(notional) not in {float, int}
                or not math.isfinite(price) or not math.isfinite(notional)
                or price <= 0 or notional <= 0):
            raise ValueError("model price/notional invalid")
        levels.append({"price": price, "notional": notional, "side": row["side"]})
    return {"symbol": symbol, "source": source.strip(), "time": stamp, "levels": levels}


async def fetch_model(symbol):
    endpoint = configured_provider()
    if endpoint is None:
        return {"model": None, "reason": "未配置可信强平模型来源"}
    headers = {"Accept": "application/json"}
    token = os.getenv("SEEALPHA_LIQ_MODEL_API_KEY", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=False, trust_env=False) as client:
            response = await client.get(endpoint, params={"symbol": symbol}, headers=headers)
        if response.status_code != 200 or len(response.content) > 128_000:
            raise ValueError("model unavailable")
        return {"model": validate_model(response.json(), symbol), "reason": None}
    except (httpx.HTTPError, ValueError):
        return {"model": None, "reason": "模型来源不可用或数据未通过来源/时效/数值校验"}
