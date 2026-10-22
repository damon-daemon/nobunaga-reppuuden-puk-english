# Nobunaga no Yabou: Reppuuden with Power-Up Kit — English Translation

A fan translation of **Nobunaga no Yabou: Reppuuden with Power-Up Kit** (信長の野望・烈風伝 with
パワーアップキット, Koei) for the PlayStation into English. *Reppuuden* is the eighth *Nobunaga's
Ambition*: you lead a clan in Sengoku-era Japan, develop your castles and the land around them,
win over officers and princesses, and fight to unify the country. It was never released in
English on any platform; the Power-Up Kit edition adds new scenarios, an officer and heirloom
editor and more.

> **This is a full machine translation.** All of the game's text was translated from
> Japanese by an AI model, not by a human translator, and it has not been
> proofread by a fluent Japanese speaker. Expect occasional mistranslations, awkward
> phrasing and inconsistent terms. Corrections are welcome.

> **Beta.** Versions 0.9.x are beta releases: the whole game is in English and a full game has
> been played through in an emulator, but not every event and ending has been seen in game yet.
> Sieges, the defending side of battles and the endings have had less testing than the main map.
> Please report text that is cut off, overlaps or reads wrongly (a screenshot helps).

This repository contains the English text and the tools that build the patch.
It does **not** contain the game, any of its data, or the Japanese script — you need
your own copy of the game.

## What's translated

- All in-game text: dialogue and event narration, notices, menus, forms, lists, help texts,
  officer biographies, the tutorial ("Imagawa's Ambition") and the glossary, the officer /
  castle / daimyo editor, the settings screens and the save/load screens.
- Names: officers, princesses, clans, castles, provinces, heirlooms, court titles and offices,
  formations, unit types, items. Names that do not fit their field use a short form.
- Text drawn as graphics: the main-map command bar and its sub-menus, the main menu, scenario
  and settings screens, editor pages, tutorial menus and callouts, battle and map marks.
- A proportional English font (original drawings) with word wrap; table columns laid out in
  pixels; month names ("Mar 1546").

Not translated: the title logo and the tutorial's logo (kept as the original artwork) and the
text inside the opening and ending movies.

## Applying the patch (players)

1. Download the release zip (`Nobunaga_Reppuuden_PUK_English_v<version>.zip`) from the Releases
   page. It contains the patch `nobunaga_reppuuden_puk_en_v<version>.xdelta`, a `.cue` file and
   a README.txt.
2. You need an unmodified image of the Japanese disc (serial `SLPM-86483`, Rev 1) in the usual
   two-track BIN/CUE form (Redump). Only track 1 is patched:

   | Track 1 | |
   |---|---|
   | Size  | 159,362,112 bytes |
   | CRC32 | `385431B2` |
   | MD5   | `aab72171b9ba0e99875c53f725ec5238` |
   | SHA-1 | `221738a8700147a69fd02d0785f1c8e28d027405` |

   Track 2 (CD audio, 37,810,752 bytes, CRC32 `40E86050`) is used unchanged.
3. Apply the patch to track 1 with an xdelta tool, e.g.
   [Delta Patcher](https://github.com/marco-calautti/DeltaPatcher) or the command line:

   ```
   xdelta3 -d -s "Nobunaga no Yabou - Reppuuden with Power-Up Kit (Japan) (Rev 1) (Track 1).bin" nobunaga_reppuuden_puk_en_v<version>.xdelta "Nobunaga no Yabou - Reppuuden with Power-Up Kit (English v<version>) (Track 1).bin"
   ```

4. Put the zip's `Nobunaga no Yabou - Reppuuden with Power-Up Kit (English v<version>).cue` next to
   the patched track 1 and the original track 2, and open the `.cue` in your emulator.

The patched disc has the same serial, so it uses the same memory card saves as the
Japanese game.

## Building from source

Requirements: Python 3.10+, `xdelta3` (for the patch file).

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp "/path/to/Nobunaga no Yabou - Reppuuden with Power-Up Kit (Japan) (Rev 1) (Track 1).bin" nobunaga_reppuuden_puk_track1.bin
cp "/path/to/Nobunaga no Yabou - Reppuuden with Power-Up Kit (Japan) (Rev 1) (Track 2).bin" nobunaga_reppuuden_puk_track2.bin
.venv/bin/python tools/setup.py      # checks the disc, extracts script/ from it
.venv/bin/python tools/build.py      # writes build/nobunaga_reppuuden_puk_en_track1.bin (+ track 2, .cue) and the .xdelta
```

`tools/setup.py` checks the disc's SHA-1 and generates `script/`, which pairs the
Japanese text from your disc with the English from `tl/`. `script/` and `build/` are
never committed. The build is reproducible: the same disc and sources give a
byte-identical patch.

To make a release package, run `.venv/bin/python tools/package.py <version>` after building:
it writes `release/Nobunaga_Reppuuden_PUK_English_v<version>.zip` with the versioned xdelta
patch, the `.cue` and a plain-text README.txt. No disc data is ever packaged.

## Repository layout

| Path | Contents |
|---|---|
| `tl/msg/` | English for the message file `MDATA1/MESSAGE.NB8`, one file per block |
| `tl/progstr/` | English for strings in the game program and its overlays, by address |
| `tl/names/` | English names: officers, castles, provinces, heirlooms, titles |
| `tools/setup.py`, `tools/build.py` | Create `script/`; build the patched disc and the patch |
| `tools/tl.py` | Validates translations (`apply`, `show`, `check`, `stats`) |
| `tools/iso.py`, `msgfile.py`, `text.py` | Disc image (ISO 9660, EDC/ECC), message file and text formats |
| `tools/progstr.py`, `cells.py` | Program strings, table columns |
| `tools/kanji.py`, `vwf.py`, `pixfont.py` | Frees the kanji font's RAM; proportional font (original drawings) and word wrap |
| `tools/names.py`, `namedict.py` | English names in the game |
| `tools/overflow.py`, `fmtlimit.py` | Buffer limits: the build fails when text could overflow a fixed-size buffer in the game |
| `tools/asm.py` | Small MIPS assembler for the program patches |
| `tools/package.py` | Makes the release zip (patch + .cue + README.txt with checksums) |
| `tools/dev/`, `tools/emu/` | Development helpers (disassembly, unicorn tests, DuckStation automation) |

## Known issues

- Machine translation: some lines will read oddly, and terms may vary between messages.
- Many labels and names are abbreviated to fit the original fixed-width boxes (for example
  "Diplo.", "Prest.", "Strat.", short castle and heirloom names); a few long castle names are
  still cut off in narrow fields such as the province panel's "Main" castle.
- Name entry (new officers, princesses) offers the Latin alphabet in full-width cells instead
  of kana and kanji.
- Goal descriptions and ending texts give the month as a number ("Month 3, 1560"); the
  message system has no month-name token there.
- Tested in DuckStation only. Saves made with the Japanese game load in the English one.

## Credits

- Translation and hacking: [damon-daemon](https://github.com/damon-daemon)
- Nobunaga no Yabou: Reppuuden with Power-Up Kit © Koei Tecmo. This is an unofficial fan
  project, not affiliated with or endorsed by Koei Tecmo. Please support the official releases.

## License

The tools, fonts, documentation and English translation text in this repository
are released under the [MIT License](LICENSE). This covers only original work in
this repository: the game itself and all of its content (code, Japanese text,
graphics, audio) are copyright Koei Tecmo and are not included or licensed here.
