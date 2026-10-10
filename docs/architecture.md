# Architecture

## The shape of the thing

```
   YOUR PC                          SENRAISE H10S
   ┌────────────────────┐           ┌──────────────────────────────────┐
   │ momirdeck.py       │  adb push │  momir.db     20 MB   SQLite     │
   │  build-db          ├──────────►│  art.pack    407 MB   1-bit art  │
   │  build-tokens      │           │                                  │
   │  build-art         │           │  ┌────────────────────────────┐  │
   └─────────┬──────────┘           │  │ app                        │  │
             │                      │  │   dial → random card       │  │
             │ bulk JSONL           │  │   renderer → 384px raster  │  │
             ▼                      │  │   ESC/POS → UART           │  │
      ┌─────────────┐               │  └─────────────┬──────────────┘  │
      │  Scryfall   │◄──────────────┤  resync        │                 │
      └─────────────┘   HTTPS       │                ▼                 │
                                    │        H10S printer MCU         │
                                    │                │                 │
                                    │                ▼   58 mm paper   │
                                    │           print head             │
                                    └──────────────────────────────────┘
```

There is no server. The diagram’s file sizes are sample corpus figures. The
PC-side builder prepares the corpus before transfer; the inherited on-device
resync updates an already-pushed corpus over WiFi, subject to device storage
and runtime validation. It cannot bootstrap a fresh installation without
`momir.db`; prepare and transfer the initial corpus from a PC.

## Hardware scope

This fork targets the H10S, using a 384-dot raster and direct UART printing.
The APK includes ARM 32-bit and ARM 64-bit native libraries and keeps the
upstream minimum API level of 25. See the current
[physical validation scope](device-setup.md#validation-scope).
The existing Views/Canvas UI, streaming corpus pipeline, and packed artwork
format are retained; this port does not claim new camera or device-policy support.

## Modules

### `data`

`CardRepository` opens `momir.db` from the app's external files directory, so
`momirdeck push` can drop a fresh corpus over adb without touching the APK and
no runtime permission is needed.

`ArtPack` is a `RandomAccessFile` over one append-only blob. The database stores
`(offset, length, height)` per card, so reading artwork is a seek and a read.

Schema is created on open with `CREATE TABLE IF NOT EXISTS`, so a corpus built
by an older `momirdeck` still opens. It simply has no token rows until the next
build or resync.

Each card carries a `type_mask`: one bit per card type its front-face type line
names, so an artifact creature is `CREATURE|ARTIFACT` and answers to both. A
category is a mask, and selecting one is `type_mask & :mask != 0` over
`ix_cards_type_mv(type_mask, mv)`. The bit values live in `CardTypes` and are
shared with the Python builder. The categories that combine them are
`CardCategory`, which also carries the label and the plural nouns, so the screen
cannot say "creatures" while the dial is rolling planeswalkers. Planeswalkers
also carry `loyalty`; without it the slip is not something you can play from.

`ensureSchema` adds both columns to a corpus that predates them, adds the index
after the column it names, and backfills `type_mask` from the `type_line`
already stored: one `UPDATE`, one scan, reading the types off the part before
the em dash. Such a corpus holds creatures only, so afterwards Creatures and
Permanents are populated and the rest are empty until it is rebuilt.

`search` is a `LIKE '%...%'` over the name column, ordered so prefix matches
come first. It scans matching names across the corpus; query time depends on
corpus size and device performance.

### `print`

The interesting part.

- `SlipRenderer` lays a card out on a 384-dot-wide canvas and converts it to
  packed 1-bit rows. It enforces the sleeve-length budget by walking a ladder of
  fallbacks rather than scaling. See [printing.md](printing.md).
- `SlipContent` is what both a `Card` and a `Token` collapse into, so one layout
  path serves both.
- `RulesText` splits oracle text into abilities, strips the braces off mana
  symbols and marks reminder text, which the renderer can then drop.
- `EscPos` builds the handful of commands needed: reset, align, raster, feed.
- `H10sPrinter` owns a process-wide `H10sTransport`. It acquires the UART only
  for a print, serializes slips, and releases after idle grace. See
  [h10s-printer.md](h10s-printer.md) for the native boundary and failure behavior.
- `QrCode` wraps ZXing's low-level `Encoder` rather than `QRCodeWriter`, so
  modules land on exact dot boundaries.

### `sync`

`ScryfallSync` streams the bulk export, filters it with the same rules the
Python builder uses, and inserts what is new. Then it fetches artwork for cards
that lack it and dithers on-device with the same parameters, so artwork pulled
by a resync is indistinguishable from artwork built on a PC. `tools/dithercheck.py`
holds the two implementations to that claim.

It runs inside a foreground `Service` with a wake lock. A partial sync is
harmless, since the corpus only grows and the pack is append-only, but one that
dies at card 9,000 every time is not useful.

On screen a running sync is the sync icon turning. The stage and the progress
bar live in its notification, where they can still be read after the app has
been swiped away. The activity applies the last status when it re-attaches its
listener, so coming back to a sync that has been running for twenty minutes
still shows something turning.

### `ui`

The screen is dressed as a Magic card rather than as an Android app: warm
blacks, a brass frame gold, a serif for anything that names a card, and
hairlines that fade out at their ends the way a card frame's rules do. None of
it costs anything at draw time. The palette is `colors.xml`, the frame is four
`<shape>` drawables, and the custom views build everything from circles,
gradients and cached shaders.

Every size and letter-spacing comes from the type scale in `themes.xml`, five
steps and three trackings, and no layout sets either. There used to be thirteen
sizes and nine trackings, with `TextAppearance.Momir.CardName` declaring 19sp
and all four of its call sites overriding it.

The header is the wordmark and four actions. It used to carry a corpus counter,
which was static and occupied the most valuable strip on the screen; those
numbers are in the settings diagnostics now. It also briefly carried a printer
lamp, which was green every time anyone looked at it and therefore said nothing.
A press with no printer bound puts "Printer not connected" on the result panel
in red, which is the same fact at the point where it changes what you do.

The category chips under it say what the dial rolls. Six of them are about twice
the width of the screen, so the row scrolls, with a fading edge to say so and
the selected chip scrolled to the middle whenever it changes. The inset belongs
to the row rather than to the scroll view: a `View` draws its fading edge from
its padding edge, so padding on the scroll view puts the fade inside the screen
and leaves the chip at full strength beyond it.

Changing the category re-reads the mana value buckets, the counts and the noun.
Planeswalkers start at mana value 2, sorceries stop long before creatures do,
and the dial lands on the nearest value the new category has rather than
snapping to the bottom. A category the corpus cannot serve gets an empty dial, a
disabled print button and a line saying so. The count line itself is a setting,
since it is the same number every time you come back to a mana value.

There is no print-mode control on the main screen. An artwork slip carries a QR
too, so the choice is only whether the middle of the slip is a picture or a
large code, which is not worth a permanent row on the one screen that matters.
It is a switch in settings.

`ManaWheel` is a drum picker. Items sit on a virtual cylinder; distance from
centre becomes an angle, and that one angle drives vertical position (through
`sin`, so items bunch towards the rim), size and opacity (`cos`). That single
mapping is what makes it read as rotation rather than as a fading list.

Each value is drawn as Magic's own generic mana symbol, rasterised once per
value from the vector drawable and blitted after that, because the drum redraws
continuously through every drag and fling. It used to be a hand-drawn stone disc
with a numeral struck into it, which was a good likeness and still only a
likeness; that drawing survives as the fallback for a mana value Scryfall has no
symbol for. The wheel only offers values that exist, since a dial you can spin
to 14 is a dial you can land on and get nothing from.

`PrintButton` is a 200 dp circular target, because it gets pressed with a thumb
while the other hand holds cards. Long-pressing it renders a preview instead of
printing.

It is drawn as a struck seal: a brass rim carrying the five colours as gems,
WUBRG clockwise from the top, over a stone face lit from above. The pentagon is
the colour wheel every player already has in their head. When a card comes back
its own colours light and the rim keeps them until the result panel retires, so
a glance at the button says what came out of the printer.

The cost is circles, arcs, one `RadialGradient` for the face and one
`LinearGradient` across the rim, both cached in `onSizeChanged`. Nothing is
allocated in `onDraw`, and the press dip is a canvas transform rather than
recomputed geometry, which is what lets those cached shaders stay valid at every
scale. The idle breath on the rim runs only while the window has focus, and
repaints only when its value has moved enough to see.

`GlowOverlay` is the print animation: the screen edge takes the card's colour
identity and a band of it sweeps up and off the top edge. This inherited
animation has not been changed by the printer transport port.

The obvious way to draw a glow is `BlurMaskFilter`. It is also unsupported by
the hardware-accelerated canvas, so reaching for it silently forces the view
into a software layer: a full-screen bitmap re-rasterised on the CPU every
frame, increasing drawing cost. Everything in the overlay is built from gradients and stacked translucent fills instead. The halo is four
banks of strips, one per edge, weighted heavily towards the top and inset at the
corners so no two banks overlap. It was a ring of fourteen rounded-rect strokes,
which was brightest where the strokes met, meaning the corners, on a device
whose paper comes out of the top.

Colours come from `ManaColors`, which is deliberately not the printed frame
palette: those are read by reflected light off white cardboard and come out
muddy as emitted light on a near-black screen. Black gets violet, because there
is no such thing as a black glow and every digital Magic client has made the
same substitution for twenty years.

`CardSheet` is the card behind the slip. Tapping the name on the result panel
opens the artwork, the mana cost, the type line and the rules text, laid out in
the order a Magic card is, with power/toughness in a box under the text box.
Everything it needs is already on the device.

Two things it does that the slip cannot. The artwork is shown on a
paper-coloured window rather than on the screen's black, because what `art.pack`
holds is a 1-bit dither prepared for a thermal head and painting it
white-on-dark is a photographic negative of the printed card. And costs and
rules text are set in **Magic's own mana symbols**: Scryfall's SVGs converted to
vector drawables by `tools/mana_symbols.py`, which also generates the
token-to-drawable table so neither can be missing from the other. They ship in
the APK because the app works with the radio off. Which symbols get built is
decided by the corpus, every `{...}` that appears in a mana cost or a rules
text, currently 60 of Scryfall's ~110.

`SearchActivity` is a field and a list: type a name, get every card on the
device whose name contains it, prefix matches first so "lotus" offers Black
Lotus before thirty cards with the word in the middle. The query runs on the IO
thread 220 ms after the last keystroke rather than on each one, because three of
the four queries "urza" would start answer a question nobody is asking any more.

It returns an oracle id, the way the scanner returns the text it read, and the
card is then shown rather than printed. In a list where touching a row spends
paper, every mis-tap is a slip. The print happens from the card view. Search is
a full screen rather than a sheet because typing needs the keyboard and the
keyboard needs the room.

`TokenSheet` is the bottom sheet a card's tokens come up in, from the panel at
the bottom of the screen or straight from a scan. It carries a stamp saying
which of the two it was, because a scan happens with the camera in your hand and
no other context on screen. Each token is shown as a token (colour stripe, P/T,
name, type line), and the quantity stepper next to the print button is there
because a Rhys the Redeemed player wants six Elves rather than six trips through
a dialog. The layout renders once no matter how many slips come out of it.

It is anchored to the bottom edge at full width. As a floating dialog it hovered
over the result panel with the card's name showing above and below it at once.

Its buttons are `TextView`s, not `Button`s: under a MaterialComponents theme
every `<Button>` is inflated as a `MaterialButton`, which brings its own
background and text colour and ignores the style it was given. That is how a
print action ends up as black text on a black slab.

The result panel at the bottom of the main screen retires itself: 15 seconds
after a print, or 45 if the card makes tokens, since then the panel is also the
way back into the sheet. It fades to `INVISIBLE` rather than `GONE`, because the
print button is anchored to it and would otherwise drop down the screen.

It also carries every notice the app has to give, from printer not connected to
resync finished, with the colour-identity stripe swapped for a solid red one
when it is bad news. Those were nine stock Android toasts: a grey lozenge in the
system font floating over a screen designed as a card.

`ScannerActivity` retains the upstream `android.hardware.Camera` implementation.
Its preview callback supplies an NV21 buffer whose luminance plane ZXing reads.
This describes the implementation, not an H10S camera compatibility result;
permission, preview, focus and QR decoding need a physical check.

## Threading

Every database query, layout pass, dither blit and UART operation happens off
the main thread via coroutines on `Dispatchers.IO`, so these operations do not
directly block UI input and drawing.

The printer belongs to the process. Activity destruction cancels its caller,
but does not close a descriptor under an active I/O call. Cleanup runs in a
process scope after five seconds of idle grace and joins the status reader first.
