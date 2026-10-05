#!/usr/bin/env python3
"""
momirdeck - builds the offline card corpus for MomirSunmi.

Turns Scryfall's bulk data into two files the Sunmi V2 can serve entirely offline:

    momir.db    SQLite: every rollable card (name, mana cost, mana value, type
                line and type mask, oracle text, P/T, loyalty, Scryfall URL)
                plus an index into...
    art.pack    a single append-only blob of pre-dithered 1-bit artwork, 384 px
                wide, ready to hand straight to the thermal printer.

Why one pack file instead of 30k PNGs: the V2's eMMC hates small files, and a
1-bit raster needs no decoding at print time - it *is* the ESC/POS payload.

Usage:
    python momirdeck.py build-db           # creatures, artifacts, enchantments,
                                           # planeswalkers, battles, instants, sorceries
    python momirdeck.py build-tokens       # what those cards put onto the battlefield
    python momirdeck.py build-art          # resumable, run it again if it dies
    python momirdeck.py stats
    python momirdeck.py push               # adb push to the device

Requires: Python 3.9+, Pillow. Everything else is stdlib on purpose.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

try:
    from PIL import Image, ImageEnhance, ImageOps
except ImportError:
    sys.exit("Pillow is required:  pip install Pillow")

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

USER_AGENT = "MomirSunmi/1.0 (+https://github.com/MagieAlex/MomirSunmi)"
BULK_INDEX = "https://api.scryfall.com/bulk-data"

# /cards/collection accepts at most 75 identifiers per request.
COLLECTION_BATCH = 75

PRINT_WIDTH_DOTS = 384          # Sunmi V2: 58 mm paper, 203 dpi -> 48 mm printable
ART_STRIDE = PRINT_WIDTH_DOTS // 8   # 48 bytes per 1-bit row

PACK_MAGIC = b"MOMIRART"
PACK_VERSION = 1
PACK_HEADER_LEN = 16

SCHEMA_VERSION = 2      # 2 added cards.type_mask and cards.loyalty

# A printed slip has to slide into a standard Magic sleeve, so it must not be
# longer than a real card: 63 x 88 mm. 203 dpi = 8 dots/mm, so 88 mm = 704 dots.
# The art alone must leave room for the name, mana cost and type line, hence the
# default cap below. See docs/printing.md for the full length budget.
DEFAULT_MAX_ART_HEIGHT = 300    # dots (37.5 mm)

# Layouts that are not real, castable cards.
EXCLUDED_LAYOUTS = {
    "token", "double_faced_token", "emblem", "art_series", "vanguard",
    "scheme", "planar", "augment", "host", "reversible_card",
}

# Un-sets, promos-that-aren't-cards, Alchemy-only products.
EXCLUDED_SET_TYPES = {"funny", "memorabilia", "token", "minigame", "alchemy"}

# Formats that only ever contain real, black-bordered paper cards. If a card is
# anything other than "not_legal" in one of these, it exists on cardboard.
#
# This replaces the obvious-looking `digital` / `games` check, which is a trap:
# oracle_cards picks one representative printing per card, and for older cards
# that is often an MTGO-only reprint. Memnite's representative is the MTGO duel
# deck (digital=true, games=["mtgo"]) even though it is a perfectly normal paper
# card. Legality is printing-independent, so it gets those right - and it also
# correctly rejects Mystery Booster playtest cards, which *are* paper but were
# never tournament-legal. See docs/data-pipeline.md.
PAPER_LEGALITY_FORMATS = ("vintage", "legacy", "commander")

# Layouts where both halves sit on the physical front of the card, so the whole
# type line counts. Everything else is judged by its front face alone.
BOTH_HALVES_ON_FRONT = {"split", "adventure", "flip"}

# Card type bits, mirrored value-for-value in the app, which rolls with
# `WHERE type_mask & :mask != 0`. Never renumber them: a corpus built last month
# is still on the device.
TYPE_CREATURE     = 1
TYPE_ARTIFACT     = 2
TYPE_ENCHANTMENT  = 4
TYPE_PLANESWALKER = 8
TYPE_LAND         = 16
TYPE_BATTLE       = 32
TYPE_INSTANT      = 64
TYPE_SORCERY      = 128

# A card gets every bit its type line names, so an artifact creature answers to a
# roll for either. Order matters only for the breakdown build-db prints.
TYPE_WORDS = (
    ("Creature", TYPE_CREATURE),
    ("Artifact", TYPE_ARTIFACT),
    ("Enchantment", TYPE_ENCHANTMENT),
    ("Planeswalker", TYPE_PLANESWALKER),
    ("Land", TYPE_LAND),
    ("Battle", TYPE_BATTLE),
    ("Instant", TYPE_INSTANT),
    ("Sorcery", TYPE_SORCERY),
)

# Lands are the one type left out. Being handed a random Wastes is not a game
# anybody wants to play, and the 1,100-odd of them would cost 15 MB of art.pack
# and two minutes of Scryfall's bandwidth for slips that never get used. The LAND
# bit is still set where it belongs, so Dryad Arbor comes along anyway - on its
# creature bit.
ADMITTED_TYPES = (TYPE_CREATURE | TYPE_ARTIFACT | TYPE_ENCHANTMENT
                  | TYPE_PLANESWALKER | TYPE_BATTLE | TYPE_INSTANT | TYPE_SORCERY)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, flush=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_url(url: str, accept: str = "application/json", timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    return urllib.request.urlopen(req, timeout=timeout)


class RateLimiter:
    """Scryfall asks for <=10 requests/second. Be a good citizen."""

    def __init__(self, rps: float):
        self._interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self._interval


# --------------------------------------------------------------------------
# Card filtering - this is the definition of what the corpus holds
# --------------------------------------------------------------------------

def _front_face(card: dict) -> dict:
    faces = card.get("card_faces")
    if faces:
        return faces[0]
    return card


def type_line_for_filter(card: dict) -> str:
    """The type line that decides what this card is."""
    if card.get("layout") in BOTH_HALVES_ON_FRONT:
        return card.get("type_line", "")
    face = _front_face(card)
    return face.get("type_line") or card.get("type_line", "")


def is_paper_card(card: dict) -> bool:
    """True if this card was ever printed on real cardboard and legal to play."""
    legalities = card.get("legalities") or {}
    return any(legalities.get(f, "not_legal") != "not_legal" for f in PAPER_LEGALITY_FORMATS)


def type_mask_for(type_line: str) -> int:
    """The TYPE_* bits a type line names, supertypes and subtypes ignored."""
    # Cutting at the em dash drops the subtypes, and on the layouts whose two
    # halves both sit on the front it does a second job: it is what keeps Brazen
    # Borrower a creature card rather than half an instant. A split card such as
    # Wear // Tear has no em dash at all and rightly keeps both of its bits.
    head = type_line.split("—", 1)[0]
    mask = 0
    for word, bit in TYPE_WORDS:
        if word in head:
            mask |= bit
    return mask


def corpus_type_mask(card: dict) -> int:
    """
    This card's type bits if it belongs in the corpus, 0 if it does not.

    Roughly the Scryfall query `-is:alchemy -is:funny -is:digital` over the seven
    admitted types, with two deliberate deviations, both documented in
    docs/data-pipeline.md:

      1. A modal/transforming card is judged by its *front* face. Scryfall's
         `t:creature` matches either face, so it also returns things like
         Westvale Abbey // Ormendahl. You roll a *card*, and Westvale Abbey is a
         land.
      2. Paper-ness is decided by legality, not by the `digital` flag - see the
         comment on PAPER_LEGALITY_FORMATS.

    Only the admitted set widened when the app learned to roll more than
    creatures; every quality rule below predates it and is unchanged.
    """
    if card.get("layout") in EXCLUDED_LAYOUTS:
        return 0
    if card.get("set_type") in EXCLUDED_SET_TYPES:
        return 0
    if not is_paper_card(card):
        return 0
    # Alchemy rebalanced cards are prefixed "A-" and are digital-only.
    if card.get("name", "").startswith("A-"):
        return 0

    type_line = type_line_for_filter(card)
    if "Token" in type_line:
        return 0

    mask = type_mask_for(type_line)
    return mask if mask & ADMITTED_TYPES else 0


def extract_card(card: dict, type_mask: int) -> "CardRow | None":
    face = _front_face(card)

    mana_cost = face.get("mana_cost") or card.get("mana_cost") or ""
    oracle_text = face.get("oracle_text") or card.get("oracle_text") or ""

    # For split/adventure cards, show both halves' text - it is one physical card.
    if card.get("layout") in BOTH_HALVES_ON_FRONT and card.get("card_faces"):
        parts = []
        for f in card["card_faces"]:
            head = f.get("name", "")
            cost = f.get("mana_cost", "")
            body = f.get("oracle_text", "")
            parts.append(f"{head} {cost}\n{body}".strip())
        oracle_text = "\n\n".join(parts)
        mana_cost = card.get("mana_cost") or mana_cost

    image_uris = card.get("image_uris") or face.get("image_uris") or {}

    oracle_id = card.get("oracle_id")
    if not oracle_id:
        return None

    return CardRow(
        oracle_id=oracle_id,
        name=card.get("name", ""),
        mana_cost=mana_cost,
        mv=int(round(float(card.get("cmc") or 0))),
        type_line=card.get("type_line") or face.get("type_line") or "",
        type_mask=type_mask,
        oracle_text=oracle_text,
        power=face.get("power") or card.get("power"),
        toughness=face.get("toughness") or card.get("toughness"),
        # Front face only, so Jace, Vryn's Prodigy prints as the creature he is
        # instead of borrowing the 5 off his flip side.
        loyalty=face.get("loyalty"),
        # Colour identity, not colour: it is defined on the whole card, so it is
        # always top-level even for a two-faced one.
        color_identity="".join(card.get("color_identity") or []),
        scryfall_uri=(card.get("scryfall_uri") or "").split("?")[0],
        art_uri=image_uris.get("art_crop") or "",
    )


@dataclass
class CardRow:
    oracle_id: str
    name: str
    mana_cost: str
    mv: int
    type_line: str
    type_mask: int
    oracle_text: str
    power: "str | None"
    toughness: "str | None"
    loyalty: "str | None"
    color_identity: str
    scryfall_uri: str
    art_uri: str


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE TABLE IF NOT EXISTS cards (
    oracle_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    mana_cost    TEXT NOT NULL DEFAULT '',
    mv           INTEGER NOT NULL,
    type_line    TEXT NOT NULL DEFAULT '',
    -- TYPE_* bits of the front face. The app rolls with `type_mask & :mask != 0`.
    type_mask    INTEGER NOT NULL DEFAULT 0,
    oracle_text  TEXT NOT NULL DEFAULT '',
    power        TEXT,
    toughness    TEXT,
    -- Starting loyalty, planeswalkers only. A planeswalker slip without it is
    -- not something you can play with.
    loyalty      TEXT,
    -- WUBRG letters. Drives the colour of the print animation on the device.
    color_identity TEXT NOT NULL DEFAULT '',
    scryfall_uri TEXT NOT NULL DEFAULT '',
    art_uri      TEXT NOT NULL DEFAULT '',
    art_off      INTEGER,
    art_len      INTEGER,
    art_h        INTEGER
);
CREATE INDEX IF NOT EXISTS ix_cards_mv ON cards(mv);
CREATE INDEX IF NOT EXISTS ix_cards_art ON cards(art_off) WHERE art_off IS NULL;

-- Scanning a printed slip resolves the QR straight back to a row.
CREATE INDEX IF NOT EXISTS ix_cards_uri ON cards(scryfall_uri);

-- Tokens a card can put onto the battlefield. Same shape as cards, minus the
-- things a token does not have (mana cost, mana value).
CREATE TABLE IF NOT EXISTS tokens (
    oracle_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    type_line    TEXT NOT NULL DEFAULT '',
    oracle_text  TEXT NOT NULL DEFAULT '',
    power        TEXT,
    toughness    TEXT,
    colors       TEXT NOT NULL DEFAULT '',
    scryfall_uri TEXT NOT NULL DEFAULT '',
    art_uri      TEXT NOT NULL DEFAULT '',
    art_off      INTEGER,
    art_len      INTEGER,
    art_h        INTEGER
);
CREATE TABLE IF NOT EXISTS card_tokens (
    card_oracle_id  TEXT NOT NULL,
    token_oracle_id TEXT NOT NULL,
    PRIMARY KEY (card_oracle_id, token_oracle_id)
);
CREATE INDEX IF NOT EXISTS ix_card_tokens ON card_tokens(card_oracle_id);
"""


# Columns added after the first release. CREATE TABLE IF NOT EXISTS will not add
# them to a database that already exists, so they are applied by hand.
MIGRATIONS = [
    ("cards", "color_identity", "TEXT NOT NULL DEFAULT ''"),
    ("cards", "type_mask", "INTEGER NOT NULL DEFAULT 0"),
    ("cards", "loyalty", "TEXT"),
]

# An index over a migrated column cannot live in SCHEMA: that runs first, on a
# database where the column does not exist yet.
LATE_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_cards_type_mv ON cards(type_mask, mv);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=DELETE")   # keep it to a single file for adb push

    for table, column, decl in MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            log(f"  migrating: adding {table}.{column}")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            if (table, column) == ("cards", "type_mask"):
                _backfill_type_mask(conn)

    conn.executescript(LATE_INDEXES)
    conn.commit()
    return conn


def _backfill_type_mask(conn: sqlite3.Connection) -> None:
    # Left at the default 0 the column would match no roll at all, which turns a
    # corpus somebody built last month into an app that never prints anything.
    # The stored type lines are enough to reconstruct the mask, so an old build
    # keeps working - as the creature-only corpus it is - until the next
    # build-db widens it.
    rows = conn.execute("SELECT oracle_id, type_line FROM cards").fetchall()
    conn.executemany(
        "UPDATE cards SET type_mask = ? WHERE oracle_id = ?",
        [(type_mask_for(type_line or ""), oracle_id) for oracle_id, type_line in rows],
    )
    log(f"  migrating: derived type_mask for {len(rows):,} rows")


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (key, str(value)))


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return row[0] if row else default


# --------------------------------------------------------------------------
# build-db
# --------------------------------------------------------------------------

def cmd_build_db(args) -> int:
    log("Fetching Scryfall bulk index...")
    with open_url(BULK_INDEX) as r:
        index = json.load(r)

    entry = next((d for d in index["data"] if d["type"] == "oracle_cards"), None)
    if entry is None:
        sys.exit("Scryfall did not offer an 'oracle_cards' bulk file.")

    url = entry.get("jsonl_download_uri") or entry.get("download_uri")
    is_jsonl = "jsonl" in url
    log(f"  oracle_cards updated {entry['updated_at']}")
    log(f"  {human(entry.get('compressed_size') or entry.get('size') or 0)} compressed")
    log(f"  {url}")

    conn = connect(args.db)
    existing = {r[0] for r in conn.execute("SELECT oracle_id FROM cards")}
    if existing:
        log(f"  {len(existing):,} cards already in {args.db} - merging")

    seen, kept, added, updated = 0, 0, 0, 0
    kept_ids: "set[str]" = set()
    t0 = time.monotonic()

    # Stream it. The uncompressed oracle file is ~180 MB; we never hold more than
    # one line of it in memory, which is the same discipline the phone needs.
    with open_url(url, accept="*/*", timeout=300) as raw:
        stream = gzip.GzipFile(fileobj=raw) if url.endswith(".gz") else raw
        buffered = io.BufferedReader(stream, buffer_size=1 << 20)

        rows = []
        for line in _iter_card_json(buffered, is_jsonl):
            seen += 1
            type_mask = corpus_type_mask(line)
            if not type_mask:
                continue
            row = extract_card(line, type_mask)
            if row is None:
                continue
            kept += 1
            kept_ids.add(row.oracle_id)
            rows.append(row)

            if len(rows) >= 2000:
                a, u = _upsert(conn, rows, existing)
                added += a
                updated += u
                conn.commit()
                rows.clear()
                log(f"  ...{seen:,} scanned, {kept:,} cards kept")

        if rows:
            a, u = _upsert(conn, rows, existing)
            added += a
            updated += u

    # Drop anything that used to qualify but no longer does - a card can lose its
    # place when Scryfall reclassifies a set or errata changes a type line. Its
    # artwork stays in the pack as dead space, which is cheaper than rewriting a
    # 200 MB file for a handful of rows.
    removed = 0
    if not args.no_prune:
        stale = [r[0] for r in conn.execute("SELECT oracle_id FROM cards")
                 if r[0] not in kept_ids]
        for oracle_id in stale:
            conn.execute("DELETE FROM cards WHERE oracle_id = ?", (oracle_id,))
        removed = len(stale)

    set_meta(conn, "schema_version", SCHEMA_VERSION)
    set_meta(conn, "bulk_updated_at", entry["updated_at"])
    set_meta(conn, "built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    set_meta(conn, "art_width", PRINT_WIDTH_DOTS)
    set_meta(conn, "card_count", kept)
    conn.commit()

    dt = time.monotonic() - t0
    log("")
    log(f"Scanned  {seen:,} Oracle cards in {dt:.0f}s")
    log(f"Kept     {kept:,} rollable cards ({added:,} new, {updated:,} refreshed)")
    if removed:
        log(f"Removed  {removed:,} cards that no longer qualify")

    _print_type_breakdown(conn)
    _print_mv_histogram(conn)
    conn.close()
    return 0


def _iter_card_json(buffered, is_jsonl: bool):
    """Yield one card dict at a time from either JSONL or a legacy JSON array."""
    if is_jsonl:
        for raw_line in buffered:
            raw_line = raw_line.strip()
            if raw_line and raw_line not in (b"[", b"]"):
                yield json.loads(raw_line.rstrip(b","))
        return
    # Legacy fallback: one giant JSON array. Parse incrementally by lines, which
    # works because Scryfall pretty-prints one object per line block.
    for raw_line in buffered:
        raw_line = raw_line.strip().rstrip(b",")
        if raw_line.startswith(b"{") and raw_line.endswith(b"}"):
            yield json.loads(raw_line)


def _upsert(conn: sqlite3.Connection, rows: "list[CardRow]", existing: set) -> "tuple[int, int]":
    added = updated = 0
    for r in rows:
        if r.oracle_id in existing:
            # Refresh the printable fields but never touch art_off/art_len/art_h -
            # the artwork already in the pack stays valid.
            conn.execute(
                """UPDATE cards SET name=?, mana_cost=?, mv=?, type_line=?,
                       type_mask=?, oracle_text=?, power=?, toughness=?, loyalty=?,
                       color_identity=?, scryfall_uri=?, art_uri=?
                   WHERE oracle_id=?""",
                (r.name, r.mana_cost, r.mv, r.type_line, r.type_mask, r.oracle_text,
                 r.power, r.toughness, r.loyalty, r.color_identity, r.scryfall_uri,
                 r.art_uri, r.oracle_id),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO cards(oracle_id, name, mana_cost, mv, type_line,
                       type_mask, oracle_text, power, toughness, loyalty,
                       color_identity, scryfall_uri, art_uri)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r.oracle_id, r.name, r.mana_cost, r.mv, r.type_line, r.type_mask,
                 r.oracle_text, r.power, r.toughness, r.loyalty, r.color_identity,
                 r.scryfall_uri, r.art_uri),
            )
            existing.add(r.oracle_id)
            added += 1
    return added, updated


def _print_type_breakdown(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    if not total:
        return
    log("")
    log("Types (a card counts once per type it carries):")
    for word, bit in TYPE_WORDS:
        count = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE type_mask & ? != 0", (bit,)
        ).fetchone()[0]
        if count:
            log(f"  {word:<13} {count:>6,}")
    log(f"  {'cards':<13} {total:>6,}")


def _print_mv_histogram(conn: sqlite3.Connection) -> None:
    log("")
    log("Mana value distribution:")
    rows = conn.execute("SELECT mv, COUNT(*) FROM cards GROUP BY mv ORDER BY mv").fetchall()
    if not rows:
        return
    peak = max(c for _, c in rows)
    for mv, count in rows:
        bar = "#" * int(40 * count / peak)
        log(f"  MV {mv:>2}  {count:>6,}  {bar}")


# --------------------------------------------------------------------------
# build-tokens
# --------------------------------------------------------------------------

def cmd_build_tokens(args) -> int:
    """
    Works out which tokens each card can make, and stores them.

    Scryfall models this with `all_parts`: every card carries a list of related
    objects, and the ones with component == "token" are exactly what that card
    puts onto the battlefield. Those entries reference a token by *printing* id,
    not by Oracle id, so they cannot be joined against oracle_cards directly.

    Rather than stream the 74 MB all-printings export just to build an id map,
    we collect the referenced printing ids - there turn out to be under a
    thousand across the whole game - and resolve them in batches of 75 through
    /cards/collection. That is roughly a dozen requests.
    """
    conn = connect(args.db)

    log("Fetching Scryfall bulk index...")
    with open_url(BULK_INDEX) as r:
        index = json.load(r)
    entry = next((d for d in index["data"] if d["type"] == "oracle_cards"), None)
    if entry is None:
        sys.exit("Scryfall did not offer an 'oracle_cards' bulk file.")
    url = entry.get("jsonl_download_uri") or entry.get("download_uri")
    is_jsonl = "jsonl" in url

    log("Scanning cards for token references...")
    edges: "list[tuple[str, str]]" = []      # (card oracle_id, token printing id)
    printing_ids: "set[str]" = set()

    with open_url(url, accept="*/*", timeout=300) as raw:
        stream = gzip.GzipFile(fileobj=raw) if url.endswith(".gz") else raw
        buffered = io.BufferedReader(stream, buffer_size=1 << 20)
        for card in _iter_card_json(buffered, is_jsonl):
            # Every card the corpus holds, not just creatures. This used to be
            # creatures only, which was right when they were all the app could
            # roll - but a planeswalker whose entire job is making Soldiers is
            # the most ordinary planeswalker there is, and half the enchantments
            # worth rolling make something too.
            if not corpus_type_mask(card):
                continue
            oracle_id = card.get("oracle_id")
            if not oracle_id:
                continue
            for part in card.get("all_parts") or []:
                if part.get("component") != "token":
                    continue
                token_id = part.get("id")
                if token_id:
                    edges.append((oracle_id, token_id))
                    printing_ids.add(token_id)

    log(f"  {len(edges):,} token references across {len({e[0] for e in edges}):,} cards")
    log(f"  {len(printing_ids):,} distinct token printings to resolve")

    log("Resolving tokens via /cards/collection...")
    printing_to_oracle: "dict[str, str]" = {}
    tokens: "dict[str, tuple]" = {}
    ordered = sorted(printing_ids)

    for start in range(0, len(ordered), COLLECTION_BATCH):
        batch = ordered[start:start + COLLECTION_BATCH]
        payload = json.dumps({"identifiers": [{"id": i} for i in batch]}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.scryfall.com/cards/collection",
            data=payload,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.load(r)

        for token in body.get("data", []):
            oracle_id = token.get("oracle_id")
            if not oracle_id:
                continue
            printing_to_oracle[token["id"]] = oracle_id
            if oracle_id in tokens:
                continue
            face = _front_face(token)
            images = token.get("image_uris") or face.get("image_uris") or {}
            tokens[oracle_id] = (
                oracle_id,
                token.get("name", ""),
                token.get("type_line") or face.get("type_line") or "",
                face.get("oracle_text") or token.get("oracle_text") or "",
                face.get("power") or token.get("power"),
                face.get("toughness") or token.get("toughness"),
                "".join(token.get("colors") or face.get("colors") or []),
                (token.get("scryfall_uri") or "").split("?")[0],
                images.get("art_crop") or "",
            )

        missing = body.get("not_found") or []
        if missing:
            log(f"  ! {len(missing)} identifiers not found in this batch")
        log(f"  {min(start + COLLECTION_BATCH, len(ordered)):,}/{len(ordered):,}")
        time.sleep(0.1)

    log(f"Resolved {len(tokens):,} distinct tokens")

    for row in tokens.values():
        conn.execute(
            """INSERT INTO tokens(oracle_id, name, type_line, oracle_text, power,
                   toughness, colors, scryfall_uri, art_uri)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(oracle_id) DO UPDATE SET
                   name=excluded.name, type_line=excluded.type_line,
                   oracle_text=excluded.oracle_text, power=excluded.power,
                   toughness=excluded.toughness, colors=excluded.colors,
                   scryfall_uri=excluded.scryfall_uri, art_uri=excluded.art_uri""",
            row,
        )

    # Collapse printing-level edges to Oracle-level ones. Two cards pointing at
    # two different printings of the same 2/2 Zombie become one token.
    conn.execute("DELETE FROM card_tokens")
    inserted = 0
    for card_oracle_id, printing_id in edges:
        token_oracle_id = printing_to_oracle.get(printing_id)
        if not token_oracle_id:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO card_tokens(card_oracle_id, token_oracle_id) VALUES (?, ?)",
            (card_oracle_id, token_oracle_id),
        )
        inserted += 1

    set_meta(conn, "tokens_built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    conn.commit()

    linked = conn.execute("SELECT COUNT(*) FROM card_tokens").fetchone()[0]
    makers = conn.execute(
        "SELECT COUNT(DISTINCT card_oracle_id) FROM card_tokens"
    ).fetchone()[0]
    log("")
    log(f"Stored {len(tokens):,} tokens, {linked:,} links, {makers:,} cards make tokens")
    log("Run build-art again to fetch the token artwork.")
    conn.close()
    return 0


# --------------------------------------------------------------------------
# build-art
# --------------------------------------------------------------------------

def _ensure_pack(path: str) -> int:
    """Create the pack with its header if missing; return the append offset."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "wb") as f:
            f.write(PACK_MAGIC)
            f.write(struct.pack("<HHI", PACK_VERSION, PRINT_WIDTH_DOTS, 0))
        return PACK_HEADER_LEN

    with open(path, "rb") as f:
        head = f.read(PACK_HEADER_LEN)
    if len(head) < PACK_HEADER_LEN or head[:8] != PACK_MAGIC:
        sys.exit(f"{path} exists but is not a Momir art pack.")
    version, width, _ = struct.unpack("<HHI", head[8:16])
    if version != PACK_VERSION or width != PRINT_WIDTH_DOTS:
        sys.exit(f"{path} has version {version}/width {width}, expected {PACK_VERSION}/{PRINT_WIDTH_DOTS}.")
    return os.path.getsize(path)


def dither_art(data: bytes, max_height: int, gamma: float, contrast: float) -> "tuple[bytes, int]":
    """
    JPEG bytes -> packed 1-bit raster, 384 px wide, MSB first, 1 = burn.

    Thermal paper has no midtones, so everything hinges on the dither. Floyd-
    Steinberg (Pillow's default for convert("1")) holds detail far better than a
    plain threshold, and a little contrast lift first stops dark art turning into
    a solid black brick that soaks the paper.
    """
    img = Image.open(io.BytesIO(data))
    img = img.convert("L")

    height = max(1, round(PRINT_WIDTH_DOTS * img.height / img.width))
    img = img.resize((PRINT_WIDTH_DOTS, height), Image.LANCZOS)

    if max_height and height > max_height:
        # Centre-crop rather than squash: keeping the 1:1 pixel grid is what makes
        # the dither survive all the way to the print head.
        top = (height - max_height) // 2
        img = img.crop((0, top, PRINT_WIDTH_DOTS, top + max_height))
        height = max_height

    img = ImageOps.autocontrast(img, cutoff=1)
    if gamma != 1.0:
        lut = [min(255, int(((i / 255.0) ** gamma) * 255 + 0.5)) for i in range(256)]
        img = img.point(lut)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)

    bw = img.convert("1")           # Floyd-Steinberg
    raw = bw.tobytes()              # already packed 1 bpp, MSB first, rows padded to bytes

    expected = ART_STRIDE * height
    if len(raw) != expected:
        raise ValueError(f"packed {len(raw)} bytes, expected {expected}")

    # Pillow's mode "1" uses 255=white. The printer wants 1=burn=black, so invert.
    inverted = bytes(b ^ 0xFF for b in raw)
    return inverted, height


def cmd_build_art(args) -> int:
    conn = connect(args.db)

    # Cards and tokens share the same art pipeline and the same pack file; only
    # the table the offset gets written back to differs.
    pending = conn.execute(
        """SELECT 'cards' AS src, oracle_id, name, art_uri FROM cards
             WHERE art_off IS NULL AND art_uri <> ''
           UNION ALL
           SELECT 'tokens' AS src, oracle_id, name, art_uri FROM tokens
             WHERE art_off IS NULL AND art_uri <> ''"""
    ).fetchall()

    total = (
        conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        + conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
    )
    done = total - len(pending)

    if args.limit:
        pending = pending[: args.limit]

    if not pending:
        log(f"Nothing to do - all {total:,} cards and tokens already have artwork.")
        conn.close()
        return 0

    log(f"{done:,}/{total:,} already have art. Fetching {len(pending):,} more.")
    log(f"Rate limit {args.rps} req/s, {args.workers} workers -> ~{len(pending)/args.rps/60:.0f} min")

    append_at = _ensure_pack(args.pack)
    pack = open(args.pack, "r+b")
    pack.seek(0, os.SEEK_END)

    limiter = RateLimiter(args.rps)
    write_lock = threading.Lock()
    counters = {"ok": 0, "fail": 0, "bytes": 0}
    t0 = time.monotonic()

    def fetch(item):
        source, oracle_id, name, art_uri = item
        for attempt in range(args.retries):
            try:
                limiter.wait()
                with open_url(art_uri, accept="image/*", timeout=45) as r:
                    return source, oracle_id, name, r.read()
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                if attempt == args.retries - 1:
                    log(f"  ! {name}: {e}")
                    return source, oracle_id, name, None
                time.sleep(1.5 * (attempt + 1))
        return source, oracle_id, name, None

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for source, oracle_id, name, blob in pool.map(fetch, pending):
                if blob is None:
                    counters["fail"] += 1
                    continue
                try:
                    raster, height = dither_art(blob, args.max_art_height, args.gamma, args.contrast)
                except Exception as e:
                    log(f"  ! {name}: dither failed ({e})")
                    counters["fail"] += 1
                    continue

                with write_lock:
                    offset = pack.tell()
                    pack.write(raster)
                    # source comes from our own UNION literals, never user input,
                    # but pin it to a known table anyway.
                    table = "tokens" if source == "tokens" else "cards"
                    conn.execute(
                        f"UPDATE {table} SET art_off=?, art_len=?, art_h=? WHERE oracle_id=?",
                        (offset, len(raster), height, oracle_id),
                    )
                    counters["ok"] += 1
                    counters["bytes"] += len(raster)

                    if counters["ok"] % 250 == 0:
                        pack.flush()
                        os.fsync(pack.fileno())
                        conn.commit()
                        rate = counters["ok"] / max(0.001, time.monotonic() - t0)
                        left = (len(pending) - counters["ok"]) / max(rate, 0.001)
                        log(f"  {counters['ok']:,}/{len(pending):,}  "
                            f"{human(counters['bytes'])}  "
                            f"{rate:.1f}/s  ETA {left/60:.0f} min")
    except KeyboardInterrupt:
        log("\nInterrupted - progress is saved, just run build-art again.")
    finally:
        pack.flush()
        os.fsync(pack.fileno())
        pack.close()
        set_meta(conn, "art_built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        conn.commit()

    dt = time.monotonic() - t0
    log("")
    log(f"Added {counters['ok']:,} artworks ({human(counters['bytes'])}) in {dt/60:.1f} min, "
        f"{counters['fail']:,} failed")
    _report_sizes(args, conn)
    conn.close()
    return 0


def _report_sizes(args, conn: sqlite3.Connection) -> None:
    db_size = os.path.getsize(args.db) if os.path.exists(args.db) else 0
    pack_size = os.path.getsize(args.pack) if os.path.exists(args.pack) else 0
    cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    card_art = conn.execute("SELECT COUNT(*) FROM cards WHERE art_off IS NOT NULL").fetchone()[0]
    tokens = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
    token_art = conn.execute("SELECT COUNT(*) FROM tokens WHERE art_off IS NOT NULL").fetchone()[0]
    log("")
    log(f"  momir.db   {human(db_size)}")
    log(f"  art.pack   {human(pack_size)}")
    log(f"  cards      {card_art:,}/{cards:,} with artwork")
    log(f"  tokens     {token_art:,}/{tokens:,} with artwork")
    log(f"  total      {human(db_size + pack_size)}")


# --------------------------------------------------------------------------
# stats / push
# --------------------------------------------------------------------------

def cmd_stats(args) -> int:
    if not os.path.exists(args.db):
        sys.exit(f"{args.db} not found - run build-db first.")
    conn = connect(args.db)
    log(f"Built at   {get_meta(conn, 'built_at', '?')}")
    log(f"Scryfall   {get_meta(conn, 'bulk_updated_at', '?')}")
    _print_type_breakdown(conn)
    _print_mv_histogram(conn)
    _report_sizes(args, conn)

    missing = conn.execute("SELECT COUNT(*) FROM cards WHERE art_off IS NULL").fetchone()[0]
    if missing:
        log(f"\n  {missing:,} cards still need artwork - run build-art")
    conn.close()
    return 0


def cmd_push(args) -> int:
    adb = args.adb or shutil.which("adb") or _guess_adb()
    if not adb:
        sys.exit("adb not found - pass --adb <path>")

    target = f"/sdcard/Android/data/{args.package}/files"
    log(f"Pushing to {target} on the device...")

    subprocess.run([adb, "shell", "mkdir", "-p", target], check=False)
    for path in (args.db, args.pack):
        if not os.path.exists(path):
            log(f"  skip {path} (missing)")
            continue
        size = os.path.getsize(path)
        log(f"  {os.path.basename(path)}  {human(size)}")
        result = subprocess.run([adb, "push", path, f"{target}/{os.path.basename(path)}"])
        if result.returncode != 0:
            sys.exit(f"adb push failed for {path}")

    log("Done. Open the app and it will pick the files up on next launch.")
    return 0


def _guess_adb() -> "str | None":
    for candidate in (
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
    ):
        if os.path.exists(candidate):
            return candidate
    return None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, "out")
    os.makedirs(default_out, exist_ok=True)

    parser = argparse.ArgumentParser(
        prog="momirdeck",
        description="Build the offline Momir card corpus for a Sunmi V2.",
    )
    parser.add_argument("--db", default=os.path.join(default_out, "momir.db"))
    parser.add_argument("--pack", default=os.path.join(default_out, "art.pack"))

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build-db", help="stream Scryfall bulk data into momir.db")
    p.add_argument("--no-prune", action="store_true",
                   help="keep rows that no longer pass the filter")
    p.set_defaults(func=cmd_build_db)

    p = sub.add_parser("build-tokens", help="map cards to the tokens they create")
    p.set_defaults(func=cmd_build_tokens)

    p = sub.add_parser("build-art", help="download, dither and pack artwork (resumable)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--rps", type=float, default=10.0, help="requests per second (Scryfall asks for <=10)")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--limit", type=int, default=0, help="only fetch N artworks, for a quick trial run")
    p.add_argument("--max-art-height", type=int, default=DEFAULT_MAX_ART_HEIGHT,
                   help="dots; keeps the slip inside a sleeve")
    p.add_argument("--gamma", type=float, default=0.85, help="<1 brightens midtones before dithering")
    p.add_argument("--contrast", type=float, default=1.25)
    p.set_defaults(func=cmd_build_art)

    p = sub.add_parser("stats", help="show what is in the corpus")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("push", help="adb push momir.db and art.pack to the device")
    p.add_argument("--adb", default=None)
    p.add_argument("--package", default="io.github.moobaloo.momir.dev.debug")
    p.set_defaults(func=cmd_push)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
