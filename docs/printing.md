# Printing

Everything between "a card was rolled" and "paper comes out".

## Every slip is the same length

This is the constraint the whole layout is built around.

A printed slip has to slide into a normal Magic sleeve, so it must be no longer
than a real card: **63 × 88 mm**. Every slip is printed to exactly that length,
whatever the card, because uniform slips stack, fan and shuffle like cards.
Slips whose length tracks how much rules text a creature happens to have stack
like receipts.

Width is not a problem. The H10S backend uses 58 mm paper at 203 dpi with 384 printable
dots, which is 48 mm and comfortably inside 63 mm.

Length is the interesting one. At 8 dots/mm the whole slip is **704 dots**, and
two margins come out of that before the layout gets any:

```
88 mm slip length           704 dots
− 12 mm head margin       −  96 dots   the head-to-tear-bar gap
−  5 mm foot margin       −  40 dots
────────────────────────────────────
  71 mm of layout           568 dots   ← every slip, exactly
```

The head margin is not a choice. The tear bar sits about 12 mm downstream of the
print head, so when a slip starts printing there are already 96 dots of paper
past the head. That paper can never be printed on, and it is the top of every
slip whether anyone wants it there or not.

Feeding exactly that distance afterwards *was* a choice, and the wrong one: it
tears the slip off flush with the last row, leaving 12 mm of white above the
card name and 0.75 mm under the rules text. Feeding more than the gap costs
nothing but layout space, and the excess comes out as a foot margin. The printer
is fed 136 dots after the raster: 40 of foot margin, then the 96 that carry the
slip to the bar.

Both numbers are settings, because the tear-bar distance varies between units
and paper rolls. Settings → Test print produces a calibration slip. Tear it off
and measure the white band above the card name, which *is* the head-to-tear
distance.

The defaults and example images are inherited from the upstream Sunmi layout.
They are not H10S physical validation. Measure and calibrate the H10S before
claiming an 88 mm slip or the stated margin placement.

So the renderer has two problems, not one: cards that want more than 568 dots,
and cards that want less.

![Six slips, all exactly 88 mm](images/slips-uniform.png)

## When a card wants less

A vanilla 3/3 with one line of rules text leaves a lot of paper unaccounted for.
Blank paper at the bottom would read as a mistake rather than as layout, so the
slack is spent instead:

1. **The QR grows.** In QR mode the code is sized against what is left after the
   text, up to 6 dots per module. The ceiling used to be 9, which is 1.1 mm
   modules on a code a phone reads from 20 cm away, and it ate three fifths of a
   vanilla slip. The extra space goes to the rules text now.
2. **What remains pads the picture**, split evenly above and below, so the image
   sits optically centred between the type line and the rules text.

Artwork cannot grow. It is stored at 384 dots wide and blitted 1:1, and
upscaling would destroy the dithering, so artwork slips lean on step 2.

## When a card wants more

Eldrazi with six keywords do not fit in 568 dots. The renderer does **not**
scale the layout down, because scaling resamples the artwork and destroys the
dithering that took 50 minutes to compute.

Instead it walks a fixed ladder of compromises, least destructive first, and
takes the first rung that fits:

| Rung | Rules text | Reminders | Artwork |
|---:|---:|:--|---:|
| 1 | 24 px | kept | 100 % |
| 2 | 24 px | kept | 92 % |
| 3 | 24 px | dropped | 92 % |
| 4 | 24 px | dropped | 82 % |
| … | … | … | … |
| 9 | 20 px | dropped | 50 % |
| 10 | 20 px | dropped | **none** |
| 11 | 18 px | dropped | none |

The order matters. **Reminder text goes before type shrinks**: it is
parenthesised text explaining a keyword the players at the table already know,
and some cards spend four of six lines on it. Type gives ground next, since
dropping the rules text one point is barely visible where cropping the picture
is not. Cropping takes rows off the top and bottom evenly, so the subject stays
centred and the pixel grid stays 1:1.

**Type stops at 20 px.** Below that, thermal bleed closes the counters of `a`,
`e`, `s` and `8`. The ladder used to bottom out at 17 px with the art at half
height on perfectly ordinary cards, because the header was 78 dots more
expensive than anyone had measured. See [below](#why-artwork-slips-still-carry-a-qr).

**Then the picture goes, and only then the floor.** Rules text outranks both. A
planeswalker with six abilities whose last two are ellipsised is not a
planeswalker you can play with, and that is what the ladder used to produce: it
ran out of rungs at half-height art and started cutting text with 150 dots of
picture still above it. Rung 10 drops the artwork entirely, rung 11 gives up the
20 px floor as well.

There is deliberately no rung between half art and no art. Below half height the
artwork can no longer hold the QR plate, so the code moves back into the header,
which costs more length than the remaining sliver of picture is worth.

Rung 11 is reached by two cards in 30,423: Urza, Planeswalker and Ashiok, Wicked
Manipulator, both with six abilities. Nothing in the corpus reaches the ellipsis.

If nothing on the ladder fits, the last rung is taken and abilities are laid in
until the space runs out, with the one that overruns ellipsised. An ability is
only cut where there is room for at least one line of it.

`Raster` always comes back at exactly the configured length: 568 dots of layout,
704 dots of paper, 88.0 mm, every time.

## Layout

Both modes print name, mana value, mana cost, type line, power/toughness and the
complete rules text. The mode only decides what fills the middle.

```
QR mode                          Artwork mode
┌──────────────────────────┐     ┌──────────────────────────┐
│ (3)  Blood-Cursed   1WB  │     │ (3)  Timmerian      1BB  │
│      Knight              │     │      Fiends              │
├──────────────────────────┤     ├──────────────────────────┤
│ White-Black Creature     │     │ Black Creature — Horror  │  ← colour is an
│ — Vampire                │     ├──────────────────────────┤    adjective
├──────────────────────────┤     │                          │
│      ▓▓▓▓▓▓▓▓▓▓▓         │     │   dithered artwork  ▓▓▓  │  ← QR set into
│      ▓▓ large QR ▓       │     │                     ▓▓▓  │    the corner
│      ▓▓▓▓▓▓▓▓▓▓▓         │     │                          │
├──────────────────────────┤     ├──────────────────────────┤
│ As long as you control   │     │ Remove this card from    │
│ an enchantment…          │     │ your deck before…        │
│                    ┌───┐ │     │                    ┌───┐ │  ← P/T in the
│                    │3/2│ │     │                    │1/1│ │    bottom corner
└────────────────────└───┘─┘     └────────────────────└───┘─┘
```

The mana value badge is an **outlined ring, not a filled disc**. A solid 54-dot
circle dumps a lot of heat into one spot and bleeds on cheap paper.

### The header

Badge, name, and the mana cost against the right edge, which is where a Magic
card puts it. The cost is set in letters rather than symbols: a thermal head has
no mana font, and 48 mm has no room for one.

The name is served first: it gets 170 dots if the header has them, and the cost
is fitted into what is left. Reaper King costs `2/W 2/U 2/B 2/R 2/G`, nineteen
characters, which cannot be allowed to squeeze the name to nothing.

A card whose artwork was dropped hands the QR back to the header, which leaves
about 150 dots for everything. Those slips go without a printed cost, and the
name is allowed four lines instead of two, because the QR has already made the
header 148 dots tall and the extra lines are free. A truncated card name is
worse than a missing cost.

### Colour is an adjective

A thermal slip is monochrome, so nothing on the paper says what colour the card
is. Usually the mana cost gives it away, but not always: a Devoid creature is
colourless despite a coloured cost, Stonecoil Serpent costs `{X}` and is
colourless, and Kobolds of Kher Keep has no mana cost at all and is red. Since
Momir hands you a *token copy* of the card, and colour decides what can target
it, the slip spells it out.

It goes **in front of the type line**, which is where a player already says it:
"Blue-Black Creature — Demon Illusion". One to three colours are named and
hyphenated. Four or five collapse to letters, since that is no longer a phrase
anyone says and a player holding a five-colour creature can read WUBRG.

It used to be its own 19 px line under the type line, at a cost of 25 dots.

### Power/toughness is in the bottom-right corner

At the end of the type line, at 23 px, it was a number in a sentence. A card
puts it in a box on the bottom-right of the frame, and that is where a player
looks for it without reading anything else, so that is where it goes: 32 px in a
stroked box, anchored to the foot of the slip.

Anchored, not laid out in sequence. On a card the box sits on the bottom edge
whether the text box above it is full or nearly empty, and a slip that let it
ride up behind two lines of rules text would not read as the same object. The
box and its gap are reserved out of the budget, so the rules text stops above it.

Loyalty takes the same box, labelled, because a bare `4` where a creature says
`3/4` reads as half a power and toughness.

### Abilities are separate

Oracle text arrives as one blob with newlines in it, and a newline is
typographically identical to a wrap. A creature with flying and a tap ability
printed as one grey paragraph in which the reader had to find the boundary
themselves. Each ability now gets its own layout and a 7-dot gap.

Braces come off the symbols in the same pass: `{2}{U}, {T}:` is twelve
characters for three symbols, and without a mana font there is nothing to draw
inside them. Reminder text is set in italic when it is kept, and dropped
entirely before the type is shrunk.

### Why artwork slips still carry a QR

The scanner resolves a slip back to its card by reading that QR, which is what
makes "which tokens does this make" work at the table. An artwork slip without
one would be a dead end.

It is **set into the artwork's bottom-right corner**, on its own white plate,
where it costs no length at all.

It used to sit in the header beside the name, at a cost this document gave as
"about 2 mm". The real figure: a 29-module symbol at 4 dots per module is 132
dots tall, and against a header that is otherwise 54 to 80 dots that comes to 50
to 78 dots of slip, or 6 to 10 mm. That one number drove ordinary cards to the
bottom of the fallback ladder, printing 17 px text beside art cropped to half.

The plate spends about a fifth of the picture's area instead, in the corner
where an art crop keeps its background. Artwork too short to hold a plate hands
the code back to the header, which is the old behaviour and still correct when
it happens.

### Shortening the URL

Slips encode `https://scryfall.com/card/ltc/22`, not the full
`https://scryfall.com/card/ltc/22/monstrosity-of-the-lake`. Scryfall resolves
set plus collector number on its own.

That drops a typical URL from 44 bytes to 32, which is the difference between a
**version 4 QR (33 modules)** and a **version 3 one (29 modules)**. In the header
that is what leaves enough width for the card name: at 33 modules the title
column got so narrow that "Windreaver" broke across two lines mid-word.

The scanner handles the mismatch. The corpus stores Scryfall's full URL, so a
scanned short URL is matched as an indexed prefix.

### QR sizing

Modules are drawn as an exact integer number of dots, always. That is why the
code uses ZXing's low-level `Encoder` rather than `QRCodeWriter`: the latter
resamples to a requested pixel size, so module edges land mid-dot and the code
comes out fuzzy.

| | Modules | Dot size | Why |
|---|---:|---:|---|
| Corner QR | 4 | 0.5 mm | The size a QR on a business card uses. 3 fits more comfortably but thermal dots bleed into their neighbours, and 0.375 mm modules start closing the gaps a decoder needs. |
| Body QR | 4–6 | 0.5–0.75 mm | Grows into whatever the rules text leaves. |

The quiet zone is **4 modules**, which is what the spec asks for. It was 2, and
that worked only because every code was surrounded by the layout's own white
paper. That assumption stopped being true the moment one was set into artwork.
The corner plate carries its full quiet zone as white pixels punched into the
picture.

Error correction is **M (15 %)**. Slips get thumbed, folded and left in sleeves;
L is smaller but does not survive a smudge across a timing pattern.

## Dithering

Thermal paper has no midtones. Every artwork is reduced to pure black and white
once, on the PC, and stored that way:

1. Decode the Scryfall `art_crop` (about 616 × 452).
2. Resize to 384 px wide with Lanczos.
3. Centre-crop to at most 300 dots tall.
4. Autocontrast, gamma 0.85, contrast 1.25. A flat original turns to mud without
   the lift, and a dark one turns into a solid brick that soaks the paper.
5. **Floyd–Steinberg** to 1 bit.
6. Pack MSB-first, invert so 1 = burn.

Floyd–Steinberg holds far more detail than a plain threshold. At 384 dots wide
you can still read a creature's silhouette and often its face.

The on-device resync repeats these steps in Kotlin with the same parameters, so
artwork pulled over WiFi is indistinguishable from artwork built on a PC. If the
two drifted, a deck built before a resync and one built after would print at
visibly different densities.

They had drifted. The device side skipped the autocontrast pass entirely, scaled
bilinear where Pillow uses Lanczos, and pivoted the contrast lift around a fixed
mid-grey where `ImageEnhance.Contrast` pivots around the image's own mean. On a
dark original that came to a mean difference of **92 levels out of 255**.

`tools/dithercheck.py` re-implements the Kotlin side in Python and diffs it
against the builder's own pipeline. It reports a maximum difference of 0 levels
on greyscale material and 2 on colour, which is JPEG rounding. Run it after
touching either implementation:

```bash
python tools/dithercheck.py
```

Three details had to be exact and none of them were obvious:

- **Pillow resamples 8-bit images in 8 bits.** The horizontal pass is rounded
  and clipped to `uint8` before the vertical pass reads it. Carrying full floats
  between the passes is more accurate and therefore wrong: it moves the result
  by up to eight levels.
- **`convert("L")` is ITU-R 601 in 16-bit fixed point**, not Android's
  77/151/28. The weights agree to about a tenth of a percent, which a 1.25
  contrast lift turns into flipped pixels.
- **Lanczos support widens with the scale factor** when shrinking. Without that
  it is closer to a point sample than to a resample.

## Compositing and thresholding

The slip is drawn on an `ARGB_8888` canvas at 384 × N, then converted to 1 bpp
with a **plain threshold at 170**, deliberately above the midpoint.

This works because the two kinds of content have different needs and the same
answer:

- Artwork is already pure black and white, so *any* threshold reproduces it
  bit-for-bit. It is blitted at exactly 1:1, no scaling and no filtering.
- Text and QR modules are the only antialiased things on the canvas. Biasing the
  cut above the midpoint keeps thin stems solid instead of letting them dissolve
  into grey that rounds to white. On paper that is the difference between
  legible and not.

Rendering type on a Canvas rather than sending it as text also sidesteps ESC/POS
code pages entirely, which matters when the corpus contains "Æther Vial", "Jötun
Grunt", "Lim-Dûl's Vault" and "Asmoranomardicadaistinaculdacar".

## ESC/POS

One serialized UART job per slip carries these commands:

```
ESC @            reset alignment, spacing, leftover state
ESC a 0          align left
GS v 0 …         raster bit image, in 128-row bands
ESC J n          feed n dots, repeated for n > 255
```

`GS v 0` is split into bands because some firmware revisions dislike a single
enormous raster command, and a short band lets the motor keep up with the head.
Bands print flush against each other, so the seam is invisible.

The whole payload is roughly 30 KB. The H10S transport sends paced 256-byte
chunks without interleaving another job. It never hands rasterization to a
vendor SDK. Drain and MCU status checks do not prove that paper has finished
moving; see [H10S printer](h10s-printer.md) for release and failure semantics.

## Checking a layout without printing

Long-press the **PRINT** button. It rolls a card, renders the slip, and writes
it to `preview.png` in the app's external files directory instead of printing:

```bash
adb pull /sdcard/Android/data/software.zeasy.momir/files/preview.png
```

What it writes is the **whole torn-off slip**, margins included: 704 dots, with
the 12 mm of white above the card name and the 5 mm below the rules text that
the printer is never asked to burn. The renderer's own bitmap is 568 dots of
layout and nothing else, and every image in `docs/images/` used to be that one.
None of them showed the object that comes out of the slot, which is part of why
a 12 mm head margin against a 0.75 mm foot went unnoticed for as long as it did.

Every slip image in this documentation was produced this way, and so were the
layout bugs described above, without wasting paper on any of them.
