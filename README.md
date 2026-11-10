# ReadLex dictionaries

Offline dictionary packs for the **ReadLex** Android e-book reader. The app downloads them from the
[packs release](https://github.com/meszarosatilla-hue/readlex-dictionaries/releases/tag/packs)
(`index.json` lists them). They are built by `tools/build.py` in GitHub Actions.

Languages: English, Hungarian, German, French, Spanish, Italian, Portuguese, Dutch, Polish, Czech, Slovak,
Romanian and Latin (`X` below).

| Pack | Content | Source | License |
|---|---|---|---|
| `en-wiktionary` | English definitions | [Wiktionary](https://en.wiktionary.org) via [kaikki.org](https://kaikki.org) | CC BY-SA 4.0 / GFDL |
| `en-wordnet` | English definitions | [Open English WordNet](https://en-word.net) | CC BY 4.0 |
| `en-X-wiktionary` | English → X (translation tables) | Wiktionary | CC BY-SA 4.0 / GFDL |
| `en-X-wiktionary-X` | English → X (collected from the X entries) | Wiktionary | CC BY-SA 4.0 / GFDL |
| `X-wiktionary` | X words explained in English | Wiktionary | CC BY-SA 4.0 / GFDL |
| `X-forms`, `en-forms` | word forms → base form (amavit → amo) | Wiktionary | CC BY-SA 4.0 / GFDL |
| `X-Y-freedict` | every FreeDict dictionary between two of these languages | [FreeDict](https://freedict.org) | GPL and other free licenses |

Between two languages without a direct dictionary the app translates through English (X → English → Y)
and shows those results separately, at the end.

The packs are derived works of these sources and are distributed under the same licenses.

## File format (`.rld`)

```
"RLD1" i32 version i32 blocks i32 metaLen meta (key=value lines)
blocks × (u16 keyLen key i64 offset i32 compLen i32 rawLen)
zlib blocks of sorted "key\tvalue\n" lines (~16 KB each)
```
