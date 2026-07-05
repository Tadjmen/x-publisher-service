from pathlib import Path

import pytest

from execution.media import (
    suffix_from_url_or_type,
    validate_local_media_path,
    validate_media_url,
)


def test_validate_media_url_accepts_http_https() -> None:
    assert validate_media_url("https://example.com/a.png") == "https://example.com/a.png"


def test_validate_media_url_rejects_file_scheme() -> None:
    with pytest.raises(ValueError):
        validate_media_url("file:///etc/passwd")


def test_suffix_from_content_type_when_url_has_no_suffix() -> None:
    assert suffix_from_url_or_type("https://example.com/media", "video/mp4") == ".mp4"


def test_validate_local_media_path_rejects_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("nope", encoding="utf-8")

    with pytest.raises(ValueError):
        validate_local_media_path(str(path))

