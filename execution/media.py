from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import urlparse
import uuid

from curl_cffi.requests import AsyncSession

ALLOWED_MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".mp4",
    ".mov",
    ".m4v",
}


def validate_media_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid media url: {url}")
    return url


def validate_local_media_path(path: str) -> str:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"media path does not exist: {path}")
    if resolved.suffix.lower() not in ALLOWED_MEDIA_EXTENSIONS:
        raise ValueError(f"unsupported media file type: {resolved.suffix}")
    return str(resolved)


def suffix_from_url_or_type(url: str, content_type: str | None) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in ALLOWED_MEDIA_EXTENSIONS:
        return suffix
    guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip())
    if guessed and guessed.lower() in ALLOWED_MEDIA_EXTENSIONS:
        return guessed.lower()
    return ".bin"


async def download_media_url(url: str, target_dir: Path, *, max_bytes: int) -> str:
    validate_media_url(url)
    async with AsyncSession(impersonate="chrome136") as session:
        response = await session.get(url, timeout=60)
    if response.status_code >= 400:
        raise ValueError(f"media download failed with status {response.status_code}")

    content = response.content
    if len(content) > max_bytes:
        raise ValueError(f"media file too large: {len(content)} bytes > {max_bytes} bytes")

    suffix = suffix_from_url_or_type(url, response.headers.get("content-type"))
    if suffix == ".bin":
        raise ValueError("unsupported media content type")

    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"x-media-{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)
    return str(target)

