"""plexamp_now_playing — what one Plexamp player is playing, or a shelf pick.

Reads Plex's /status/sessions and picks the first music session whose
player matches the configured name (e.g. a headless Plexamp called
"caldera"). When nothing is playing it shows a random album from the
music libraries instead.

The random pick is seeded by a time bucket (default 6 h), so every render
inside the bucket returns the *same* album. That matters on battery
panels: an identical frame keeps the same ETag, the device gets a 304 and
never repaints. A fresh random cover per render would repaint every wake.

Cover art is fetched server-side through Plex's photo transcoder, cached
in data_dir and inlined as a data URI; the token never reaches the panel.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:  # inside Tesserae
    from app.plugin_http import fetch_json
except ImportError:  # offline tests
    import json

    def fetch_json(url: str, *, headers: dict[str, str], timeout: int) -> Any:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())


logger = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 10
ART_PX = 600  # square; plenty for 400x300 up to 800x480 panels
ART_JPEG_QUALITY = 85


# ----- helpers --------------------------------------------------------


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "tesserae-plexamp_now_playing/0.1",
        "X-Plex-Token": token,
    }


def _int_opt(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _bool_opt(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _get(base: str, token: str, path: str, params: dict[str, str] | None = None) -> dict:
    url = f"{base}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    payload = fetch_json(url, headers=_headers(token), timeout=HTTP_TIMEOUT_S)
    container = payload.get("MediaContainer") if isinstance(payload, dict) else None
    return container if isinstance(container, dict) else {}


# ----- now playing ----------------------------------------------------


def _player_matches(session: dict[str, Any], needle: str) -> bool:
    if not needle:
        return True
    player = session.get("Player") or {}
    if not isinstance(player, dict):
        return False
    hay = " ".join(
        str(player.get(k) or "")
        for k in ("title", "product", "device", "machineIdentifier", "address")
    ).lower()
    return needle.lower() in hay


def find_session(
    sessions: list[dict[str, Any]], needle: str, include_paused: bool
) -> dict[str, Any] | None:
    """First music session on the matching player. Playing beats paused."""
    tracks = [
        s
        for s in sessions
        if isinstance(s, dict)
        and str(s.get("type", "")).lower() == "track"
        and _player_matches(s, needle)
    ]

    def state(s: dict[str, Any]) -> str:
        p = s.get("Player") or {}
        return str(p.get("state", "")).lower() if isinstance(p, dict) else ""

    for s in tracks:
        if state(s) == "playing":
            return s
    if include_paused:
        for s in tracks:
            if state(s) in {"paused", "buffering"}:
                return s
    return None


def _shape_track(s: dict[str, Any]) -> dict[str, Any]:
    player = s.get("Player") or {}
    return {
        "mode": "playing",
        "state": str(player.get("state", "playing")).lower(),
        "album": s.get("parentTitle") or "",
        # originalTitle carries the track artist on compilations
        "artist": s.get("originalTitle") or s.get("grandparentTitle") or "",
        "track": s.get("title") or "",
        "year": s.get("parentYear") or s.get("year"),
        "player": player.get("title") or "",
        "_thumb": s.get("parentThumb") or s.get("thumb") or s.get("grandparentThumb"),
        "_track_left_s": _track_left_s(s),
    }


def _track_left_s(s: dict[str, Any]) -> float | None:
    try:
        duration, offset = int(s["duration"]), int(s.get("viewOffset") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    return max(0.0, (duration - offset) / 1000) if duration > 0 else None


# ----- wake hint ------------------------------------------------------

# The device's own sleep interval is the idle cadence (long, to save
# battery). While a track is actually playing we pull the next wake in to
# just after that track ends: the album can only change on its own at a
# track boundary, so the panel catches the next record without polling.
# Tesserae adds its own ~20 s render margin on top of this.
TRACK_END_MARGIN_S = 5
MIN_RECHECK_S = 60  # Plex's viewOffset lags a little; don't wake twice per track
MAX_RECHECK_S = 600  # cap for long tracks, so a manual album switch shows up


def next_change_at(item: dict[str, Any], now: float | None = None) -> float | None:
    """When the frame should look again, or None to let the device sleep its
    full interval (nothing playing, or paused: nothing will change by itself)."""
    if item.get("mode") != "playing" or item.get("state") != "playing":
        return None
    now = time.time() if now is None else now
    left = item.get("_track_left_s")
    wait = MAX_RECHECK_S if left is None else left + TRACK_END_MARGIN_S
    return now + min(max(wait, MIN_RECHECK_S), MAX_RECHECK_S)


# ----- shelf pick (random album) -------------------------------------


def _music_sections(base: str, token: str, only: str) -> list[str]:
    container = _get(base, token, "/library/sections")
    keys = []
    for d in container.get("Directory") or []:
        if not isinstance(d, dict) or d.get("type") != "artist":
            continue
        key, title = str(d.get("key", "")), str(d.get("title", ""))
        if only and only.lower() not in {key.lower(), title.lower()}:
            continue
        keys.append(key)
    return keys


def _bucket(hours: int, now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // (hours * 3600))


def pick_album(base: str, token: str, *, hours: int, only_section: str) -> dict[str, Any] | None:
    sections = _music_sections(base, token, only_section)
    if not sections:
        return None
    sizes = []
    for key in sections:
        c = _get(
            base,
            token,
            f"/library/sections/{key}/all",
            {"type": "9", "X-Plex-Container-Start": "0", "X-Plex-Container-Size": "0"},
        )
        sizes.append(int(c.get("totalSize") or c.get("size") or 0))
    total = sum(sizes)
    if total <= 0:
        return None

    # Deterministic within a bucket: same album, same frame, no repaint.
    rng = random.Random(f"{_bucket(hours)}:{','.join(sections)}:{total}")
    idx = rng.randrange(total)
    for key, size in zip(sections, sizes):
        if idx < size:
            c = _get(
                base,
                token,
                f"/library/sections/{key}/all",
                {
                    "type": "9",
                    "sort": "titleSort",
                    "X-Plex-Container-Start": str(idx),
                    "X-Plex-Container-Size": "1",
                },
            )
            meta = (c.get("Metadata") or [None])[0]
            if not isinstance(meta, dict):
                return None
            return {
                "mode": "shelf",
                "state": "idle",
                "album": meta.get("title") or "",
                "artist": meta.get("parentTitle") or "",
                "track": "",
                "year": meta.get("year"),
                "player": "",
                "_thumb": meta.get("thumb"),
            }
        idx -= size
    return None


# ----- artwork --------------------------------------------------------


def _art_url(base: str, token: str, thumb: str) -> str:
    q = urllib.parse.urlencode(
        {
            "width": str(ART_PX),
            "height": str(ART_PX),
            "minSize": "1",
            "upscale": "1",
            "url": thumb,
            "X-Plex-Token": token,
        }
    )
    return f"{base}/photo/:/transcode?{q}"


def _process(raw: bytes, tone: str) -> bytes | None:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return raw
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            im = ImageOps.fit(im, (ART_PX, ART_PX), Image.Resampling.LANCZOS)
            if tone == "mono":
                # Greyscale + stretched contrast dithers far cleaner to
                # black/white than a full-colour cover to a 4-ink palette.
                im = ImageOps.autocontrast(ImageOps.grayscale(im), cutoff=1).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=ART_JPEG_QUALITY, optimize=True)
            return buf.getvalue()
    except (OSError, ValueError):
        return None


def art_data_uri(base: str, token: str, thumb: Any, tone: str, data_dir: str | None) -> str | None:
    if not (isinstance(thumb, str) and thumb.startswith("/")):
        return None
    # Cache key is the thumb path (which embeds Plex's own update timestamp)
    # plus tone — not the URL, which carries the token.
    digest = hashlib.sha1(f"{thumb}|{tone}|{ART_PX}".encode()).hexdigest()[:20]
    cache = Path(data_dir) / "art" / f"{digest}.jpg" if data_dir else None
    if cache and cache.exists():
        try:
            return "data:image/jpeg;base64," + base64.b64encode(cache.read_bytes()).decode()
        except OSError:
            pass
    req = urllib.request.Request(_art_url(base, token, thumb), headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            raw = resp.read()
    except Exception as exc:  # artwork is optional
        logger.info("plexamp_now_playing: art fetch failed: %s", exc)
        return None
    jpeg = _process(raw, tone)
    if jpeg is None:
        return None
    if cache:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(jpeg)
        except OSError:
            pass
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


# ----- plugin contract ------------------------------------------------


DEMO = {
    "demo": True,
    "mode": "playing",
    "state": "playing",
    "album": "Chromatic Rooms",
    "artist": "The Paper Satellites",
    "track": "Midnight Geometry",
    "year": 2024,
    "player": "caldera",
    "art": None,
    "note": "Demo data — set the Plex URL + token in the widget settings.",
}


def fetch(options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    base = str(settings.get("plex_url") or "").strip().rstrip("/")
    token = str(settings.get("plex_token") or "").strip()
    if not (base and token):
        return dict(DEMO)

    needle = str(options.get("player") or "").strip()
    include_paused = _bool_opt(options.get("include_paused"), True)
    show_track = _bool_opt(options.get("show_track"), False)
    hours = _int_opt(options.get("shelf_hours"), 6, 1, 168)
    only_section = str(options.get("section") or "").strip()
    tone = "mono" if str(options.get("tone") or "").lower() == "mono" else "color"
    data_dir = ctx.get("data_dir") if isinstance(ctx, dict) else None

    try:
        sessions = _get(base, token, "/status/sessions").get("Metadata") or []
        item = find_session(sessions, needle, include_paused)
        item = _shape_track(item) if item else pick_album(
            base, token, hours=hours, only_section=only_section
        )
    except Exception as exc:
        return {"error": f"Plex: {type(exc).__name__}: {exc}"}

    if item is None:
        return {"mode": "empty", "note": "No music library found on this Plex server."}

    wake = next_change_at(item)
    item.pop("_track_left_s", None)
    if wake is not None:
        item["next_change_at"] = wake
    thumb = item.pop("_thumb", None)
    item["art"] = art_data_uri(base, token, thumb, tone, data_dir)
    if not show_track:
        # Track changes every few minutes; album changes rarely. Leaving the
        # track out keeps the frame stable for a whole record, so a battery
        # panel repaints once per album instead of once per song.
        item["track"] = ""
    item["demo"] = False
    return item
