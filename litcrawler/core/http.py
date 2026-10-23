# -*- coding: utf-8 -*-
"""HTTP 客户端：仅依赖 Python 标准库，带重试、超时、UA 伪装、编码兜底。"""

from __future__ import annotations

import gzip
import io
import json
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .. import __app_name__, __version__

DEFAULT_UA = (
    f"{__app_name__}/{__version__} (academic literature tool; +https://example.invalid) "
    "Python-urllib"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_SSL_CTX = ssl.create_default_context()
try:  # 兼容部分缺少根证书的 Python 发行版
    import certifi  # type: ignore

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover
    pass


class HttpError(Exception):
    """HTTP 请求失败。"""

    def __init__(self, message: str, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class Response:
    """轻量响应对象。"""

    __slots__ = ("status", "headers", "content", "url")

    def __init__(self, status: int, headers: dict, content: bytes, url: str):
        self.status = status
        self.headers = headers
        self.content = content
        self.url = url

    @property
    def content_type(self) -> str:
        return (self.headers.get("Content-Type") or "").lower()

    def text(self, encoding: str | None = None) -> str:
        enc = encoding or self._guess_encoding()
        try:
            return self.content.decode(enc, "replace")
        except LookupError:
            return self.content.decode("utf-8", "replace")

    def _guess_encoding(self) -> str:
        ctype = self.content_type
        if "charset=" in ctype:
            return ctype.split("charset=", 1)[1].split(";")[0].strip().strip('"') or "utf-8"
        head = self.content[:4096].lower()
        if b"charset=gb" in head or b"charset=\"gb" in head:
            return "gb18030"
        return "utf-8"

    def json(self):
        return json.loads(self.text())


def _decompress(raw: bytes, headers: dict) -> bytes:
    enc = (headers.get("Content-Encoding") or "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(raw)
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        return raw
    return raw


def request(
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    data: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 1.6,
    browser_ua: bool = False,
    max_bytes: int | None = None,
) -> Response:
    """发起 HTTP 请求，自动重试 429/5xx 与网络错误。

    ``max_bytes`` 用于只读取前 N 字节（预览用途）。
    """
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}{'&' if '?' in url else '?'}{query}"

    body = None
    hdrs = {"User-Agent": BROWSER_UA if browser_ua else DEFAULT_UA, "Accept-Encoding": "gzip, deflate"}
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    if headers:
        hdrs.update(headers)

    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                if max_bytes is not None:
                    raw = resp.read(max_bytes)
                else:
                    raw = resp.read()
                hdrs_out = {k: v for k, v in resp.headers.items()}
                return Response(resp.status, hdrs_out, _decompress(raw, hdrs_out), resp.url)
        except urllib.error.HTTPError as exc:  # 4xx/5xx
            status = exc.code
            detail = ""
            try:
                detail = exc.read(400).decode("utf-8", "replace")
            except Exception:
                pass
            last_err = HttpError(f"HTTP {status} {url} {detail[:200]}", status, url)
            retryable = status in (408, 425, 429, 500, 502, 503, 504, 509)
            if status == 403 and attempt < retries - 1:
                retryable = True  # 部分站点对默认 UA 敏感
            if not retryable or attempt == retries - 1:
                raise last_err from None
        except Exception as exc:  # 网络/超时/TLS
            last_err = HttpError(f"{type(exc).__name__}: {exc}", None, url)
            if attempt == retries - 1:
                raise last_err from None
        # 指数退避 + 抖动
        sleep_for = backoff ** attempt + random.uniform(0, 0.4)
        time.sleep(min(sleep_for, 12.0))
    raise last_err or HttpError("unknown error", None, url)


def get_json(url: str, params: dict | None = None, **kw) -> dict:
    kw.setdefault("headers", {})
    kw["headers"].setdefault("Accept", "application/json")
    return request(url, params=params, **kw).json()


def get_text(url: str, params: dict | None = None, **kw) -> str:
    return request(url, params=params, **kw).text()
