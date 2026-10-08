# Data pipeline

This pipeline is inherited from the upstream Sunmi app and is unchanged by the
H10S printing port. Device-specific timing and memory figures below describe
the upstream hardware, not H10S acceptance results.

How Scryfall's bulk export becomes the offline card corpus, and which cards get
in and why.

## The three build steps

```bash
python momirdeck.py build-db       # every rollable card   ~4 s
python momirdeck.py build-tokens   # what creatures make   ~1 min
python momirdeck.py build-art      # artwork               ~50 min, resumable
python momirdeck.py stats          # what you ended up with
python momirdeck.py push           # adb push to the device
```

Output lands in `tools/momirdeck/out/`:

| File | Size | Contents |
|---|---|---|
| `momir.db` | 19.8 MB | 30,423 cards, 733 tokens, 3,796 card→token links |
| `art.pack` | 407 MB | 31,156 pre-dithered 1-bit artworks |

Measured, on the corpus this documentation was written against: 30,423 cards, of
which 28,117 carry a colour identity and 2,306 are colourless.

## Streaming, not loading

Scryfall now publishes bulk data as **gzipped JSONL**, one complete card object
per line. The Oracle export is 23 MB compressed, about 180 MB expanded.

That format is what makes on-device resync possible at all. The V2 has 909 MB of
RAM with roughly 340 MB free; parsing 180 MB as a single JSON array would kill
the app. As JSONL it is a loop over `readLine()` and peak memory is one card.

Both the Python builder and the Kotlin resync stream it the same way. (Both also
keep a fallback path for the legacy single-array form.)

## Which cards count

Two independent questions: **is this a real card at all**, and **is it a type you
can roll**. The first has not changed since the first release. The second widened
when the app stopped being creatures-only, and nothing about the first was
loosened to make room for it.

### The seven rollable types

`cards.type_mask` is a bitmask of the front face's card types:

| Type | Bit |
|---|---:|
| Creature | 1 |
| Artifact | 2 |
| Enchantment | 4 |
| Planeswalker | 8 |
| Land | 16 |
| Battle | 32 |
| Instant | 64 |
| Sorcery | 128 |

The app rolls with `WHERE type_mask & :mask != 0`, so one query covers any
combination of types, and a card carries *every* bit its type line names: an
artifact creature is `CREATURE|ARTIFACT` = 3 and answers to a roll for either,
Dryad Arbor is `CREATURE|LAND` = 17.

These values are a contract between the builder and the app. Never renumber
them: a corpus built last month is still sitting on somebody's device.

The mask comes from the type line up to the em dash, which makes supertypes and
subtypes irrelevant: `Legendary Enchantment Creature — God` is
`CREATURE|ENCHANTMENT`. That cut also handles the layouts that print both halves
on the front. `Creature — Faerie Rogue // Instant — Adventure` stops at the first
em dash, so Brazen Borrower stays a creature card instead of turning up on a roll
for instants. `Instant // Sorcery` has no em dash at all and keeps both bits.

### Lands are the one type left out

The LAND bit exists and is set wherever it belongs, but a card whose *only* type
is land never enters the corpus. Nobody wants to be handed a random Wastes, and
the 1,100-odd plain lands would cost about 15 MB of `art.pack` and two minutes of
Scryfall's bandwidth for slips nobody would use. Land creatures come along on
their creature bit and keep their LAND bit for the app to display.

Instants and sorceries are in, permanent or not. You are rolling a card and
printing it; whether it stays on the battlefield is not the pipeline's business.

### Planeswalkers carry their starting loyalty

`cards.loyalty` is Scryfall's `loyalty`, `NULL` for everything that is not a
planeswalker. Without it a planeswalker slip has no starting loyalty, and you
cannot play from it.

It is read off the front face only, so Jace, Vryn's Prodigy prints as the
creature he is instead of borrowing the 5 from his flip side.

### Paper-ness comes from legality, not the `digital` flag

The natural filter is `card["digital"] == false` and `"paper" in card["games"]`.
It is wrong, and it silently drops real cards.

`oracle_cards` contains **one representative printing per card**, and Scryfall
picks "the most up-to-date recognizable version". For older cards that is often
an MTGO-only reprint. Memnite's representative printing is an MTGO duel deck:

```
Memnite               set=td2  set_type=duel_deck  digital=True   games=[mtgo]
Shield Sphere         set=me1  set_type=masters    digital=True   games=[mtgo]
Kobolds of Kher Keep  set=me3  set_type=masters    digital=True   games=[mtgo]
```

All three exist on cardboard. The naive filter threw them out along with about
200 others.

Legality is printing-independent, so that is what the filter uses:

```python
PAPER_LEGALITY_FORMATS = ("vintage", "legacy", "commander")

def is_paper_card(card):
    legalities = card.get("legalities") or {}
    return any(legalities.get(f, "not_legal") != "not_legal"
               for f in PAPER_LEGALITY_FORMATS)
```

It fixes the other direction too. Mystery Booster playtest cards like `Gobland`
are `games=[paper]` and `digital=false`, and they do exist on cardboard, but they
were never tournament-legal and are not real Magic cards. `vintage=not_legal`
catches them.

Alchemy rebalances fall out for free: they are legal only in `alchemy`,
`historic` and `timeless`, so none of the three paper formats matches.

### Transforming cards are judged by their front face

Scryfall's `t:creature` matches a card if *either* face is a creature. That
returns things like:

```
Westvale Abbey // Ormendahl, Profane Prince   [transform]  Land // Creature
Azusa's Many Journeys // Likeness of Seeker   [transform]  Saga // Creature
```

Westvale Abbey is a land. You roll a *card*, and a card's type is its front
face. So the filter uses the front face only, and so does the type mask:

```python
BOTH_HALVES_ON_FRONT = {"split", "adventure", "flip"}   # both halves are printed on the front

def type_line_for_filter(card):
    if card.get("layout") in BOTH_HALVES_ON_FRONT:
        return card.get("type_line", "")     # whole line counts
    return front_face(card).get("type_line", "")
```

This is a **deliberate divergence** from Scryfall's count, worth exactly 78
cards.

### Verifying it

Measured while the corpus was creatures only. The quality rules have not changed
since, so the check still holds; it is just no longer the whole corpus.

The corrected filter's per-mana-value counts match Scryfall's search API exactly
at the top end, where the numbers are small enough to check by hand:

| Mana value | This filter | Scryfall `unique=cards` |
|---:|---:|---:|
| 10 | 33 | 33 |
| 11 | 12 | 12 |
| 12 | 11 | 11 |
| 13 | 1 | 1 |
| 15 | 4 | 4 |
| 16 | 1 | 1 |
| 0 | 21 | 23 |

The two extra at mana value 0 are Westvale Abbey and Hostile Hostel, which is
the transform-card divergence above showing up where it should.

There is no creature at mana value 14, and none above 16. The dial reflects
that.

### Everything else the filter drops

```python
EXCLUDED_LAYOUTS   = {token, double_faced_token, emblem, art_series, vanguard,
                      scheme, planar, augment, host, reversible_card}
EXCLUDED_SET_TYPES = {funny, memorabilia, token, minigame, alchemy}
```

Plus names starting `A-` (Alchemy rebalances), type lines containing `Token`, and
cards whose only type is land.

> **If you change any of this**, change it in *both* `tools/momirdeck/momirdeck.py`
> and `app/src/main/java/software/zeasy/momir/sync/ScryfallSync.kt`. A resync
> that disagrees with the pushed corpus is a confusing bug to chase.

## Schema changes after a release

`CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a
new column will not appear in a corpus somebody built last month. Both the
builder and the app therefore apply added columns by hand: the builder from a
`MIGRATIONS` list, the app from `addColumnIfMissing` in `CardRepository`.

Three so far:

| Column | Added for |
|---|---|
| `cards.color_identity` | the print animation |
| `cards.type_mask` | rolling something other than a creature |
| `cards.loyalty` | planeswalker slips |

An older corpus opens fine. It simply animates colourless until it is rebuilt.

`type_mask` needs more than a default. Left at 0 it would match no roll at all
and the app would answer every press with nothing, so the builder backfills it
as it adds it, deriving each row's mask from the `type_line` already stored.
`ix_cards_type_mv` is created after the migration runs, since the column does not
exist before it.

A backfilled corpus stays usable as the creature-only corpus it was. Rolling an
enchantment finds nothing until the next `build-db`.

## Tokens

Scryfall models "what does this card create" as `all_parts`, a list of related
objects whose `component == "token"` entries are what the card puts onto the
battlefield.

`build-tokens` looks at every card the corpus admits. It used to look at
creatures only, which was right while they were all the app could roll. A
planeswalker whose entire job is making Soldiers is an ordinary planeswalker,
though, and plenty of enchantments make something too. The token sheet is keyed
on oracle id and never cared what type the card was, so widening the scan costs
one condition and a few hundred rows.

Over the whole corpus:

```
3,796 token references from 3,615 cards (11.9 %)
1,456 distinct token printings referenced
  733 distinct tokens after collapsing to Oracle identity
```

Restricting that scan to creatures had cost nearly half the graph: 2,060
references from 1,976 cards onto 532 tokens.

The catch: `all_parts` references tokens by **printing id**, not Oracle id, so
it cannot be joined against `oracle_cards` directly. That file holds a different
printing of the same token.

Rather than stream the 74 MB all-printings export just to build an id map,
`build-tokens` collects the referenced printing ids (under a thousand across the
whole game) and resolves them through `/cards/collection` in batches of 75.
Thirteen requests.

Then printing-level edges collapse to Oracle-level ones, so two cards pointing
at two different printings of the same 2/2 black Zombie end up sharing one token
row.

## Artwork

`build-art` walks every card and token without artwork, downloads the
`art_crop`, and produces a packed 1-bit raster. Details of the dithering are in
[printing.md](printing.md).

It is resumable by construction: the pack is append-only and offsets are
committed to the database every 250 images. If it dies, run it again.

Non-creature cards need nothing special. Every card object carries an `art_crop`,
and `build-art` works off "has no artwork yet" rather than off a type.

Rate limiting is a global 10 requests/second, which is what Scryfall asks for.
The full 31,156 images take a little under an hour from cold: the creature-only
17,230 took 29 minutes, and the 13,127 that widening the roll added took another
22. Please do not raise it.

## The art pack format

```
offset  0   magic "MOMIRART"    8 bytes
offset  8   version             u16 LE
offset 10   width in dots       u16 LE   (384)
offset 12   reserved            u32 LE
offset 16   blobs, back to back
```

Each blob is `48 × height` bytes: packed 1 bpp, MSB first, **1 = burn**. That is
byte-for-byte what `GS v 0` wants, so printing artwork involves no decoding and
no bitmap allocation.

`build-db` prunes rows that no longer qualify. Their artwork stays in the pack as
dead space, which is cheaper than rewriting a 400 MB file for a few hundred rows.

## How big it gets

Widening the roll past creatures very nearly doubles both files, so it is worth
knowing what the device is in for. The V2 has about 1.4 GB free.

| | Creatures only | All seven types |
|---|---:|---:|
| Cards | 17,497 | 30,423 |
| `momir.db` | 11 MB | 19.8 MB |
| Artworks in `art.pack` | 18,029 | 31,156 |
| `art.pack` | 238 MB | 407 MB |
| On the device | 249 MB | 427 MB |

Per type, counting a card once for every bit it carries:

| Type | Rows |
|---|---:|
| Creature | 17,497 |
| Instant | 3,620 |
| Enchantment | 3,562 |
| Artifact | 3,507 |
| Sorcery | 3,385 |
| Planeswalker | 287 |
| Battle | 36 |
| Land | 22 |

The overlaps are large. 1,192 of those artifacts and 238 of those enchantments
are also creatures, which is why the column adds up to more than the corpus.

The 12,926 cards widening added are ~13 KB of dithered artwork each, so the pack
grew by 169 MB and the download by 22 minutes. Both are one-offs: the pack is
append-only, so an existing `art.pack` keeps everything it already has and only
the new cards are fetched.

## On-device resync

The Resync button does the same work over WiFi:

1. `GET /bulk-data`, compare `updated_at` against what the corpus recorded. If
   unchanged, skip straight to artwork.
2. Stream the Oracle JSONL or JSON array, filter, insert new cards and refresh existing rows.
3. Fetch, dither and append up to 4,000 missing artworks per run, with at least
   100 ms between artwork requests.

A set release adds a few hundred cards, so in practice this takes a couple of
minutes. Cards are never deleted on-device; pruning only happens in the PC
builder, where it is easy to verify.

The resync filter has to admit the same types and derive the same mask as the
builder, or a resynced card will not answer the roll that the same card would
answer if it had come over adb.

Full-resource downloads require HTTP 200. Partial responses (HTTP 206) are
rejected before their body is read: even a valid JSONL prefix must not mark an
incomplete export as current.

Card rows and the bulk timestamp commit in one transaction after a complete,
nonempty export is read. Malformed/truncated input, database write errors or
cancellation during import roll that transaction back. JSON arrays may be
compact or pretty-printed; parsing retains one card at a time. No rows are
silently skipped because their JSON cannot be parsed.

Artwork is a separate, resumable phase: an artwork failure does not roll back
already committed card data. HTTP 429 or 503 stops the batch immediately and
reports the phase and status, plus a numeric Retry-After delay when supplied.
There is no automatic retry loop; wait before starting another sync. Other
artwork failures are counted and surfaced in the completion notice. Failed
artwork stays eligible for the next run, even when the bulk timestamp is current.
Cancellation is reported as such instead of successful completion. The last-sync
time advances only when the run completes without reported artwork failures;
a successful batch may still leave artwork beyond the per-run limit.

The local sync tests use synthetic data and HTTP fixtures, including a forced
SQLite write failure. These tests establish error handling, not a diagnosis of
any particular installation. Service lifecycle, low-storage and full-corpus
performance still require separately authorized device validation.
