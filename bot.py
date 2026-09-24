"""Web3 Project Scout — single-file Telegram bot (2026-09-24 persona + risk overhaul)."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import aiosqlite
import httpx
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    TypeHandler,
)

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
    "polygon": "polygon", "avax": "avalanche", "optimism": "optimism",
    "sui-network": "sui", "sui": "sui", "ton": "ton", "abstract": "abstract",
    "hyperevm": "hyperevm", "hyperliquid": "hyperevm", "monad": "monad",
    "ink": "ink", "robinhood": "robinhood", "robinhood-chain": "robinhood",
    "arc": "arc", "sonic": "sonic", "blast": "blast", "linea": "linea",
    "berachain": "berachain", "unichain": "unichain", "cronos": "cronos",
    "pulsechain": "pulsechain", "pulse": "pulsechain", "xrpl": "xrpl",
    "xrp": "xrpl", "aptos": "aptos", "near": "near", "fantom": "fantom",
    "scroll": "scroll", "zksync": "zksync", "mantle": "mantle",
}

CMC_CHAIN_MAP = {
    "ethereum": "ethereum",
    "bnb": "bsc",
    "bnb smart chain (bep20)": "bsc",
    "binance smart chain": "bsc",
    "base": "base",
    "solana": "solana",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "avalanche": "avalanche",
    "arc": "arc",
    "arc-token": "arc",
    "robinhood chain": "robinhood",
    "robinhood-placeholder": "robinhood",
    "robinhood": "robinhood",
    "sui": "sui",
    "ton": "ton",
    "cronos": "cronos",
    "pulsechain": "pulsechain",
    "ink": "ink",
    "abstract": "abstract",
    "hyperevm": "hyperevm",
    "blast": "blast",
    "linea": "linea",
    "scroll": "scroll",
    "zksync": "zksync",
    "mantle": "mantle",
    "sonic": "sonic",
}

DEFAULT_CHAINS = {
    "solana", "base", "ethereum", "bsc", "abstract", "robinhood", "arc",
    "hyperevm", "sui", "arbitrum", "ink", "monad", "polygon", "ton",
    "cronos", "pulsechain", "xrpl", "optimism", "avalanche", "blast",
    "linea", "scroll", "zksync", "mantle", "sonic", "berachain",
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

SCOUT_BUILD = "2026-09-24-persona-risk-overhaul-v2"

HELP = """🔎 <b>Web3 Project Scout</b>

Finds early public-facing projects and stores them while you sleep.

/newtokens [6h|12h|24h|3d] — catch-up list
/project &lt;CA or chain:CA&gt; — full report
/jobs — opportunity shortlist
/digest 12h — morning top picks
/persona &lt;id&gt; — generate post-ready messages in chosen voice
/approach &lt;id&gt; — choose persona (Investor / Curious / Question / Bullish / Holder)
/ask &lt;id&gt; &lt;question&gt; — persona replies to a TG question
/gaps &lt;id&gt; — researched product / TG / X gap analysis
/risks &lt;id&gt; — researched risk brief + fixes
/watch &lt;id or CA&gt; — monitor until socials / liquidity / activity appear
/unwatch &lt;id&gt;
/watchlist — your monitored projects
/cg — CoinGecko trending
/cmc — CMC new listings
/early — social-first / thin liquidity
/menu — command menu (inserts into chat)
/alerts on|off
/status

Watch alerts:
📡 socials appeared · 💧 liquidity jump · 📊 volume spike · 📝 profile changed

Labels: 🔧 Utility · 🐸 Meme · ⚖️ Mixed
"""

KEY_STATUS: dict[str, str] = {
    "gemini": "not set", "x": "not set", "xai": "not set",
    "groq": "not set", "openrouter": "not set", "llm_error": "",
}

PERSONAS = {
    "investor": {
        "label": "💰 Investor",
        "desc": "Actual crypto/Web3 investor who is either holding or looking to size in. Speak with confidence, conviction, financial interest, long-term thesis, scaling potential, and backing the team.",
    },
    "curious": {
        "label": "👀 Curious Community",
        "desc": "Curious community member reacting to recent news, tweets, website updates or Telegram discussions. Share observations, express genuine intrigue, ask natural follow-up thoughts.",
    },
    "question": {
        "label": "❓ Question / Inquirer",
        "desc": "Ask deep, specific questions about aspects NOT clearly answered on the website or docs (tokenomics edge-cases, mainnet timelines, tech trade-offs, partnerships, product details).",
    },
    "bullish": {
        "label": "🚀 Bullish Community",
        "desc": "Bullish community member who genuinely likes the project. Positive energy, culture fit, belief in the vision, without sounding like a paid shill or AI.",
    },
    "holder": {
        "label": "💎 Holder",
        "desc": "Someone who already holds the token. Speaks from skin-in-the-game perspective — updates, clarity needs, community health, product progress.",
    },
    "strategist": {
        "label": "🧠 Strategist",
        "desc": "Thoughtful operator offering useful, non-generic suggestions on community, content, or product clarity. Helpful without being pushy.",
    },
}


def env_secret(*names: str) -> str:
    for name in names:
        raw = os.getenv(name)
        if not raw:
            continue
        raw = raw.strip().strip('"').strip("'")
        raw = unquote(raw)
        if raw:
            return raw
    return ""


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
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()
        await self._migrate()

    async def _migrate(self) -> None:
        cur = await self.c.execute("PRAGMA table_info(projects)")
        cols = {row[1] for row in await cur.fetchall()}
        if "community_json" not in cols:
            await self.c.execute("ALTER TABLE projects ADD COLUMN community_json TEXT")
        await self.c.execute(
            """
            CREATE TABLE IF NOT EXISTS social_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                url TEXT,
                seen_at INTEGER NOT NULL,
                alerted INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await self.c.commit()
        await self.c.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_snapshots (
                user_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                snapshot_json TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, project_id)
            )
            """
        )
        await self.c.commit()

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
            if not old.get("qualified") and merged.get("qualified"):
                await self.set_meta("last_qualified_at", str(now()))
            await self._record_new_socials(int(old["id"]), old, merged)
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
        pid = int(cur.lastrowid)
        if data.get("qualified"):
            await self.set_meta("last_qualified_at", str(now()))
        return pid, True

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

    async def get_snapshot(self, user_id: int, pid: int) -> dict[str, Any] | None:
        cur = await self.c.execute(
            "SELECT snapshot_json FROM watch_snapshots WHERE user_id=? AND project_id=?",
            (user_id, pid),
        )
        row = await cur.fetchone()
        if not row or not row["snapshot_json"]:
            return None
        try:
            return json.loads(row["snapshot_json"])
        except Exception:
            return None

    async def save_snapshot(self, user_id: int, pid: int, snap: dict[str, Any]) -> None:
        await self.c.execute(
            """
            INSERT INTO watch_snapshots(user_id, project_id, snapshot_json, updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(user_id, project_id) DO UPDATE SET
                snapshot_json=excluded.snapshot_json,
                updated_at=excluded.updated_at
            """,
            (user_id, pid, json.dumps(snap), now()),
        )
        await self.c.commit()

    async def all_watches(self) -> list[dict[str, Any]]:
        cur = await self.c.execute(
            """
            SELECT w.user_id, w.project_id, p.*
            FROM watches w
            JOIN projects p ON p.id = w.project_id
            """
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
            (now() - 48 * 3600, user_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def _record_new_socials(self, pid: int, old: dict[str, Any], new: dict[str, Any]) -> None:
        mapping = (("website", "website"), ("twitter", "x"), ("telegram", "telegram"), ("discord", "discord"))
        for field, kind in mapping:
            if new.get(field) and not old.get(field):
                await self.c.execute(
                    "INSERT INTO social_events(project_id, kind, url, seen_at, alerted) VALUES(?,?,?,?,0)",
                    (pid, kind, new.get(field), now()),
                )
        await self.c.commit()

    async def pending_social_events(self, limit: int = 8) -> list[dict[str, Any]]:
        cur = await self.c.execute(
            """
            SELECT e.*, p.name, p.symbol, p.chain, p.token_address, p.website, p.twitter,
                   p.telegram, p.discord, p.docs, p.discovered_at, p.launched_at,
                   p.liquidity_usd, p.volume_24h, p.description, p.id AS project_pk
            FROM social_events e
            JOIN projects p ON p.id = e.project_id
            WHERE e.alerted=0 AND e.kind IN ('x','telegram','website')
            ORDER BY e.seen_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def mark_social_alerted(self, event_id: int) -> None:
        await self.c.execute("UPDATE social_events SET alerted=1 WHERE id=?", (event_id,))
        await self.c.commit()

    async def save_community(self, pid: int, payload: dict[str, Any]) -> None:
        await self.c.execute(
            "UPDATE projects SET community_json=? WHERE id=?",
            (json.dumps(payload), pid),
        )
        patch: dict[str, Any] = {}
        if payload.get("found_website"):
            patch["website"] = payload["found_website"]
        if payload.get("found_twitter"):
            patch["twitter"] = payload["found_twitter"]
        if payload.get("found_telegram"):
            patch["telegram"] = payload["found_telegram"]
        if payload.get("found_discord"):
            patch["discord"] = payload["found_discord"]
        if payload.get("found_docs"):
            patch["docs"] = payload["found_docs"]
        if patch:
            cur = await self.c.execute("SELECT * FROM projects WHERE id=?", (pid,))
            old = await cur.fetchone()
            if old:
                old_d = dict(old)
                sets = []
                args: list[Any] = []
                for k, v in patch.items():
                    if not old_d.get(k) and v:
                        sets.append(f"{k}=?")
                        args.append(v)
                if sets:
                    args.append(pid)
                    await self.c.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", args)
                    fresh = dict(old_d)
                    fresh.update(patch)
                    if is_qualified(fresh) and not old_d.get("qualified"):
                        await self.c.execute("UPDATE projects SET qualified=1 WHERE id=?", (pid,))
                        await self.set_meta("last_qualified_at", str(now()))
                    await self._record_new_socials(pid, old_d, fresh)
        await self.c.commit()

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


def community(p: dict[str, Any]) -> dict[str, Any]:
    raw = p.get("community_json")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def tg_username(url: str | None) -> str | None:
    if not url:
        return None
    low = url.lower()
    if "joinchat" in low or "/+" in url:
        return None
    match = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)", url, re.I)
    if not match:
        return None
    name = match.group(1)
    if name.lower() in {"share", "socks", "proxy", "addstickers", "iv"}:
        return None
    return name


def discord_invite(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"(?:discord\.gg|discord\.com/invite)/([A-Za-z0-9-]+)", url, re.I)
    return match.group(1) if match else None


def x_handle(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)", url, re.I)
    if not match:
        return None
    name = match.group(1)
    if name.lower() in {"intent", "share", "i", "home", "search"}:
        return None
    return name


def extract_meta(html_text: str) -> tuple[str | None, str | None]:
    title = None
    desc = None
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:160]
    m = re.search(
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
        html_text,
        re.I | re.S,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:name|property)=["\'](?:description|og:description)["\']',
            html_text,
            re.I | re.S,
        )
    if m:
        desc = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()[:280]
    return title, desc


def links_from_html(html_text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for url in re.findall(r"https?://[^\s\"'<>]+", html_text, re.I):
        clean = url.rstrip(").,]")
        low = clean.lower()
        if ("t.me/" in low or "telegram.me/" in low) and "found_telegram" not in found:
            if "share" not in low and "socks" not in low:
                found["found_telegram"] = clean
        elif ("x.com/" in low or "twitter.com/" in low) and "found_twitter" not in found:
            if not any(x in low for x in ("/intent", "/share", "/home", "/search")):
                found["found_twitter"] = clean
        elif ("discord.gg/" in low or "discord.com/invite/" in low) and "found_discord" not in found:
            found["found_discord"] = clean
        elif ("gitbook" in low or "/docs" in low) and "found_docs" not in found:
            found["found_docs"] = clean
    return found


async def fetch_website_meta(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    if not url or not url.startswith("http"):
        return {}
    try:
        resp = await client.get(url, timeout=12, follow_redirects=True)
        if resp.status_code >= 400 or not resp.text:
            return {}
        blob = resp.text[:80_000]
        title, desc = extract_meta(blob)
        out: dict[str, Any] = {}
        if title:
            out["site_title"] = title
        if desc:
            out["site_about"] = desc
        out.update(links_from_html(blob))
        return out
    except Exception as exc:
        log.warning("website meta failed %s: %s", url, exc)
        return {}


async def fetch_discord(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    code = discord_invite(url)
    if not code:
        return {}
    data = await http_get(client, f"https://discord.com/api/v9/invites/{code}", params={"with_counts": "true"})
    if not isinstance(data, dict) or not data.get("guild"):
        return {}
    guild = data.get("guild") or {}
    return {
        "dc_name": guild.get("name"),
        "dc_members": data.get("approximate_member_count"),
        "dc_online": data.get("approximate_presence_count"),
    }


async def fetch_telegram(bot, url: str) -> dict[str, Any]:
    username = tg_username(url)
    if not username or bot is None:
        return {}
    try:
        chat = await bot.get_chat(f"@{username}")
        members = None
        try:
            members = await bot.get_chat_member_count(chat.id)
        except Exception:
            members = None
        return {
            "tg_title": getattr(chat, "title", None) or getattr(chat, "full_name", None),
            "tg_about": getattr(chat, "description", None) or getattr(chat, "bio", None),
            "tg_members": members,
            "tg_type": getattr(chat, "type", None),
        }
    except Exception as exc:
        log.warning("telegram chat lookup failed @%s: %s", username, exc)
        return {}


async def fetch_x_tweets(client: httpx.AsyncClient, handle: str) -> list[dict[str, Any]]:
    token = env_secret("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN")
    if not token or not handle:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    try:
        user = await client.get(
            f"https://api.x.com/2/users/by/username/{handle}",
            headers=headers,
            timeout=15,
        )
        if user.status_code >= 400:
            return []
        uid = (user.json().get("data") or {}).get("id")
        if not uid:
            return []
        tw = await client.get(
            f"https://api.x.com/2/users/{uid}/tweets",
            headers=headers,
            params={"max_results": 5, "tweet.fields": "created_at,text"},
            timeout=15,
        )
        if tw.status_code >= 400:
            return []
        return [
            {"id": t.get("id"), "text": t.get("text"), "created_at": t.get("created_at")}
            for t in (tw.json().get("data") or [])
        ]
    except Exception as exc:
        log.warning("X tweets failed @%s: %s", handle, exc)
        return []


async def search_early_x(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    token = env_secret("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN")
    if not token:
        KEY_STATUS["x"] = "missing"
        return []
    KEY_STATUS["x"] = "present"
    query = (
        '("we just launched" OR "testnet is live" OR "join our telegram" OR "docs are live" '
        'OR "building in public") (web3 OR crypto OR L2 OR "ai agent") -is:retweet -is:reply lang:en'
    )
    try:
        resp = await client.get(
            "https://api.x.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "query": query,
                "max_results": 10,
                "tweet.fields": "created_at,author_id,text",
                "expansions": "author_id",
                "user.fields": "username,name,description",
            },
            timeout=20,
        )
        if resp.status_code >= 400:
            KEY_STATUS["x"] = f"http {resp.status_code}"
            log.warning("X early search %s: %s", resp.status_code, resp.text[:200])
            return []
        data = resp.json()
        users = {u["id"]: u for u in (data.get("includes") or {}).get("users") or []}
        out = []
        for t in data.get("data") or []:
            u = users.get(t.get("author_id") or "") or {}
            out.append({
                "tweet_id": t.get("id"),
                "text": t.get("text"),
                "created_at": t.get("created_at"),
                "username": u.get("username"),
                "name": u.get("name"),
                "bio": u.get("description"),
            })
        return out
    except Exception as exc:
        log.warning("X early search failed: %s", exc)
        return []


async def llm_write(prompt: str) -> str | None:
    """Try free providers first (Groq → OpenRouter → Gemini → xAI → OpenAI)."""
    groq = env_secret("GROQ_API_KEY")
    openrouter = env_secret("OPENROUTER_API_KEY", "OPENROUTER_KEY")
    gemini = env_secret("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY")
    xai = env_secret("XAI_API_KEY")
    oai = env_secret("OPENAI_API_KEY")
    system = (
        "You write short, human crypto community comments and analysis. "
        "No GM bullish. No investment advice. No fake claims. "
        "Sound like a real person who actually read the project. "
        "Never sound like an AI responding to a prompt."
    )

    if groq:
        KEY_STATUS["groq"] = "present"
        models = [
            (os.getenv("GROQ_MODEL") or "").strip(),
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-120b",
            "qwen/qwen3-32b",
        ]
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                for model in models:
                    if not model:
                        continue
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {groq}", "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "temperature": 0.85,
                            "max_tokens": 1100,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": prompt},
                            ],
                        },
                    )
                    if resp.status_code >= 400:
                        KEY_STATUS["groq"] = f"{model} http {resp.status_code}"
                        continue
                    text = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
                    if text:
                        KEY_STATUS["groq"] = f"ok:{model}"
                        KEY_STATUS["llm_error"] = ""
                        return text
        except Exception as exc:
            KEY_STATUS["groq"] = "error"
            log.warning("Groq failed: %s", exc)
    else:
        KEY_STATUS["groq"] = "missing"

    if openrouter:
        KEY_STATUS["openrouter"] = "present"
        models = [
            (os.getenv("OPENROUTER_MODEL") or "").strip(),
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen3-235b-a22b:free",
            "deepseek/deepseek-chat-v3.1:free",
            "google/gemini-2.0-flash-exp:free",
            "openrouter/auto",
        ]
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                for model in models:
                    if not model:
                        continue
                    resp = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {openrouter}",
                            "Content-Type": "application/json",
                            "HTTP-Referer": "https://github.com/web3-project-scout",
                            "X-Title": "Web3 Project Scout",
                        },
                        json={
                            "model": model,
                            "temperature": 0.85,
                            "max_tokens": 1100,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": prompt},
                            ],
                        },
                    )
                    if resp.status_code >= 400:
                        KEY_STATUS["openrouter"] = f"{model} http {resp.status_code}"
                        continue
                    text = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
                    if text:
                        KEY_STATUS["openrouter"] = f"ok:{model}"
                        KEY_STATUS["llm_error"] = ""
                        return text
        except Exception as exc:
            KEY_STATUS["openrouter"] = "error"
            log.warning("OpenRouter failed: %s", exc)
    else:
        KEY_STATUS["openrouter"] = "missing"

    if gemini:
        KEY_STATUS["gemini"] = "present"
        models = [
            (os.getenv("GEMINI_MODEL") or "").strip(),
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
        ]
        seen: set[str] = set()
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                for model in models:
                    if not model or model in seen:
                        continue
                    seen.add(model)
                    resp = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        params={"key": gemini},
                        headers={"Content-Type": "application/json"},
                        json={
                            "system_instruction": {"parts": [{"text": system}]},
                            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {"temperature": 0.9, "maxOutputTokens": 1100},
                        },
                    )
                    if resp.status_code >= 400:
                        continue
                    parts = (((resp.json().get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
                    text = "".join(p.get("text") or "" for p in parts).strip()
                    if text:
                        KEY_STATUS["gemini"] = f"ok:{model}"
                        return text
        except Exception as exc:
            KEY_STATUS["gemini"] = "error"
            log.warning("Gemini failed: %s", exc)
    else:
        KEY_STATUS["gemini"] = "missing"

    providers: list[tuple[str, str, str]] = []
    if xai:
        for model in ((os.getenv("XAI_MODEL") or "").strip(), "grok-4.1-fast", "grok-3-mini"):
            if model:
                providers.append(("xai", xai, model))
    if oai:
        providers.append(("openai", oai, "gpt-4o-mini"))
    if not providers:
        return None
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            for kind, key, model in providers:
                url = "https://api.x.ai/v1/chat/completions" if kind == "xai" else "https://api.openai.com/v1/chat/completions"
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "temperature": 0.8,
                        "max_tokens": 1100,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                if resp.status_code >= 400:
                    continue
                text = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
                if text:
                    KEY_STATUS["xai" if kind == "xai" else "openai"] = f"ok:{model}"
                    return text
        return None
    except Exception as exc:
        log.warning("LLM failed: %s", exc)
        return None


async def enrich_community(bot, client: httpx.AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    payload = dict(community(project))
    site = await fetch_website_meta(client, project.get("website") or "")
    payload.update(site)
    dc = await fetch_discord(client, project.get("discord") or payload.get("found_discord") or "")
    payload.update(dc)
    tg_url = project.get("telegram") or payload.get("found_telegram")
    tg = await fetch_telegram(bot, tg_url or "")
    payload.update(tg)
    handle = x_handle(project.get("twitter") or payload.get("found_twitter"))
    if handle:
        payload["x_handle"] = handle
        tweets = await fetch_x_tweets(client, handle)
        if tweets:
            payload["x_tweets"] = tweets[:5]
            if not tg_url:
                for tw in tweets:
                    found = re.findall(r"https?://t\.me/[A-Za-z0-9_+]+", tw.get("text") or "")
                    if found:
                        payload["found_telegram"] = found[0]
                        payload.update(await fetch_telegram(bot, found[0]))
                        break
    extra = await extra_onchain(client, project)
    payload.update(extra)
    return payload


async def extra_onchain(client: httpx.AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    chain = (project.get("chain") or "").lower()
    addr = (project.get("token_address") or "").strip()
    if not addr:
        return out

    if chain == "solana":
        try:
            resp = await client.get(
                f"https://api.rugcheck.xyz/v1/tokens/{addr}/report",
                headers={"User-Agent": "Web3ProjectScout/1.0"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    if data.get("creator"):
                        out["deployer"] = data["creator"]
                    holders = data.get("totalHolders")
                    if holders is not None:
                        try:
                            out["holders"] = int(holders)
                        except (TypeError, ValueError):
                            pass
                    if data.get("score") is not None:
                        out["rugcheck_score"] = data.get("score")
                    if data.get("rugged") is not None:
                        out["rugged"] = data.get("rugged")
                    # rough CTO heuristic: if top holders look community-like or mint revoked etc.
                    if data.get("mintAuthority") is None and data.get("freezeAuthority") is None:
                        out["mint_revoked"] = True
        except Exception as exc:
            log.warning("rugcheck failed %s: %s", addr[:12], exc)

        if not out.get("deployer") or out.get("holders") is None:
            data = await http_get(client, f"https://frontend-api.pump.fun/coins/{addr}")
            if not isinstance(data, dict):
                data = await http_get(client, f"https://frontend-api-v2.pump.fun/coins/{addr}")
            if isinstance(data, dict) and (data.get("mint") or data.get("creator")):
                if data.get("creator") and not out.get("deployer"):
                    out["deployer"] = data["creator"]
                if data.get("holder_count") is not None and out.get("holders") is None:
                    out["holders"] = data.get("holder_count")
                if data.get("twitter") and not project.get("twitter"):
                    tw = str(data["twitter"])
                    out["found_twitter"] = tw if tw.startswith("http") else f"https://x.com/{tw.lstrip('@')}"
                if data.get("telegram") and not project.get("telegram"):
                    out["found_telegram"] = data["telegram"]
                if data.get("website") and not project.get("website"):
                    out["found_website"] = data["website"]
                out["pump_complete"] = data.get("complete")
                if data.get("usd_market_cap") is not None:
                    out["pump_mcap"] = data.get("usd_market_cap")

    blockscout = {
        "ethereum": "https://eth.blockscout.com",
        "base": "https://base.blockscout.com",
        "bsc": "https://bsc.blockscout.com",
        "arbitrum": "https://arbitrum.blockscout.com",
        "polygon": "https://polygon.blockscout.com",
        "optimism": "https://optimism.blockscout.com",
    }
    if chain in blockscout and not out.get("holders"):
        base = blockscout[chain]
        data = await http_get(client, f"{base}/api/v2/tokens/{addr}/counters")
        if isinstance(data, dict):
            hc = data.get("token_holders_count") or data.get("holders_count")
            if hc is not None:
                try:
                    out["holders"] = int(str(hc).replace(",", ""))
                except ValueError:
                    pass
        if not out.get("deployer"):
            cdata = await http_get(client, f"{base}/api/v2/smart-contracts/{addr}")
            if isinstance(cdata, dict) and cdata.get("creator_address_hash"):
                out["deployer"] = cdata["creator_address_hash"]

    # Free honeypot check attempt for EVM (best-effort)
    if chain in {"ethereum", "bsc", "base", "arbitrum", "polygon"} and addr.startswith("0x"):
        try:
            hp = await http_get(client, f"https://api.honeypot.is/v2/IsHoneypot?address={addr}")
            if isinstance(hp, dict):
                out["honeypot"] = hp.get("isHoneypot")
                out["buy_tax"] = hp.get("buyTax")
                out["sell_tax"] = hp.get("sellTax")
                if hp.get("simulationSuccess") is not None:
                    out["sim_ok"] = hp.get("simulationSuccess")
        except Exception:
            pass

    return out


# ---------- reports ----------

def project_snapshot(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "website": p.get("website") or "",
        "twitter": p.get("twitter") or "",
        "telegram": p.get("telegram") or "",
        "discord": p.get("discord") or "",
        "docs": p.get("docs") or "",
        "description": (p.get("description") or "")[:200],
        "liquidity_usd": float(p.get("liquidity_usd") or 0),
        "volume_24h": float(p.get("volume_24h") or 0),
        "market_cap": float(p.get("market_cap") or 0),
        "qualified": int(p.get("qualified") or 0),
    }


def snapshot_diffs(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    for field, label in (
        ("website", "Website appeared"),
        ("twitter", "X / Twitter appeared"),
        ("telegram", "Telegram appeared"),
        ("discord", "Discord appeared"),
        ("docs", "Docs appeared"),
    ):
        if new.get(field) and not old.get(field):
            signals.append(f"📡 {label}")
    old_liq = float(old.get("liquidity_usd") or 0)
    new_liq = float(new.get("liquidity_usd") or 0)
    if old_liq < 500 and new_liq >= 2000:
        signals.append(f"💧 Liquidity started (${new_liq:,.0f})")
    elif old_liq > 0 and new_liq >= old_liq * 2.5 and new_liq - old_liq >= 3000:
        signals.append(f"💧 Liquidity jumped {old_liq:,.0f} → {new_liq:,.0f}")
    old_vol = float(old.get("volume_24h") or 0)
    new_vol = float(new.get("volume_24h") or 0)
    if old_vol < 1000 and new_vol >= 5000:
        signals.append(f"📊 Trading activity started (vol ${new_vol:,.0f})")
    elif old_vol > 0 and new_vol >= old_vol * 3 and new_vol - old_vol >= 10000:
        signals.append(f"📊 Volume spike {old_vol:,.0f} → {new_vol:,.0f}")
    if len(new.get("description") or "") > 40 and len(old.get("description") or "") < 20:
        signals.append("📝 Project description filled in")
    if new.get("qualified") and not old.get("qualified"):
        signals.append("✅ Became public-facing (website or socials)")
    return signals


def classify_project(p: dict[str, Any]) -> dict[str, Any]:
    name = str(p.get("name") or "").lower()
    symbol = str(p.get("symbol") or "").lower()
    desc = str(p.get("description") or "").lower()
    comm = community(p)
    site = str(comm.get("site_about") or "").lower()
    tg = str(comm.get("tg_about") or "").lower()
    blob = " ".join([name, symbol, desc, site, tg])

    utility_kw = [
        "ai ", " artificial", "agent", "infra", "protocol", "defi", "lend", "borrow",
        "stake", "staking", "yield", "swap", "dex", "bridge", "oracle", "layer",
        "l2", "rollup", "zk", "privacy", "identity", "wallet", "sdk", "api ",
        "tool", "platform", "marketplace", "gamefi", "rwa", "payment", "compute",
        "governance", "dao", "launchpad", "restake", "perps", "insurance",
        "testnet", "mainnet", "docs", "whitepaper", "roadmap", "utility token",
    ]
    meme_kw = [
        "meme", "pepe", "doge", "wojak", "chad", "based", "only up", "moon",
        "pump", "to the moon", "community coin", "fair launch", "no utility",
        "just a meme", "culture", "vibes", "funny", "cat coin", "dog coin",
        "frog", "inu ", "shiba", "elon", "trump", "maga", "mascot", "cto",
        "community take over",
    ]

    u_hits = sum(1 for k in utility_kw if k in blob)
    m_hits = sum(1 for k in meme_kw if k in blob)
    has_docs = bool(p.get("docs"))
    has_site = bool(p.get("website"))
    has_desc = len(desc) > 60
    long_product = any(x in blob for x in ("how it works", "use case", "product", "users can", "built for"))
    if has_docs:
        u_hits += 3
    if has_site and has_desc:
        u_hits += 2
    if long_product:
        u_hits += 2
    if not has_site and not has_docs and len(desc) < 30:
        m_hits += 2
    if m_hits >= 2 and u_hits == 0:
        m_hits += 1

    if m_hits >= u_hits + 2 or (m_hits >= 2 and u_hits <= 1 and not has_docs):
        return {"label": "🐸 Meme", "type": "meme"}
    if u_hits >= m_hits + 2 or (has_docs and u_hits >= 1):
        return {"label": "🔧 Utility", "type": "utility"}
    if u_hits > 0 and m_hits > 0:
        return {"label": "⚖️ Mixed", "type": "mixed"}
    if has_site and has_desc:
        return {"label": "🔧 Utility", "type": "utility"}
    if not has_site and not has_docs:
        return {"label": "🐸 Meme", "type": "meme"}
    return {"label": "⚖️ Mixed", "type": "mixed"}


def score_project(p: dict[str, Any]) -> dict[str, Any]:
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
    comm = community(p)
    members = comm.get("tg_members") or comm.get("dc_members") or 0
    try:
        members = int(members or 0)
    except (TypeError, ValueError):
        members = 0
    if members >= 5000:
        score += 8
    elif members >= 800:
        score += 12
        if "Community / Social" not in roles:
            roles.append("Community / Social")
    elif members >= 150:
        score += 8
    if comm.get("site_about") and not desc:
        score += 6
    kind = classify_project(p)
    if kind.get("type") == "utility":
        score += 14
        if "Utility / product" not in roles:
            roles.append("Utility / product")
    elif kind.get("type") == "meme":
        score += 2
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


def list_item(index: int, p: dict[str, Any]) -> str:
    s = score_project(p)
    kind = classify_project(p)
    return (
        f"<b>{index}.</b> {esc(title_of(p))} · {s['band']} {s['score']} · {kind['label']} · {source_label(p)}\n"
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
    comm = community(p)
    kind = classify_project(p)
    badge = source_label(p)
    launch_ts = p.get("launched_at") or p.get("discovered_at")
    lines = [
        "🧠 <b>PROJECT INTELLIGENCE</b>",
        f"<b>{esc(title_of(p))}</b>",
        f"{kind['label']}" + (f" · {badge}" if badge else ""),
        f"⛓ {(p.get('chain') or '?').title()}",
        f"🏷 <code>{esc(p.get('token_address') or '')}</code>",
        f"🕒 Launched: {esc(ago(launch_ts))}",
        f"🎯 {s['band']} {s['score']}/100",
        "",
        "💰 <b>MARKET</b>",
        f"MCAP: <b>{esc(money(p.get('market_cap')))}</b>",
        f"FDV: {esc(money(p.get('fdv')))}",
        f"Liquidity: {esc(money(p.get('liquidity_usd')))}",
        f"Volume 24h: {esc(money(p.get('volume_24h')))}",
        f"Price: {esc(money(p.get('price_usd')))}",
        f"DEX: {esc(p.get('dex') or '—')}",
        "",
        "🎯 <b>WHAT THEY BUILD</b>",
        esc(str(what)[:700]),
        "",
        "🌐 <b>PRESENCE</b>",
        f"Website: {esc(p.get('website') or '—')}",
        f"X: {esc(p.get('twitter') or '—')}",
        f"Telegram: {esc(p.get('telegram') or '—')}",
        f"Discord: {esc(p.get('discord') or '—')}",
        f"Docs: {esc(p.get('docs') or '—')}",
        "",
        "⛓ <b>ON-CHAIN SNAPSHOT</b>",
    ]
    if comm.get("deployer"):
        lines.append(f"Deployer: <code>{esc(comm.get('deployer'))}</code>")
    else:
        lines.append("Deployer: —")
    if comm.get("holders") is not None:
        lines.append(f"Holders: <b>{esc(comm.get('holders'))}</b>")
    else:
        lines.append("Holders: —")
    if comm.get("rugcheck_score") is not None:
        lines.append(f"Rugcheck: {esc(comm.get('rugcheck_score'))}")
    if comm.get("rugged"):
        lines.append("⚠️ Flagged rugged by rugcheck")
    lines += [
        "",
        "👉 <b>NEXT</b>",
        esc(s.get("action") or "Review gaps + risks, then decide approach."),
        f"Roles: {esc(', '.join(s.get('roles') or []))}",
    ]
    return "\n".join(lines)


def report_keyboard(p: dict[str, Any]) -> InlineKeyboardMarkup:
    pid = p["id"]
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("⭐ Watch", callback_data=f"w:{pid}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"inv:{pid}"),
        ],
        [
            InlineKeyboardButton("🎭 Persona", callback_data=f"ps:{pid}"),
            InlineKeyboardButton("🎯 Approach", callback_data=f"ap:{pid}"),
        ],
        [
            InlineKeyboardButton("⛓ On-chain", callback_data=f"oc:{pid}"),
            InlineKeyboardButton("🔍 Gaps", callback_data=f"gp:{pid}"),
        ],
        [
            InlineKeyboardButton("⚠️ Risks", callback_data=f"rk:{pid}"),
        ],
    ]
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


def persona_select_keyboard(pid: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("💰 Investor", callback_data=f"pn:{pid}:investor"),
            InlineKeyboardButton("👀 Curious", callback_data=f"pn:{pid}:curious"),
        ],
        [
            InlineKeyboardButton("❓ Question", callback_data=f"pn:{pid}:question"),
            InlineKeyboardButton("🚀 Bullish", callback_data=f"pn:{pid}:bullish"),
        ],
        [
            InlineKeyboardButton("💎 Holder", callback_data=f"pn:{pid}:holder"),
            InlineKeyboardButton("🧠 Strategist", callback_data=f"pn:{pid}:strategist"),
        ],
        [InlineKeyboardButton("« Back to report", callback_data=f"inv:{pid}")],
    ]
    return InlineKeyboardMarkup(rows)


def onchain_text(p: dict[str, Any]) -> str:
    """Clean on-chain card. Only facts from available data."""
    comm = community(p)
    s = score_project(p)
    kind = classify_project(p)
    liq = float(p.get("liquidity_usd") or 0)
    vol = float(p.get("volume_24h") or 0)
    mcap = p.get("market_cap")
    holders = comm.get("holders")
    launch_ts = p.get("launched_at") or p.get("discovered_at")
    deployer = comm.get("deployer")

    # CTO heuristic
    cto = "UNKNOWN"
    if kind.get("type") == "meme" and (comm.get("mint_revoked") or (holders and holders > 300 and not deployer)):
        cto = "POSSIBLE"
    if "cto" in str(p.get("name") or "").lower() or "community take" in str(p.get("description") or "").lower():
        cto = "YES (self-described)"

    lines = [
        "⛓ <b>ON-CHAIN ANALYSIS</b>",
        f"<b>{esc(title_of(p))}</b> · {kind['label']}",
        f"⛓ {(p.get('chain') or '?').title()}",
        f"🏷 <code>{esc(p.get('token_address') or '')}</code>",
        f"🕒 Deployed: <b>{esc(ago(launch_ts))}</b>",
        "",
        "💰 <b>MARKET</b>",
        f"MCAP: <b>{esc(money(mcap))}</b>",
        f"FDV: {esc(money(p.get('fdv')))}",
        f"Liquidity: {esc(money(liq))}",
        f"Volume 24h: {esc(money(vol))}",
        f"Price: {esc(money(p.get('price_usd')))}",
        f"DEX: {esc(p.get('dex') or '—')}",
        "",
        "👤 <b>CREATOR / HOLDERS</b>",
        f"Deployer: <code>{esc(deployer or '—')}</code>",
        f"Holders: <b>{esc(holders if holders is not None else '—')}</b>",
        f"CTO Status: <b>{esc(cto)}</b>",
    ]
    if comm.get("pump_complete") is not None:
        lines.append(f"Pump.fun graduated: {'yes' if comm.get('pump_complete') else 'no'}")
    if comm.get("rugcheck_score") is not None:
        lines.append(f"Rugcheck score: {esc(comm.get('rugcheck_score'))}")
    if comm.get("rugged"):
        lines.append("⚠️ Rugcheck flagged rugged")
    if comm.get("honeypot") is not None:
        lines.append(f"Honeypot: {'YES ⚠️' if comm.get('honeypot') else 'No'}")
    if comm.get("buy_tax") is not None or comm.get("sell_tax") is not None:
        lines.append(f"Buy tax: {esc(comm.get('buy_tax'))} · Sell tax: {esc(comm.get('sell_tax'))}")

    lines += ["", "⚠️ <b>OBSERVED ON-CHAIN SIGNALS</b>"]
    risks = []
    if liq and liq < 3000:
        risks.append("Very thin liquidity — price easy to move, exit risk high.")
    elif liq and liq < 15000:
        risks.append("Modest liquidity — size carefully.")
    if vol and liq and vol > liq * 5:
        risks.append("Volume >> liquidity — possible wash / aggressive churn.")
    if holders is not None and holders < 50:
        risks.append(f"Low holder count ({holders}) — concentration risk.")
    elif holders is not None and holders < 200:
        risks.append(f"Holder base still small ({holders}).")
    if not deployer:
        risks.append("Deployer not indexed on free sources — verify on explorer.")
    if comm.get("rugged"):
        risks.append("Automated rug flag present.")
    if comm.get("honeypot"):
        risks.append("Honeypot check returned positive — high risk.")
    if not risks:
        risks.append("No strong automated red flags from free data. Still verify LP lock, mint authority, tax on explorer.")
    for r in risks:
        lines.append(f"• {esc(r)}")

    lines += ["", "💡 <b>WHAT WOULD HELP (from observed data only)</b>"]
    tips = []
    if liq and liq < 10000:
        tips.append("Deeper liquidity would reduce volatility that scares serious size.")
    if holders is not None and holders < 150:
        tips.append("Broader holder distribution reduces concentration risk narrative.")
    if not deployer:
        tips.append("Publishing verified deployer + LP status in one official place builds trust.")
    if kind.get("type") == "meme":
        tips.append("Clear pinned CA + buy path + LP status in TG/X reduces FUD loops.")
    else:
        tips.append("Contract addresses + admin power explanation on site header reduces diligence friction.")
    if not tips:
        tips.append("No additional on-chain derived suggestions from available free data.")
    for t in tips:
        lines.append(f"• {esc(t)}")

    lines += [
        "",
        f"🎯 Opportunity: {s['band']} {s['score']}/100",
        "<i>Free data is incomplete — always cross-check explorer + official channels.</i>",
    ]
    return "\n".join(lines)


async def persona_text(p: dict[str, Any], persona_key: str = "curious") -> str:
    """Generate human, post-ready messages in a specific persona voice."""
    s = score_project(p)
    comm = community(p)
    what = p.get("description") or comm.get("site_about") or comm.get("tg_about") or "Thin public description."
    tweets = comm.get("x_tweets") or []
    tweet_blob = "\n".join(f"- {(t.get('text') or '')[:180]}" for t in tweets[:4]) or "No recent tweets indexed."
    has_tg = bool(p.get("telegram"))
    has_x = bool(p.get("twitter"))
    door = "X only" if has_x and not has_tg else ("Telegram + X" if has_tg and has_x else ("Telegram" if has_tg else "thin socials"))
    persona = PERSONAS.get(persona_key, PERSONAS["curious"])
    kind = classify_project(p)

    prompt = (
        f"Project: {title_of(p)}\nChain: {p.get('chain')}\nType: {kind['label']}\n"
        f"Website: {p.get('website')}\nX: {p.get('twitter')}\nTelegram: {p.get('telegram')}\n"
        f"About: {what}\nSite title: {comm.get('site_title')}\nTG about: {comm.get('tg_about')}\n"
        f"Recent X posts:\n{tweet_blob}\n"
        f"Door available: {door}\n\n"
        f"PERSONA TO ADOPT (strict):\n{persona['label']}: {persona['desc']}\n\n"
        f"Write 4-6 short, ready-to-post messages this persona would actually send.\n"
        f"Rules:\n"
        f"- Sound 100% human. No AI cadence. No 'As an investor...' meta talk.\n"
        f"- Each message must stand alone and be postable on X or Telegram.\n"
        f"- Include 2-3 options that work as X replies or DM openers if only X exists.\n"
        f"- For DM openers: make interest + questions convincing enough that the team might invite a DM or accept one. "
        f"Sound like you've used the product or done real diligence and want to dig further.\n"
        f"- No hashtags dump. No 'to the moon'. Variation seed {random.randint(1,9999)}.\n"
        f"Format exactly:\nOPTION 1\n<option text>\nOPTION 2\n<option text>\n... "
    )
    generated = await llm_write(prompt)

    header = [
        f"🎭 <b>PERSONA · {esc(persona['label'])}</b>",
        f"<b>{esc(title_of(p))}</b> · {s['band']} {s['score']}/100",
        f"⛓ {esc((p.get('chain') or '?').title())} · Door: {esc(door)}",
        "",
        "📌 Context",
        esc(what)[:300],
        "",
    ]
    if generated:
        body = [copyable_brief(generated)]
    else:
        # strong human fallbacks per persona
        name = title_of(p)
        if persona_key == "investor":
            opts = [
                f"Been watching {name} for a bit. The positioning feels clearer than most launches this week — what's the actual retention loop after day one?",
                f"Looking at sizing a position here. Who is the user that pays if the token didn't exist?",
                f"Thesis so far looks solid on paper. Any public numbers on usage or waitlist conversion yet?",
            ]
        elif persona_key == "question":
            opts = [
                f"Quick one on {name} — is the product live for anyone outside friends-and-family, or still closed?",
                "Where should someone dig into the admin / upgrade powers on the contracts? Not seeing it spelled out clearly.",
                "Token capture vs product value still fuzzy to me. Is there a short doc on that?",
            ]
        elif persona_key == "bullish":
            opts = [
                f"This is one of the cleaner narratives I've seen this week. Keeping an eye on {name}.",
                "Finally a project that tries to explain itself instead of just posting candles. Respect.",
                f"Culture + clarity on {name} is rare lately. Following.",
            ]
        elif persona_key == "holder":
            opts = [
                f"Holding {name}. Would love a pinned 'how this works' so the chat stops looping the same questions.",
                "As a holder — any timeline clarity on the next public milestone?",
                "In from early. The more transparent the LP + admin status, the easier it is to stay long.",
            ]
        elif persona_key == "strategist":
            opts = [
                "Happy to draft a short FAQ from the site copy if it helps the first-week chat stay clean.",
                "One suggestion: put official links + CA in the Telegram description so people stop asking.",
                "If useful I can sit in the chat this week and answer newbie questions from the public docs.",
            ]
        else:  # curious
            opts = [
                f"Just landed on {name} via Dex → X. How does a first-time user actually try this in the first 5 minutes?",
                "The site copy clicked more than the average launch. What's the one action you want new people to take?",
                "Saw the recent posts. Is the product something people open daily or more of a hold?",
            ]
        body = []
        for i, o in enumerate(opts, 1):
            body.append(f"<b>OPTION {i}</b>")
            body.append(f"<code>{esc(o)}</code>")
            body.append("")
        body.append(f"⚠️ AI offline · {esc(KEY_STATUS.get('groq') or KEY_STATUS.get('openrouter') or 'no key')}")

    extra = [
        "",
        f"X: {esc(p.get('twitter') or '—')}",
        f"TG: {esc(p.get('telegram') or '—')}",
        "Tap another persona below or 🔀 for a fresh set.",
    ]
    return "\n".join(header + body + extra)


def copyable_brief(text: str) -> str:
    labels = {"CURIOUS", "INVESTOR", "SUGGESTION", "QUESTION", "SUPPORTER", "STRATEGIST", "RANDOM",
              "OPTION1", "OPTION2", "OPTION3", "OPTION4", "OPTION5", "OPTION6",
              "OPTION 1", "OPTION 2", "OPTION 3", "OPTION 4", "OPTION 5", "OPTION 6"}
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        key = re.sub(r"[^A-Za-z0-9 ]", "", line).upper().strip()
        if key in labels or key.startswith("OPTION"):
            out.append(f"<b>{esc(line)}</b>")
        elif line:
            out.append(f"<code>{esc(line)}</code>")
        else:
            out.append("")
    return "\n".join(out)


def social_alert_text(p: dict[str, Any], kinds: list[str]) -> str:
    chain = (p.get("chain") or "?").upper()
    kind = classify_project(p)
    src_badge = source_label(p, for_alert=True)
    launch_ts = p.get("launched_at") or p.get("discovered_at")
    head = f"🔔 <b>SOCIAL UPDATE ({esc(chain)})</b>"
    if src_badge:
        head += f" · {src_badge}"
    lines = [
        head,
        f"<b>{esc(title_of(p))}</b> · {kind['label']}",
        f"Contract: <code>{esc(p.get('token_address') or '—')}</code>",
        f"Deployed: {esc(ago(launch_ts))}",
        "",
    ]
    if "telegram" in kinds and p.get("telegram"):
        lines += ["💬 <b>TELEGRAM GROUP DETECTED</b>", esc(str(p.get("telegram"))), ""]
    if "x" in kinds and p.get("twitter"):
        lines += ["𝕏 <b>X DETECTED</b>", esc(str(p.get("twitter"))), ""]
    if "website" in kinds and p.get("website"):
        lines += ["🌐 <b>WEBSITE DETECTED</b>", esc(str(p.get("website"))), ""]
    lines += [
        f"💰 MCAP: <b>{esc(money(p.get('market_cap')))}</b> · 💧 {esc(money(p.get('liquidity_usd')))}",
        f"Channels: {esc(', '.join(k.upper() for k in kinds))}",
        f"First seen by Scout: {esc(ago(p.get('discovered_at')))}",
    ]
    return "\n".join(lines)


def alert_text(p: dict[str, Any]) -> str:
    s = score_project(p)
    chain = (p.get("chain") or "?").upper()
    kind = classify_project(p)
    src_badge = source_label(p, for_alert=True)
    launch_ts = p.get("launched_at") or p.get("discovered_at")
    identity = []
    if p.get("twitter"):
        identity.append("X")
    if p.get("telegram"):
        identity.append("Telegram")
    if p.get("website"):
        identity.append("Website")
    if p.get("discord"):
        identity.append("Discord")
    id_line = " + ".join(identity) if identity else "social"
    head = f"🚨 <b>NEW PROJECT IDENTIFIED ({esc(chain)})</b>"
    if src_badge:
        head += f" · {src_badge}"
    lines = [
        head,
        f"<b>{esc(title_of(p))}</b> · {kind['label']}",
        f"Identity: <b>{esc(id_line)}</b>",
        f"Contract: <code>{esc(p.get('token_address') or '—')}</code>",
        f"Deployed: {esc(ago(launch_ts))}",
        "",
        f"💰 MCAP: <b>{esc(money(p.get('market_cap')))}</b> · FDV: {esc(money(p.get('fdv')))}",
        f"💧 Liq: {esc(money(p.get('liquidity_usd')))} · 📊 Vol 24h: {esc(money(p.get('volume_24h')))}",
        "",
    ]
    if p.get("telegram"):
        lines.append(f"💬 TG: {esc(p.get('telegram'))}")
    if p.get("twitter"):
        lines.append(f"𝕏 X: {esc(p.get('twitter'))}")
    if p.get("website"):
        lines.append(f"🌐 Web: {esc(p.get('website'))}")
    lines += [
        "",
        f"First seen by Scout: {esc(ago(p.get('discovered_at')))}",
        f"🎯 {s['band']} {s['score']}/100",
    ]
    return "\n".join(lines)


def source_label(p: dict[str, Any], *, for_alert: bool = False) -> str:
    s = (p.get("source") or "").lower()
    if s in {"cmc", "coinmarketcap"}:
        return "📈 CMC"
    if s in {"coingecko", "cg", "gecko-trending"}:
        return "🦎 CoinGecko"
    if for_alert:
        return ""
    if s in {"gecko", "geckoterminal"}:
        return "🦎 GeckoTerminal"
    if s.startswith("dex") or s in {"dex-profile", "dex-pair", "dex-boost"}:
        return ""
    if s:
        return s
    return ""


# ---------- app helpers ----------

def allowed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    owners: list[int] = context.application.bot_data.get("allowed") or []
    if not owners:
        return True
    return user_id in owners


async def safe_cb_answer(query, text: str | None = None, show_alert: bool = False) -> None:
    try:
        if text:
            await query.answer(text, show_alert=show_alert)
        else:
            await query.answer()
    except Exception as exc:
        log.debug("cb answer: %s", exc)


async def safe_edit(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except Exception as exc:
        err = str(exc).lower()
        if "not modified" in err:
            await safe_cb_answer(query, "Already up to date")
            return
        log.warning("edit_message failed: %s", exc)
        try:
            await query.message.reply_html(
                text, disable_web_page_preview=True, reply_markup=reply_markup
            )
        except Exception as exc2:
            log.warning("reply fallback failed: %s", exc2)


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    await claim_owner_if_needed(context, user.id)
    if not allowed(user.id, context):
        if update.callback_query:
            await safe_cb_answer(
                update.callback_query,
                "This Scout is private (wrong Telegram account / ALLOWED_USER_IDS).",
                show_alert=True,
            )
        elif update.effective_message:
            await update.effective_message.reply_text(
                "This Scout is private (wrong Telegram account / ALLOWED_USER_IDS)."
            )
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


async def enrich_one(db: DB, client: httpx.AsyncClient, project: dict[str, Any], bot=None) -> dict[str, Any]:
    chain, addr = project["chain"], project["token_address"]
    data = await http_get(client, f"{DEX_API}/token-pairs/v1/{chain}/{addr}")
    pairs = data if isinstance(data, list) else []
    if pairs:
        best = max(pairs, key=lambda p: float(((p.get("liquidity") or {}) or {}).get("usd") or 0))
        extra = pair_to_project(best)
        extra["chain"] = chain
        extra["token_address"] = addr
        extra["last_enriched_at"] = now()
        await db.upsert(extra)
    else:
        await db.mark_enriched(project["id"])
    fresh = await db.by_id(project["id"]) or project
    try:
        payload = await enrich_community(bot, client, fresh)
        if payload:
            await db.save_community(fresh["id"], payload)
            fresh = await db.by_id(fresh["id"]) or fresh
    except Exception:
        log.exception("community enrich failed for %s", fresh.get("id"))
    return fresh


async def resolve_id_or_ca(db: DB, client: httpx.AsyncClient, query: str, bot=None) -> dict[str, Any] | None:
    q = (query or "").strip()
    if not q:
        return None
    if q.isdigit():
        return await db.by_id(int(q))
    return await resolve_project(db, client, q, bot)


async def resolve_project(db: DB, client: httpx.AsyncClient, query: str, bot=None) -> dict[str, Any] | None:
    query = query.strip()
    chain = None
    addr = query
    if ":" in query and not query.startswith("http"):
        chain, addr = query.split(":", 1)
        chain, addr = chain.lower().strip(), addr.strip()
    if chain:
        found = await db.by_token(chain, addr)
        if found:
            return await enrich_one(db, client, found, bot)
        data = await http_get(client, f"{DEX_API}/tokens/v1/{chain}/{addr}")
        pairs = data if isinstance(data, list) else []
        if pairs:
            proj = pair_to_project(pairs[0])
            pid, _ = await db.upsert(proj)
            row = await db.by_id(pid)
            return await enrich_one(db, client, row, bot) if row else None
    data = await http_get(client, f"{DEX_API}/latest/dex/search", params={"q": query})
    pairs = (data or {}).get("pairs") if isinstance(data, dict) else []
    if not pairs:
        return None
    proj = pair_to_project(pairs[0])
    if not proj.get("token_address"):
        return None
    pid, _ = await db.upsert(proj)
    row = await db.by_id(pid)
    return await enrich_one(db, client, row, bot) if row else None


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
        "I store early contracts silently.\n"
        "I only alert when a project identity appears "
        "(X / Telegram / website).\n\n"
        "/newtokens 12h — catch up\n"
        "/menu — command menu\n"
        "/watch &lt;CA&gt; — notify when socials appear\n"
        "/help — all commands"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    await update.effective_message.reply_html(HELP)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline menu that inserts command text into the input field (does not auto-send)."""
    if not await gate(update, context) or not update.effective_message:
        return
    keyboard = [
        [
            InlineKeyboardButton("🔎 /newtokens", switch_inline_query_current_chat="/newtokens "),
            InlineKeyboardButton("💼 /jobs", switch_inline_query_current_chat="/jobs "),
        ],
        [
            InlineKeyboardButton("🧠 /project", switch_inline_query_current_chat="/project "),
            InlineKeyboardButton("🎭 /persona", switch_inline_query_current_chat="/persona "),
        ],
        [
            InlineKeyboardButton("🎯 /approach", switch_inline_query_current_chat="/approach "),
            InlineKeyboardButton("🔍 /gaps", switch_inline_query_current_chat="/gaps "),
        ],
        [
            InlineKeyboardButton("⚠️ /risks", switch_inline_query_current_chat="/risks "),
            InlineKeyboardButton("⭐ /watch", switch_inline_query_current_chat="/watch "),
        ],
        [
            InlineKeyboardButton("📋 /watchlist", switch_inline_query_current_chat="/watchlist"),
            InlineKeyboardButton("📡 /status", switch_inline_query_current_chat="/status"),
        ],
        [
            InlineKeyboardButton("🦎 /cg", switch_inline_query_current_chat="/cg"),
            InlineKeyboardButton("📈 /cmc", switch_inline_query_current_chat="/cmc"),
        ],
    ]
    await update.effective_message.reply_html(
        "📋 <b>Command Menu</b>\n"
        "Tap a button — it inserts the command into your text field so you can add parameters before sending.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


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
        await safe_edit(update.callback_query, text, markup)
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
    await safe_cb_answer(update.callback_query)
    try:
        _, since_s, offset_s = update.callback_query.data.split(":")
        since_ts, offset = int(since_s), int(offset_s)
        await render_page(update, context, since_ts, offset, ago(since_ts), edit=True)
    except Exception:
        log.exception("cb_newtokens failed")
        await safe_cb_answer(update.callback_query, "Error loading page", show_alert=True)


async def cmd_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    query = " ".join(context.args or []).strip()
    if not query:
        await update.effective_message.reply_text("Usage: /project solana:Address")
        return
    db, client = deps(context)
    project = await resolve_project(db, client, query, context.bot)
    if not project:
        await update.effective_message.reply_text("Could not find that project. Try chain:address.")
        return
    await update.effective_message.reply_html(
        report_text(project), disable_web_page_preview=True, reply_markup=report_keyboard(project)
    )


async def cb_investigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.callback_query.data:
        return
    if not await gate(update, context):
        return
    q = update.callback_query
    await safe_cb_answer(q, "Researching…")
    try:
        pid = int(q.data.split(":")[1])
    except (IndexError, ValueError):
        await safe_cb_answer(q, "Bad button data", show_alert=True)
        return
    db, client = deps(context)
    project = await db.by_id(pid)
    if not project:
        await safe_cb_answer(q, "Expired — send /jobs again", show_alert=True)
        return
    try:
        project = await enrich_one(db, client, project, context.bot)
        await safe_edit(q, report_text(project), report_keyboard(project))
    except Exception as exc:
        log.exception("cb_investigate failed")
        await safe_cb_answer(q, f"Error: {str(exc)[:80]}", show_alert=True)


async def cb_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.effective_user:
        return
    if not await gate(update, context):
        return
    try:
        pid = int(update.callback_query.data.split(":")[1])
        db, _ = deps(context)
        await db.watch(update.effective_user.id, pid)
        await safe_cb_answer(update.callback_query, "Saved to /watchlist")
    except Exception:
        log.exception("cb_watch failed")
        await safe_cb_answer(update.callback_query, "Error", show_alert=True)


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message or not update.effective_user:
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n/watch <project_id>\n/watch <CA>\n/watch chain:CA\n\n"
            "Scout monitors and alerts when socials, liquidity, or activity appear."
        )
        return
    db, client = deps(context)
    query = " ".join(context.args).strip()
    project = None
    if query.isdigit():
        project = await db.by_id(int(query))
    if not project:
        project = await resolve_project(db, client, query, context.bot)
    if not project:
        await update.effective_message.reply_text(
            "Could not resolve that id/CA. Try /project chain:address first, then /watch <id>."
        )
        return
    await db.watch(update.effective_user.id, int(project["id"]))
    snap = project_snapshot(project)
    await db.save_snapshot(update.effective_user.id, int(project["id"]), snap)
    chain = (project.get("chain") or "?").upper()
    missing = []
    if not project.get("telegram"):
        missing.append("Telegram")
    if not project.get("twitter"):
        missing.append("X")
    if not project.get("website"):
        missing.append("Website")
    miss = ", ".join(missing) if missing else "none (already public-facing)"
    await update.effective_message.reply_html(
        f"⭐ <b>Watching</b> #{project['id']} {esc(title_of(project))}\n"
        f"⛓ {esc(chain)}\n"
        f"Monitoring for: socials · liquidity · volume · profile changes\n"
        f"Still missing: {esc(miss)}\n"
        f"See /watchlist · alerts stay queued while you are offline."
    )


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message or not update.effective_user:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /unwatch <id|CA>")
        return
    db, client = deps(context)
    project = await resolve_id_or_ca(db, client, " ".join(context.args), context.bot)
    if not project:
        await update.effective_message.reply_text("Could not resolve id/CA.")
        return
    await db.unwatch(update.effective_user.id, int(project["id"]))
    await update.effective_message.reply_html(f"Removed #{project['id']} {esc(title_of(project))}.")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message or not update.effective_user:
        return
    db, _ = deps(context)
    rows = await db.watchlist(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text(
            "Watchlist empty.\n/watch <CA> or /watch <id> — or tap ⭐ Watch on a report."
        )
        return
    lines = ["⭐ <b>WATCHLIST</b>", "Alerts fire on socials · liquidity · volume · profile changes", ""]
    for p in rows:
        kind = classify_project(p)
        miss = []
        if not p.get("telegram"):
            miss.append("TG")
        if not p.get("twitter"):
            miss.append("X")
        if not p.get("website"):
            miss.append("web")
        miss_s = ",".join(miss) if miss else "complete"
        lines.append(
            f"#{p['id']} <b>{esc(title_of(p))}</b> · {esc((p.get('chain') or '').title())} · {kind['label']}\n"
            f"💧 {esc(money(p.get('liquidity_usd')))} · missing: {esc(miss_s)}"
        )
    markup = list_keyboard(rows[:8], now() - 86400, 0, len(rows)) if rows else None
    await update.effective_message.reply_html(
        "\n".join(lines), disable_web_page_preview=True, reply_markup=markup
    )


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
    last_q = await db.get_meta("last_qualified_at")
    last_scan = context.application.bot_data.get("last_scan_at")
    last_cmd = context.application.bot_data.get("last_command_at")
    await update.effective_message.reply_html(
        "📡 <b>Scout status</b>\n"
        f"Projects stored: {stats['total']}\n"
        f"Qualified: {stats['qualified']}\n"
        f"Seen last 24h: {stats['last_24h']}\n"
        f"Last new qualified: {esc(ago(int(last_q)) if last_q else 'none yet')}\n"
        f"Last scan: {esc(ago(last_scan) if last_scan else 'not yet')}\n"
        f"Last command: {esc(ago(last_cmd) if last_cmd else 'none')}\n"
        f"Scanner: {'running' if context.application.bot_data.get('scan_alive') else 'restarting'}\n"
        f"Build: {SCOUT_BUILD}\n"
        f"Groq: {esc(KEY_STATUS.get('groq') or 'not tried')} {'set' if env_secret('GROQ_API_KEY') else 'NOT in env'}\n"
        f"OpenRouter: {esc(KEY_STATUS.get('openrouter') or 'not tried')} {'set' if env_secret('OPENROUTER_API_KEY','OPENROUTER_KEY') else 'NOT in env'}\n"
        f"Gemini: {esc(KEY_STATUS.get('gemini') or 'not tried')}\n"
        f"xAI: {esc(KEY_STATUS.get('xai') or 'not tried')} {'set' if env_secret('XAI_API_KEY') else 'NOT in env'}\n"
        f"X bearer: {esc(KEY_STATUS.get('x') or 'not tried')} {'set' if env_secret('X_BEARER_TOKEN','TWITTER_BEARER_TOKEN') else 'NOT in env'}\n"
        f"DB: {esc(str(db.path))}\n"
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


async def cmd_approach(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show persona selector for a project."""
    if not await gate(update, context) or not update.effective_message:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /approach <project_id|CA|chain:CA>")
        return
    db, client = deps(context)
    project = await resolve_id_or_ca(db, client, " ".join(context.args), context.bot)
    if not project:
        await update.effective_message.reply_text("Could not resolve id/CA. Try /project <CA> first.")
        return
    await update.effective_message.reply_html(
        f"🎯 <b>Choose Approach / Persona</b>\n"
        f"<b>{esc(title_of(project))}</b>\n\n"
        "Pick the voice you want the generated messages written in:",
        reply_markup=persona_select_keyboard(int(project["id"])),
    )


async def cmd_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate with default curious persona or show selector."""
    if not await gate(update, context) or not update.effective_message:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /persona <project_id|CA>")
        return
    db, client = deps(context)
    project = await resolve_id_or_ca(db, client, " ".join(context.args), context.bot)
    if not project:
        await update.effective_message.reply_text("Could not resolve id/CA.")
        return
    project = await enrich_one(db, client, project, context.bot)
    text = await persona_text(project, "curious")
    await update.effective_message.reply_html(
        text, disable_web_page_preview=True,
        reply_markup=persona_select_keyboard(int(project["id"])),
    )


async def cb_approach(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show persona selector."""
    if not await gate(update, context) or not update.callback_query or not update.callback_query.data:
        return
    q = update.callback_query
    await safe_cb_answer(q)
    try:
        pid = int(q.data.split(":")[1])
    except Exception:
        return
    db, _ = deps(context)
    project = await db.by_id(pid)
    if not project:
        await safe_cb_answer(q, "Expired. Open /jobs again.", show_alert=True)
        return
    await safe_edit(
        q,
        f"🎯 <b>Choose Approach / Persona</b>\n<b>{esc(title_of(project))}</b>\n\n"
        "Pick the voice for the generated messages:",
        persona_select_keyboard(pid),
    )


async def cb_persona_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate text in the selected persona."""
    if not await gate(update, context) or not update.callback_query or not update.callback_query.data:
        return
    q = update.callback_query
    await safe_cb_answer(q, "Writing…")
    try:
        parts = q.data.split(":")
        pid = int(parts[1])
        persona_key = parts[2] if len(parts) > 2 else "curious"
    except Exception:
        return
    db, client = deps(context)
    project = await db.by_id(pid)
    if not project:
        await safe_cb_answer(q, "Expired. Open /jobs again.", show_alert=True)
        return
    try:
        project = await enrich_one(db, client, project, context.bot)
        text = await persona_text(project, persona_key)
        await safe_edit(q, text, persona_select_keyboard(pid))
    except Exception as exc:
        log.exception("cb_persona_select failed")
        await safe_cb_answer(q, f"Error: {str(exc)[:60]}", show_alert=True)


async def cb_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Legacy / quick persona (curious default)."""
    if not await gate(update, context) or not update.callback_query or not update.callback_query.data:
        return
    q = update.callback_query
    await safe_cb_answer(q, "Building…")
    try:
        pid = int(q.data.split(":")[1])
    except Exception:
        return
    db, client = deps(context)
    project = await db.by_id(pid)
    if not project:
        await safe_cb_answer(q, "Expired.", show_alert=True)
        return
    try:
        project = await enrich_one(db, client, project, context.bot)
        text = await persona_text(project, "curious")
        await safe_edit(q, text, persona_select_keyboard(pid))
    except Exception as exc:
        log.exception("cb_persona failed")
        await safe_cb_answer(q, f"Error: {str(exc)[:60]}", show_alert=True)


async def cb_gaps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.callback_query or not update.callback_query.data:
        return
    q = update.callback_query
    await safe_cb_answer(q, "Researching gaps…")
    try:
        pid = int(q.data.split(":")[1])
    except Exception:
        return
    db, client = deps(context)
    project = await db.by_id(pid)
    if not project:
        await safe_cb_answer(q, "Expired.", show_alert=True)
        return
    try:
        project = await enrich_one(db, client, project, context.bot)
        text = await gaps_text(project)
        await safe_edit(q, text, report_keyboard(project))
    except Exception as exc:
        log.exception("cb_gaps failed")
        await safe_cb_answer(q, f"Error: {str(exc)[:60]}", show_alert=True)


async def cb_risks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.callback_query or not update.callback_query.data:
        return
    q = update.callback_query
    await safe_cb_answer(q, "Researching risks…")
    try:
        pid = int(q.data.split(":")[1])
    except Exception:
        return
    db, client = deps(context)
    project = await db.by_id(pid)
    if not project:
        await safe_cb_answer(q, "Expired.", show_alert=True)
        return
    try:
        project = await enrich_one(db, client, project, context.bot)
        text = await risks_text(project)
        await safe_edit(q, text, report_keyboard(project))
    except Exception as exc:
        log.exception("cb_risks failed")
        await safe_cb_answer(q, f"Error: {str(exc)[:60]}", show_alert=True)


async def cb_onchain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.callback_query or not update.callback_query.data:
        return
    q = update.callback_query
    await safe_cb_answer(q, "On-chain…")
    try:
        pid = int(q.data.split(":")[1])
    except Exception:
        return
    db, client = deps(context)
    project = await db.by_id(pid)
    if not project:
        await safe_cb_answer(q, "Expired.", show_alert=True)
        return
    try:
        project = await enrich_one(db, client, project, context.bot)
        text = onchain_text(project)
        await safe_edit(q, text, report_keyboard(project))
    except Exception as exc:
        log.exception("cb_onchain failed")
        await safe_cb_answer(q, f"Error: {str(exc)[:60]}", show_alert=True)


async def cmd_early(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    db, client = deps(context)
    x_items = await search_early_x(client)
    rows = await db.qualified_since(now() - 48 * 3600)
    social_first = [
        p for p in rows
        if (p.get("twitter") or p.get("telegram")) and (p.get("liquidity_usd") or 0) < 8000
    ][:10]
    lines = ["🌱 <b>EARLY / SOCIAL-FIRST</b>"]
    if x_items:
        lines.append("From X:")
        for i, item in enumerate(x_items[:8], 1):
            user = item.get("username") or "?"
            lines.append(
                f"{i}. <b>@{esc(user)}</b> · {esc(item.get('name') or '')}\n"
                f"{esc((item.get('text') or '')[:180])}\n"
                f"https://x.com/{user}/status/{item.get('tweet_id')}"
            )
    else:
        lines.append("X search off or failed (set X_BEARER_TOKEN).")
    if social_first:
        lines += ["", "Token live but still thin — socials showed up early:"]
        for i, p in enumerate(social_first[:6], 1):
            lines.append(list_item(i, p))
    markup = list_keyboard(social_first, now() - 48 * 3600, 0, len(social_first)) if social_first else None
    await update.effective_message.reply_html("\n\n".join(lines), disable_web_page_preview=True, reply_markup=markup)


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

async def monitor_watches(app: Application) -> None:
    db: DB = app.bot_data["db"]
    client: httpx.AsyncClient = app.bot_data["http"]
    rows = await db.all_watches()
    if not rows:
        return
    seen_pids: set[int] = set()
    for row in rows:
        uid = int(row["user_id"])
        pid = int(row["project_id"])
        if pid not in seen_pids:
            seen_pids.add(pid)
            try:
                await enrich_one(db, client, row, app.bot)
            except Exception:
                log.exception("watch enrich failed %s", pid)
        fresh = await db.by_id(pid)
        if not fresh:
            continue
        new_snap = project_snapshot(fresh)
        old_snap = await db.get_snapshot(uid, pid) or {}
        diffs = snapshot_diffs(old_snap, new_snap) if old_snap else []
        await db.save_snapshot(uid, pid, new_snap)
        if not diffs:
            continue
        chain = (fresh.get("chain") or "?").upper()
        text = (
            f"⭐ <b>WATCH ALERT ({esc(chain)})</b>\n"
            f"#{pid} {esc(title_of(fresh))}\n"
            + "\n".join(esc(d) for d in diffs)
        )
        if fresh.get("telegram"):
            text += f"\n💬 Telegram: {esc(fresh.get('telegram'))}"
        text += f"\n\n💧 {esc(money(fresh.get('liquidity_usd')))} · 📊 {esc(money(fresh.get('volume_24h')))}"
        try:
            await app.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=report_keyboard(fresh),
            )
        except Exception as exc:
            log.warning("watch alert failed uid=%s: %s", uid, exc)


async def discovery_once(app: Application) -> None:
    db: DB = app.bot_data["db"]
    client: httpx.AsyncClient = app.bot_data["http"]

    for path in ("token-profiles/latest/v1", "token-profiles/recent-updates/v1"):
        profiles = await http_get(client, f"{DEX_API}/{path}")
        if isinstance(profiles, list):
            for raw in profiles:
                parsed = parse_profile(raw)
                if parsed.get("chain") in DEFAULT_CHAINS and parsed.get("token_address"):
                    await db.upsert(parsed)

    boosted = await http_get(client, f"{DEX_API}/token-boosts/latest/v1")
    if isinstance(boosted, list):
        for raw in boosted:
            chain = (raw.get("chainId") or "").lower()
            addr = raw.get("tokenAddress") or ""
            if chain in DEFAULT_CHAINS and addr:
                await db.upsert({
                    "chain": chain,
                    "token_address": addr,
                    "source": "dex-boost",
                    "qualified": False,
                })

    all_gecko = [
        ("solana", "solana"), ("eth", "ethereum"), ("base", "base"), ("bsc", "bsc"),
        ("arbitrum", "arbitrum"), ("polygon_pos", "polygon"), ("sui-network", "sui"),
        ("ton", "ton"), ("ink", "ink"), ("abstract", "abstract"), ("hyperevm", "hyperevm"),
        ("cronos", "cronos"), ("optimism", "optimism"), ("avax", "avalanche"),
        ("blast", "blast"), ("linea", "linea"), ("scroll", "scroll"), ("sonic", "sonic"),
        ("monad", "monad"),
    ]
    start = (now() // 60) % max(1, len(all_gecko) - 3)
    gecko_batch = all_gecko[start:start + 4]
    for network_id, _chain in gecko_batch:
        gecko = await http_get(
            client,
            f"{GECKO_API}/networks/{network_id}/new_pools",
            params={"page": 1, "include": "base_token,quote_token,network,dex"},
            headers={"Accept": "application/json"},
        )
        if isinstance(gecko, dict):
            for parsed in parse_gecko(gecko):
                if parsed.get("chain") in DEFAULT_CHAINS:
                    await db.upsert(parsed)
        await asyncio.sleep(1.2)

    rows = await db.unenriched(16)
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
    await monitor_watches(app)


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
            if not is_qualified(project):
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
    await send_social_alerts(app)


async def send_social_alerts(app: Application) -> None:
    db: DB = app.bot_data["db"]
    owners: list[int] = app.bot_data.get("allowed") or []
    if not owners:
        raw = await db.get_meta("owner_id")
        if raw:
            owners = [int(raw)]
            app.bot_data["allowed"] = owners
    events = await db.pending_social_events(8)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for ev in events:
        grouped.setdefault(int(ev["project_id"]), []).append(ev)
    for pid, evs in grouped.items():
        project = await db.by_id(pid)
        if not project:
            for ev in evs:
                await db.mark_social_alerted(ev["id"])
            continue
        kinds = [e["kind"] for e in evs]
        identity_kinds = [k for k in kinds if k in {"x", "telegram", "website", "discord"}]
        if not identity_kinds:
            for ev in evs:
                await db.mark_social_alerted(ev["id"])
            continue
        kinds = identity_kinds
        for user_id in owners:
            user = await db.ensure_user(user_id)
            if not user.get("alerts_enabled", 1):
                continue
            try:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=social_alert_text(project, kinds),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=report_keyboard(project),
                )
            except Exception as exc:
                log.warning("social alert failed: %s", exc)
        for ev in evs:
            await db.mark_social_alerted(ev["id"])


async def discovery_loop(app: Application) -> None:
    app.bot_data["scan_alive"] = True
    await asyncio.sleep(5)
    while True:
        try:
            app.bot_data["scan_alive"] = True
            last_cmd = int(app.bot_data.get("last_command_at") or 0)
            busy = last_cmd and (now() - last_cmd) < 30
            last_scan = int(app.bot_data.get("last_scan_at") or 0)
            interval = int(os.getenv("DISCOVERY_INTERVAL_SEC", "90"))
            due = (now() - last_scan) >= interval
            idle_kick = last_cmd and (now() - last_cmd) >= 30 and (now() - last_scan) >= 30
            if (due or idle_kick or not last_scan) and not busy:
                await discovery_once(app)
                app.bot_data["last_scan_at"] = now()
                log.info("discovery cycle ok")
        except Exception:
            log.exception("discovery cycle failed")
        await asyncio.sleep(8)


async def note_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        context.application.bot_data["last_command_at"] = now()


async def on_start(app: Application) -> None:
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "Start Scout"),
                BotCommand("help", "Command list"),
                BotCommand("menu", "Insertable command menu"),
                BotCommand("newtokens", "New projects catch-up"),
                BotCommand("project", "Full report by CA or id"),
                BotCommand("jobs", "Opportunity shortlist"),
                BotCommand("digest", "Top picks window"),
                BotCommand("approach", "Choose persona / voice"),
                BotCommand("persona", "Generate persona messages"),
                BotCommand("ask", "Ask AI about a project"),
                BotCommand("gaps", "Researched gaps analysis"),
                BotCommand("risks", "Researched risk brief"),
                BotCommand("watch", "Watch CA until socials"),
                BotCommand("unwatch", "Remove from watchlist"),
                BotCommand("watchlist", "Your watches"),
                BotCommand("cg", "CoinGecko listings"),
                BotCommand("cmc", "CMC new listings"),
                BotCommand("early", "Early / thin liquidity"),
                BotCommand("alerts", "alerts on|off"),
                BotCommand("status", "Scanner + keys status"),
            ]
        )
        log.info("Telegram command menu registered")
    except Exception as exc:
        log.warning("set_my_commands failed: %s", exc)

    db: DB = app.bot_data["db"]
    await db.connect()
    app.bot_data["http"] = httpx.AsyncClient(headers={"User-Agent": "Web3ProjectScout/1.0"}, timeout=25)
    app.bot_data["last_command_at"] = 0
    app.bot_data["last_scan_at"] = 0
    app.bot_data["scan_alive"] = False
    owner = await db.get_meta("owner_id")
    if owner and not app.bot_data.get("allowed"):
        app.bot_data["allowed"] = [int(owner)]
    app.create_task(discovery_loop(app), name="scout-discovery")
    me = await app.bot.get_me()
    log.info("Logged in as @%s  build=%s", me.username, SCOUT_BUILD)


async def on_stop(app: Application) -> None:
    http = app.bot_data.get("http")
    if http:
        await http.aclose()
    db = app.bot_data.get("db")
    if db:
        await db.close()


async def cmd_risks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /risks <id|CA|chain:CA>")
        return
    db, client = deps(context)
    project = await resolve_id_or_ca(db, client, " ".join(context.args), context.bot)
    if not project:
        await update.effective_message.reply_text("Could not resolve.")
        return
    project = await enrich_one(db, client, project, context.bot)
    text = await risks_text(project)
    await update.effective_message.reply_html(
        text, disable_web_page_preview=True, reply_markup=report_keyboard(project)
    )


async def cmd_gaps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /gaps <project_id|CA|chain:CA>")
        return
    db, client = deps(context)
    project = await resolve_id_or_ca(db, client, " ".join(context.args), context.bot)
    if not project:
        await update.effective_message.reply_text("Could not resolve id/CA.")
        return
    project = await enrich_one(db, client, project, context.bot)
    text = await gaps_text(project)
    await update.effective_message.reply_html(
        text, disable_web_page_preview=True, reply_markup=report_keyboard(project)
    )


async def gaps_text(p: dict[str, Any]) -> str:
    """Researched gaps. Verify presence before claiming missing. Meme vs utility aware."""
    kind = classify_project(p)
    is_meme = kind.get("type") == "meme"
    ctx = _project_ctx(p)
    has_web = bool(p.get("website"))
    has_x = bool(p.get("twitter"))
    has_tg = bool(p.get("telegram"))
    has_docs = bool(p.get("docs"))

    if is_meme:
        prompt = (
            f"{ctx}\n\n"
            "This is a MEME / culture coin. Do NOT demand whitepaper or enterprise docs.\n"
            "CRITICAL: Only claim something is missing if the data above truly lacks it. "
            f"Website present: {has_web}. X present: {has_x}. TG present: {has_tg}. Docs present: {has_docs}.\n"
            "Focus on meme-native gaps: unclear CA posts, scattered lore, dead TG energy, "
            "no LP transparency, mixed signals, weak mascot story, confusing buy path.\n"
            "Return clean sections:\nMEME_VIBE\nWHAT_WORKS\nMEME_PROBLEMS\nCOMMUNITY_GAPS\n"
            "TRUST_SHORTCUTS\nCONTENT_ANGLES\nFIXES\n"
            "Tone: sharp, meme-literate, practical. Human critique who actually looked."
        )
    else:
        prompt = (
            f"{ctx}\n\n"
            "This looks UTILITY / product-led.\n"
            "CRITICAL: Only claim something is missing if the data above truly lacks it. "
            f"Website present: {has_web}. X present: {has_x}. TG present: {has_tg}. Docs present: {has_docs}.\n"
            "Dig into product UX clarity, trust, investor-grade questions.\n"
            "Prefer: differentiation, retention loop, who pays, why token, admin keys, proof of usage.\n"
            "Labels:\nPRODUCT_UX\nWHAT_SITE_ALREADY_SAYS\nREAL_GAPS\nINVESTOR_QUESTIONS\n"
            "TRUST_GAPS\nCONTENT_OPPORTUNITIES\nFIXES\n"
            "Evidence-based only. Sound like someone who tested the product."
        )
    out = await llm_write(prompt)
    header = [
        "🔍 <b>GAPS &amp; FIT</b>",
        f"<b>{esc(title_of(p))}</b> · {kind['label']}",
        f"💰 MCAP: <b>{esc(money(p.get('market_cap')))}</b>",
        f"Verified links: Web {mark(has_web)} · X {mark(has_x)} · TG {mark(has_tg)} · Docs {mark(has_docs)}",
        "",
    ]
    if not out:
        return "\n".join(header + _gaps_fallback(p, is_meme))
    text = out.replace("**", "")
    pretty = []
    for ln in text.splitlines()[:90]:
        ln = ln.strip()
        if not ln:
            pretty.append("")
            continue
        if ln.isupper() and len(ln) < 40:
            pretty.append(f"\n<b>{esc(ln.replace('_', ' ').title())}</b>")
        elif ln.startswith("•") or ln.startswith("-"):
            pretty.append("• " + esc(ln.lstrip("•- ").strip()))
        else:
            pretty.append(esc(ln))
    return "\n".join(header + pretty)


def _project_ctx(p: dict[str, Any]) -> str:
    comm = community(p)
    return (
        f"Name: {title_of(p)}\nChain: {p.get('chain')}\nCA: {p.get('token_address')}\n"
        f"Website: {p.get('website')}\nX: {p.get('twitter')}\nTG: {p.get('telegram')}\n"
        f"Docs: {p.get('docs')}\n"
        f"Desc: {p.get('description')}\nSite about: {comm.get('site_about')}\n"
        f"TG about: {comm.get('tg_about')}\nLiq: {p.get('liquidity_usd')} MCAP: {p.get('market_cap')}\n"
        f"Holders: {comm.get('holders')} Deployer: {comm.get('deployer')}\n"
        f"Honeypot: {comm.get('honeypot')} BuyTax: {comm.get('buy_tax')} SellTax: {comm.get('sell_tax')}"
    )


def _gaps_fallback(p: dict[str, Any], is_meme: bool) -> list[str]:
    if is_meme:
        return [
            "<b>Meme problems to check</b>",
            "• " + ("Official TG missing — culture needs a campfire" if not p.get("telegram") else "TG linked — is the lore pinned?"),
            "• " + ("X missing" if not p.get("twitter") else "X linked — is CA consistent in bio?"),
            "• LP / mint status not obvious from free data — community will ask.",
            "",
            "<b>Meme-native content angles</b>",
            "• One clear 'how to buy' card (CA + chain + link).",
            "• Lore thread that matches the ticker energy.",
            "• Transparent LP post > fake utility roadmap.",
        ]
    return [
        "<b>Product / trust gaps</b>",
        "• " + ("No site" if not p.get("website") else "Site present — check if product path is obvious above the fold"),
        "• " + ("No docs" if not p.get("docs") else "Docs linked"),
        "• Token role vs product value may need sharper explanation",
        "",
        "<b>Investor-style questions</b>",
        "• Who is the paying user if the token did not exist?",
        "• What is non-replicable vs the nearest competitor?",
        "• Which admin powers remain on the contracts?",
    ]


async def risks_text(p: dict[str, Any]) -> str:
    """Clean risk brief cards. Research-first. No markdown tables."""
    kind = classify_project(p)
    is_meme = kind.get("type") == "meme"
    ctx = _project_ctx(p)
    has_web = bool(p.get("website"))
    has_x = bool(p.get("twitter"))
    has_tg = bool(p.get("telegram"))
    has_docs = bool(p.get("docs"))
    comm = community(p)

    prompt = (
        f"{ctx}\n\nType: {'MEME' if is_meme else 'UTILITY'}\n"
        f"Verified: Website={has_web}, X={has_x}, TG={has_tg}, Docs={has_docs}\n\n"
        "RESEARCH PROTOCOL: Do NOT claim missing socials/docs if data shows they exist. "
        "Only state risks supported by the observed data (on-chain numbers, site text, social presence).\n"
        "Produce a clean risk brief with these exact section headers:\n"
        "ONCHAIN_RISKS\nWEBSITE_RISKS\nSOCIALS_RISKS\nINVESTOR_RED_FLAGS\nHOW_TO_FIX\n"
        "Under each risk put a short '👉 Fix: ...' line.\n"
        "For memes: focus on transparency, LP, narrative consistency — skip whitepaper lectures.\n"
        "For utility: focus on product trust, admin power, unclear token capture.\n"
        "Be specific. No invented holder %, tax, or LP lock facts."
    )
    out = await llm_write(prompt)

    lines = [
        f"⚠️ <b>RISK BRIEF: {esc(title_of(p))}</b>",
        f"🏷 Sector / Type: {kind['label']}",
        f"💰 MCAP: {esc(money(p.get('market_cap')))} · 💧 Liquidity: {esc(money(p.get('liquidity_usd')))}",
        f"Verified: Web {mark(has_web)} · X {mark(has_x)} · TG {mark(has_tg)} · Docs {mark(has_docs)}",
        "",
        "⛓ <b>ON-CHAIN (observed)</b>",
        f"Deployer: <code>{esc(comm.get('deployer') or '—')}</code>",
        f"Holders: {esc(comm.get('holders') if comm.get('holders') is not None else '—')}",
        f"Rugcheck: {esc(comm.get('rugcheck_score') if comm.get('rugcheck_score') is not None else '—')}",
    ]
    if comm.get("honeypot") is not None:
        lines.append(f"Honeypot: {'YES ⚠️' if comm.get('honeypot') else 'No'}")
    if float(p.get("liquidity_usd") or 0) < 5000:
        lines.append("• Thin liquidity — market-cap moves can be fragile.")

    lines.append("")
    lines.append("🚨 <b>IDENTIFIED RISKS &amp; FIXES</b>")

    if not out:
        lines += [
            "",
            "• On-Chain Risks:",
            "  - Limited free data — verify LP lock & mint authority on explorer.",
            "  👉 Fix: Publish verified LP + admin status in one official place.",
            "",
            "• Website & Security Risks:",
            f"  - {'No website indexed' if not has_web else 'Website present — check trust messaging'}.",
            "  👉 Fix: Keep CA + official links consistent everywhere.",
            "",
            "• Socials & Community Risks:",
            f"  - X: {'missing' if not has_x else 'present'} · TG: {'missing' if not has_tg else 'present'}.",
            "  👉 Fix: Single source of truth for links + CA.",
            "",
            "• Major Investor Red Flags:",
            "  - Incomplete free data on taxes / LP / admin powers.",
            "  👉 Fix: Transparent one-pager covering the above.",
        ]
        return "\n".join(lines)

    text = out.replace("**", "")
    for ln in text.splitlines()[:80]:
        ln = ln.strip()
        if not ln:
            lines.append("")
            continue
        if ln.isupper() and len(ln) < 36:
            title = ln.replace("_", " ").title()
            lines.append(f"\n<b>• {esc(title)}</b>")
        elif ln.startswith("👉") or "fix:" in ln.lower():
            lines.append("  " + esc(ln))
        elif ln.startswith("•") or ln.startswith("-"):
            lines.append("  - " + esc(ln.lstrip("•- ").strip()))
        else:
            lines.append(esc(ln))
    return "\n".join(lines)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage:\n/ask <project_id|CA> <question>\n\n"
            "Example:\n/ask 42 how does staking work?"
        )
        return
    db, client = deps(context)
    first = args[0]
    if ":" in first and not first.isdigit():
        query = first
        question = " ".join(args[1:]).strip()
    elif first.isdigit() or len(first) >= 20:
        query = first
        question = " ".join(args[1:]).strip()
    else:
        query = first
        question = " ".join(args[1:]).strip()
    if not question:
        await update.effective_message.reply_text("Add a question after the id/CA.")
        return
    project = await resolve_id_or_ca(db, client, query, context.bot)
    if not project:
        await update.effective_message.reply_text("Could not resolve id/CA.")
        return
    project = await enrich_one(db, client, project, context.bot)
    text = await ask_personas_text(project, question)
    await update.effective_message.reply_html(
        text, disable_web_page_preview=True, reply_markup=report_keyboard(project)
    )


async def ask_personas_text(p: dict[str, Any], question: str) -> str:
    s = score_project(p)
    comm = community(p)
    what = p.get("description") or comm.get("site_about") or comm.get("tg_about") or "Thin public description."
    kind = classify_project(p)
    prompt = (
        f"Project: {title_of(p)}\nChain: {p.get('chain')}\nType: {kind['label']}\n"
        f"Website: {p.get('website')}\nX: {p.get('twitter')}\nTelegram: {p.get('telegram')}\n"
        f"About: {what}\n"
        f"Community question:\n{question}\n\n"
        f"Write 4-5 ready-to-post reply options in different human voices "
        f"(curious, investor, holder, strategist). "
        f"Sound real. No AI meta. Variation seed {random.randint(1,9999)}.\n"
        f"Format:\nOPTION 1\n<text>\nOPTION 2\n<text>..."
    )
    generated = await llm_write(prompt)
    header = [
        "🗣 <b>PERSONA REPLIES</b>",
        f"<b>{esc(title_of(p))}</b> · {kind['label']} · {s['band']} {s['score']}/100",
        "",
        "❓ <b>Question</b>",
        f"<i>{esc(question)[:500]}</i>",
        "",
    ]
    if generated:
        body = [copyable_brief(generated)]
    else:
        body = [
            f"⚠️ AI offline",
            "",
            "<b>OPTION 1</b>",
            f"<code>Good question. From what I read about {esc(title_of(p))}, the public copy is still thin on this — does the team have a short FAQ?</code>",
            "<b>OPTION 2</b>",
            "<code>Pin a 3-line answer so the same question stops looping.</code>",
        ]
    return "\n".join(header + body + ["", f"X: {esc(p.get('twitter') or '—')}", f"TG: {esc(p.get('telegram') or '—')}"])


async def cmd_cg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    db, client = deps(context)
    await update.effective_message.reply_text("🦎 Scanning CoinGecko trending…")
    lines = ["🦎 <b>COINGECKO · LATEST + SOCIALS</b>", ""]
    saved: list[dict[str, Any]] = []
    try:
        resp = await client.get(
            "https://api.coingecko.com/api/v3/search/trending",
            timeout=20,
            headers={"Accept": "application/json"},
        )
        if resp.status_code >= 400:
            await update.effective_message.reply_text(f"CoinGecko HTTP {resp.status_code}")
            return
        coins = (resp.json().get("coins") or [])[:15]
        for item in coins:
            c = item.get("item") or {}
            cid = c.get("id")
            if not cid:
                continue
            await asyncio.sleep(0.3)
            detail = await http_get(
                client,
                f"https://api.coingecko.com/api/v3/coins/{cid}",
                params={"localization": "false", "tickers": "false", "market_data": "false", "community_data": "true", "developer_data": "false"},
            )
            if not isinstance(detail, dict):
                continue
            links = detail.get("links") or {}
            homepage = next((u for u in (links.get("homepage") or []) if u), None)
            tw = links.get("twitter_screen_name") or ""
            tg = links.get("telegram_channel_identifier") or ""
            chat = next((u for u in (links.get("chat_url") or []) if u), None)
            if not (homepage or tw or tg or chat):
                continue
            platforms = detail.get("platforms") or {}
            chain, addr = None, None
            for plat, contract in platforms.items():
                if not contract:
                    continue
                mapped = GECKO_CHAIN.get(plat.lower()) or CMC_CHAIN_MAP.get(plat.lower()) or plat.lower()
                if mapped in DEFAULT_CHAINS or mapped:
                    chain, addr = mapped if mapped in DEFAULT_CHAINS else plat.lower(), contract
                    if chain in DEFAULT_CHAINS:
                        break
            if not addr:
                continue
            if chain not in DEFAULT_CHAINS:
                chain = "ethereum" if addr.startswith("0x") else chain
            proj = {
                "chain": chain or "ethereum",
                "token_address": addr,
                "name": detail.get("name") or c.get("name"),
                "symbol": detail.get("symbol") or c.get("symbol"),
                "website": homepage,
                "twitter": f"https://x.com/{tw}" if tw else None,
                "telegram": f"https://t.me/{tg}" if tg else (chat if chat and "t.me" in str(chat) else None),
                "discord": chat if chat and "discord" in str(chat).lower() else None,
                "source": "coingecko",
                "qualified": True,
            }
            pid, _ = await db.upsert(proj)
            row = await db.by_id(pid)
            if row:
                saved.append(row)
                lines.append(
                    f"#{row['id']} <b>{esc(title_of(row))}</b> · {esc((row.get('chain') or '').title())}\n"
                    f"🌐 {mark(row.get('website'))}  𝕏 {mark(row.get('twitter'))}  💬 {mark(row.get('telegram'))}"
                )
            if len(saved) >= 10:
                break
        if not saved:
            lines.append("No trending coins with socials + contract right now.")
    except Exception as exc:
        await update.effective_message.reply_text(f"CoinGecko failed: {exc}")
        return
    markup = list_keyboard(saved, now() - 86400, 0, len(saved)) if saved else None
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True, reply_markup=markup)


async def cmd_cmc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await gate(update, context) or not update.effective_message:
        return
    db, client = deps(context)
    await update.effective_message.reply_text("📈 Scanning CMC new listings…")
    lines = ["📈 <b>CMC · NEWLY LISTED + SOCIALS</b>", ""]
    saved: list[dict[str, Any]] = []
    try:
        resp = await client.get(
            "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing",
            params={"start": 1, "limit": 25, "sortBy": "date_added", "sortType": "asc", "convert": "USD", "cryptoType": "all", "tagType": "all"},
            timeout=25,
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; Web3ProjectScout/1.0)"},
        )
        if resp.status_code >= 400:
            await update.effective_message.reply_text(f"CMC HTTP {resp.status_code}")
            return
        payload = resp.json()
        data = payload.get("data") or {}
        items = data.get("cryptoCurrencyList") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
        for c in items[:25]:
            plat = c.get("platform") or {}
            addr = (plat.get("token_address") or "").strip()
            if not addr:
                continue
            plat_name = (plat.get("name") or plat.get("slug") or "").lower()
            chain = CMC_CHAIN_MAP.get(plat_name) or CMC_CHAIN_MAP.get((plat.get("slug") or "").lower())
            if not chain:
                for key, val in CMC_CHAIN_MAP.items():
                    if key in plat_name:
                        chain = val
                        break
            if not chain:
                chain = "ethereum" if addr.startswith("0x") else "solana"
            cid = c.get("id")
            website = twitter = telegram = discord = None
            if cid:
                await asyncio.sleep(0.2)
                detail = await http_get(
                    client,
                    "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail",
                    params={"id": cid},
                    headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; Web3ProjectScout/1.0)"},
                )
                if isinstance(detail, dict):
                    d = detail.get("data") if isinstance(detail.get("data"), dict) else detail
                    urls = (d or {}).get("urls") or {}
                    website = next((u for u in (urls.get("website") or []) if u), None)
                    twitter = next((u for u in (urls.get("twitter") or []) if u), None)
                    chats = urls.get("chat") or []
                    for u in chats:
                        if not u:
                            continue
                        if "t.me" in u:
                            telegram = u
                        elif "discord" in u.lower():
                            discord = u
            if not (website or twitter or telegram or discord):
                continue
            added_raw = c.get("dateAdded") or ""
            try:
                if isinstance(added_raw, str) and "T" in added_raw:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(added_raw.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
                    if age_days > 7:
                        continue
            except Exception:
                pass
            price = None
            quotes = c.get("quotes")
            if isinstance(quotes, list) and quotes:
                price = quotes[0].get("price")
            added = c.get("dateAdded") or ""
            if isinstance(added, str) and "T" in added:
                added = added.split("T")[0]
            proj = {
                "chain": chain,
                "token_address": addr,
                "name": c.get("name"),
                "symbol": c.get("symbol"),
                "website": website,
                "twitter": twitter,
                "telegram": telegram,
                "discord": discord,
                "price_usd": price,
                "source": "cmc",
                "qualified": True,
            }
            pid, _ = await db.upsert(proj)
            row = await db.by_id(pid)
            if not row:
                continue
            saved.append(row)
            lines.append(
                f"#{row['id']} <b>{esc(title_of(row))}</b> · {esc((row.get('chain') or '').title())}\n"
                f"listed {esc(added)} · {esc(money(price) if price is not None else '—')}\n"
                f"🌐 {mark(row.get('website'))}  𝕏 {mark(row.get('twitter'))}  💬 {mark(row.get('telegram'))}"
            )
            if len(saved) >= 10:
                break
        if not saved:
            lines.append("No new CMC listings with socials + contract in this batch.")
    except Exception as exc:
        await update.effective_message.reply_text(f"CMC failed: {exc}")
        return
    markup = list_keyboard(saved, now() - 86400, 0, len(saved)) if saved else None
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True, reply_markup=markup)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN. That is the only required variable.")
    allowed_ids = env_ints("ALLOWED_USER_IDS") or env_ints("ALERT_USER_IDS")
    db = DB(pick_db_path())
    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .post_init(on_start)
        .post_shutdown(on_stop)
        .build()
    )
    app.bot_data["db"] = db
    app.bot_data["allowed"] = allowed_ids
    app.add_handler(TypeHandler(Update, note_activity), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("newtokens", cmd_newtokens))
    app.add_handler(CommandHandler("project", cmd_project))
    app.add_handler(CommandHandler("research", cmd_project))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("approach", cmd_approach))
    app.add_handler(CommandHandler("persona", cmd_persona))
    app.add_handler(CommandHandler("early", cmd_early))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("gaps", cmd_gaps))
    app.add_handler(CommandHandler("risks", cmd_risks))
    app.add_handler(CommandHandler("cg", cmd_cg))
    app.add_handler(CommandHandler("cmc", cmd_cmc))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(cb_newtokens, pattern=r"^nt:"))
    app.add_handler(CallbackQueryHandler(cb_investigate, pattern=r"^inv:"))
    app.add_handler(CallbackQueryHandler(cb_approach, pattern=r"^ap:"))
    app.add_handler(CallbackQueryHandler(cb_persona, pattern=r"^ps:"))
    app.add_handler(CallbackQueryHandler(cb_persona_select, pattern=r"^pn:"))
    app.add_handler(CallbackQueryHandler(cb_onchain, pattern=r"^oc:"))
    app.add_handler(CallbackQueryHandler(cb_gaps, pattern=r"^gp:"))
    app.add_handler(CallbackQueryHandler(cb_risks, pattern=r"^rk:"))
    app.add_handler(CallbackQueryHandler(cb_watch, pattern=r"^w:"))
    log.info("Polling Telegram… build=%s", SCOUT_BUILD)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
