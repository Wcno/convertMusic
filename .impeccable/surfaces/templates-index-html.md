---
version: 1
slug: "templates-index-html"
primary_target: "templates/index.html"
related_targets: ["static/styles.css","static/app.js"]
---

# Surface: converter (templates/index.html)

Scope: the single page of the app. Visitor mode: Operate.
Audience: the author and friends converting an occasional YouTube link to MP3.
Task: paste link → confirm preview → choose bitrate → download. States: idle, fetching info, ready, converting (real progress), done, error.
Constraints: Spanish copy, keep "By: Wilcno", no external network assets beyond Google Fonts; works at 390px.

## Direction contract

THESIS: The converter is a studio export rack unit: you patch a link in and it bounces the track to MP3. It refuses the centered white card with a red button that every converter ships.

OWN-WORLD: Light screen-printed aluminium faceplate (#d8d5cc) with black uppercase legends, rack ears with screw holes, one recessed dark display window (#1f2320) for the preview and readouts, flat colour only (no gradients/glow/glass), LED ladder green→amber→red, a 3-position bitrate selector, one big orange BOUNCE key with an offset soft shadow.

STORY: The visitor recognises gear, pastes a link, sees the real track in the display, picks bitrate, hits the key, watches LEDs climb with real progress, gets the file. Raises: physical state vocabulary (acetate manual); flat colour commitment (guide map); hierarchy by scale contrast alone (type specimen).

FIRST VIEWPORT: Desktop: one wide faceplate spanning the content width, brand legend top-left, model number top-right. Row 1: patch-slot input full width with LOAD key. Row 2: display window (thumbnail left, big title, channel, duration in tabular figures) beside the control block (bitrate selector, BOUNCE key). Row 3: full-width LED ladder with percent and stage readout. Credit printed on the lower rack ear strip. Mobile: rows stack, ears shrink.

FORM: Export Rack Unit, grounded candidate 4 of 7; seed key cecd0717. Signature interaction: LED ladder lighting segment by segment from real server progress, with a status lamp per stage (LINK, AUDIO, MP3).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
