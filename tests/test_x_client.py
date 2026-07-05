from execution.x_client import XCookieClient


def make_client() -> XCookieClient:
    return XCookieClient("auth", "ct0", web_bearer_token="bearer", max_tweet_chars=20)


def test_fit_tweet_compacts_whitespace() -> None:
    client = make_client()

    assert client._fit_tweet(" hello   world ") == "hello world"


def test_fit_tweet_trims_long_text() -> None:
    client = make_client()

    result = client._fit_tweet("one two three four five six")

    assert len(result) <= 20
    assert result.endswith("…")


def test_media_category() -> None:
    client = make_client()

    assert client._media_category("video/mp4") == "tweet_video"
    assert client._media_category("image/gif") == "tweet_gif"
    assert client._media_category("image/png") == "tweet_image"


def test_classifies_daily_limit_error() -> None:
    client = make_client()

    message = client._classify_error(
        {"errors": [{"code": "501", "message": "Authorization: You've hit the daily limit."}]},
        403,
    )

    assert message.startswith("X_DAILY_LIMIT")


def test_payload_attaches_reply_and_media() -> None:
    client = make_client()

    payload = client._payload("hello", "query-id", "parent-id", ["media-id"])

    assert payload["queryId"] == "query-id"
    assert payload["variables"]["tweet_text"] == "hello"
    assert payload["variables"]["reply"]["in_reply_to_tweet_id"] == "parent-id"
    assert payload["variables"]["media"]["media_entities"][0]["media_id"] == "media-id"


def test_get_graphql_query_id_uses_cached_operation() -> None:
    client = make_client()
    client._gql_cache = {"DeleteTweet": "delete-query-id"}
    client._cache_ts = 9999999999

    import asyncio

    query_id = asyncio.run(client._get_graphql_query_id("DeleteTweet"))

    assert query_id == "delete-query-id"


def test_get_graphql_query_id_uses_fallback() -> None:
    client = make_client()
    client._gql_cache = {"OtherOperation": "other-id"}
    client._cache_ts = 9999999999

    import asyncio

    query_id = asyncio.run(client._get_graphql_query_id("CreateTweet", "fallback-id"))

    assert query_id == "fallback-id"


def test_extract_operation_ids_supports_relay_params() -> None:
    client = make_client()
    js = "params:{id:`6DqoQrCrai4VWFRVzm9pDg`,metadata:{},name:`deleteTweetMutation`,operationKind:`mutation`,text:null}"

    ops = client._extract_operation_ids(js)

    assert ops["deleteTweetMutation"] == "6DqoQrCrai4VWFRVzm9pDg"


def test_delete_tweet_operation_prefers_legacy_when_cached() -> None:
    client = make_client()
    client._gql_cache = {"DeleteTweet": "legacy-delete-id"}
    client._cache_ts = 9999999999

    import asyncio

    operation_name, query_id, variables = asyncio.run(client._delete_tweet_operation("123"))

    assert operation_name == "DeleteTweet"
    assert query_id == "legacy-delete-id"
    assert variables["tweet_id"] == "123"


def test_delete_tweet_operation_uses_relay_mutation_when_cached() -> None:
    client = make_client()
    client._gql_cache = {"deleteTweetMutation": "relay-delete-id"}
    client._cache_ts = 9999999999

    import asyncio

    operation_name, query_id, variables = asyncio.run(client._delete_tweet_operation("123"))

    assert operation_name == "deleteTweetMutation"
    assert query_id == "relay-delete-id"
    assert variables["tweetId"] == "123"

