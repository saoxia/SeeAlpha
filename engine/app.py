"""Internal HTTP API. The business backend is the only intended client."""

import hmac
import json
from contextlib import aclosing

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from engine.config import EngineConfig
from engine.connectors.binance.queries import QueryError
from engine.runtime import EngineConflict, EngineRuntime, valuation_events


class Credentials(BaseModel):
    environment: str = Field(pattern=r"^(production|sandbox)$")
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)

    def account(self, account_id):
        return {
            "id": int(account_id) if str(account_id).isdigit() else account_id,
            "environment": self.environment,
            "credentials": {"apiKey": self.api_key, "secret": self.api_secret},
        }


class CommandBody(Credentials):
    action: str
    values: dict
    fingerprint: str
    client_order_id: str


class ManualBody(Credentials):
    symbol: str
    command: dict


class WorkerBody(BaseModel):
    command: list[str]
    log_path: str
    access: list | None = None
    hold_lease: bool = False
    log_mode: str = "ab"


class GapBody(BaseModel):
    scope: str
    gap_key: str
    gap: dict


class WorkerNote(BaseModel):
    command: list[str]


def create_app(config: EngineConfig | None = None, runtime: EngineRuntime | None = None) -> FastAPI:
    config = config or EngineConfig.from_environment()
    runtime = runtime or EngineRuntime(config.state_dir, config.project)

    def authorized(authorization: str = Header(default="")):
        expected = f"Bearer {config.token}"
        if len(authorization) != len(expected) or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "引擎令牌无效。")

    app = FastAPI(title="SeeAlpha Engine", version="0.1.0", dependencies=[Depends(authorized)])
    app.state.runtime = runtime

    def conflict(error: EngineConflict):
        raise HTTPException(409, error.message) from None

    @app.get("/health")
    def health(account_id: str | None = None):
        return runtime.health(account_id)

    @app.post("/accounts/{account_id}/connections")
    def connect(account_id: str, body: Credentials):
        return runtime.register(account_id, body.environment, body.api_key, body.api_secret)

    @app.delete("/accounts/{account_id}/connections")
    def disconnect(account_id: str, environment: str):
        runtime.forget(account_id, environment)
        return {"account_id": account_id, "environment": environment, "status": "closed"}

    @app.get("/accounts/{account_id}/health")
    def account_health(account_id: str):
        return runtime.health(account_id)

    @app.post("/accounts/{account_id}/snapshots")
    async def snapshot(account_id: str, body: Credentials):
        try:
            result = await runtime.account_snapshot(body.account(account_id))
        except EngineConflict as error:
            conflict(error)
        return {
            "balances": result.balances, "info": result.info,
            "positions": result.positions, "observed_at": result.observed_at,
        }

    @app.post("/accounts/{account_id}/manual")
    async def manual(account_id: str, body: ManualBody):
        try:
            return await runtime.manual_execute(body.account(account_id), body.symbol, body.command)
        except EngineConflict as error:
            conflict(error)

    @app.post("/accounts/{account_id}/orders")
    async def order(account_id: str, body: CommandBody):
        try:
            return await runtime.command(
                body.account(account_id), body.action, body.values, body.fingerprint, body.client_order_id,
            )
        except EngineConflict as error:
            conflict(error)

    @app.get("/accounts/{account_id}/orders")
    def orders(account_id: str, environment: str):
        return {"orders": runtime.store.orders(account_id, environment)}

    @app.get("/accounts/{account_id}/fills")
    def fills(account_id: str, environment: str):
        return {"fills": runtime.store.fills(account_id, environment)}

    @app.get("/accounts/{account_id}/positions")
    def positions(account_id: str, environment: str):
        return {"positions": runtime.store.positions(account_id, environment)}

    @app.get("/accounts/{account_id}/gaps")
    def gaps(account_id: str, scope: str | None = None):
        return {"gaps": runtime.store.gaps(account_id, scope)}

    @app.post("/accounts/{account_id}/gaps")
    def put_gap(account_id: str, body: GapBody):
        return runtime.note_gap(account_id, body.scope, body.gap_key, body.gap)

    @app.get("/accounts/{account_id}/requests/{request_id}")
    def request_row(account_id: str, request_id: str):
        stored = runtime.store.get_request(account_id, request_id)
        if stored is None:
            raise HTTPException(404, "服务器没有该请求记录，请在交易所核对后处理。")
        return stored

    @app.put("/accounts/{account_id}/requests/{request_id}")
    def save_request(account_id: str, request_id: str, body: dict):
        return runtime.save_request(account_id, request_id, body)

    @app.get("/accounts/{account_id}/events")
    def events(account_id: str, after: int = 0):
        rows = runtime.store.events(account_id, after)
        return {"events": rows, "next": rows[-1]["id"] if rows else after}

    @app.get("/accounts/{account_id}/market/subscriptions")
    def subscriptions(account_id: str):
        return {"subscriptions": runtime.store.subscriptions(account_id)}

    @app.post("/accounts/{account_id}/market/workers/note")
    def note_worker(account_id: str, body: WorkerNote):
        noted = runtime.note_worker(account_id, body.command)
        if noted is None:
            raise HTTPException(422, "无法从命令识别行情订阅。")
        return noted

    @app.post("/accounts/{account_id}/market/workers")
    def start_worker(account_id: str, body: WorkerBody):
        try:
            started = runtime.spawn_worker(
                body.command, body.log_path, access=tuple(body.access) if body.access else None,
                hold_lease=body.hold_lease, log_mode=body.log_mode,
            )
        except EngineConflict as error:
            conflict(error)
        runtime.note_worker(account_id, body.command)
        return started

    @app.get("/workers/{worker_id}")
    def worker(worker_id: str):
        try:
            return runtime.worker(worker_id)
        except EngineConflict as error:
            raise HTTPException(404, error.message) from None

    @app.post("/workers/{worker_id}/stop")
    def stop_worker(worker_id: str):
        try:
            return runtime.stop_worker(worker_id)
        except EngineConflict as error:
            raise HTTPException(404, error.message) from None

    @app.post("/accounts/{account_id}/valuation")
    async def valuation(account_id: str, body: Credentials):
        account = body.account(account_id)

        async def lines():
            try:
                async with aclosing(valuation_events(account)) as events:
                    async for event in events:
                        runtime.store.add_event(account_id, "valuation", {
                            "environment": body.environment, "reason": event.get("reason"), "time": event.get("time"),
                        })
                        yield json.dumps(event, ensure_ascii=False) + "\n"
            except QueryError as error:
                yield json.dumps({"error": str(error)}, ensure_ascii=False) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    return app
