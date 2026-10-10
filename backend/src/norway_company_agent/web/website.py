from __future__ import annotations

import http.client
import copy
import json
import ipaddress
import os
import re
import socket
import signal
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from contextlib import contextmanager
from typing import Any

from bs4 import BeautifulSoup
import extruct
import tldextract
import trafilatura

from ..core.evidence import evidence

USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"
SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
}
PRIORITY_TERMS = (
    "om-oss", "om_oss", "about", "kontakt", "contact", "ledelse", "management",
    "team", "people", "locations", "lokasjoner", "avdelinger", "butikker",
    "news", "press", "aktuelt", "nyheter", "blog", "presse", "investor",
    "karriere", "career", "jobb", "jobs", "stillinger", "ledige-stillinger",
)
SITEMAP_PRIORITY_TERMS = (
    (0, "contact"), (0, "kontakt"),
    (1, "om-oss"), (1, "om_oss"), (1, "about"),
    (2, "legal"), (2, "privacy"), (2, "personvern"), (2, "terms"), (2, "vilkar"), (2, "impressum"),
    (3, "team"), (3, "people"), (3, "services"), (3, "tjenester"),
)
# Candidate normalization runs in batch and should be deterministic/offline. The
# bundled Public Suffix List snapshot is sufficient for this use and avoids a
# hidden network request or a user-home cache write on every process start.
TLD_EXTRACTOR = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())

try:
    MAX_CONCURRENT_DNS_LOOKUPS = max(1, int(os.environ.get("SIGNALPOST_MAX_CONCURRENT_DNS_LOOKUPS", "32")))
except ValueError:
    MAX_CONCURRENT_DNS_LOOKUPS = 32
GLOBAL_DNS_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_DNS_LOOKUPS)


@contextmanager
def _dns_slot(timeout: float = 8.0):
    acquired = GLOBAL_DNS_SEMAPHORE.acquire(timeout=max(0.1, float(timeout)))
    if not acquired:
        raise TimeoutError("global DNS concurrency limit")
    try:
        yield
    finally:
        GLOBAL_DNS_SEMAPHORE.release()


def network_failure_class(exc: BaseException) -> str | None:
    """Classify DNS/network failures without weakening the public-URL gate."""
    explicit = getattr(exc, "failure_class", None)
    if explicit in {"permanent", "transient"}:
        return explicit
    reason = getattr(exc, "reason", None)
    if reason is not None and reason is not exc:
        classified = network_failure_class(reason)
        if classified:
            return classified
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        classified = network_failure_class(cause)
        if classified:
            return classified
    errno = getattr(exc, "errno", None)
    permanent_codes = {code for code in (getattr(socket, "EAI_NONAME", None), getattr(socket, "EAI_NODATA", None)) if code is not None}
    transient_codes = {code for code in (getattr(socket, "EAI_AGAIN", None), getattr(socket, "EAI_FAIL", None)) if code is not None}
    if errno in permanent_codes:
        return "permanent"
    if errno in transient_codes:
        return "transient"
    text = str(exc).casefold()
    if any(term in text for term in ("nxdomain", "name or service not known", "nodename nor servname", "no address associated with hostname")):
        return "permanent"
    if any(term in text for term in ("eai_again", "temporary failure", "servfail", "timed out", "timeout", "connection reset", "connection aborted", "try again")):
        return "transient"
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return "transient"
    return None


def resolve_host(host: str, *, port: int = 443, timeout: float = 8.0) -> dict[str, Any]:
    """Resolve a host under the process-wide DNS cap and retain failure class."""
    try:
        with _dns_slot(timeout):
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
        if not addresses:
            return {"resolved": False, "addresses": [], "failure_kind": "resolution", "failure_class": "permanent"}
        return {"resolved": True, "addresses": addresses, "failure_kind": None, "failure_class": None}
    except OSError as exc:
        return {"resolved": False, "addresses": [], "failure_kind": "resolution", "failure_class": network_failure_class(exc) or "permanent", "error": str(exc)}


def assert_public_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise PublicURLPolicyError("Only public HTTP(S) URLs are allowed")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise PublicURLPolicyError("Local hosts are blocked")
    resolution = resolve_host(host, port=parsed.port or (443 if parsed.scheme == "https" else 80))
    if not resolution.get("resolved"):
        raise PublicURLResolutionError("Hostname did not resolve", failure_class=resolution.get("failure_class") or "permanent")
    addresses = set(resolution.get("addresses") or [])
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise PublicURLPolicyError("Private, loopback, link-local, multicast, and reserved addresses are blocked")


def _network_failure_kind(exc: BaseException) -> str | None:
    if isinstance(exc, PublicURLResolutionError):
        return "resolution"
    if isinstance(exc, ssl.SSLError):
        return "tls"
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return "connect"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror) or "resolve" in str(reason).casefold() or "name or service" in str(reason).casefold():
            return "resolution"
        return "connect"
    return None


def assert_public_peer(sock: Any) -> None:
    """Reject the connection if the socket landed on a non-public address.

    assert_public_url resolves the name once; the connection resolves it again, so a
    DNS-rebinding host can answer public first and private second. Checking the
    connected peer closes that gap.
    """
    try:
        peer = ipaddress.ip_address(sock.getpeername()[0])
    except (OSError, ValueError, IndexError):
        sock.close()
        raise ValueError("Could not verify the connected address")
    if not peer.is_global:
        sock.close()
        raise ValueError("Connected address is not public")


class _PublicPeerHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        super().connect()
        assert_public_peer(self.sock)


class _PublicPeerHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        super().connect()
        assert_public_peer(self.sock)


class _PublicPeerHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: Any) -> Any:
        return self.do_open(_PublicPeerHTTPConnection, req)


class _PublicPeerHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: Any) -> Any:
        return self.do_open(_PublicPeerHTTPSConnection, req, context=self._context)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        assert_public_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            chain = list(getattr(_redirect_context, "chain", [req.full_url]))
            chain.append(newurl)
            _redirect_context.chain = chain
        return redirected


SAFE_OPENER = urllib.request.build_opener(SafeRedirectHandler(), _PublicPeerHTTPHandler(), _PublicPeerHTTPSHandler())
try:
    MAX_CONCURRENT_FETCHES = max(1, int(os.environ.get("SIGNALPOST_MAX_CONCURRENT_FETCHES", "48")))
except ValueError:
    MAX_CONCURRENT_FETCHES = 48
GLOBAL_FETCH_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_FETCHES)
_redirect_context = threading.local()


class PublicURLResolutionError(ValueError):
    """The hostname could not be resolved; its class determines retryability."""

    def __init__(self, message: str, *, failure_class: str = "transient") -> None:
        super().__init__(message)
        self.failure_class = failure_class


class PublicURLPolicyError(ValueError):
    """The URL is invalid or resolves to a private/non-public address."""


class CircuitOpenError(ConnectionError):
    """A host has had repeated transient failures during this run."""


class NetworkPreflightError(RuntimeError):
    """The runner cannot establish the minimum network prerequisites."""


class ResolutionFailureBreaker:
    """Stop a run when resolution failures indicate a broken network, not bad data."""

    def __init__(self, *, threshold: float = 0.20, minimum_samples: int = 5) -> None:
        if not 0 < threshold <= 1 or minimum_samples < 1:
            raise ValueError("invalid resolution breaker configuration")
        self.threshold = threshold
        self.minimum_samples = minimum_samples
        self.total_fetches = 0
        self.resolution_failures = 0
        self.failure_counts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def observe(self, operations: dict[str, Any]) -> None:
        with self._lock:
            self.total_fetches += 1
            failure_kind = operations.get("failure_kind")
            if failure_kind:
                self.failure_counts[str(failure_kind)] += 1
            if failure_kind == "resolution":
                self.resolution_failures += 1
            if (
                self.total_fetches >= self.minimum_samples
                and self.resolution_failures / self.total_fetches > self.threshold
            ):
                raise NetworkPreflightError(
                    f"resolution failures exceeded {self.threshold:.0%}: "
                    f"{self.resolution_failures}/{self.total_fetches} website fetches"
                )


def network_preflight(hosts: tuple[str, ...] = ("data.brreg.no", "example.com")) -> dict[str, Any]:
    """Resolve known public hosts before a network-backed run starts."""
    resolved: dict[str, list[str]] = {}
    failures: dict[str, str] = {}
    for host in hosts:
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            }
            public = sorted(address for address in addresses if ipaddress.ip_address(address).is_global)
            if not public:
                raise OSError("no public address returned")
            resolved[host] = public
        except (OSError, ValueError) as exc:
            failures[host] = str(exc)
    if failures:
        detail = "; ".join(f"{host}: {reason}" for host, reason in failures.items())
        raise NetworkPreflightError(f"network preflight failed ({detail})")
    return {"hosts": resolved, "checked_at": time.time()}


@contextmanager
def _wall_timeout(seconds: float):
    """Bound a blocking network operation in the sequential runner.

    The competition runner calls website retrieval on the main thread. Some
    Python/OpenSSL combinations can ignore the socket timeout while waiting for
    a response header/body; an alarm is the final bound for that case. Worker
    threads (the registry batch) continue using ordinary socket timeouts.
    """
    if seconds <= 0 or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def raise_timeout(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"network operation exceeded {seconds}s")

    signal.signal(signal.SIGALRM, raise_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def _response_socket(response: Any) -> Any | None:
    """Return urllib's underlying socket when the response exposes one."""
    file_object = getattr(response, "fp", None)
    raw = getattr(file_object, "raw", None)
    return getattr(raw, "_sock", None) or getattr(file_object, "_sock", None)


class HostRequestPolicy:
    """Coordinate concurrent retrievals without overloading one host."""

    def __init__(self, *, min_interval: float = 1.0, max_inflight: int = 2, breaker_threshold: int = 3, cache_max_entries: int = 1000, cache_max_bytes: int = 50_000_000) -> None:
        if min_interval < 0 or max_inflight < 1:
            raise ValueError("invalid host request policy")
        self.min_interval = min_interval
        self.max_inflight = max_inflight
        self._condition = threading.Condition()
        self._active: dict[str, int] = defaultdict(int)
        self._last_started: dict[str, float] = {}
        self._robots: dict[str, tuple[bool, list[str]]] = {}
        self._robots_inflight: set[str] = set()
        self._breaker_threshold = max(1, int(breaker_threshold))
        self._failures: dict[str, int] = defaultdict(int)
        self._homepage_cache: dict[str, tuple[dict[str, Any], int]] = {}
        self._cache_max_entries = max(1, int(cache_max_entries))
        self._cache_max_bytes = max(1, int(cache_max_bytes))
        self._cache_bytes = 0

    @staticmethod
    def host_key(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        return (parsed.hostname or parsed.netloc).casefold().rstrip(".")

    @contextmanager
    def request(self, url: str):
        host = self.host_key(url)
        with self._condition:
            if self._failures.get(host, 0) >= self._breaker_threshold:
                raise CircuitOpenError(f"circuit open for {host}")
            while True:
                now = time.monotonic()
                wait_for_interval = self.min_interval - (now - self._last_started.get(host, 0.0))
                if self._active[host] < self.max_inflight and wait_for_interval <= 0:
                    self._active[host] += 1
                    self._last_started[host] = now
                    break
                self._condition.wait(max(wait_for_interval, 0.01))
        try:
            yield
        except Exception as exc:
            with self._condition:
                if _network_failure_kind(exc) in {"connect", "timeout", "resolution"} or isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
                    self._failures[host] += 1
                else:
                    self._failures[host] = 0
            raise
        else:
            with self._condition:
                self._failures[host] = 0
        finally:
            with self._condition:
                self._active[host] -= 1
                self._condition.notify_all()

    def cached_homepage(self, url: str) -> dict[str, Any] | None:
        domain = _registered_domain(url)
        with self._condition:
            item = self._homepage_cache.get(domain)
            return copy.deepcopy(item[0]) if item else None

    def cache_homepage(self, url: str, record: dict[str, Any]) -> None:
        domain = _registered_domain(url)
        size = len(json.dumps(record, ensure_ascii=False, default=str).encode("utf-8"))
        if size > self._cache_max_bytes:
            return
        with self._condition:
            if domain in self._homepage_cache:
                self._cache_bytes -= self._homepage_cache[domain][1]
            while self._homepage_cache and (len(self._homepage_cache) >= self._cache_max_entries or self._cache_bytes + size > self._cache_max_bytes):
                oldest = next(iter(self._homepage_cache))
                _old, old_size = self._homepage_cache.pop(oldest)
                self._cache_bytes -= old_size
            self._homepage_cache[domain] = (copy.deepcopy(record), size)
            self._cache_bytes += size

    def robots(self, url: str, timeout: float) -> tuple[bool, list[str]]:
        host = self.host_key(url)
        with self._condition:
            while True:
                cached = self._robots.get(host)
                if cached is not None:
                    return cached[0], list(cached[1])
                if host not in self._robots_inflight:
                    self._robots_inflight.add(host)
                    break
                self._condition.wait(0.05)
        try:
            result = _robots_policy(url, timeout, request_policy=self)
        finally:
            with self._condition:
                if "result" in locals():
                    self._robots[host] = (bool(result[0]), list(result[1]))
                self._robots_inflight.discard(host)
                self._condition.notify_all()
        return result


def _read_response(response: Any, limit: int, timeout: float) -> bytes:
    """Read at most ``limit`` bytes with a per-read socket timeout.

    urllib's ``urlopen(timeout=...)`` primarily constrains connection setup. A
    server that accepts a connection and then drips response bytes can otherwise
    hold a discovery run indefinitely. Chunking and applying the timeout to the
    connected socket keeps sitemap/page retrieval bounded while preserving the
    existing byte cap.
    """
    sock = _response_socket(response)
    if sock is not None:
        sock.settimeout(timeout)
    chunks: list[bytes] = []
    remaining = limit
    with _wall_timeout(timeout):
        while remaining > 0:
            requested = min(64 * 1024, remaining)
            chunk = response.read(requested)
            if not chunk:
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                break
            chunks.append(chunk)
            remaining -= len(chunk)
            if len(chunk) < requested:
                break
    return b"".join(chunks)


@contextmanager
def _open_response(
    request: Any,
    timeout: float,
    request_policy: HostRequestPolicy | None = None,
    *,
    record_redirects: bool = False,
):
    """Open a public URL with both urllib and process-default connect timeouts."""
    if record_redirects:
        _redirect_context.chain = [request.full_url]
    policy_context = request_policy.request(request.full_url) if request_policy else None
    if policy_context:
        policy_context.__enter__()
    fetch_acquired = GLOBAL_FETCH_SEMAPHORE.acquire(timeout=max(0.1, float(timeout)))
    if not fetch_acquired:
        if policy_context:
            policy_context.__exit__(TimeoutError, TimeoutError("global fetch concurrency limit"), None)
        raise TimeoutError("global fetch concurrency limit")
    try:
        if threading.current_thread() is not threading.main_thread():
            yield SAFE_OPENER.open(request, timeout=timeout)
            return
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            with _wall_timeout(timeout):
                yield SAFE_OPENER.open(request, timeout=timeout)
        finally:
            socket.setdefaulttimeout(previous)
    finally:
        GLOBAL_FETCH_SEMAPHORE.release()
        if policy_context:
            policy_context.__exit__(None, None, None)


def normalize_homepage(value: str | None) -> str | None:
    value = str(value or "").strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def site_root(value: str | None) -> str | None:
    """Return the scheme/host root used to judge search-derived candidates."""
    normalized = normalize_homepage(value)
    if not normalized:
        return None
    parsed = urllib.parse.urlparse(normalized)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def _registered_domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    ext = TLD_EXTRACTOR(parsed.hostname or "")
    return ext.top_domain_under_public_suffix


def registered_domain(url: str) -> str:
    """Return the registrable domain used for candidate deduplication."""
    return _registered_domain(url)


def _robots_policy(url: str, timeout: float, *, request_policy: HostRequestPolicy | None = None) -> tuple[bool, list[str]]:
    assert_public_url(url)
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with _open_response(request, timeout, request_policy) as response:
            lines = _read_response(response, 250_000, timeout).decode("utf-8", errors="replace").splitlines()
        parser.parse(lines)
        sitemap_urls = [
            line.split(":", 1)[1].strip()
            for line in lines
            if line.casefold().startswith("sitemap:") and ":" in line
        ]
        return parser.can_fetch(USER_AGENT, url), sitemap_urls[:5]
    except Exception:
        # An unavailable robots file is not permission to ignore explicit site terms; callers retain
        # the URL and can route uncertain domains to review. For this bounded homepage POC, allow one
        # ordinary GET when robots.txt is absent rather than crawl deeper.
        return True, []


def _robots_allowed(url: str, timeout: float, request_policy: HostRequestPolicy | None = None) -> bool:
    if request_policy:
        return request_policy.robots(url, timeout)[0]
    return _robots_policy(url, timeout)[0]


def _social_links(base_url: str, soup: BeautifulSoup) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    candidates = [str(node.get("href") or "") for node in soup.select("a[href]")]
    candidates.extend(str(node.get("data-href") or "") for node in soup.select("[data-href]"))
    candidates.extend(str(node.get("src") or "") for node in soup.select("iframe[src]"))
    for candidate in candidates:
        url = urllib.parse.urljoin(base_url, candidate)
        parsed_candidate = urllib.parse.urlparse(url)
        if (parsed_candidate.hostname or "").casefold().removeprefix("www.") == "facebook.com" and parsed_candidate.path.startswith("/plugins/"):
            embedded = urllib.parse.parse_qs(parsed_candidate.query).get("href", [])
            if embedded:
                url = embedded[0]
        normalized = normalize_social_url(url)
        if not normalized:
            continue
        found[(normalized["platform"], normalized["url"])] = normalized
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def structured_social_links(value: Any) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            same_as = node.get("sameAs")
            urls = same_as if isinstance(same_as, list) else [same_as]
            for raw in urls:
                if not isinstance(raw, str):
                    continue
                normalized = normalize_social_url(raw.strip())
                if normalized:
                    found[(normalized["platform"], normalized["url"])] = normalized
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def normalize_social_url(url: str) -> dict[str, str] | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    platform = next((label for domain, label in SOCIAL_HOSTS.items() if host == domain or host.endswith("." + domain)), None)
    if not platform:
        return None
    parts = [part.strip() for part in parsed.path.split("/") if part.strip()]
    lowered = [part.casefold() for part in parts]
    rejected_first = {
        "facebook": {"sharer", "sharer.php", "share", "share.php", "dialog", "policy.php", "privacy", "events", "groups", "plugins", "pages", "people", "profile", "home"},
        "instagram": {"p", "reel", "reels", "stories", "explore", "share", "intent", "home"},
        "x": {"intent", "share", "home", "search", "i"},
        "linkedin": {"share", "intent", "home", "feed"},
        "youtube": {"share", "intent", "home"},
        "tiktok": {"share", "intent", "home"},
    }
    if not parts or lowered[0] in rejected_first.get(platform, set()):
        return None
    if platform == "facebook" and lowered[0] == "profile.php":
        return None
    if platform == "linkedin" and (lowered[0] != "company" or len(parts) < 2):
        return None
    if platform == "youtube" and lowered[0] not in {"channel", "user", "c"} and not parts[0].startswith("@"):
        return None
    if host == "youtu.be":
        return None
    if platform == "tiktok" and not parts[0].startswith("@"):
        return None
    if platform == "x" and len(parts) != 1:
        return None
    canonical_host = {
        "linkedin": "linkedin.com",
        "facebook": "facebook.com",
        "instagram": "instagram.com",
        "x": "x.com",
        "youtube": "youtube.com",
        "tiktok": "tiktok.com",
    }[platform]
    if platform == "linkedin":
        parts = parts[:2]
    elif platform == "youtube":
        parts = parts[:1] if parts[0].startswith("@") else parts[:2]
    return {"platform": platform, "url": f"https://{canonical_host}/{'/'.join(parts)}"}


def _priority_links(base_url: str, soup: BeautifulSoup, limit: int = 4) -> list[str]:
    base = urllib.parse.urlparse(base_url)
    candidates: dict[str, int] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != base.netloc.lower():
            continue
        haystack = (parsed.path + " " + anchor.get_text(" ", strip=True)).casefold()
        rank = next((index for index, term in enumerate(PRIORITY_TERMS) if term in haystack), None)
        if rank is None:
            continue
        clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        if clean.rstrip("/") == base_url.rstrip("/"):
            continue
        candidates[clean] = min(rank, candidates.get(clean, rank))
    return [url for url, _ in sorted(candidates.items(), key=lambda item: (item[1], item[0]))[:limit]]


def _same_registered_domain_url(base_url: str, candidate: str) -> str | None:
    normalized = normalize_homepage(urllib.parse.urljoin(base_url, candidate))
    if not normalized or registered_domain(normalized) != registered_domain(base_url):
        return None
    return normalized


def _sitemap_document(raw: bytes, base_url: str) -> tuple[str, list[str]]:
    # Untrusted XML: sitemaps never need a DTD, so refuse entity declarations outright.
    if b"<!DOCTYPE" in raw[:4096].upper() or b"<!ENTITY" in raw.upper():
        return "invalid", []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return "invalid", []
    root_kind = root.tag.rsplit("}", 1)[-1].casefold()
    locations = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].casefold() != "loc":
            continue
        location = _same_registered_domain_url(base_url, str(element.text or "").strip())
        if location and location not in locations:
            locations.append(location)
    return root_kind, locations[:500]


def parse_sitemap_locations(raw: bytes, base_url: str, *, limit: int = 500) -> list[str]:
    """Parse same-domain URLs from a sitemap or sitemap index without crawling it."""
    _kind, locations = _sitemap_document(raw, base_url)
    return locations[:limit]


def priority_sitemap_links(base_url: str, sitemap_urls: list[str], *, limit: int = 4) -> list[str]:
    """Select same-domain sitemap URLs likely to contain identity/contact evidence."""
    candidates: dict[str, int] = {}
    for candidate in sitemap_urls:
        normalized = _same_registered_domain_url(base_url, candidate)
        if not normalized or normalized.rstrip("/") == base_url.rstrip("/"):
            continue
        path = urllib.parse.urlparse(normalized).path.casefold()
        rank = next((weight for weight, term in SITEMAP_PRIORITY_TERMS if term in path), None)
        if rank is not None:
            candidates[normalized] = min(rank, candidates.get(normalized, rank))
    return [url for url, _rank in sorted(candidates.items(), key=lambda item: (item[1], item[0]))[:limit]]


def _discover_sitemap_pages(
    base_url: str,
    declared_sitemaps: list[str],
    *,
    timeout: float,
    max_bytes: int,
    limit: int = 4,
    request_policy: HostRequestPolicy | None = None,
) -> tuple[list[str], int, int, list[int], list[str]]:
    queue = [_same_registered_domain_url(base_url, item) for item in declared_sitemaps]
    queue = [item for item in queue if item]
    if not queue:
        queue = [urllib.parse.urljoin(base_url, "/sitemap.xml")]
    visited: set[str] = set()
    page_locations: list[str] = []
    requests = 0
    bytes_received = 0
    latencies: list[int] = []
    errors: list[str] = []
    while queue and len(visited) < 4 and len(page_locations) < 500:
        sitemap_url = queue.pop(0)
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        started = time.monotonic()
        requests += 1
        try:
            assert_public_url(sitemap_url)
            request = urllib.request.Request(sitemap_url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,text/plain"})
            with _open_response(request, timeout, request_policy) as response:
                raw = _read_response(response, max_bytes + 1, timeout)
                final_url = response.geturl()
            elapsed = int((time.monotonic() - started) * 1000)
            latencies.append(elapsed)
            bytes_received += len(raw)
            if len(raw) > max_bytes or registered_domain(final_url) != registered_domain(base_url):
                errors.append("sitemap_oversized_or_cross_domain")
                continue
            kind, locations = _sitemap_document(raw, base_url)
            if kind == "sitemapindex":
                queue.extend(item for item in locations if item not in visited and item not in queue)
            elif kind == "urlset":
                page_locations.extend(item for item in locations if item not in page_locations)
            else:
                errors.append("invalid_sitemap_document")
        except Exception as exc:
            errors.append(type(exc).__name__)
            latencies.append(int((time.monotonic() - started) * 1000))
    return priority_sitemap_links(base_url, page_locations, limit=limit), requests, bytes_received, latencies, errors


def _fetch_secondary_page(
    url: str,
    *,
    homepage_domain: str,
    timeout: float,
    max_bytes: int,
    request_policy: HostRequestPolicy | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]], int, int, int, str | None]:
    if not _robots_allowed(url, timeout, request_policy):
        return None, [], 1, 0, 0, "robots.txt disallows page"
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with _open_response(request, timeout, request_policy) as response:
            raw = _read_response(response, max_bytes + 1, timeout)
            elapsed = int((time.monotonic() - started) * 1000)
            final_url = response.geturl()
            if len(raw) > max_bytes or "html" not in response.headers.get("content-type", "").lower():
                return None, [], 2, len(raw), elapsed, "unsupported or oversized page"
            if _registered_domain(final_url) != homepage_domain:
                return None, [], 2, len(raw), elapsed, "redirected outside registered domain"
        page_html = raw.decode("utf-8", errors="replace")
        page_soup = BeautifulSoup(page_html, "lxml")
        page_text = trafilatura.extract(page_html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        page = {
            "url": final_url,
            "title": page_soup.title.get_text(" ", strip=True)[:500] if page_soup.title else "",
            "main_text_excerpt": page_text[:5000],
            "identity_text_excerpt": _identity_text_excerpt(page_soup),
            "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
        }
        # Keep only deterministic metadata needed by the site-facts layer.  The
        # raw HTML remains bounded to this function and is never emitted into a
        # profile, which keeps the cache/report compact.
        from ..site_facts import parse_html_metadata
        page.update(parse_html_metadata(page_html, final_url, soup=page_soup))
        page_social = [*_social_links(final_url, page_soup), *structured_social_links(page.get("json_ld") or [])]
        return page, list({(item["platform"], item["url"]): item for item in page_social}.values()), 2, len(raw), elapsed, None
    except Exception as exc:
        return None, [], 2, 0, int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {str(exc)[:120]}"


def _jsonld_organisations(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            kind = value.get("@type")
            kinds = set(kind if isinstance(kind, list) else [kind])
            if kinds & {"Organization", "Corporation", "LocalBusiness", "Store", "Restaurant"}:
                values.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(metadata.get("json-ld", []))
    return values[:20]


def _jsonld_identity_values(metadata: dict[str, Any]) -> list[str]:
    """Extract legal identifiers even when JSON-LD omits an Organization type."""
    found: list[str] = []
    keys = {"vatid", "vat_id", "identifier", "taxid", "tax_id", "organisationnumber", "organizationnumber"}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).casefold().replace("-", "_") in keys and isinstance(value, (str, int, float)):
                    text = str(value)
                    if text not in found:
                        found.append(text)
                walk(value)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(metadata)
    return found[:50]


def _extraction_state(text: str, soup: BeautifulSoup) -> str:
    return "js_fallback_candidate" if len(text.strip()) < 100 and len(soup.select("script[src]")) >= 2 else "static_complete"


def _identity_text_excerpt(soup: BeautifulSoup, limit: int = 5000) -> str:
    """Retain visible footer/legal text and explicit identity containers.

    Main-content extraction intentionally removes boilerplate. Identity evidence
    must do the opposite: keep the end of the document and containers whose tag,
    id, class or itemprop marks them as footer/contact/legal/imprint content.
    """
    visible = " ".join(soup.get_text(" ", strip=True).split())
    explicit: list[str] = []
    markers = ("footer", "imprint", "contact", "kontakt", "legal", "juridisk", "address", "telephone", "email", "identifier", "vat")
    for node in soup.find_all(True):
        attributes = " ".join(str(node.get(key) or "") for key in ("id", "class", "itemprop", "role")).casefold()
        if any(marker in attributes for marker in markers):
            text = " ".join(node.get_text(" ", strip=True).split())
            if text and text not in explicit:
                explicit.append(text)
    explicit_text = " ".join(explicit)
    if len(visible) <= limit and not explicit_text:
        return visible
    if len(visible) <= limit:
        return " ".join(dict.fromkeys([visible, explicit_text]))[:limit]
    head = limit // 3
    tail = limit // 3
    return " ".join(dict.fromkeys([visible[:head], visible[-tail:], explicit_text]))[: max(limit, len(explicit_text))]


def _failure_metrics(
    elapsed: int,
    *,
    requests: int = 0,
    bytes_received: int = 0,
    failure_kind: str | None = None,
    failure_class: str | None = None,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "requests": requests,
        "bytes": bytes_received,
        "latencies_ms": [elapsed] if elapsed else [],
    }
    if failure_kind:
        metrics["failure_kind"] = failure_kind
    if failure_class:
        metrics["failure_class"] = failure_class
    return metrics


def _fetch_website_once(
    url: str | None,
    *,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
    request_policy: HostRequestPolicy | None = None,
    max_secondary_pages: int = 4,
    accept_header: str = "text/html,application/xhtml+xml",
) -> tuple[dict[str, Any], dict[str, Any]]:
    supplied_url = str(url or "").strip()
    supplied_scheme = bool(re.match(r"^https?://", supplied_url, re.I))
    normalized = normalize_homepage(url)
    if not normalized:
        return evidence("website", "not_found", "registry_linked_company_website", "https://data.brreg.no/enhetsregisteret/api/enheter", note="No valid registry website URL"), {"requests": 0, "bytes": 0, "latencies_ms": []}
    if request_policy:
        cached = request_policy.cached_homepage(normalized)
        if cached is not None:
            return cached, {"requests": 0, "bytes": 0, "latencies_ms": [], "cache_hit": True}
    try:
        assert_public_url(normalized)
    except PublicURLResolutionError as exc:
        return evidence("website", "failed", "registry_linked_company_website", normalized, note=str(exc)), _failure_metrics(0, failure_kind="resolution", failure_class=network_failure_class(exc))
    except ValueError as exc:
        return evidence("website", "blocked", "registry_linked_company_website", normalized, note=str(exc)), {"requests": 0, "bytes": 0, "latencies_ms": []}
    try:
        if request_policy:
            robots_allowed, declared_sitemaps = request_policy.robots(normalized, timeout)
        else:
            robots_allowed, declared_sitemaps = _robots_policy(normalized, timeout)
    except PublicURLResolutionError as exc:
        return evidence("website", "failed", "registry_linked_company_website", normalized, note=str(exc)), _failure_metrics(0, failure_kind="resolution", failure_class=network_failure_class(exc))
    except PublicURLPolicyError as exc:
        return evidence("website", "blocked", "registry_linked_company_website", normalized, note=str(exc)), {"requests": 0, "bytes": 0, "latencies_ms": []}
    if not robots_allowed:
        return evidence("website", "blocked", "registry_linked_company_website", normalized, note="robots.txt disallows this user agent"), {"requests": 1, "bytes": 0, "latencies_ms": []}
    started = time.monotonic()
    request = urllib.request.Request(normalized, headers={"User-Agent": USER_AGENT, "Accept": accept_header})
    try:
        with _open_response(request, timeout, request_policy, record_redirects=True) as response:
            content_type = response.headers.get("content-type", "")
            raw = _read_response(response, max_bytes + 1, timeout)
            elapsed = int((time.monotonic() - started) * 1000)
            if len(raw) > max_bytes:
                return evidence("website", "blocked", "registry_linked_company_website", normalized, note="Homepage exceeds byte limit"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            if "html" not in content_type.lower():
                return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"Unsupported content type: {content_type}"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            final_url = response.geturl()
            assert_public_url(final_url)
        redirect_chain = list(getattr(_redirect_context, "chain", [normalized, final_url]))
        _redirect_context.chain = []
        html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        structured = extruct.extract(html, base_url=final_url, syntaxes=["json-ld", "microdata", "opengraph"])
        text = trafilatura.extract(html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        description_tag = soup.select_one('meta[name="description"], meta[property="og:description"]')
        description = str(description_tag.get("content") or "").strip() if description_tag else ""
        value = {
            "requested_url": normalized,
            "final_url": final_url,
            "redirect_chain": list(dict.fromkeys(redirect_chain + ([final_url] if final_url not in redirect_chain else []))),
            "registered_domain": _registered_domain(final_url),
            "title": title[:500],
            "description": description[:2000],
            "main_text_excerpt": text[:5000],
            "identity_text_excerpt": _identity_text_excerpt(soup),
            "social_links": _social_links(final_url, soup),
            "structured_organisations": _jsonld_organisations(structured),
            "structured_identifiers": _jsonld_identity_values(structured),
            "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "extraction_state": _extraction_state(text, soup),
        }
        from ..site_facts import parse_html_metadata
        value.update(parse_html_metadata(html, final_url, soup=soup))
        homepage_social = [*value["social_links"], *structured_social_links(structured)]
        value["social_links"] = list({(item["platform"], item["url"]): item for item in homepage_social}.values())
        pages = [{
            "url": final_url,
            "title": title[:500],
            "main_text_excerpt": text[:5000],
            "identity_text_excerpt": value["identity_text_excerpt"],
            "content_sha256": value["content_sha256"],
        }]
        social = value["social_links"]
        crawl_errors = []
        sitemap_pages, sitemap_requests, sitemap_bytes, sitemap_latencies, sitemap_errors = _discover_sitemap_pages(
            final_url,
            declared_sitemaps,
            timeout=timeout,
            max_bytes=min(max_bytes, 1_000_000),
            limit=max(0, max_secondary_pages),
            request_policy=request_policy,
        )
        requests = 2 + sitemap_requests
        bytes_received = len(raw) + sitemap_bytes
        page_latencies = [elapsed]
        page_latencies.extend(sitemap_latencies)
        homepage_domain = value["registered_domain"]
        homepage_pages = _priority_links(final_url, soup, limit=max(0, max_secondary_pages))
        page_urls = list(dict.fromkeys(homepage_pages + sitemap_pages))[:max(0, max_secondary_pages)]
        value["sitemap"] = {
            "declared": bool(declared_sitemaps),
            "documents_fetched": sitemap_requests,
            "priority_pages_selected": len(sitemap_pages),
            "errors": sitemap_errors,
        }
        for page_url in page_urls:
            page, page_social, page_requests, page_bytes, page_elapsed, page_error = _fetch_secondary_page(
                page_url,
                homepage_domain=homepage_domain,
                timeout=timeout,
                max_bytes=min(max_bytes, 1_000_000),
                request_policy=request_policy,
            )
            requests += page_requests
            bytes_received += page_bytes
            if page_elapsed:
                page_latencies.append(page_elapsed)
            if page:
                pages.append(page)
                social.extend(page_social)
            elif page_error:
                crawl_errors.append({"url": page_url, "error": page_error})
        value["pages"] = pages
        value["social_links"] = list({(item["platform"], item["url"]): item for item in social}.values())
        value["crawl_errors"] = crawl_errors
        record = evidence("website", "available", "registry_linked_company_website", final_url, value=value, note="Company-controlled claim layer; not an official registry fact", content_sha256=value["content_sha256"])
        if request_policy:
            request_policy.cache_homepage(normalized, record)
        return record, {"requests": requests, "bytes": bytes_received, "latencies_ms": page_latencies}
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        status = "not_found" if exc.code in {404, 410} else "blocked" if exc.code in {401, 403} else "source_error"
        retry_after = str(exc.headers.get("Retry-After") or "") if exc.headers else ""
        return evidence("website", status, "registry_linked_company_website", normalized, note=f"HTTP {exc.code}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed], "status_code": int(exc.code), "retry_after": retry_after}
    except CircuitOpenError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "failed", "registry_linked_company_website", normalized, note=str(exc)), _failure_metrics(elapsed, failure_kind="circuit_open", failure_class="transient")
    except urllib.error.URLError as exc:
        if not supplied_scheme and normalized.startswith("https://"):
            first_elapsed = int((time.monotonic() - started) * 1000)
            record, metrics = fetch_website("http://" + supplied_url, timeout=timeout, max_bytes=max_bytes, request_policy=request_policy)
            metrics["requests"] += 2
            metrics["latencies_ms"].insert(0, first_elapsed)
            return record, metrics
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "failed", "registry_linked_company_website", normalized, note=f"URLError: {str(exc.reason)[:180]}"), _failure_metrics(elapsed, requests=2, failure_kind=_network_failure_kind(exc) or "connect", failure_class=network_failure_class(exc))
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        failure_kind = _network_failure_kind(exc)
        status = "failed" if failure_kind else "source_error"
        return evidence("website", status, "registry_linked_company_website", normalized, note=f"{type(exc).__name__}: {str(exc)[:180]}"), _failure_metrics(elapsed, requests=2, failure_kind=failure_kind, failure_class=network_failure_class(exc))


def fetch_website(
    url: str | None,
    *,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
    request_policy: HostRequestPolicy | None = None,
    max_secondary_pages: int = 4,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch one candidate, retrying only transient failures within an 8 s cap."""
    started = time.monotonic()
    deadline = started + 8.0
    attempts: list[dict[str, Any]] = []
    accept_header = "text/html,application/xhtml+xml"
    http_retry_used = False
    http_retry_count = 0
    for attempt in range(5):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        record, metrics = _fetch_website_once(
            url,
            timeout=min(max(0.1, float(timeout)), remaining),
            max_bytes=max_bytes,
            request_policy=request_policy,
            max_secondary_pages=max_secondary_pages,
            accept_header=accept_header,
        )
        attempts.append(metrics)
        status_code = int(metrics.get("status_code") or 0)
        if status_code == 406 and not http_retry_used:
            http_retry_used = True
            http_retry_count += 1
            accept_header = "*/*"
            continue
        if status_code in {429, 502, 503, 504} and not http_retry_used:
            http_retry_used = True
            http_retry_count += 1
            try:
                retry_after = min(5.0, max(0.0, float(metrics.get("retry_after") or 0.0)))
            except (TypeError, ValueError):
                retry_after = 0.25
            if retry_after and time.monotonic() + retry_after < deadline:
                time.sleep(retry_after)
            continue
        failure_class = metrics.get("failure_class")
        if record.get("status") not in {"failed", "source_error"} or failure_class != "transient" or attempt >= 2:
            break
        backoff = min(0.25 * (2 ** attempt), max(0.0, deadline - time.monotonic()))
        if backoff:
            time.sleep(backoff)
    if not attempts:
        return evidence("website", "failed", "registry_linked_company_website", str(url or ""), note="candidate retry budget exhausted"), {"requests": 0, "bytes": 0, "latencies_ms": [], "retry_count": 0, "attempts": 0, "failure_kind": "timeout", "failure_class": "transient"}
    result_metrics = dict(attempts[-1])
    result_metrics["requests"] = sum(int(item.get("requests") or 0) for item in attempts)
    result_metrics["bytes"] = sum(int(item.get("bytes") or 0) for item in attempts)
    result_metrics["latencies_ms"] = [latency for item in attempts for latency in item.get("latencies_ms") or []]
    result_metrics["retry_count"] = len(attempts) - 1
    result_metrics["attempts"] = len(attempts)
    result_metrics["http_retry_count"] = http_retry_count
    result_metrics["real_attempts"] = len(attempts)
    retry_failure_classes = [item.get("failure_class") for item in attempts[:-1] if item.get("failure_class")]
    if retry_failure_classes:
        result_metrics["retry_failure_class"] = retry_failure_classes[-1]
    return record, result_metrics
