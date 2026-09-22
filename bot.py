"""Web3 Project Scout — single-file Telegram bot."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from datetime import datetime
from typing import Any

import aiosqlite
import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("scout")

DEX_API = "https://api.dexscreener.com"
GECKO_API = "https://api.geckoterminal.com/api/v2"

SKIP_SYMBOLS = {
    "SOL", "WSOL", "USDC", "USDT", "USD1", "DAI", "WETH", "ETH",
    "WBNB", "BNB", "WBTC", "BTC", "USDB", "WMATIC", "MATIC", "WAVAX", "AVAX",
}

GECKO_CHAIN = {
    "eth": "ethereum", "ethereum": "ethereum", "bsc": "bsc", "base": "base",
    "solana": "solana", "arbitrum": "arbitrum", "polygon_pos": "polygon",
    "avax": "avalanche", "optimism": "optimism", "sui-network": "sui",
    "sui": "sui", "ton": "ton", "abstract": "abstract", "hyperevm": "hyperevm",
    "hyperliquid": "hyperevm", "monad": "monad", "ink": "ink",
    "robinhood": "robinhood", "sonic": "sonic", "blast": "blast",
    "linea": "linea", "berachain": "berachain", "unichain": "unichain",
}

DEFAULT_CHAINS = {
    "solana", "base", "bsc", "ethereum", "abstract", "robinhood",
    "hyperevm", "sui", "arbitrum", "ink", "monad",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    pair_address TEXT,
    name TEXT,
    symbol TEXT,
    dex TEXT,
    launched_at INTEGER,
    discovered_at INTEGER NOT NULL,
    website TEXT,
    twitter TEXT,
    telegram TEXT,
    discord TEXT,
    docs TEXT,
    description TEXT,
    liquidity_usd REAL,
    volume_24h REAL,
    market_cap REAL,
    fdv REAL,
    price_usd REAL,
    image_url TEXT,
    source TEXT,
    qualified INTEGER NOT NULL DEFAULT 0,
    last_enriched_at INTEGER,
    UNIQUE(chain, token_address)
);
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    last_check_at INTEGER,
    alerts_enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS watches (
    user_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, project_id)
);
CREATE TABLE IF NOT EXISTS alerts_sent (
    user_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    sent_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_proj_disc ON projects(discovered_at DESC);
"""

HELP = """🔎 <b>Web3 Project Scout</b>

Finds early public-facing projects and stores them while you sleep.

/newtokens — everything since your last check
/newtokens 6h — last 6 hours
/newtokens 24h — last 24 hours
/newtokens 3d — last 3 days
/project chain:address — one project
/research chain:address — same report
/jobs — projects worth your time (opportunity score)
/digest 12h — morning shortlist, ranked
/watchlist — saved projects
/alerts on — live pings (score 45+)
/alerts off — silence
/status — scanner health

Alerts only fire when a project looks public-facing AND scores high enough.
"""


def env_ints(name: str) -> list[int]:
    raw = os.getenv(name, "")
    out: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            out.append(int(part))
    return out


def now() -> int:
    return int(time.time())


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def ago(ts: int | None) -> str:
    if not ts:
        return "unknown"
    sec = max(0, now() - int(ts))
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h {(sec % 3600) // 60}m ago"
    return f"{sec // 86400}d ago"


def money(value: float | None) -> str:
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    if n >= 1_000_000:
        return f"${n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"${n / 1_000:.1f}K"
    if n >= 1:
        return f"${n:.2f}"
    if n > 0:
        return f"${n:.6f}"
    return "$0"


def mark(ok: Any) -> str:
    return "✓" if ok else "—"


def title_of(p: dict[str, Any]) -> str:
    name = p.get("name") or p.get("symbol") or "Unknown"
    symbol = p.get("symbol")
    if symbol and symbol.lower() not in str(name).lower():
        return f"{name} ({symbol})"
    return str(name)


def parse_lookback(arg: str | None, last_check: int | None) -> tuple[int, str]:
    raw = (arg or "").strip().lower()
    if raw in {"", "last", "since", "since_last"}:
        return int(last_check or (now() - 12 * 3600)), "since last check"
    match = re.fullmatch(r"(\d+)([hdm])", raw)
    if not match:
        return now() - 12 * 3600, "12h"
    amount, unit = int(match.group(1)), match.group(2)
    seconds = amount * {"h": 3600, "d": 86400, "m": 60}[unit]
    return now() - seconds, f"{amount}{unit}"


def is_qualified(row: dict[str, Any]) -> bool:
    return bool(row.get("website") or row.get("twitter") or row.get("telegram") or row.get("discord"))


def pick_db_path() -> str:
    requested = os.getenv("DATABASE_PATH", "./scout.db")
    folder = os.path.dirname(requested)
    if folder:
        try:
            os.makedirs(folder, exist_ok=True)
            return requested
        except OSError:
            log.warning("Cannot create %s — using ./scout.db", folder)
            return "./scout.db"
    return requested


# ---------- database ----------

class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    @property
    def c(self) -> aiosqlite.Connection:
        assert self.conn is not None
        return self.conn

    async def get_meta(self, key: str) -> str | None:
        cur = await self.c.execute("SELECT v FROM meta WHERE k=?", (key,))
        row = await cur.fetchone()
        return row["v"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self.c.execute(
            "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )
        await self.c.commit()

    async def upsert(self, data: dict[str, Any]) -> tuple[int, bool]:
        chain = (data.get("chain") or "").lower()
        token = (data.get("token_address") or "").strip()
        if not chain or not token:
            return 0, False
        cur = await self.c.execute(
            "SELECT * FROM projects WHERE chain=? AND lower(token_address)=lower(?)",
            (chain, token),
        )
        existing = await cur.fetchone()
        data = dict(data)
        data["chain"] = chain
        data["token_address"] = token
        data["qualified"] = 1 if is_qualified(data) else int(data.get("qualified") or 0)
        if existing:
            old = dict(existing)
            merged = dict(old)
            for key, value in data.items():
                if key == "discovered_at":
                    continue
                if value in (None, "", 0) and merged.get(key) not in (None, "", 0):
                    continue
                if key == "qualified":
                    merged[key] = 1 if (old.get("qualified") or value) else 0
                else:
                    merged[key] = value
            merged["qualified"] = 1 if is_qualified(merged) else int(merged.get("qualified") or 0)
            await self.c.execute(
                """
                UPDATE projects SET
                    pair_address=?, name=?, symbol=?, dex=?, launched_at=?,
                    website=?, twitter=?, telegram=?, discord=?, docs=?, description=?,
                    liquidity_usd=?, volume_24h=?, market_cap=?, fdv=?, price_usd=?,
                    image_url=?, source=?, qualified=?, last_enriched_at=?
                WHERE id=?
                """,
                (
                    merged.get("pair_address"), merged.get("name"), merged.get("symbol"),
                    merged.get("dex"), merged.get("launched_at"), merged.get("website"),
                    merged.get("twitter"), merged.get("telegram"), merged.get("discord"),
                    merged.get("docs"), merged.get("description"), merged.get("liquidity_usd"),
                    merged.get("volume_24h"), merged.get("market_cap"), merged.get("fdv"),
                    merged.get("price_usd"), merged.get("image_url"),
                    merged.get("source") or old.get("source"),
                    int(merged.get("qualified") or 0), merged.get("last_enriched_at"),
                    old["id"],
                ),
            )
            await self.c.commit()
            return int(old["id"]), False
        data.setdefault("discovered_at", now())
        cur = await self.c.execute(
            """
            INSERT INTO projects (
                chain, token_address, pair_address, name, symbol, dex, launched_at,
                discovered_at, website, twitter, telegram, discord, docs, description,
                liquidity_usd, volume_24h, market_cap, fdv, price_usd, image_url,
                source, qualified, last_enriched_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data["chain"], data["token_address"], data.get("pair_address"),
                data.get("name"), data.get("symbol"), data.get("dex"),
                data.get("launched_at"), data["discovered_at"], data.get("website"),
                data.get("twitter"), data.get("telegram"), data.get("discord"),
                data.get("docs"), data.get("description"), data.get("liquidity_usd"),
                data.get("volume_24h"), data.get("market_cap"), data.get("fdv"),
                data.get("price_usd"), data.get("image_url"), data.get("source"),
                int(data.get("qualified") or 0), data.get("last_enriched_at"),
            ),
        )
        await self.c.commit()
        return int(cur.lastrowid), True

    async def by_id(self, pid: int) -> dict[str, Any] | None:
        cur = await self.c.execute("SELECT * FROM projects WHERE id=?", (pid,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def by_token(self, chain: str, token: str) -> dict[str, Any] | None:
        cur = await self.c.execute(
            "SELECT * FROM projects WHERE chain=? AND lower(token_address)=lower(?)",
            (chain.lower(), token),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_since(self, since_ts: int, limit: int, offset: int) -> list[dict[str, Any]]:
        cur = await self.c.execute(
            """
            SELECT * FROM projects
            WHERE discovered_at >= ? AND qualified=1
            ORDER BY discovered_at DESC
            LIMIT ? OFFSET ?
            """,
            (since_ts, limit, offset),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def count_since(self, since_ts: int) -> int:
        cur = await self.c.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE discovered_at >= ? AND qualified=1",
            (since_ts,),
        )
        row = await cur.fetchone()
        return int(row["n"] if row else 0)

    async def qualified_since(self, since_ts: int) -> list[dict[str, Any]]:
        cur = await self.c.execute(
            """
            SELECT * FROM projects
            WHERE discovered_at >= ? AND qualified=1
            ORDER BY discovered_at DESC
            LIMIT 200
            """,
            (since_ts,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def stats(self) -> dict[str, int]:
        cur = await self.c.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN qualified=1 THEN 1 ELSE 0 END) AS qualified,
                   SUM(CASE WHEN discovered_at >= ? THEN 1 ELSE 0 END) AS last_24h
            FROM projects
            """,
            (now() - 86400,),
        )
        row = await cur.fetchone()
        return {
            "total": int(row["total"] or 0),
            "qualified": int(row["qualified"] or 0),
            "last_24h": int(row["last_24h"] or 0),
        }

    async def unenriched(self, limit: int = 12) -> list[dict[str, Any]]:
        cur = await self.c.execute(
            """
            SELECT * FROM projects
            WHERE last_enriched_at IS NULL OR last_enriched_at < ?
            ORDER BY discovered_at DESC LIMIT ?
            """,
            (now() - 1800, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def mark_enriched(self, pid: int) -> None:
        await self.c.execute("UPDATE projects SET last_enriched_at=? WHERE id=?", (now(), pid))
        await self.c.commit()

    async def ensure_user(self, user_id: int) -> dict[str, Any]:
        cur = await self.c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if row:
            return dict(row)
        await self.c.execute(
            "INSERT INTO users(user_id, last_check_at, alerts_enabled) VALUES(?,?,1)",
            (user_id, now()),
        )
        await self.c.commit()
        return {"user_id": user_id, "last_check_at": now(), "alerts_enabled": 1}

    async def set_last_check(self, user_id: int) -> None:
        await self.ensure_user(user_id)
        await self.c.execute("UPDATE users SET last_check_at=? WHERE user_id=?", (now(), user_id))
        await self.c.commit()

    async def set_alerts(self, user_id: int, enabled: bool) -> None:
        await self.ensure_user(user_id)
        await self.c.execute(
            "UPDATE users SET alerts_enabled=? WHERE user_id=?",
            (1 if enabled else 0, user_id),
        )
        await self.c.commit()

    async def watch(self, user_id: int, pid: int) -> None:
        await self.c.execute(
            "INSERT OR IGNORE INTO watches(user_id, project_id, created_at) VALUES(?,?,?)",
            (user_id, pid, now()),
        )
        await self.c.commit()

    async def unwatch(self, user_id: int, pid: int) -> None:
        await self.c.execute(
            "DELETE FROM watches WHERE user_id=? AND project_id=?",
            (user_id, pid),
        )
        await self.c.commit()

    async def watchlist(self, user_id: int) -> list[dict[str, Any]]:
        cur = await self.c.execute(
            """
            SELECT p.* FROM watches w
            JOIN projects p ON p.id = w.project_id
            WHERE w.user_id=?
            ORDER BY w.created_at DESC
            """,
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def alert_candidates(self, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        cur = await self.c.execute(
            """
            SELECT p.* FROM projects p
            WHERE p.qualified=1
              AND p.discovered_at >= ?
              AND p.id NOT IN (SELECT project_id FROM alerts_sent WHERE user_id=?)
            ORDER BY p.discovered_at DESC LIMIT ?
            """,
            (now() - 6 * 3600, user_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def mark_alerted(self, user_id: int, pid: int) -> None:
        await self.c.execute(
            "INSERT OR IGNORE INTO alerts_sent(user_id, project_id, sent_at) VALUES(?,?,?)",
            (user_id, pid, now()),
        )
        await self.c.commit()


# ---------- HTTP sources ----------

async def http_get(client: httpx.AsyncClient, url: str, **kwargs) -> Any:
    try:
        resp = await client.get(url, timeout=20, **kwargs)
        if resp.status_code == 429:
            log.warning("429 from %s", url)
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("HTTP error %s: %s", url, exc)
        return None


def parse_profile(raw: dict[str, Any]) -> dict[str, Any]:
    website = twitter = telegram = discord = docs = None
    for link in raw.get("links") or []:
        if not isinstance(link, dict):
            continue
        url = (link.get("url") or "").strip()
        typ = (link.get("type") or "").lower()
        label = (link.get("label") or "").lower()
        if not url:
            continue
        if typ == "twitter" or "x.com/" in url or "twitter.com/" in url:
            twitter = url
        elif typ == "telegram" or "t.me/" in url:
            telegram = url
        elif typ == "discord" or "discord" in url:
            discord = url
        elif "doc" in label or "gitbook" in url or "/docs" in url:
            docs = url
        elif typ == "website" or label in {"website", "site", "web"}:
            website = url
        elif not website and url.startswith("http") and "dexscreener.com" not in url:
            website = url
    return {
        "chain": (raw.get("chainId") or "").lower(),
        "token_address": raw.get("tokenAddress") or "",
        "website": website,
        "twitter": twitter,
        "telegram": telegram,
        "discord": discord,
        "docs": docs,
        "description": raw.get("description"),
        "image_url": raw.get("icon"),
        "source": "dex-profile",
        "qualified": bool(website or twitter or telegram or discord),
    }


def pair_to_project(pair: dict[str, Any]) -> dict[str, Any]:
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    if (base.get("symbol") or "").upper() in SKIP_SYMBOLS and (quote.get("symbol") or "").upper() not in SKIP_SYMBOLS:
        base, quote = quote, base
    info = pair.get("info") or {}
    website = twitter = telegram = discord = docs = None
    for w in info.get("websites") or []:
        url = w.get("url") if isinstance(w, dict) else w
        if not url:
            continue
        low = str(url).lower()
        if "gitbook" in low or "/docs" in low:
            docs = url
        elif not website:
            website = url
    for s in info.get("socials") or []:
        if not isinstance(s, dict):
            continue
        platform = (s.get("type") or s.get("platform") or "").lower()
        url = s.get("url") or ""
        handle = (s.get("handle") or "").lstrip("@")
        if not url and handle:
            if platform in {"twitter", "x"}:
                url = f"https://x.com/{handle}"
            elif platform == "telegram":
                url = f"https://t.me/{handle}"
            elif platform == "discord":
                url = f"https://discord.gg/{handle}"
        if platform in {"twitter", "x"} or "x.com/" in url or "twitter.com/" in url:
            twitter = url or twitter
        elif platform == "telegram" or "t.me/" in url:
            telegram = url or telegram
        elif platform == "discord" or "discord" in url:
            discord = url or discord
    launched = pair.get("pairCreatedAt")
    if launched and launched > 10_000_000_000:
        launched = int(launched / 1000)
    liq = pair.get("liquidity") or {}
    vol = pair.get("volume") or {}
    return {
        "chain": (pair.get("chainId") or "").lower(),
        "token_address": base.get("address") or "",
        "pair_address": pair.get("pairAddress"),
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "dex": pair.get("dexId"),
        "launched_at": launched,
        "website": website,
        "twitter": twitter,
        "telegram": telegram,
        "discord": discord,
        "docs": docs,
        "liquidity_usd": to_float(liq.get("usd")),
        "volume_24h": to_float(vol.get("h24")),
        "market_cap": to_float(pair.get("marketCap")),
        "fdv": to_float(pair.get("fdv")),
        "price_usd": to_float(pair.get("priceUsd")),
        "image_url": info.get("imageUrl"),
        "source": "dex-pair",
        "qualified": bool(website or twitter or telegram or discord),
    }


def parse_gecko(payload: dict[str, Any]) -> list[dict[str, Any]]:
    included = {item["id"]: item for item in payload.get("included") or [] if "id" in item}
    out: list[dict[str, Any]] = []
    for pool in payload.get("data") or []:
        attrs = pool.get("attributes") or {}
        rel = pool.get("relationships") or {}
        network_id = ((rel.get("network") or {}).get("data") or {}).get("id") or ""
        chain = GECKO_CHAIN.get(network_id, network_id)
        base_ref = ((rel.get("base_token") or {}).get("data") or {}).get("id")
        quote_ref = ((rel.get("quote_token") or {}).get("data") or {}).get("id")
        dex_ref = ((rel.get("dex") or {}).get("data") or {}).get("id")
        base = (included.get(base_ref) or {}).get("attributes") or {}
        quote = (included.get(quote_ref) or {}).get("attributes") or {}
        dex = (included.get(dex_ref) or {}).get("attributes") or {}
        token = base
        if (base.get("symbol") or "").upper() in SKIP_SYMBOLS and (quote.get("symbol") or "").upper() not in SKIP_SYMBOLS:
            token = quote
        address = token.get("address") or ""
        if not address:
            continue
        launched = None
        created = attrs.get("pool_created_at")
        if created:
            try:
                launched = int(datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp())
            except ValueError:
                launched = None
        name = token.get("name") or (attrs.get("name") or "").split("/")[0].strip()
        out.append({
            "chain": chain,
            "token_address": address,
            "pair_address": attrs.get("address"),
            "name": name,
            "symbol": token.get("symbol"),
            "dex": dex.get("name") or dex_ref,
            "launched_at": launched,
            "liquidity_usd": to_float(attrs.get("reserve_in_usd")),
            "volume_24h": to_float(((attrs.get("volume_usd") or {}) or {}).get("h24")),
            "market_cap": to_float(attrs.get("market_cap_usd")),
            "fdv": to_float(attrs.get("fdv_usd")),
            "price_usd": to_float(attrs.get("base_token_price_usd")),
            "image_url": token.get("image_url"),
            "source": "gecko",
            "qualified": False,
        })
    return out


# ---------- reports ----------

def score_project(p: dict[str, Any]) -> dict[str, Any]:
    """Heuristic score for community / social opportunity — not a trade call."""
    score = 0
    roles: list[str] = []
    age_ts = p.get("launched_at") or p.get("discovered_at")
    age_h = ((time.time() - age_ts) / 3600) if age_ts else None
    liq = p.get("liquidity_usd") or 0
    vol = p.get("volume_24h") or 0
    desc = str(p.get("description") or "")
    socials = sum(1 for k in ("website", "twitter", "telegram", "discord", "docs") if p.get(k))

    if p.get("website"):
        score += 15
    if p.get("twitter"):
        score += 12
    if p.get("telegram"):
        score += 12
    if p.get("discord"):
        score += 8
    if p.get("docs"):
        score += 10
    else:
        if p.get("telegram") or p.get("discord"):
            score += 8
            roles.append("Content / FAQ")
    if len(desc) > 40:
        score += 12
    if age_h is not None:
        if age_h < 12:
            score += 14
        elif age_h < 48:
            score += 10
        elif age_h < 168:
            score += 4
    if liq >= 25_000:
        score += 10
    elif liq >= 8_000:
        score += 6
    elif liq and liq < 1500:
        score -= 8
    if vol >= 50_000:
        score += 6
    if p.get("twitter") and not p.get("telegram"):
        roles.append("Community setup")
        score += 6
    if p.get("telegram") and p.get("twitter"):
        roles.append("Community / Social")
    if socials >= 3 and age_h is not None and age_h < 72:
        roles.append("Early community voice")
    if not roles and socials >= 2:
        roles.append("Research then engage")

    score = max(0, min(100, score))
    if score >= 70:
        band = "HIGH"
    elif score >= 45:
        band = "MEDIUM"
    else:
        band = "LOW"
    action = "Join chat → ask one product question → watch how the team replies"
    if "Content / FAQ" in roles:
        action = "Read the chat gaps → draft 3 FAQs in your notes → then join usefully"
    if "Community setup" in roles:
        action = "Check X first → see if they need a Telegram/Discord home"
    return {
        "score": score,
        "band": band,
        "roles": roles[:3] or ["Watch only"],
        "action": action,
    }


def opportunity_signals(p: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    age_ts = p.get("launched_at") or p.get("discovered_at")
    age_h = ((time.time() - age_ts) / 3600) if age_ts else None
    socials = sum(1 for k in ("website", "twitter", "telegram", "discord", "docs") if p.get(k))
    if age_h is not None and age_h < 24:
        signals.append("Recently launched — early window to show up as a useful community voice")
    if socials >= 3:
        signals.append("Public-facing project. Teams like this often need community help")
    if p.get("telegram") and not p.get("docs"):
        signals.append("Has a chat but thin docs — FAQ / content work is a common gap")
    if p.get("twitter") and not p.get("telegram"):
        signals.append("On X but no obvious Telegram — community setup may be missing")
    if (p.get("liquidity_usd") or 0) >= 10_000 and socials >= 2:
        signals.append("Enough liquidity + presence that this is more than a throwaway mint")
    if p.get("description") and len(str(p.get("description"))) > 40:
        signals.append("Team wrote a real description — treat as a product, not only a ticker")
    if not signals:
        signals.append("Qualified by public links. Investigate before spending time.")
    return signals[:5]


def observations_for(p: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if not p.get("website"):
        notes.append("No official website indexed yet")
    if not p.get("twitter"):
        notes.append("No X account linked")
    if not p.get("telegram") and not p.get("discord"):
        notes.append("No public community chat indexed")
    if not p.get("docs"):
        notes.append("No docs page found")
    if not p.get("description"):
        notes.append("No project description on the token profile")
    if (p.get("liquidity_usd") or 0) and p.get("liquidity_usd") < 3000:
        notes.append("Very thin liquidity — treat market numbers as noisy")
    if not notes:
        notes.append("Profile looks more complete than average new launch")
    return notes[:6]


def list_item(index: int, p: dict[str, Any]) -> str:
    s = score_project(p)
    return (
        f"<b>{index}.</b> {esc(title_of(p))} · {s['band']} {s['score']}\n"
        f"⛓ {esc((p.get('chain') or '?').title())} · 🕒 {esc(ago(p.get('launched_at') or p.get('discovered_at')))}\n"
        f"💧 {esc(money(p.get('liquidity_usd')))} · 📊 {esc(money(p.get('volume_24h')))}\n"
        f"🌐 {mark(p.get('website'))}  𝕏 {mark(p.get('twitter'))}  "
        f"💬 {mark(p.get('telegram'))}  📚 {mark(p.get('docs'))}\n"
        f"💼 {esc(', '.join(s['roles']))}"
    )


def list_keyboard(projects: list[dict[str, Any]], since_ts: int, offset: int, total: int) -> InlineKeyboardMarkup:
    page = 5
    rows = [[InlineKeyboardButton(f"🔍 {title_of(p)[:28]}", callback_data=f"inv:{p['id']}")] for p in projects]
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(InlineKeyboardButton("← Back", callback_data=f"nt:{since_ts}:{max(offset - page, 0)}"))
    if offset + page < total:
        nav.append(InlineKeyboardButton("Next →", callback_data=f"nt:{since_ts}:{offset + page}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def report_text(p: dict[str, Any]) -> str:
    what = p.get("description") or "No public description indexed yet."
    s = score_project(p)
    lines = [
        "🧠 <b>PROJECT INTELLIGENCE</b>",
        f"<b>{esc(title_of(p))}</b>",
        f"⛓ {(p.get('chain') or '?').title()}",
        f"🕒 {esc(ago(p.get('launched_at') or p.get('discovered_at')))}",
        f"🎯 Opportunity: <b>{s['band']}</b> {s['score']}/100",
        f"🏷 CA: <code>{esc(p.get('token_address') or '')}</code>",
        "",
        "🎯 <b>WHAT THEY BUILD</b>",
        esc(what)[:700],
        "",
        "🌐 <b>PRESENCE</b>",
        f"Website: {esc(p.get('website') or '—')}",
        f"X: {esc(p.get('twitter') or '—')}",
        f"Telegram: {esc(p.get('telegram') or '—')}",
        f"Discord: {esc(p.get('discord') or '—')}",
        f"Docs: {esc(p.get('docs') or '—')}",
        "",
        "📊 <b>MARKET</b>",
        f"Liquidity: {esc(money(p.get('liquidity_usd')))}",
        f"Volume 24h: {esc(money(p.get('volume_24h')))}",
        f"FDV: {esc(money(p.get('fdv')))}",
        f"DEX: {esc(p.get('dex') or '—')}",
        "",
        "⚠️ <b>OBSERVATIONS</b>",
    ]
    for item in observations_for(p):
        lines.append(f"• {esc(item)}")
    lines += ["", "💼 <b>POTENTIAL OPPORTUNITY</b>", f"Roles: {esc(', '.join(s['roles']))}"]
    for item in opportunity_signals(p):
        lines.append(f"• {esc(item)}")
    lines += ["", "👉 <b>NEXT MOVE</b>", esc(s["action"])]
    return "\n".join(lines)


def report_keyboard(p: dict[str, Any]) -> InlineKeyboardMarkup:
    pid = p["id"]
    rows: list[list[InlineKeyboardButton]] = [[
        InlineKeyboardButton("⭐ Watch", callback_data=f"w:{pid}"),
        InlineKeyboardButton("🔄 Refresh", callback_data=f"inv:{pid}"),
    ]]
    links: list[InlineKeyboardButton] = []
    if p.get("website"):
        links.append(InlineKeyboardButton("Website", url=p["website"]))
    if p.get("twitter"):
        links.append(InlineKeyboardButton("X", url=p["twitter"]))
    if links:
        rows.append(links)
    more: list[InlineKeyboardButton] = []
    if p.get("telegram"):
        more.append(InlineKeyboardButton("Telegram", url=p["telegram"]))
    if p.get("discord"):
        more.append(InlineKeyboardButton("Discord", url=p["discord"]))
    if more:
        rows.append(more)
    chain, addr = p.get("chain") or "", p.get("token_address") or ""
    if chain and addr:
        rows.append([InlineKeyboardButton("DexScreener", url=f"https://dexscreener.com/{chain}/{addr}")])
    return InlineKeyboardMarkup(rows)


def alert_text(p: dict[str, Any]) -> str:
    s = score_project(p)
    return (
        "🚨 <b>NEW PROJECT DETECTED</b>\n"
        f"🪙 {esc(title_of(p))}\n"
        f"🎯 {s['band']} {s['score']}/100 · {esc(', '.join(s['roles']))}\n"
        f"⛓ {esc((p.get('chain') or '?').title())}\n"
        f"🕒 {esc(ago(p.get('launched_at') or p.get('discovered_at')))}\n"
        f"🌐 {mark(p.get('website'))}  𝕏 {mark(p.get('twitter'))}  "
        f"💬 {mark(p.get('telegram'))}  📚 {mark(p.get('docs'))}\n"
        f"💧 {esc(money(p.get('liquidity_usd')))} · 📊 {esc(money(p.get('volume_24h')))}"
    )


# ---------- app helpers ----------

def allowed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    owners: list[int] = context.application.bot_data.get("allowed") or []
    if not owners:
        return True
    return user_id in owners


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user or not allowed(user.id, context):
        if update.effective_message:
            await update.effective_message.reply_text("This Scout is private.")
        return False
    return True


def deps(context: ContextTypes.DEFAULT_TYPE) -> tuple[DB, httpx.AsyncClient]:
    return context.application.bot_data["db"], context.application.bot_data["http"]


async def claim_owner_if_needed(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    db: DB = context.application.bot_data["db"]
    configured = context.application.bot_data.get("allowed") or []
    if configured:
        return
    existing = await db.get_meta("owner_id")
    if existing:
        context.application.bot_data["allowed"] = [int(existing)]
        return
    await db.set_meta("owner_id", str(user_id))
    context.application.bot_data["allowed"] = [user_id]
    log.info("Owner locked to Telegram user %s", user_id)


async def enrich_one(db: DB, client: httpx.AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    chain, addr = project["chain"], project["token_address"]
    data = await http_get(client, f"{DEX_API}/token-pairs/v1/{chain}/{addr}")
    pairs = data if isinstance(data, list) else []
    if not pairs:
        await db.mark_enriched(project["id"])
        return project
    best = max(pairs, key=lambda p: float(((p.get("liquidity") or {}) or {}).get("usd") or 0))
    extra = pair_to_project(best)
    extra["chain"] = chain
    extra["token_address"] = addr
    extra["last_enriched_at"] = now()
    await db.upsert(extra)
    return await db.by_id(project["id"]) or project


async def resolve_project(db: DB, client: httpx.AsyncClient, query: str) -> dict[str, Any] | None:
    query = query.strip()
    chain = None
    addr = query
    if ":" in query and not query.startswith("http"):
        chain, addr = query.split(":", 1)
        chain, addr = chain.lower().strip(), addr.strip()
    if chain:
        found = await db.by_token(chain, addr)
        if found:
            return await enrich_one(db, client, found)
        data = await http_get(client, f"{DEX_API}/tokens/v1/{chain}/{addr}")
        pairs = data if isinstance(data, list) else []
        if pairs:
            proj = pair_to_project(pairs[0])
            pid, _ = await db.upsert(proj)
            row = await db.by_id(pid)
            return row
    data = await http_get(client, f"{DEX_API}/latest/dex/search", params={"q": query})
    pairs = (data or {}).get("pairs") if isinstance(data, dict) else []
    if not pairs:
        return None
    proj = pair_to_project(pairs[0])
    if not proj.get("token_address"):
        return None
    pid, _ = await db.upsert(proj)
    return await db.by_id(pid)


# ---------- handlers ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_message:
        return
    await claim_owner_if_needed(context, update.effective_user.id)
    if not await gate(update, context):
        return
    db, _ = deps(context)
    await db.ensure_user(update.effective_user.id)
    await update.effective_message.reply_html(
        "🔎 <b>Web3 Project Scout is online.</b>\n\n"
        "I collect new public-facing projects in the background.\n"
        "Use /newtokens 12h to see what you missed while offline.\n"
        "Use /help for commands."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    await update.effective_message.reply_html(HELP)


async def render_page(update: Update, context: ContextTypes.DEFAULT_TYPE, since_ts: int, offset: int, label: str, edit: bool) -> None:
    db, _ = deps(context)
    total = await db.count_since(since_ts)
    rows = await db.list_since(since_ts, limit=5, offset=offset)
    header = (
        "🔎 <b>WEB3 PROJECT SCOUT</b>\n"
        f"Window: {esc(label)} · since {esc(ago(since_ts))}\n"
        f"Qualified projects: <b>{total}</b>\n"
        "Only projects with a website or socials."
    )
    if not rows:
        text = header + "\n\nNothing in this window yet. Leave the bot running for a few minutes."
        markup = None
    else:
        text = header + "\n\n" + "\n\n".join(list_item(offset + i + 1, p) for i, p in enumerate(rows))
        markup = list_keyboard(rows, since_ts, offset, total)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup
        )
    elif update.effective_message:
        await update.effective_message.reply_html(text, disable_web_page_preview=True, reply_markup=markup)


async def cmd_newtokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_user:
        return
    db, _ = deps(context)
    user = await db.ensure_user(update.effective_user.id)
    since_ts, label = parse_lookback(context.args[0] if context.args else "", user.get("last_check_at"))
    await render_page(update, context, since_ts, 0, label, edit=False)
    await db.set_last_check(update.effective_user.id)


async def cb_newtokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.callback_query or not update.callback_query.data:
        return
    await update.callback_query.answer()
    _, since_s, offset_s = update.callback_query.data.split(":")
    since_ts, offset = int(since_s), int(offset_s)
    await render_page(update, context, since_ts, offset, ago(since_ts), edit=True)


async def cmd_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    query = " ".join(context.args or []).strip()
    if not query:
        await update.effective_message.reply_text("Usage: /project solana:Address")
        return
    db, client = deps(context)
    project = await resolve_project(db, client, query)
    if not project:
        await update.effective_message.reply_text("Could not find that project. Try chain:address.")
        return
    await update.effective_message.reply_html(
        report_text(project), disable_web_page_preview=True, reply_markup=report_keyboard(project)
    )


async def cb_investigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.callback_query or not update.callback_query.data:
        return
    await update.callback_query.answer("Researching…")
    pid = int(update.callback_query.data.split(":")[1])
    db, client = deps(context)
    project = await db.by_id(pid)
    if not project:
        await update.callback_query.edit_message_text("Project no longer in the database.")
        return
    project = await enrich_one(db, client, project)
    await update.callback_query.edit_message_text(
        report_text(project),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=report_keyboard(project),
    )


async def cb_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.callback_query or not update.effective_user:
        return
    pid = int(update.callback_query.data.split(":")[1])
    db, _ = deps(context)
    await db.watch(update.effective_user.id, pid)
    await update.callback_query.answer("Saved to /watchlist")


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message or not update.effective_user:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /watch <id>")
        return
    db, _ = deps(context)
    await db.watch(update.effective_user.id, int(context.args[0]))
    await update.effective_message.reply_text("Watching. See /watchlist")


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message or not update.effective_user:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /unwatch <id>")
        return
    db, _ = deps(context)
    await db.unwatch(update.effective_user.id, int(context.args[0]))
    await update.effective_message.reply_text("Removed.")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message or not update.effective_user:
        return
    db, _ = deps(context)
    rows = await db.watchlist(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text("Watchlist empty. Tap ⭐ Watch on a report.")
        return
    lines = ["⭐ <b>WATCHLIST</b>"] + [
        f"#{p['id']} {esc(title_of(p))} · {esc((p.get('chain') or '').title())}" for p in rows
    ]
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message or not update.effective_user:
        return
    arg = (context.args[0].lower() if context.args else "")
    if arg not in {"on", "off"}:
        await update.effective_message.reply_text("Usage: /alerts on   or   /alerts off")
        return
    db, _ = deps(context)
    await db.set_alerts(update.effective_user.id, arg == "on")
    await update.effective_message.reply_text(f"Alerts {arg}.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    db, _ = deps(context)
    stats = await db.stats()
    owner = context.application.bot_data.get("allowed") or []
    await update.effective_message.reply_html(
        "📡 <b>Scout status</b>\n"
        f"Projects stored: {stats['total']}\n"
        f"Qualified: {stats['qualified']}\n"
        f"Seen last 24h: {stats['last_24h']}\n"
        f"Owner id: {owner[0] if owner else 'will lock on /start'}"
    )


async def ranked_window(db: DB, since_ts: int, min_score: int = 45) -> list[dict[str, Any]]:
    rows = await db.qualified_since(since_ts)
    ranked = []
    for p in rows:
        s = score_project(p)
        if s["score"] >= min_score:
            ranked.append((s["score"], p))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [p for _, p in ranked]


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    db, _ = deps(context)
    arg = context.args[0] if context.args else "24h"
    since_ts, label = parse_lookback(arg, now() - 24 * 3600)
    rows = await ranked_window(db, since_ts, min_score=45)
    top = rows[:8]
    if not top:
        await update.effective_message.reply_text("No medium/high opportunities in that window yet.")
        return
    header = f"💼 <b>OPPORTUNITY SHORTLIST</b>\nWindow: {esc(label)} · {len(rows)} scored 45+\n"
    text = header + "\n" + "\n\n".join(list_item(i + 1, p) for i, p in enumerate(top))
    markup = list_keyboard(top, since_ts, 0, len(top))
    await update.effective_message.reply_html(text, disable_web_page_preview=True, reply_markup=markup)


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    db, _ = deps(context)
    arg = context.args[0] if context.args else "12h"
    since_ts, label = parse_lookback(arg, now() - 12 * 3600)
    rows = await ranked_window(db, since_ts, min_score=40)
    top = rows[:5]
    if not top:
        await update.effective_message.reply_text("Digest is empty for that window.")
        return
    lines = [f"☀️ <b>DIGEST</b> · {esc(label)}", "Pick 2. Ignore the rest today.", ""]
    for i, p in enumerate(top, 1):
        s = score_project(p)
        lines.append(
            f"{i}. <b>{esc(title_of(p))}</b> · {s['score']}\n"
            f"{esc((p.get('chain') or '').title())} · {esc(', '.join(s['roles']))}\n"
            f"{esc(s['action'])}"
        )
        lines.append("")
    markup = list_keyboard(top, since_ts, 0, len(top))
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True, reply_markup=markup)


# ---------- background loops ----------

async def discovery_once(app: Application) -> None:
    db: DB = app.bot_data["db"]
    client: httpx.AsyncClient = app.bot_data["http"]

    profiles = await http_get(client, f"{DEX_API}/token-profiles/latest/v1")
    if isinstance(profiles, list):
        for raw in profiles:
            parsed = parse_profile(raw)
            if parsed.get("chain") in DEFAULT_CHAINS and parsed.get("token_address"):
                await db.upsert(parsed)

    gecko = await http_get(
        client,
        f"{GECKO_API}/networks/new_pools",
        params={"page": 1, "include": "base_token,quote_token,network,dex"},
        headers={"Accept": "application/json"},
    )
    if isinstance(gecko, dict):
        for parsed in parse_gecko(gecko):
            if parsed.get("chain") in DEFAULT_CHAINS:
                await db.upsert(parsed)

    rows = await db.unenriched(12)
    by_chain: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_chain.setdefault(row["chain"], []).append(row)
    for chain, items in by_chain.items():
        addrs = ",".join(i["token_address"] for i in items[:30])
        data = await http_get(client, f"{DEX_API}/tokens/v1/{chain}/{addrs}")
        pairs = data if isinstance(data, list) else []
        best: dict[str, dict[str, Any]] = {}
        for pair in pairs:
            proj = pair_to_project(pair)
            key = (proj.get("token_address") or "").lower()
            if not key:
                continue
            prev = best.get(key)
            if not prev or (proj.get("liquidity_usd") or 0) >= (prev.get("liquidity_usd") or 0):
                best[key] = proj
        for item in items:
            extra = best.get(item["token_address"].lower())
            if extra:
                extra["chain"] = chain
                extra["token_address"] = item["token_address"]
                extra["last_enriched_at"] = now()
                await db.upsert(extra)
            else:
                await db.mark_enriched(item["id"])

    await send_alerts(app)


async def send_alerts(app: Application) -> None:
    db: DB = app.bot_data["db"]
    owners: list[int] = app.bot_data.get("allowed") or []
    if not owners:
        raw = await db.get_meta("owner_id")
        if raw:
            owners = [int(raw)]
            app.bot_data["allowed"] = owners
    for user_id in owners:
        user = await db.ensure_user(user_id)
        if not user.get("alerts_enabled", 1):
            continue
        for project in await db.alert_candidates(user_id):
            if score_project(project)["score"] < 45:
                await db.mark_alerted(user_id, project["id"])
                continue
            try:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=alert_text(project),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=report_keyboard(project),
                )
                await db.mark_alerted(user_id, project["id"])
            except Exception as exc:
                log.warning("alert failed for %s: %s", user_id, exc)


async def discovery_loop(app: Application) -> None:
    await asyncio.sleep(8)
    while True:
        try:
            await discovery_once(app)
            log.info("discovery cycle ok")
        except Exception:
            log.exception("discovery cycle failed")
        await asyncio.sleep(int(os.getenv("DISCOVERY_INTERVAL_SEC", "120")))


async def on_start(app: Application) -> None:
    db: DB = app.bot_data["db"]
    await db.connect()
    app.bot_data["http"] = httpx.AsyncClient(headers={"User-Agent": "Web3ProjectScout/1.0"}, timeout=25)
    owner = await db.get_meta("owner_id")
    if owner and not app.bot_data.get("allowed"):
        app.bot_data["allowed"] = [int(owner)]
    app.create_task(discovery_loop(app))
    me = await app.bot.get_me()
    log.info("Logged in as @%s", me.username)


async def on_stop(app: Application) -> None:
    http = app.bot_data.get("http")
    if http:
        await http.aclose()
    db = app.bot_data.get("db")
    if db:
        await db.close()


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN. That is the only required variable.")
    allowed_ids = env_ints("ALLOWED_USER_IDS") or env_ints("ALERT_USER_IDS")
    db = DB(pick_db_path())
    app = (
        Application.builder()
        .token(token)
        .post_init(on_start)
        .post_shutdown(on_stop)
        .build()
    )
    app.bot_data["db"] = db
    app.bot_data["allowed"] = allowed_ids
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("newtokens", cmd_newtokens))
    app.add_handler(CommandHandler("project", cmd_project))
    app.add_handler(CommandHandler("research", cmd_project))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(cb_newtokens, pattern=r"^nt:"))
    app.add_handler(CallbackQueryHandler(cb_investigate, pattern=r"^inv:"))
    app.add_handler(CallbackQueryHandler(cb_watch, pattern=r"^w:"))
    log.info("Polling Telegram…")
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
