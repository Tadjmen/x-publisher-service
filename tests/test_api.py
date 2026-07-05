from fastapi.testclient import TestClient

from execution import main


class FakeXClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def post_thread(self, tweets, media_paths=None):
        assert tweets
        assert media_paths == []
        return ["1234567890"]

    async def delete_tweet(self, tweet_id: str) -> bool:
        assert tweet_id == "1234567890"
        return True


def configure_env(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_ACCESS_TOKEN", "secret")
    monkeypatch.setenv("X_AUTH_TOKEN", "auth")
    monkeypatch.setenv("X_CT0", "ct0")
    monkeypatch.setenv("X_WEB_BEARER_TOKEN", "bearer")
    monkeypatch.setenv("ALLOW_LOCAL_MEDIA_PATHS", "false")


def test_tweet_endpoint_posts_text(monkeypatch) -> None:
    configure_env(monkeypatch)
    monkeypatch.setattr(main, "XCookieClient", FakeXClient)
    client = TestClient(main.app)

    response = client.post("/tweet", headers={"Authorization": "Bearer secret"}, json={"text": "hello"})

    assert response.status_code == 200
    assert response.json()["tweet_id"] == "1234567890"


def test_delete_endpoint_deletes_tweet(monkeypatch) -> None:
    configure_env(monkeypatch)
    monkeypatch.setattr(main, "XCookieClient", FakeXClient)
    client = TestClient(main.app)

    response = client.post("/delete", headers={"Authorization": "Bearer secret"}, json={"tweet_id": "1234567890"})

    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_protected_endpoint_requires_service_access_token(monkeypatch) -> None:
    configure_env(monkeypatch)
    client = TestClient(main.app)

    response = client.post("/tweet", json={"text": "hello"})

    assert response.status_code == 401


def test_local_media_paths_are_disabled_by_default(monkeypatch) -> None:
    configure_env(monkeypatch)
    monkeypatch.setattr(main, "XCookieClient", FakeXClient)
    client = TestClient(main.app)

    response = client.post(
        "/tweet",
        headers={"Authorization": "Bearer secret"},
        json={"text": "hello", "media_paths": ["C:/tmp/image.png"]},
    )

    assert response.status_code == 400
    assert "local media paths are disabled" in response.text
