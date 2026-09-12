"""Exa client: web_search / web_fetch. Shape from
`reference/infra/search/exa.py` + `_shared.py`, trimmed to Ora's own scope (Exa
on the tag turn only, ORA-7). Never raises; three states (R-27, kept):
`ok`, `nothing_found` (the provider's own honest miss), `unavailable` (the
call itself failed — "I couldn't check", never "nothing found").
"""

from __future__ import annotations

import json
import logging
from typing import Any
from xml.sax.saxutils import escape

import aiohttp

log = logging.getLogger("ora.search")

EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_CONTENTS_URL = "https://api.exa.ai/contents"

# Exa's own bound on `contents.text.maxCharacters` (1-10000); above it the
# call 422s.
_EXA_MAX_CHARACTERS = 10000

UNAVAILABLE_SEARCH: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't run that search just now. Say so — don't answer as "
    "if you'd looked it up.",
}

UNAVAILABLE_FETCH: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't open that link just now. Say so — don't answer as "
    "if you'd read it.",
}


def clamp_chars(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def wrap(source: str, text: str) -> str:
    """The untrusted-content wrapper (R-13): a page controls its own
    text, so it cannot forge the boundary around it."""
    return f'<untrusted source="{escape(source)}">{escape(text)}</untrusted>'


class ExaSearchProvider:
    def __init__(
        self,
        *,
        api_key: str,
        max_results: int = 5,
        content_max_chars: int = 1500,
        extract_max_chars: int = 3000,
        timeout_seconds: float = 12.0,
    ) -> None:
        self._api_key = api_key
        self._max_results = max_results
        self._content_max_chars = content_max_chars
        self._extract_max_chars = extract_max_chars
        self._timeout_seconds = timeout_seconds

    async def search(self, *, query: str) -> dict[str, Any]:
        try:
            return await self._search(query)
        except Exception:
            log.exception("exa search failed")
            return dict(UNAVAILABLE_SEARCH)

    async def _search(self, query: str) -> dict[str, Any]:
        body = {
            "query": query,
            "numResults": self._max_results,
            "type": "auto",
            "contents": {
                "text": {"maxCharacters": self._request_chars(self._content_max_chars)}
            },
        }
        payload = await self._post(EXA_SEARCH_URL, body, "exa search")
        if payload is None:
            return dict(UNAVAILABLE_SEARCH)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return {"status": "nothing_found", "results": []}
        wrapped: list[dict[str, Any]] = []
        for item in results[: self._max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if not url:
                continue
            title = str(item.get("title") or "")
            text = str(item.get("text") or "")
            body_text = f"{title}\n{text}".strip() if title else text
            if not body_text:
                continue
            clamped, truncated = clamp_chars(body_text, self._content_max_chars)
            wrapped.append({"url": url, "content": wrap(url, clamped), "truncated": truncated})
        if not wrapped:
            return {"status": "nothing_found", "results": []}
        return {"status": "ok", "results": wrapped}

    async def fetch(self, *, url: str) -> dict[str, Any]:
        try:
            return await self._fetch(url)
        except Exception:
            log.exception("exa contents failed")
            return dict(UNAVAILABLE_FETCH)

    async def _fetch(self, url: str) -> dict[str, Any]:
        body = {
            "urls": [url],
            "text": {"maxCharacters": self._request_chars(self._extract_max_chars)},
        }
        payload = await self._post(EXA_CONTENTS_URL, body, "exa contents")
        if not isinstance(payload, dict):
            return dict(UNAVAILABLE_FETCH)
        results = payload.get("results")
        if not isinstance(results, list):
            log.warning("exa contents envelope missing results")
            return dict(UNAVAILABLE_FETCH)
        if not results:
            if not isinstance(payload.get("statuses"), list):
                return dict(UNAVAILABLE_FETCH)
            return {"status": "nothing_found"}
        item = results[0]
        if not isinstance(item, dict):
            return {"status": "nothing_found"}
        text = str(item.get("text") or "")
        if not text:
            return {"status": "nothing_found"}
        clamped, truncated = clamp_chars(text, self._extract_max_chars)
        return {"status": "ok", "url": url, "text": wrap(url, clamped), "truncated": truncated}

    async def _post(self, url: str, body: dict[str, Any], what: str) -> Any | None:
        headers = {"x-api-key": self._api_key, "Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(url, json=body, headers=headers) as response,
            ):
                text = await response.text()
                if response.status != 200:
                    log.warning("%s answered %s", what, response.status)
                    return None
        except Exception as exc:  # noqa: BLE001 — never raises into the loop
            log.warning("%s unreachable: %s", what, exc)
            return None
        try:
            return json.loads(text)
        except ValueError:
            log.warning("%s did not return JSON", what)
            return None

    def _request_chars(self, max_chars: int) -> int:
        return max(1, min(max_chars, _EXA_MAX_CHARACTERS))
