"""HTTP API and dashboard."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .service import Fleet, Invalid, NotFound


class DeployRequest(BaseModel):
    host: str
    mode: Literal["test", "switch", "boot"]
    rev: str | None = None


def create_app(fleet: Fleet, *, start_background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_background:
            fleet.start()
        yield
        if start_background:
            fleet.stop()

    app = FastAPI(title="fleet-deployer", lifespan=lifespan)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        expected = fleet.config.token()
        if not expected:
            raise HTTPException(503, "no API token configured; actions are disabled")
        scheme, _, given = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(given.strip(), expected):
            raise HTTPException(401, "missing or wrong bearer token", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(NotFound)
    async def not_found(_, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Invalid)
    async def invalid(_, exc: Invalid):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/hosts")
    def hosts() -> list[dict]:
        return fleet.host_views()

    @app.get("/api/hosts/{name}")
    def host(name: str) -> dict:
        return fleet.host_view(name)

    @app.get("/api/main")
    def main() -> dict:
        return fleet.main_view()

    @app.get("/api/jobs")
    def jobs(limit: int = Query(30, ge=1, le=200)) -> list[dict]:
        return fleet.job_views(limit)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int) -> dict:
        return fleet.job_view(job_id)

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: int, offset: int = Query(0, ge=0)) -> dict:
        text, next_offset = fleet.job_log(job_id, offset)
        return {"text": text, "offset": next_offset, "state": fleet.job_view(job_id)["state"]}

    @app.post("/api/refresh", status_code=202, dependencies=[Depends(require_token)])
    def refresh() -> dict:
        fleet.wake()
        return {"status": "refresh scheduled"}

    @app.post("/api/deploy", status_code=202, dependencies=[Depends(require_token)])
    def deploy(req: DeployRequest) -> dict:
        job_id = fleet.submit(req.host, req.mode, req.rev)
        return {"job": job_id}

    @app.get("/api/auth", dependencies=[Depends(require_token)])
    def auth() -> dict:
        """Lets the dashboard check a token without side effects."""
        return {"ok": True}

    if fleet.config.static_dir:
        app.mount("/", StaticFiles(directory=fleet.config.static_dir, html=True), name="static")

    return app
