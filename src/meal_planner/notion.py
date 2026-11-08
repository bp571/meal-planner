"""Read Notion pages or whole databases as plain text through the official API."""

import json
import os
import re
import time
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

API_URL = "https://api.notion.com/v1"
API_VERSION = "2026-03-11"  # databases hold data sources since 2025-09-03
TIMEOUT_SECONDS = 15
RETRIES = 2  # after HTTP 429; Notion allows about three requests per second

# Links end in a 32-character id. "p=" holds a page opened from a database view.
_PEEK_ID = re.compile(r"[?&]p=([0-9a-f]{32})")
_ID = re.compile(r"([0-9a-f]{32})(?:[/?#&]|$)")
_HINTS = {
    401: "NOTION_TOKEN is not valid.",
    403: "The integration may not read content. Enable 'Read content' in its capabilities.",
    404: (
        "The page or database does not exist or is not shared with the integration. "
        "Open it in Notion, choose '...' > 'Connections' and add the integration."
    ),
    429: "Notion's rate limit was reached. Try again in a minute.",
}
_PREFIXES = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
}
_SEPARATE_PAGES = {"child_page", "child_database"}  # only their titles, not their content


class NotionError(Exception):
    """A Notion link could not be read. The message says what to fix."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def read_notion(url: str) -> list[dict[str, str]]:
    """Read a page (one entry) or a database (one entry per page) as {title, url, text}."""
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise NotionError(
            "NOTION_TOKEN is not set. Create an integration at "
            "https://www.notion.so/profile/integrations and put its token into .env."
        )
    object_id = _object_id(url)
    try:
        database = _call("GET", f"/databases/{object_id}", token)
    except NotionError as error:
        if error.status not in (400, 404):  # 400/404 here: probably a page, not a database
            raise
        return [_read_page(_call("GET", f"/pages/{object_id}", token), token)]
    return [
        _read_page(page, token, with_properties=True)
        for source in database["data_sources"]
        for page in _query(source["id"], token)
    ]


def _object_id(url: str) -> str:
    compact = url.strip().lower().replace("-", "")
    match = _PEEK_ID.search(compact) or _ID.search(compact)
    if match is None:
        raise NotionError("This is not a valid Notion link: it contains no page or database id.")
    return str(UUID(match.group(1)))


def _call(
    method: str, path: str, token: str, body: dict[str, Any] | None = None, retries: int = RETRIES
) -> dict[str, Any]:
    request = Request(
        f"{API_URL}{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": API_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except HTTPError as error:
        if error.code == 429 and retries:
            time.sleep(float(error.headers.get("Retry-After") or 1))
            return _call(method, path, token, body, retries - 1)
        hint = _HINTS.get(error.code, "The request failed.")
        raise NotionError(f"Notion returned HTTP {error.code}. {hint}", error.code) from error
    except OSError as error:
        raise NotionError(f"Notion is not reachable: {error}") from error


def _query(data_source_id: str, token: str) -> Iterator[dict[str, Any]]:
    body: dict[str, Any] = {"page_size": 100}
    while True:
        result = _call("POST", f"/data_sources/{data_source_id}/query", token, body)
        yield from result["results"]
        if not result["has_more"]:
            return
        body["start_cursor"] = result["next_cursor"]


def _children(block_id: str, token: str) -> Iterator[dict[str, Any]]:
    params: dict[str, Any] = {"page_size": 100}
    while True:
        result = _call("GET", f"/blocks/{block_id}/children?{urlencode(params)}", token)
        yield from result["results"]
        if not result["has_more"]:
            return
        params["start_cursor"] = result["next_cursor"]


def _read_page(page: dict[str, Any], token: str, with_properties: bool = False) -> dict[str, str]:
    """Database pages keep data in properties too (e.g. ingredients), so those come first."""
    properties = (
        [f"{name}: {value}" for name, prop in page["properties"].items() if (value := _value(prop))]
        if with_properties
        else []
    )
    lines = [*properties, *_lines(page["id"], token)]
    return {"title": _title(page), "url": page["url"], "text": "\n".join(lines)}


def _value(prop: dict[str, Any]) -> str:
    """A property value as text; other types than these are left out."""
    kind, value = prop["type"], prop.get(prop["type"])
    if kind in ("title", "rich_text"):
        return _plain(value)
    if kind == "number":
        return "" if value is None else f"{value:g}"
    if kind == "select":
        return value["name"] if value else ""
    if kind == "multi_select":
        return ", ".join(option["name"] for option in value)
    if kind == "url":
        return value or ""
    if kind == "checkbox":
        return "yes" if value else "no"
    if kind == "date":
        return " – ".join(filter(None, (value["start"], value["end"]))) if value else ""
    return ""


def _title(page: dict[str, Any]) -> str:
    for page_property in page["properties"].values():
        if page_property["type"] == "title":
            return _plain(page_property["title"])
    return ""


def _lines(block_id: str, token: str, depth: int = 0) -> Iterator[str]:
    """Plain text of all blocks below block_id, children indented."""
    for block in _children(block_id, token):
        kind = block["type"]
        data = block.get(kind, {})
        if kind == "table_row":
            text = " | ".join(_plain(cell) for cell in data["cells"])
        elif kind in _SEPARATE_PAGES:
            text = data.get("title", "")
        else:
            text = _plain(data.get("rich_text", []))
        prefix = ("[x] " if data.get("checked") else "[ ] ") if kind == "to_do" else _PREFIXES.get(kind, "")
        if text:
            yield "  " * depth + prefix + text
        if block["has_children"] and kind not in _SEPARATE_PAGES:
            yield from _lines(block["id"], token, depth + 1)


def _plain(rich_text: list[dict[str, Any]]) -> str:
    return "".join(item["plain_text"] for item in rich_text)
