# ReadLex dictionaries

Offline dictionary packs for the **ReadLex** Android e-book reader. The app downloads them from the
[packs release](https://github.com/meszarosatilla-hue/readlex-dictionaries/releases/tag/packs)
(`index.json` lists them). They are built by `tools/build.py` in GitHub Actions.

| Pack | Content | Source | License |
|---|---|---|---|
| `en-wiktionary` | English definitions | [Wiktionary](https://en.wiktionary.org) via [kaikki.org](https://kaikki.org) (wiktextract) | CC BY-SA 4.0 / GFDL |
| `en-hu-wiktionary` | English → Hungarian (translation tables) | Wiktionary | CC BY-SA 4.0 / GFDL |
| `en-hu-wiktionary-hu` | English → Hungarian (from Hungarian entries) | Wiktionary | CC BY-SA 4.0 / GFDL |
| `en-forms` | English word forms (went → go) | Wiktionary | CC BY-SA 4.0 / GFDL |
| `en-wordnet` | English definitions | [Open English WordNet](https://en-word.net) | CC BY 4.0 |
| `en-hu-freedict` | English → Hungarian | [FreeDict](https://freedict.org) | GPL |

The packs are derived works of these sources and are distributed under the same licenses.

## File format (`.rld`)

```
"RLD1" i32 version i32 blocks i32 metaLen meta (key=value lines)
blocks × (u16 keyLen key i64 offset i32 compLen i32 rawLen)
zlib blocks of sorted "key\tvalue\n" lines (~16 KB each)
```
