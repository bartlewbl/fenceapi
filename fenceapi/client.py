from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_USER_AGENT = (
    "fenceapi/0.1 (+https://github.com/boutfence; polite tournament scraper; "
    "contact via Boutfence)"
)


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str) -> None:
        super().__init__(f"{message} [{url}]")
        self.url = url
        self.status = status


class HttpClient:
    """Small stdlib HTTP client with retries and a request gap for Lambda."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 30.0,
        min_interval: float = 1.0,
        max_retries: int = 3,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last_request_at = 0.0
        self._opener = opener or urllib.request.build_opener()

    def get_text(self, url: str) -> str:
        body, _ = self._get(url)
        return body.decode("utf-8")

    def get_json(self, url: str) -> Any:
        body, content_type = self._get(url)
        text = body.decode("utf-8")
        if "json" not in content_type and not text.lstrip().startswith(("[", "{")):
            raise FetchError(url, None, f"Expected JSON, got {content_type!r}")
        # Ophardt calendar-json prefixes a nation code before the array: GER[{...}]
        start = min(
            (i for i in (text.find("["), text.find("{")) if i >= 0),
            default=-1,
        )
        if start > 0:
            text = text[start:]
        return json.loads(text)

    def _get(self, url: str) -> tuple[bytes, str]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en",
                },
            )
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    content_type = response.headers.get_content_type()
                    return response.read(), content_type
            except urllib.error.HTTPError as exc:
                last_error = FetchError(url, exc.code, f"HTTP {exc.code}")
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep((2**attempt) + random.random())
                    continue
                raise last_error from exc
            except urllib.error.URLError as exc:
                last_error = FetchError(url, None, str(exc.reason))
                if attempt < self.max_retries:
                    time.sleep((2**attempt) + random.random())
                    continue
                raise last_error from exc
        raise last_error or FetchError(url, None, "request failed")

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            self._last_request_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_request_at
        wait_for = self.min_interval - elapsed
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request_at = time.monotonic()
