from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .media import download_media_url, validate_local_media_path
from .x_client import BROWSER, DEFAULT_MAX_TWEET_CHARS, XCookieClient, XCookieError

load_dotenv()

app = FastAPI(
    title="x-publisher-service",
    description="Cookie-based X/Twitter publisher with tweet, thread, media upload, and delete endpoints.",
    version="0.1.0",
)
bearer_auth = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Settings:
    service_access_token: str
    x_auth_token: str
    x_ct0: str
    x_web_bearer_token: str
    proxy_url: str
    browser: str
    max_tweet_chars: int
    max_media_download_bytes: int
    allow_local_media_paths: bool


class TweetRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    media_urls: list[str] = Field(default_factory=list, max_length=4)
    media_paths: list[str] = Field(default_factory=list, max_length=4)


class ThreadRequest(BaseModel):
    tweets: list[str] = Field(min_length=1, max_length=25)
    media_urls: list[str] = Field(default_factory=list, max_length=4)
    media_paths: list[str] = Field(default_factory=list, max_length=4)


class DeleteRequest(BaseModel):
    tweet_id: str = Field(min_length=1, max_length=64)


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    load_dotenv(override=False)
    max_media_mb = int(os.getenv("MAX_MEDIA_DOWNLOAD_MB", "50"))
    return Settings(
        service_access_token=os.getenv("SERVICE_ACCESS_TOKEN", ""),
        x_auth_token=os.getenv("X_AUTH_TOKEN", ""),
        x_ct0=os.getenv("X_CT0", ""),
        x_web_bearer_token=os.getenv("X_WEB_BEARER_TOKEN", ""),
        proxy_url=os.getenv("PROXY_URL") or os.getenv("X_PROXY_URL", ""),
        browser=os.getenv("BROWSER", BROWSER),
        max_tweet_chars=int(os.getenv("MAX_TWEET_CHARS", str(DEFAULT_MAX_TWEET_CHARS))),
        max_media_download_bytes=max_media_mb * 1024 * 1024,
        allow_local_media_paths=_bool_env("ALLOW_LOCAL_MEDIA_PATHS", False),
    )


def require_service_access(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_auth),
) -> Settings:
    settings = load_settings()
    if not settings.service_access_token:
        raise HTTPException(status_code=503, detail="SERVICE_ACCESS_TOKEN is not configured")
    if not credentials or credentials.scheme.lower() != "bearer" or credentials.credentials != settings.service_access_token:
        raise HTTPException(status_code=401, detail="invalid service access token")
    return settings


def build_client(settings: Settings) -> XCookieClient:
    missing = [
        name
        for name, value in {
            "X_AUTH_TOKEN": settings.x_auth_token,
            "X_CT0": settings.x_ct0,
            "X_WEB_BEARER_TOKEN": settings.x_web_bearer_token,
        }.items()
        if not value
    ]
    if missing:
        raise HTTPException(status_code=503, detail=f"missing environment variables: {', '.join(missing)}")
    return XCookieClient(
        auth_token=settings.x_auth_token,
        ct0=settings.x_ct0,
        web_bearer_token=settings.x_web_bearer_token,
        proxy_url=settings.proxy_url,
        browser=settings.browser,
        max_tweet_chars=settings.max_tweet_chars,
        label="api",
    )


async def collect_media_paths(
    *,
    media_urls: list[str],
    media_paths: list[str],
    settings: Settings,
    temp_dir: Path,
) -> list[str]:
    collected: list[str] = []
    for url in media_urls:
        collected.append(
            await download_media_url(url, temp_dir, max_bytes=settings.max_media_download_bytes)
        )
    if media_paths:
        if not settings.allow_local_media_paths:
            raise HTTPException(status_code=400, detail="local media paths are disabled")
        collected.extend(validate_local_media_path(path) for path in media_paths)
    return collected[:4]


@app.get("/health")
def health() -> dict:
    settings = load_settings()
    return {
        "ok": True,
        "configured": {
            "service_access_token": bool(settings.service_access_token),
            "x_auth_token": bool(settings.x_auth_token),
            "x_ct0": bool(settings.x_ct0),
            "x_web_bearer_token": bool(settings.x_web_bearer_token),
            "proxy": bool(settings.proxy_url),
            "allow_local_media_paths": settings.allow_local_media_paths,
        },
    }


@app.get("/ip")
async def ip(settings: Settings = Depends(require_service_access)) -> dict:
    async with AsyncSession(impersonate=settings.browser, proxies=_proxies(settings)) as session:
        response = await session.get("https://api.ipify.org?format=json", timeout=15)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="ip check failed")
    return response.json()


@app.post("/tweet")
async def tweet(request: TweetRequest, settings: Settings = Depends(require_service_access)) -> dict:
    try:
        client = build_client(settings)
        with tempfile.TemporaryDirectory() as directory:
            media_paths = await collect_media_paths(
                media_urls=request.media_urls,
                media_paths=request.media_paths,
                settings=settings,
                temp_dir=Path(directory),
            )
            tweet_ids = await client.post_thread([request.text], media_paths)
        return {"success": True, "tweet_id": tweet_ids[0] if tweet_ids else None, "tweet_ids": tweet_ids}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except XCookieError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/thread")
async def thread(request: ThreadRequest, settings: Settings = Depends(require_service_access)) -> dict:
    try:
        client = build_client(settings)
        with tempfile.TemporaryDirectory() as directory:
            media_paths = await collect_media_paths(
                media_urls=request.media_urls,
                media_paths=request.media_paths,
                settings=settings,
                temp_dir=Path(directory),
            )
            tweet_ids = await client.post_thread(request.tweets, media_paths)
        return {"success": True, "tweet_ids": tweet_ids}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except XCookieError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/delete")
async def delete(request: DeleteRequest, settings: Settings = Depends(require_service_access)) -> dict:
    try:
        client = build_client(settings)
        deleted = await client.delete_tweet(request.tweet_id)
        return {"success": True, "deleted": deleted, "tweet_id": request.tweet_id}
    except HTTPException:
        raise
    except XCookieError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _proxies(settings: Settings) -> dict[str, str] | None:
    return {"https": settings.proxy_url, "http": settings.proxy_url} if settings.proxy_url else None
