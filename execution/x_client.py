from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin
import uuid

from bs4 import BeautifulSoup
from curl_cffi import CurlMime
from curl_cffi.requests import AsyncSession
from x_client_transaction import ClientTransaction
from x_client_transaction.constants import ON_DEMAND_FILE_REGEX
from x_client_transaction.constants import ON_DEMAND_FILE_URL
from x_client_transaction.constants import ON_DEMAND_HASH_PATTERN

log = logging.getLogger("x_publisher_service")

BROWSER = "chrome136"
FALLBACK_CREATE_TWEET_QUERY_ID = "S1qcGUn68_U0lDKdMlYSGg"
DEFAULT_MAX_TWEET_CHARS = 250
DEFAULT_FEATURES = {
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "articles_preview_enabled": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "longform_notetweets_rich_text_read_enabled": True,
    "post_ctas_fetch_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_profile_redirect_enabled": False,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "rweb_tipjar_consumption_enabled": False,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "verified_phone_label_enabled": True,
    "view_counts_everywhere_api_enabled": True,
}


class XCookieError(Exception):
    """Raised when X returns an error or the client cannot extract a result."""


class XCookieClient:
    def __init__(
        self,
        auth_token: str,
        ct0: str,
        *,
        label: str = "x",
        proxy_url: str = "",
        browser: str = BROWSER,
        max_tweet_chars: int = DEFAULT_MAX_TWEET_CHARS,
        web_bearer_token: str | None = None,
    ) -> None:
        if not auth_token:
            raise ValueError("auth_token is required")
        if not ct0:
            raise ValueError("ct0 is required")
        self.label = label
        self.auth_token = auth_token
        self.ct0 = ct0
        self.proxy_url = proxy_url
        self.browser = browser
        self.max_tweet_chars = max_tweet_chars
        self.web_bearer_token = web_bearer_token or os.getenv("X_WEB_BEARER_TOKEN", "")
        if not self.web_bearer_token:
            raise ValueError("web_bearer_token is required or set X_WEB_BEARER_TOKEN")
        self.client_uuid = str(uuid.uuid4())
        self._gql_cache: dict[str, str] = {}
        self._features_cache: dict[str, bool] = {}
        self._transaction_ctx: ClientTransaction | None = None
        self._cache_ts = 0.0

    async def post_thread(self, tweets: list[str], media_paths: list[str] | None = None) -> list[str]:
        ids: list[str] = []
        reply_to: str | None = None
        media_ids = await self.upload_media_files(media_paths or [])
        for index, tweet in enumerate(tweets):
            tweet_media_ids = media_ids if index == 0 else None
            tweet_id = await self.post_tweet(self._fit_tweet(tweet), reply_to, tweet_media_ids)
            if tweet_id:
                ids.append(tweet_id)
                reply_to = tweet_id
            await self._small_delay()
        return ids

    async def post_tweet(
        self,
        text: str,
        in_reply_to_tweet_id: str | None = None,
        media_ids: list[str] | None = None,
    ) -> str | None:
        query_id = await self._get_create_tweet_id()
        result = await self._attempt_tweet(text, query_id, in_reply_to_tweet_id, media_ids or [])
        status = result["status_code"]
        data = result["data"]
        if status != 200:
            raise XCookieError(self._classify_error(data, status) or f"X API {status}: {json.dumps(data)[:500]}")
        tweet_id = self._extract_tweet_id(data)
        if tweet_id:
            return tweet_id
        if "errors" in data:
            err = self._classify_error(data, status)
            if err.startswith("DUPLICATE_TWEET"):
                return None
            raise XCookieError(err)
        raise XCookieError(f"EMPTY_RESULT: {json.dumps(data)[:500]}")

    async def delete_tweet(self, tweet_id: str) -> bool:
        operation_name, query_id, variables = await self._delete_tweet_operation(str(tweet_id))
        path = f"/i/api/graphql/{query_id}/{operation_name}"
        url = f"https://x.com{path}"
        payload = {
            "variables": variables,
            "queryId": query_id,
        }
        async with AsyncSession(impersonate=self.browser, proxies=self._proxies()) as session:
            resp = await session.post(
                url,
                headers=self._headers("POST", path),
                json=payload,
                timeout=30,
            )
        data = self._safe_json(resp)
        if resp.status_code != 200:
            raise XCookieError(self._classify_error(data, resp.status_code) or f"X API {resp.status_code}: {json.dumps(data)[:500]}")
        if "errors" in data:
            raise XCookieError(self._classify_error(data, resp.status_code))
        return True

    async def _delete_tweet_operation(self, tweet_id: str) -> tuple[str, str, dict]:
        try:
            query_id = await self._get_graphql_query_id("DeleteTweet")
            return "DeleteTweet", query_id, {"tweet_id": tweet_id, "dark_request": False}
        except XCookieError:
            query_id = await self._get_graphql_query_id("deleteTweetMutation")
            return "deleteTweetMutation", query_id, {"tweetId": tweet_id}

    async def upload_media_files(self, media_paths: list[str]) -> list[str]:
        media_ids: list[str] = []
        for media_path in media_paths[:4]:
            path = Path(media_path)
            if not path.exists() or not path.is_file():
                continue
            try:
                media_id = await self._upload_media(path)
            except Exception as exc:
                log.warning("[%s] media upload failed for %s; posting text-only: %s", self.label, path, exc)
                continue
            if media_id:
                media_ids.append(media_id)
        return media_ids

    async def _small_delay(self) -> None:
        await asyncio.sleep(1)

    def _fit_tweet(self, text: str) -> str:
        text = " ".join(text.split()).strip()
        if len(text) <= self.max_tweet_chars:
            return text
        trimmed = text[: self.max_tweet_chars - 1].rsplit(" ", 1)[0].rstrip(".,;: ")
        return f"{trimmed}…" if trimmed else text[: self.max_tweet_chars]

    async def _upload_media(self, path: Path) -> str:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        media_category = self._media_category(media_type)
        total_bytes = path.stat().st_size
        init = await self._media_upload_request(
            data={
                "command": "INIT",
                "total_bytes": str(total_bytes),
                "media_type": media_type,
                "media_category": media_category,
            }
        )
        media_id = str(init.get("media_id_string") or init.get("media_id") or "")
        if not media_id:
            raise XCookieError(f"MEDIA_INIT_FAILED: {json.dumps(init)[:500]}")

        segment_index = 0
        with path.open("rb") as file:
            while True:
                chunk = file.read(4 * 1024 * 1024)
                if not chunk:
                    break
                await self._media_upload_request(
                    data={"command": "APPEND", "media_id": media_id, "segment_index": str(segment_index)},
                    media_part=(path.name, chunk, media_type),
                )
                segment_index += 1

        final = await self._media_upload_request(data={"command": "FINALIZE", "media_id": media_id})
        await self._wait_media_processing(media_id, final)
        return media_id

    def _media_category(self, media_type: str) -> str:
        if media_type.startswith("video/"):
            return "tweet_video"
        if media_type == "image/gif":
            return "tweet_gif"
        return "tweet_image"

    async def _wait_media_processing(self, media_id: str, data: dict) -> None:
        processing_info = data.get("processing_info") or {}
        while processing_info:
            state = processing_info.get("state")
            if state == "succeeded":
                return
            if state == "failed":
                raise XCookieError(f"MEDIA_PROCESSING_FAILED: {json.dumps(processing_info)[:500]}")
            await asyncio.sleep(int(processing_info.get("check_after_secs") or 2))
            status = await self._media_upload_request(data={"command": "STATUS", "media_id": media_id}, method="GET")
            processing_info = status.get("processing_info") or {}

    async def _media_upload_request(
        self,
        data: dict,
        media_part: tuple[str, bytes, str] | None = None,
        method: str = "POST",
    ) -> dict:
        url = "https://upload.twitter.com/i/media/upload.json"
        async with AsyncSession(impersonate=self.browser, proxies=self._proxies()) as session:
            if method == "GET":
                resp = await session.get(url, headers=self._upload_headers(), params=data, timeout=60)
            elif media_part:
                multipart = CurlMime()
                for key, value in data.items():
                    multipart.addpart(key, data=str(value).encode("utf-8"))
                filename, content, content_type = media_part
                multipart.addpart("media", filename=filename, content_type=content_type, data=content)
                resp = await session.post(url, headers=self._upload_headers(), multipart=multipart, timeout=120)
            else:
                resp = await session.post(url, headers=self._upload_headers(), data=data, timeout=120)
        payload = self._safe_json(resp)
        if resp.status_code >= 400:
            raise XCookieError(f"MEDIA_UPLOAD_{resp.status_code}: {json.dumps(payload)[:500]}")
        return payload

    async def _get_create_tweet_id(self) -> str:
        return await self._get_graphql_query_id("CreateTweet", FALLBACK_CREATE_TWEET_QUERY_ID)

    async def _get_graphql_query_id(self, operation_name: str, fallback: str | None = None) -> str:
        if self._gql_cache and time.time() - self._cache_ts < 3600:
            query_id = self._gql_cache.get(operation_name) or fallback
            if query_id:
                return query_id
            raise XCookieError(f"QUERY_ID_NOT_FOUND: {operation_name}")
        await self._scrape_gql_config()
        query_id = self._gql_cache.get(operation_name) or fallback
        if not query_id:
            raise XCookieError(f"QUERY_ID_NOT_FOUND: {operation_name}")
        return query_id

    async def _scrape_gql_config(self) -> None:
        try:
            async with AsyncSession(impersonate=self.browser, proxies=self._proxies()) as session:
                resp = await session.get("https://x.com/x", timeout=15)
                html = resp.text
                if ">document.location =" in html:
                    url = html.split('document.location = "')[1].split('"')[0]
                    html = (await session.get(url, timeout=15)).text
                await self._init_transaction_context(session, html)
                script_urls = self._html_script_urls(html, "https://x.com/x")
                ops: dict[str, str] = {}
                features: dict[str, bool] = {}
                seen: set[str] = set()
                queue = list(script_urls)
                while queue and len(seen) < 400:
                    url = queue.pop(0)
                    if url in seen:
                        continue
                    seen.add(url)
                    try:
                        js = (await session.get(url, timeout=15)).text
                    except Exception:
                        continue
                    ops.update(self._extract_operation_ids(js))
                    base_url = url.rsplit("/", 1)[0] + "/"
                    for asset_url in self._js_asset_urls(js, base_url):
                        if asset_url not in seen and asset_url not in queue:
                            queue.append(asset_url)
                    match = re.search(r'operationName:"CreateTweet".*?featureSwitches:\[([^\]]+)\]', js)
                    if match:
                        names = re.findall(r'"([^"]+)"', match.group(1))
                        features = {name: True for name in names}
                if ops:
                    self._gql_cache = ops
                    self._cache_ts = time.time()
                if features:
                    self._features_cache = features
        except Exception as exc:
            log.warning("[%s] X bundle scrape failed, using fallback: %s", self.label, exc)

    def _html_script_urls(self, html: str, base_url: str) -> list[str]:
        urls: list[str] = []
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)', html):
            url = urljoin(base_url, src)
            if "abs.twimg.com" in url and url.endswith(".js"):
                urls.append(url)
        return urls

    def _js_asset_urls(self, js: str, base_url: str) -> list[str]:
        urls: list[str] = []
        patterns = (
            r'["\'](assets/[^"\']+?\.js)["\']',
            r'["\'](/x-web/[^"\']+?\.js)["\']',
            r'["\'](https://abs\.twimg\.com/x-web/[^"\']+?\.js)["\']',
        )
        for pattern in patterns:
            for value in re.findall(pattern, js):
                url = urljoin(base_url, value)
                if "abs.twimg.com" in url and url.endswith(".js"):
                    urls.append(url)
        return urls

    def _extract_operation_ids(self, js: str) -> dict[str, str]:
        ops: dict[str, str] = {}
        for query_id, op_name in re.findall(r'queryId:"([^"]+)".{0,500}?operationName:"([^"]+)"', js):
            ops[op_name] = query_id
        for op_name, query_id in re.findall(r'operationName:"([^"]+)".{0,500}?queryId:"([^"]+)"', js):
            ops[op_name] = query_id
        for query_id, op_name in re.findall(r"params:\{id:`([^`]+)`[\s\S]{0,500}?name:`([^`]+)`", js):
            ops[op_name] = query_id
        return ops

    async def _init_transaction_context(self, session: AsyncSession, html: str) -> None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            ondemand_match = ON_DEMAND_FILE_REGEX.search(html)
            if not ondemand_match:
                return
            hash_match = re.search(ON_DEMAND_HASH_PATTERN.format(ondemand_match.group(1)), html)
            if not hash_match:
                return
            url = ON_DEMAND_FILE_URL.format(filename=hash_match.group(1))
            od_resp = await session.get(url, timeout=15)
            if od_resp.status_code == 200:
                self._transaction_ctx = ClientTransaction(soup, od_resp.text)
        except Exception as exc:
            log.warning("[%s] transaction context unavailable: %s", self.label, exc)

    async def _attempt_tweet(
        self,
        text: str,
        query_id: str,
        in_reply_to_tweet_id: str | None,
        media_ids: list[str],
    ) -> dict:
        path = f"/i/api/graphql/{query_id}/CreateTweet"
        url = f"https://x.com{path}"
        async with AsyncSession(impersonate=self.browser, proxies=self._proxies()) as session:
            resp = await session.post(
                url,
                headers=self._headers("POST", path),
                json=self._payload(text, query_id, in_reply_to_tweet_id, media_ids),
                timeout=30,
            )
        return {"status_code": resp.status_code, "data": self._safe_json(resp)}

    def _headers(self, method: str, path: str) -> dict:
        headers = {
            "authorization": f"Bearer {self.web_bearer_token}",
            "cookie": f"auth_token={self.auth_token}; ct0={self.ct0}",
            "x-csrf-token": self.ct0,
            "content-type": "application/json",
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "en",
            "x-client-uuid": self.client_uuid,
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "origin": "https://x.com",
            "referer": "https://x.com/compose/post",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if self._transaction_ctx:
            try:
                headers["x-client-transaction-id"] = self._transaction_ctx.generate_transaction_id(method=method, path=path)
            except Exception:
                pass
        return headers

    def _upload_headers(self) -> dict:
        return {
            "authorization": f"Bearer {self.web_bearer_token}",
            "cookie": f"auth_token={self.auth_token}; ct0={self.ct0}",
            "x-csrf-token": self.ct0,
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "en",
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "origin": "https://x.com",
            "referer": "https://x.com/compose/post",
        }

    def _payload(self, text: str, query_id: str, in_reply_to_tweet_id: str | None, media_ids: list[str]) -> dict:
        variables = {
            "tweet_text": self._fit_tweet(text),
            "dark_request": False,
            "media": {
                "media_entities": [{"media_id": media_id, "tagged_users": []} for media_id in media_ids],
                "possibly_sensitive": False,
            },
            "semantic_annotation_ids": [],
        }
        if in_reply_to_tweet_id:
            variables["reply"] = {"in_reply_to_tweet_id": in_reply_to_tweet_id, "exclude_reply_user_ids": []}
        return {"variables": variables, "features": self._features_cache or DEFAULT_FEATURES, "queryId": query_id}

    def _proxies(self) -> dict[str, str] | None:
        return {"https": self.proxy_url, "http": self.proxy_url} if self.proxy_url else None

    def _extract_tweet_id(self, data: dict) -> str | None:
        try:
            result = data["data"]["create_tweet"]["tweet_results"].get("result") or {}
            return result.get("rest_id") or result.get("tweet", {}).get("rest_id")
        except Exception:
            return None

    def _safe_json(self, response) -> dict:
        try:
            return response.json()
        except Exception:
            return {"raw": response.text[:1000]}

    def _classify_error(self, data: dict, status_code: int) -> str:
        errors = data.get("errors", []) if isinstance(data, dict) else []
        if not errors:
            if status_code in (401, 403):
                return "AUTH_EXPIRED: cookies are invalid or expired"
            return ""
        code = errors[0].get("code") or errors[0].get("extensions", {}).get("code")
        try:
            code_key = int(code)
        except (TypeError, ValueError):
            code_key = code
        msg = errors[0].get("message", "")
        if code_key == 501 or "daily limit" in msg.lower():
            return "X_DAILY_LIMIT: hit X daily post limit"
        return {
            32: "AUTH_EXPIRED: could not authenticate",
            36: "ACCOUNT_SUSPENDED",
            64: "ACCOUNT_SUSPENDED",
            89: "AUTH_EXPIRED: invalid token",
            130: "RATE_LIMIT",
            131: "X_INTERNAL_ERROR",
            187: "DUPLICATE_TWEET",
            226: "AUTOMATION_DETECTED",
            326: "ACCOUNT_LOCKED",
            344: "RATE_LIMIT: daily tweet limit reached",
        }.get(code_key, f"X_ERROR_{code}: {msg}")
