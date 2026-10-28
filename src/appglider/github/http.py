"""Restricted GitHub HTTP access: requests, rate limits, ETag cache and web resources."""

import contextlib
import json
import re
import time
from collections.abc import Callable
from http.client import HTTPException, HTTPMessage
from pathlib import Path
from threading import Event, Lock
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from appglider import APP_VERSION
from appglider.core.catalog import App
from appglider.core.storage import atomic_json
from appglider.github import status as github_status
from appglider.github.versions import check_cancel, release_version

RETRY_DELAYS = (0.5, 1.5)


RETRY_STATUSES = (500, 502, 503, 504)


_RETRY_AFTER: dict[str, float] = {}


ETAG_ENTRIES = 200


ETAG_BODY_LIMIT = 512 * 1024


_ETAG_LOCK = Lock()


_ETAG_FILE: Path | None = None


_ETAG_CACHE: tuple[Path, tuple[int, int, int] | None, dict[str, Any], dict[str, int]] | None = None


MAX_METADATA = 4 * 1024 * 1024


class MissingResource(ValueError):
    """A GitHub resource returned HTTP 404."""


def valid_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc
        in {
            "api.github.com",
            "github.com",
            "raw.githubusercontent.com",
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
            "github-releases.githubusercontent.com",
        }
        and not parsed.username
        and not parsed.password
    )


class GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> Request | None:
        if not valid_url(newurl):
            raise ValueError("GitHub redirected to an untrusted download location.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirect(HTTPRedirectHandler):
    """Report a redirect instead of following it, so its destination can be read."""

    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> Request | None:
        return None


class RateLimited(ValueError):
    def __init__(self, host: str, retry_at: float) -> None:
        self.host = host
        self.retry_at = retry_at
        wait = max(1, int(retry_at - time.time()))
        super().__init__(
            f"GitHub's request limit was reached. Retry in about {wait // 60 + 1} minutes."
        )


UNREACHABLE = "Could not reach GitHub. Check your connection and try again."


def timed_out(error: Exception) -> bool:
    reason = error.reason if isinstance(error, URLError) else error
    return isinstance(reason, TimeoutError)


def request(url: str, *, method: str = "GET", etag: str = "") -> Any:
    if not valid_url(url):
        raise ValueError("Refusing an untrusted download location.")
    host = urlsplit(url).netloc
    if _RETRY_AFTER.get(host, 0) > time.time():
        raise RateLimited(host, _RETRY_AFTER[host])
    headers = {"User-Agent": f"AppGlider/{APP_VERSION}"}
    if etag:
        headers["If-None-Match"] = etag
    req = Request(url, headers=headers, method=method)
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return _open(req, host)
        except HTTPError as error:
            if error.code in RETRY_STATUSES:
                error.close()
                if attempt < len(RETRY_DELAYS):
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                raise ValueError(
                    f"GitHub is temporarily unavailable (HTTP {error.code}). Try again shortly."
                ) from error
            raise
        except (URLError, OSError, HTTPException) as error:
            if timed_out(error) and attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise ValueError(UNREACHABLE) from error
    raise AssertionError("unreachable")  # pragma: no cover


_OPENER: tuple[Callable[..., OpenerDirector], OpenerDirector] | None = None


def _opener() -> OpenerDirector:
    """Share one stateless opener between threads; rebuild if the factory is replaced."""
    global _OPENER
    if _OPENER is None or _OPENER[0] is not build_opener:
        _OPENER = (build_opener, build_opener(GitHubRedirect()))
    return _OPENER[1]


def _open(req: Request, host: str) -> Any:
    try:
        response = _opener().open(req, timeout=20)
        if host == "api.github.com":
            github_status.observe_headers(dict(response.headers.items()))
        return response
    except HTTPError as error:
        if host == "api.github.com":
            github_status.observe_headers(dict(error.headers.items()))
        if error.code in (403, 429):
            limited = (
                error.code == 429
                or error.headers.get("X-RateLimit-Remaining") == "0"
                or error.headers.get("Retry-After") is not None
            )
            if not limited:
                limited = b"rate limit" in error.read(4096).lower()
            if limited:
                now = time.time()
                retry_at = now + 60
                with contextlib.suppress(ValueError, TypeError):
                    retry_at = max(retry_at, float(error.headers.get("X-RateLimit-Reset", 0)))
                with contextlib.suppress(ValueError, TypeError):
                    retry_at = max(retry_at, now + float(error.headers.get("Retry-After", 0)))
                retry_at = min(retry_at, now + 24 * 60 * 60)
                _RETRY_AFTER[host] = retry_at
                error.close()
                raise RateLimited(host, retry_at) from error
            error.close()
            raise ValueError("GitHub denied access to this resource (HTTP 403).") from error
        if error.code == 404:
            error.close()
            raise MissingResource("No published release was found in this repository.") from error
        raise


def redirect_location(url: str) -> str | None:
    """Where a github.com page redirects to, or None; never counts against the API allowance."""
    if not valid_url(url) or urlsplit(url).netloc != "github.com":
        return None
    req = Request(url, headers={"User-Agent": f"AppGlider/{APP_VERSION}"}, method="HEAD")
    try:
        build_opener(NoRedirect()).open(req, timeout=10).close()
    except HTTPError as error:
        location = error.headers.get("Location", "") if error.code in (301, 302, 303) else ""
        error.close()
        return location or None
    except (URLError, OSError, HTTPException, ValueError):
        return None
    return None


MAX_LISTING = 3 * 1024 * 1024


MAX_FEED = 8 * 1024 * 1024


FEED_LINK = re.compile(r'<link[^>]*rel="alternate"[^>]*href="([^"]+)"')


def _fetch(url: str, headers: dict[str, str], limit: int) -> tuple[bytes, str]:
    """Read a small GitHub web resource without the API.

    Redirects (a renamed or moved repository) are followed only to GitHub's own trusted hosts.
    Returns the body and the address it finally came from.
    """
    if not valid_url(url):
        raise ValueError("Refusing an untrusted download location.")
    req = Request(url, headers={"User-Agent": f"AppGlider/{APP_VERSION}", **headers})
    try:
        with build_opener(GitHubRedirect()).open(req, timeout=20) as reply:
            body: bytes = reply.read(limit + 1)
            final: str = reply.geturl()
    except HTTPError as error:
        code = error.code
        error.close()
        raise ValueError(f"GitHub answered HTTP {code}.") from error
    except (URLError, OSError, HTTPException) as error:
        raise ValueError(UNREACHABLE) from error
    if len(body) > limit:
        raise ValueError("The file is larger than the allowed size.")
    return body, final


def raw_bytes(repo: str, path: str, limit: int, cancel: Event) -> bytes:
    """A file of the default branch from raw.githubusercontent.com (no API request)."""
    check_cancel(cancel)
    return _fetch(f"https://raw.githubusercontent.com/{repo}/HEAD/{quote(path)}", {}, limit)[0]


def web_listing(repo: str, path: str, cancel: Event) -> tuple[list[dict[str, str]], str]:
    """One folder of the default branch as GitHub's file browser reports it (no API request).

    Each item has `name`, `path` and `contentType` ("file" or "directory"). Also returns the
    repository's current `owner/name`, which differs from `repo` when it was renamed or moved,
    so later requests can go straight to it. The page is an undocumented interface of
    github.com, so any surprise is reported as a ValueError.
    """
    check_cancel(cancel)
    url = f"https://github.com/{repo}/tree/HEAD/{quote(path)}".rstrip("/")
    body, final = _fetch(url, {"Accept": "application/json"}, MAX_LISTING)
    parts = urlsplit(final).path.split("/")
    current = "/".join(parts[1:3]) if len(parts) > 3 and parts[3] == "tree" else repo
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", current):
        current = repo
    try:
        items = json.loads(body)["payload"]["codeViewTreeRoute"]["tree"]["items"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("GitHub's file listing was not in the expected form.") from error
    result: list[dict[str, str]] = []
    for item in items if isinstance(items, list) else []:
        if (
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("path"), str)
            and item.get("contentType") in ("file", "directory")
            and len(item["path"]) <= 512
            and not any(ord(c) < 32 or ord(c) == 127 for c in item["path"])
            and not any(part in {"", ".", ".."} for part in item["path"].split("/"))
        ):
            result.append(
                {"name": item["name"], "path": item["path"], "contentType": item["contentType"]}
            )
    return result, current


def feed_request(repo: str, etag: str) -> tuple[int, bytes, str] | None:
    """Fetch a repository's public release feed, conditionally; None on any failure."""
    url = f"https://github.com/{repo}/releases.atom"
    if not valid_url(url):
        return None
    headers = {"User-Agent": f"AppGlider/{APP_VERSION}"}
    if etag:
        headers["If-None-Match"] = etag
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=20) as reply:
            body = reply.read(MAX_FEED + 1)
            if len(body) > MAX_FEED:
                return None
            return 200, body, reply.headers.get("ETag", "")
    except HTTPError as error:
        code = error.code
        error.close()
        return (304, b"", etag) if code == 304 and etag else None
    except (URLError, OSError, HTTPException, ValueError):
        return None


def feed_tags(app: App, cache_dir: Path, cancel: Event) -> list[str] | None:
    """Tags of the repository's recent releases, read from its public feed without the API.

    The feed's ETag and tags are remembered per app, so an unchanged feed is a cheap 304 that
    does not download the release notes again. None means "unknown": callers use the API.
    """
    check_cancel(cancel)
    path = cache_dir / f"{app.id}-feed.json"
    saved_etag, saved_tags = "", None
    with contextlib.suppress(OSError, ValueError, TypeError):
        if path.stat().st_size <= MAX_METADATA:
            data = json.loads(path.read_text())
            if (
                isinstance(data, dict)
                and isinstance(data.get("etag"), str)
                and isinstance(data.get("tags"), list)
                and all(isinstance(tag, str) for tag in data["tags"])
            ):
                saved_etag, saved_tags = data["etag"], data["tags"]
    result = feed_request(app.repo, saved_etag if saved_tags is not None else "")
    if result is None:
        return None
    status, body, etag = result
    if status == 304:
        return saved_tags
    prefix = f"https://github.com/{app.repo}/releases/tag/".casefold()
    tags: list[str] = []
    for link in FEED_LINK.findall(body.decode("utf-8", "replace")):
        if link.casefold().startswith(prefix):
            tag = unquote(link[len(prefix) :])
            if release_version(tag) is not None and tag not in tags:
                tags.append(tag)
        if len(tags) >= 50:
            break
    with contextlib.suppress(OSError):
        atomic_json(path, {"etag": etag, "tags": tags})
    return tags


def latest_tag(repo: str, cancel: Event) -> str | None:
    """The tag GitHub's "Latest" release page points to, read without using the API.

    Returns None whenever the answer is not clearly a release tag of this repository, so
    callers fall back to the API.
    """
    check_cancel(cancel)
    location = redirect_location(f"https://github.com/{repo}/releases/latest")
    if not location:
        return None
    parsed = urlsplit(location)
    if parsed.scheme not in ("", "https") or parsed.netloc not in ("", "github.com"):
        return None
    prefix = f"/{repo}/releases/tag/".casefold()
    if not parsed.path.casefold().startswith(prefix):
        return None
    tag = unquote(parsed.path[len(prefix) :])
    return tag if release_version(tag) is not None else None


def _entry_size(url: str, value: Any) -> int:
    return len(json.dumps(url)) + len(json.dumps(value)) + 4


def _etag_entries() -> tuple[dict[str, Any], dict[str, int]]:
    """The saved API bodies and their serialised sizes; call with `_ETAG_LOCK` held.

    The parsed file is kept and read again only when the file itself has changed.
    """
    global _ETAG_CACHE
    if _ETAG_FILE is None:
        return {}, {}
    stamp = file_stamp(_ETAG_FILE)
    cached = _ETAG_CACHE
    if cached is not None and cached[0] == _ETAG_FILE and cached[1] == stamp:
        return cached[2], cached[3]
    _ETAG_CACHE = None
    try:
        if _ETAG_FILE.stat().st_size > MAX_METADATA:
            return {}, {}
        data = json.loads(_ETAG_FILE.read_text())
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    sizes = {url: _entry_size(url, value) for url, value in data.items()}
    _ETAG_CACHE = (_ETAG_FILE, stamp, data, sizes)
    return data, sizes


def _remember_etag(url: str, etag: str | None, body: bytes) -> None:
    """Keep API bodies by ETag so a later 304 (free of rate limit) can reuse them."""
    global _ETAG_CACHE
    if _ETAG_FILE is None:
        return
    with _ETAG_LOCK:
        entries, sizes = _etag_entries()
        entries.pop(url, None)
        sizes.pop(url, None)
        if etag and len(etag) <= 256 and len(body) <= ETAG_BODY_LIMIT:
            with contextlib.suppress(UnicodeDecodeError):
                entries[url] = {"etag": etag, "body": body.decode()}
                sizes[url] = _entry_size(url, entries[url])
        total = sum(sizes.values())
        while len(entries) > ETAG_ENTRIES or total > MAX_METADATA:
            oldest = next(iter(entries))
            del entries[oldest]
            total -= sizes.pop(oldest)
        try:
            atomic_json(_ETAG_FILE, entries)
        except OSError:
            _ETAG_CACHE = None
        else:
            _ETAG_CACHE = (_ETAG_FILE, file_stamp(_ETAG_FILE), entries, sizes)


def read_metadata(url: str) -> bytes:
    stored: Any = None
    if urlsplit(url).netloc == "api.github.com":
        with _ETAG_LOCK:
            stored = _etag_entries()[0].get(url)
        if not (
            isinstance(stored, dict)
            and isinstance(stored.get("etag"), str)
            and isinstance(stored.get("body"), str)
        ):
            stored = None
    try:
        with request(url, etag=stored["etag"] if stored else "") as response:
            data: bytes = response.read(MAX_METADATA + 1)
            etag = response.headers.get("ETag") if hasattr(response, "headers") else None
    except HTTPError as error:
        if error.code == 304 and stored:
            error.close()
            return str(stored["body"]).encode()
        code = error.code
        error.close()
        raise ValueError(f"GitHub answered HTTP {code}.") from error
    except (OSError, HTTPException) as error:
        raise ValueError(UNREACHABLE) from error
    if len(data) > MAX_METADATA:
        raise ValueError("The release metadata is too large.")
    if urlsplit(url).netloc == "api.github.com":
        _remember_etag(url, etag, data)
    return data


def file_stamp(path: Path) -> tuple[int, int, int] | None:
    """Identify a file's current contents cheaply; None when it cannot be read."""
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size, info.st_ino


def restore_rate_limits(cache_dir: Path) -> None:
    # Also marks where API ETags are kept, since every discovery path calls this first.
    global _ETAG_FILE
    _ETAG_FILE = cache_dir / "etags.json"
    try:
        limits_path = cache_dir / "limits.json"
        if limits_path.stat().st_size <= MAX_METADATA:
            limits = json.loads(limits_path.read_text())
            if isinstance(limits, dict):
                for host, until in limits.items():
                    if (
                        valid_url(f"https://{host}/")
                        and type(until) in (int, float)
                        and 0 < until - time.time() <= 24 * 60 * 60
                    ):
                        _RETRY_AFTER[host] = max(_RETRY_AFTER.get(host, 0), until)
    except (OSError, ValueError, TypeError):
        pass


def save_rate_limits(cache_dir: Path) -> None:
    with contextlib.suppress(OSError):
        atomic_json(cache_dir / "limits.json", dict(_RETRY_AFTER))


def github_request_status() -> tuple[str, str, bool]:
    return github_status.describe(
        api_retry=_RETRY_AFTER.get("api.github.com", 0),
        public_retry=_RETRY_AFTER.get("github.com", 0),
    )
