# 10Key Revive Pack Dual Difficulty Tables

Upload this folder as-is to host both tables on the same server.

Use these header URLs:

- Revive Lv table: `revive/header.json`
- Circus Rating table: `circus/header.json`
- Level viewer: `level-viewer.html`

Each `header.json` points to the `body.json` in the same directory.

If a BMSTable client supports multiple table metadata tags on one page, `index.html` also advertises both tables. If it only accepts one table per URL, register the two header URLs separately. The level viewer can switch between Revive Lv, Circus Rating, and a combined view, and shows the BMS `#TOTAL` gauge recovery amount when available.
