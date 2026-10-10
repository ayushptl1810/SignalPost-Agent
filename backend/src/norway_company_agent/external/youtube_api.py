from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .external_footprint import connector_policy_entry, observation_id

CONNECTOR_ID = "youtube_data_api"
PLATFORM = "youtube"
API_ROOT = "https://www.googleapis.com/youtube/v3"


def parse_youtube_link(url: str) -> dict[str, str] | None:
    parsed = urllib.parse.urlparse(str(url or ""))
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if host not in {"youtube.com", "m.youtube.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    if parts[0].startswith("@"):
        return {"kind": "forHandle", "value": parts[0][1:], "url": f"https://youtube.com/{parts[0]}"}
    if parts[0] == "channel" and len(parts) > 1:
        return {"kind": "id", "value": parts[1], "url": f"https://youtube.com/channel/{parts[1]}"}
    if parts[0] == "user" and len(parts) > 1:
        return {"kind": "forUsername", "value": parts[1], "url": f"https://youtube.com/user/{parts[1]}"}
    if parts[0] in {"c", "user"}:
        return {"kind": "unresolvable_link", "value": "/".join(parts[1:]), "url": str(url)}
    return None


def _keys() -> list[str]:
    values = []
    for name in ("YOUTUBE_API_KEYS", "YOUTUBE_API_KEY", "YOUTUBE_DATA_API_KEYS", "YOUTUBE_DATA_API_KEY"):
        raw = os.environ.get(name, "")
        values.extend(part.strip() for part in raw.split(",") if part.strip())
    return list(dict.fromkeys(values))


class YouTubeClient:
    def __init__(self, key: str, *, timeout: float = 15.0, opener=urllib.request.urlopen):
        self.key = key
        self.timeout = timeout
        self.opener = opener
        self.units = 0
        self.requests = 0

    def get(self, resource: str, params: dict[str, Any], *, units: int = 1) -> dict[str, Any]:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None}, doseq=True)
        url = f"{API_ROOT}/{resource}?{query}"
        request = urllib.request.Request(f"{url}&key={urllib.parse.quote(self.key)}", headers={"Accept": "application/json"})
        self.requests += 1
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"YouTube API HTTP {exc.code}") from exc
        self.units += units
        return body


def _usage_path(context: dict[str, Any]) -> Path:
    return Path(context.get("usage_path", "out/youtube-usage.json"))


def _reserve_units(path: Path, units: int, *, now: datetime, daily_limit: int = 10000) -> tuple[bool, dict[str, Any]]:
    pacific_date = now.date().isoformat()  # operational date marker; quota reset is documented as Pacific.
    body = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"date": pacific_date, "units": 0}
    if body.get("date") != pacific_date:
        body = {"date": pacific_date, "units": 0}
    if int(body.get("units", 0)) + units > daily_limit:
        return False, body
    body["units"] = int(body.get("units", 0)) + units
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    return True, body


def _proof(profile: dict[str, Any], website_url: str, link: str) -> list[dict[str, Any]]:
    website = profile.get("evidence", {}).get("website", {})
    value = website.get("value") or {}
    return [
        {"type": "linked_from_verified_company_site", "source_url": website_url},
        {"type": "matched_verified_link", "url": link},
        {"type": "website_identity_assessment", "assessment": value.get("identity_assessment") or {}},
    ]


def _observation(profile: dict[str, Any], *, signal: str, source_url: str, retrieved_at: str, raw: Any, proof: list[dict[str, Any]], strategy: str, policy_path: str, **extra: Any) -> dict[str, Any]:
    policy = connector_policy_entry(CONNECTOR_ID, platform=PLATFORM, acquisition_mode="official_api", path=policy_path)
    return {
        "id": observation_id(CONNECTOR_ID, str(profile["organisation_number"]), source_url, signal),
        "organisation_number": str(profile["organisation_number"]), "platform": PLATFORM, "signal_type": signal,
        "source_url": source_url, "retrieved_at": retrieved_at,
        "content_sha256": hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "exact_entity": True, "identity_proof": proof, "acquisition_mode": "official_api",
        "rights_status": policy.get("rights_status", "review_required"), "connector_id": CONNECTOR_ID,
        "source_class": "youtube_data_api", "strategy": strategy, **extra,
    }


def collect(profile: dict[str, Any], *, now: datetime, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    started = time.monotonic()
    website = profile.get("evidence", {}).get("website", {})
    value = website.get("value") or {}
    if website.get("status") != "available" or not value.get("identity_assessment", {}).get("publishable"):
        return {"status": "not_applicable", "observations": [], "operations": {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": [0]}, "note": "No verified company site is available."}
    links = value.get("social_links") or value.get("discovered_social_links") or []
    links = [parse_youtube_link(item.get("url", "")) for item in links if isinstance(item, dict) and item.get("platform") == "youtube"]
    links = [item for item in links if item]
    if not links:
        return {"status": "not_applicable", "observations": [], "operations": {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": [0]}, "note": "Verified site has no YouTube link."}
    policy_path = str(context.get("policy_path", "config/connector-policy.json"))
    usage_path = _usage_path(context)
    client = context.get("client")
    if client is None:
        keys = _keys()
        if not keys:
            return {"status": "failed", "observations": [], "operations": {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": [0]}, "note": "No YouTube API key configured."}
        client = YouTubeClient(keys[0], timeout=float(context.get("timeout", 15)))
    observations: list[dict[str, Any]] = []
    channel_items: list[dict[str, Any]] = []
    try:
        for link in links:
            if link["kind"] == "unresolvable_link":
                continue
            if not _reserve_units(usage_path, 1, now=now)[0]:
                return {"status": "failed", "observations": observations, "operations": {"requests": getattr(client, "requests", 0), "third_party_cost_usd": 0.0, "latency_ms": [round((time.monotonic() - started) * 1000)]}, "note": "quota_exhausted_until next daily reset"}
            fixture_channels = context.get("channels") or {}
            channel = fixture_channels.get(link["value"]) if isinstance(fixture_channels, dict) else None
            if channel is None:
                params = {"part": "snippet,statistics,contentDetails", link["kind"]: link["value"]}
                body = client.get("channels", params, units=1)
                channel_items = body.get("items") or []
                channel = channel_items[0] if channel_items else None
            if not channel:
                continue
            channel_id = str(channel.get("id") or (channel.get("snippet") or {}).get("channelId") or "")
            if not channel_id:
                continue
            channel_url = f"https://youtube.com/channel/{channel_id}"
            retrieved = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            website_url = str(value.get("final_url") or website.get("source_url") or "")
            proof = _proof(profile, website_url, link["url"])
            observations.append(_observation(profile, signal="profile_handle", source_url=link["url"], retrieved_at=retrieved, raw={"channel_id": channel_id, "source": link["url"]}, proof=proof, strategy="verified_handle_extraction", policy_path=policy_path, channel_id=channel_id))
            stats = channel.get("statistics") or {}
            metrics = {key: int(stats[key]) for key in ("subscriberCount", "viewCount", "videoCount") if key in stats and not (key == "subscriberCount" and channel.get("statistics", {}).get("hiddenSubscriberCount"))}
            if metrics:
                observations.append(_observation(profile, signal="profile_metrics", source_url=channel_url, retrieved_at=retrieved, raw=metrics, proof=proof, strategy="social_profile_metrics", policy_path=policy_path, metrics={"subscribers": metrics.get("subscriberCount"), "views": metrics.get("viewCount"), "videos": metrics.get("videoCount")}))
            uploads = (channel.get("contentDetails") or {}).get("relatedPlaylists", {}).get("uploads")
            items = (context.get("videos") or {}).get(channel_id, []) if isinstance(context.get("videos"), dict) else []
            for video in items[:10]:
                video_id = str(video.get("id") or "")
                if isinstance(video.get("id"), dict):
                    video_id = str(video["id"].get("videoId") or "")
                if not video_id:
                    continue
                snippet = video.get("snippet") or {}
                stats = video.get("statistics") or {}
                observations.append(_observation(profile, signal="public_post", source_url=f"https://youtube.com/watch?v={video_id}", retrieved_at=retrieved, raw=video, proof=proof, strategy="youtube_channel_feed", policy_path=policy_path, evidence_span=str(snippet.get("title") or "")[:500], metrics={key: int(stats[key]) for key in ("viewCount", "likeCount", "commentCount") if key in stats}, published_at=snippet.get("publishedAt")))
        latency = [round((time.monotonic() - started) * 1000)]
        return {"status": "available" if any(item.get("signal_type") == "profile_handle" for item in observations) else "not_available", "observations": observations, "operations": {"requests": getattr(client, "requests", 0), "third_party_cost_usd": 0.0, "latency_ms": latency, "units": getattr(client, "units", 0)}, "note": None if observations else "YouTube link did not resolve to a public channel."}
    except (RuntimeError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"status": "failed", "observations": observations, "operations": {"requests": getattr(client, "requests", 0), "third_party_cost_usd": 0.0, "latency_ms": [round((time.monotonic() - started) * 1000)], "units": getattr(client, "units", 0)}, "note": f"YouTube API failure: {type(exc).__name__}"}
