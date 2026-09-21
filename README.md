# ParkLoop

A GPX desktop editor for Linux, Windows and macOS, built with Python, Qt for
Python and Shapely.

## Run

Install Python 3.11 or newer (3.12 recommended for packaging), then:

- **Linux / macOS:** `./run.sh`
- **Windows:** double-click `run.bat` (requires the Python `py` launcher).

The launch scripts create a local virtual environment and install dependencies
on first use. Alternatively, on any platform:

```sh
python -m venv .venv
# Activate .venv using your platform's activation command, then:
python -m pip install -e .
python -m parkloop
```

Linux Qt requires a graphical desktop and the system libraries used by Qt's
Wayland or XCB platform plugin. The app was exercised on Linux. Windows and
macOS builds are configured in `.github/workflows/build.yml`; they have not been
executed or verified in this Linux workspace. The workflow builds portable app
folders, not signed installers. Run it from GitHub Actions after publishing the
repository, or package locally with:

```sh
python -m pip install -e '.[dev]'
python -m PyInstaller --noconfirm --clean --windowed --name ParkLoop launcher.py
```

## Getting started

ParkLoop opens in **Auto route** mode with the starting-point panel expanded:

1. Choose a start on the map, enter latitude/longitude, or select a saved place.
2. Set the target distance and route preference.
3. Choose **Find route options**, preview an alternative, then select
   **Use this route**.

Labeled places stay pinned above ordinary recent coordinates and are not added
again when reused. Switch Planning mode to **Manual editor · draw or edit** to
create or adjust a route point by point.

## Manual editor

- Click to add points. New segments can follow walking paths or be straight.
- Drag an existing track point to move it; right-click to delete it.
- Shift-click near a track segment to insert a point.
- Walking mode recalculates adjacent paths when moving, inserting or deleting
  points. Straight-segment mode edits GPX geometry directly.
- Manual walking mode allows local roads missing sidewalk tags and reports
  these sections as unverified. Choose **Verified paths / sidewalks only**
  to require affirmative map evidence. Explicit restrictions remain excluded.
- Walking mode selects the shortest route by distance between snapped points
  on the allowed map network, without extra weighting for sidewalk verification.
  This manual mode also admits primary/secondary roads with missing sidewalk
  tags, labelled unverified. Motorways, prohibited access and explicitly absent
  sidewalks remain excluded. Automatic alternatives retain their local-road policy.
- Close loop connects the last point to the first using the selected mode.
- Undo/redo tracks up to 40 changes. The current draft is saved locally.
- File → Open GPX opens tracks or routes. Separate track segments remain separate.
- File → Open reference GPX shows another route in purple without editing it.
- Export GPX writes a GPX 1.1 track with full geometry and available elevations.
  Elevations on unchanged points are preserved. Workout timestamps, extensions
  and standalone GPX waypoints are not imported.

Use View → Fit route to frame the route. Drag the map to pan; scroll to zoom.

## Auto route

Choose **Auto route**, click the starting point on the map (or enter latitude,
longitude), set 1–30 km, select a preference, and generate:

- **Park-first loop · maximize green paths** (default): creates a real loop with
  **no repeated paths** in either direction. Mapped bridges and short paths
  outside green zones can connect the loop. Green-zone coverage is maximized,
  not required: if no majority-green route is found, the best walkable loop
  found is returned automatically even below 50%.
- **Entirely inside green zones:** requires the start and every route path to
  stay inside mapped green-zone boundaries.
- **Any walkable route:** searches pedestrian loops without a green-zone
  preference.

Park-first routing treats these mapped areas as green-zone coverage: parks,
gardens, nature reserves, forests, woods, recreation grounds, grass, village
greens, meadows, grassland, heath and scrub. It combines those polygons with
mapped pedestrian paths and roads with explicit sidewalk evidence. It handles
multipolygon holes and excludes private/inaccessible ways. A green polygon does
not by itself make a path eligible; the path must still pass the access and
road-safety rules. The default searches the complete pedestrian graph using
bounded beam search over complete loops. Degree-two
chains are compressed for search while retaining their full geometry and
one-way restrictions. Used physical paths are blocked in both directions.
Shortest outward/return pairs supply fast initial candidates; the search also
explores longer alternatives that those pairs miss. A final geometric check also
rejects overlaps hidden by different OSM IDs or different track-point sampling.
Within the **5% distance tolerance**, green-zone coverage and loop quality
matter more than hitting an exact distance. There is no minimum green-zone
percentage in the default mode; it falls back automatically to the
highest-coverage walkable loop the bounded search finds. Within
those constraints, route scoring favors fewer meaningful direction changes at
junctions so the result is easier to follow while running. Estimated navigation
turns are shown for each alternative.

**All automatic modes reject any repeated mapped path**, including small shared
access sections or bridges, even if the resulting distance would be perfect.
Entirely-inside mode uses the same search and never mirrors an access leg. A start
on a dead-end may therefore have no valid loop; the app reports that instead of
relaxing the rule. Returning to the starting point and crossing a path at a
single point are allowed. There is no overlap allowance; the geometric validator
uses only a 0.1 mm numerical tolerance. It does not fill gaps with straight lines.

The result displays distance-weighted green-zone coverage, estimated navigation
turns, verification distances, start offset and repeated-path distance.

Search is bounded to 100,000 arc expansions, 192 retained states per depth,
and 256 compressed chains per loop, at up to eight nearby starting vertices.
The app distinguishes search-budget exhaustion from a mapped network too small
to contain the requested distance. Exhausting this heuristic search is not a
proof that no suitable route exists. Green zones can have incomplete boundary
or path mapping, so the search may reject a request even where a runner could
find a route. It does not check opening hours, construction, temporary closures
or accessibility beyond available OSM tags. A start may snap to a nearby mapped
path; the result reports the offset. If no suitable loop is found, change the
distance or start.

## Road and sidewalk checks

Manual **Verified paths / sidewalks only** and map-checked automatic candidates
use a conservative OSM tag filter. The default manual walking mode and automatic
alternatives also allow clearly labelled unverified local roads as described
below. The no-repeat and distance rules remain mandatory for automatic alternatives.

- Motorways, motorway links, trunks, trunk links and `motorroad=yes` are excluded.
- Ordinary roads (including residential, service, parking access and living
  streets) require affirmative sidewalk tags. Missing or negative tags fail.
- Separately mapped sidewalks must be followed on their own footway geometry.
  `sidewalk=separate` and `foot=use_sidepath` do not approve the carriageway.
- Dedicated pedestrian paths and cycleways with explicit pedestrian permission
  are eligible, subject to access restrictions. Private, prohibited, conditional
  or uncertain pedestrian access is excluded, as are tagged construction and
  explicitly unmarked/forbidden crossings.
- Each generated route receives a final edge-by-edge check against its source
  map evidence. Old graphs without safety metadata cannot generate routes.

**Check road safety** audits the current GPX against a fresh map lookup. It
reports checked, excluded and unverified distance, and saves details in
`last-road-safety-check.json` beside the local draft. GPS edges must match mapped
edges; only 0.2 m of coordinate-rounding error is allowed. The checker never
assumes a road is safe because a sidewalk runs nearby. Missing matches remain
unverified. Editing or importing a route clears its in-app check status.

This is a **map-data check, not an on-site safety certification**. Tags may be
incomplete or outdated. Sidewalks tagged on road ways may be represented by the
road centerline in GPX; tags do not model safe crossing movements or guarantee
which side is usable at each junction. Observe the actual sidewalk, crossings,
signs and temporary conditions. Automatic starts can snap up to 80 m; the
reported offset is explicit, and access from the original pin to the snapped
start is **not included or verified**. No unchecked connector is inserted.

Tag semantics follow the OSM documentation for
[sidewalks](https://wiki.openstreetmap.org/wiki/Key:sidewalk),
[pedestrian access](https://wiki.openstreetmap.org/wiki/Key:foot), and
[motorroads](https://wiki.openstreetmap.org/wiki/Key:motorroad).

## Local data and services

Routing is calculated locally. **Map data → Use offline map…** selects a saved
Overpass JSON response (ways must include node IDs and geometry); this choice
persists across restarts and never calls the routing map service. Routes must
stay within that extract's coverage. The sidebar shows the active offline file.
`PARKLOOP_OFFLINE_MAP` can also select an extract for scripted use.

In downloaded-map mode, successful extracts are saved under `routing-maps` in
the application data directory. Requests fully covered by a saved extract reuse
it immediately, including after restart, without contacting Overpass. Extracts
are snapshots and may be outdated; road checks use the selected/saved map too.
New areas still require a download. Background map tiles have a separate cache
and may require internet even when route calculation works offline.

Drafts and the map cache use Qt's platform-specific application data/cache
directories. Original GPX files are never overwritten by importing.
`local-data/` is ignored by Git and is not included in Python packages or
desktop builds.

Internet is needed for uncached map tiles, pedestrian routing and green-zone lookup.
Coordinates are sent to the selected map/routing services; no account is needed.
The defaults are OSM tiles and Overpass. All route finding uses a locally built
map graph with the same road/sidewalk filter; no external router can bypass it. These shared
services can time out or throttle requests. Cancellation takes effect after an
in-flight request finishes (a routing-map lookup can take up to two 55-second requests).

Override providers without changing code using `PARKLOOP_OVERPASS_URL` and `PARKLOOP_TILE_URL` (a template containing
`{z}`, `{x}`, `{y}`). OSM attribution remains displayed. Configure suitable
providers before distributing to a large audience. Tile requests cover only the
visible viewport; tiles are cached for seven days. Map lookups are
user-triggered and only one route/edit/check operation runs at a time. See the [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

## Verification

```sh
python -m pip install -e '.[dev]'
QT_QPA_PLATFORM=offscreen python -m pytest -q  # Linux/macOS
python -m build
```

Tests cover GPX round-trip geometry/elevations/segment breaks, invalid inputs,
green-zone-only loops, private paths, polygon holes, distance rejection, cancellation,
and manual UI editing with undo/redo. Synthetic fixtures require no network.

### No-repeat validation

Synthetic tests cover undirected edge blocking, dead-end starts in both green-zone
modes, duplicate OSM IDs over the same geometry, same-direction and reverse
repetition, differently sampled overlapping segments, and rejection of repeats
in Any walkable route. Separate parallel paths and point crossings remain valid.

## Offline route-search diagnosis

Run `.venv/bin/python tools/diagnose_route.py ROUTE.gpx OSM.json --km 10`
with a saved Overpass response to reproduce a request without network access.
It reports reference length, sidewalk audit, and generation outcome.

## Route alternatives and unverified sections

Auto generation now offers up to three distinct routes in a comparison table.
Every alternative must contain at least 30% different mapped path from every
earlier option; reversed direction and differently sampled copies do not count
as different routes. Each option shows distance and target deviation,
green-zone coverage, estimated turns, map-checked and unverified distance,
excluded distance, repeated distance and start offset. Select a row and choose
**Use this route** to load it. Fewer alternatives are shown when the search
cannot find three sufficiently different loops.

Missing sidewalk tags on local residential, service, living-street, tertiary,
and unclassified roads are eligible only as **unverified** alternatives.
Missing evidence is not evidence that a route is unsafe; field verification is
separate from this map assessment.
Explicit no-sidewalk/access restrictions, major roads without sidewalk evidence,
and motorways remain excluded. No-repeat and distance requirements still apply.
Map-checked options rank first. Exported descriptions retain verification distances.
Manual walking mode also allows these clearly labelled unverified local roads;
the separate verified-only mode retains the strict policy.

## Auto route diagnostics

Every Auto route attempt replaces `last-auto-route.log` in ParkLoop's local
application-data directory. On Linux the default path is
`~/.local/share/ParkLoop/ParkLoop/last-auto-route.log`. The log records the
request, map source and cache decision, HTTP attempts, graph size, search
progress, alternative statistics, turn counts, and the complete traceback on
failure. A failed request also shows the log path in the status area.
