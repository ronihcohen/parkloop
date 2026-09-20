# ParkLoop

A new, independent GPX desktop editor for Linux, Windows and macOS. Built with
Python, Qt for Python and Shapely. No GPXRunner code or runtime dependency.

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

- **Park loop · mostly in parks** (default): use `yarkon-good` as the quality
  benchmark: a real loop, mostly in parkland, with **no repeated paths** in either
  direction. Mapped bridges and short paths outside park boundaries can connect the
  loop. Park coverage is a preference, not an absolute boundary in this mode.
- **Entirely inside parks:** explicitly requires the start and all route paths
  to stay inside mapped park boundaries.
- **Any walkable route:** searches pedestrian loops without a park restriction.

Park routing uses OpenStreetMap park/garden/nature-reserve polygons and mapped
pedestrian paths and roads with explicit sidewalk evidence. It handles multipolygon
holes and excludes private/inaccessible ways. The default searches the complete
pedestrian graph using bounded beam search over complete loops. Degree-two
chains are compressed for search while retaining their full geometry and
one-way restrictions. Used physical paths are blocked in both directions.
Shortest outward/return pairs supply fast initial candidates; the search also
explores longer alternatives that those pairs miss. A final geometric check also
rejects overlaps hidden by different OSM IDs or different track-point sampling. Within the **5% distance tolerance**, majority
park coverage and loop quality matter more than hitting an exact distance.
Routes with less than 50% park coverage are rejected in the default mode.
**All automatic modes reject any repeated mapped path**, including small shared
access sections or bridges, even if the resulting distance would be perfect.
Strict park mode uses the same search and never mirrors an access leg. A start
on a dead-end may therefore have no valid loop; the app reports that instead of
relaxing the rule. Returning to the starting point and crossing a path at a
single point are allowed. There is no overlap allowance; the geometric validator
uses only a 0.1 mm numerical tolerance. It does not fill gaps with straight lines.

The result displays distance-weighted mapped park coverage and repeated-path
percentage. `yarkon-good` remains a private local benchmark, not a hard-coded
route or a dependency required to generate routes elsewhere.

Search is bounded to 100,000 arc expansions, 192 retained states per depth,
and 256 compressed chains per loop, at up to eight nearby starting vertices.
The app distinguishes search-budget exhaustion from a mapped network too small
to contain the requested distance. Exhausting this heuristic search is not a
proof that no suitable route exists. Some
parks have incomplete boundary/path mapping. It may reject a request even where
a runner could find a route. It does not check opening hours, construction,
temporary closures or accessibility beyond available OSM tags. A start may snap
to a nearby mapped path; the park result reports the offset. If no suitable park
loop is found, change the distance/start or select Any walkable route.

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
directories. Original GPX files are never overwritten by importing. GPXRunner's
saved database is not accessed by the application. `local-data/` is ignored by
Git and is not included in Python packages or desktop builds.

Internet is needed for uncached map tiles, pedestrian routing and park lookup.
Coordinates are sent to the selected map/routing services; no account is needed.
The defaults are OSM tiles and Overpass. All route finding uses a locally built
map graph with the same road/sidewalk filter; no external router can bypass it. These shared
services can time out or throttle requests. Cancellation takes effect after an
in-flight request finishes (park lookup can take up to two 55-second requests).

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
park-only loops, private paths, polygon holes, distance rejection, cancellation,
and manual UI editing with undo/redo. Synthetic fixtures require no network.

### No-repeat validation

Synthetic tests cover undirected edge blocking, dead-end starts in both park
modes, duplicate OSM IDs over the same geometry, same-direction and reverse
repetition, differently sampled overlapping segments, and rejection of repeats
in Any walkable route. Separate parallel paths and point crossings remain valid.

`yarkon-good` is the route-quality reference. Earlier generated examples with
small repeated sections do not meet the current requirement and are not used as
successful no-repeat examples. The generator must satisfy the no-repeat rule
before park coverage and distance ranking can select a route.

Before the sidewalk filter was added, the saved Yarkon pedestrian graph
and `yarkon-good` start with seed 42 produced a 10.372 km loop for a 10 km request (87.3% mapped park coverage) and a
13.949 km loop for the reference's 13.626 km request (91.3% mapped park coverage).
Both passed the no-repeat tests, but they were **not road-safety verified**.
The later audit rejected about 2.45 km of the 10.372 km route, mainly service
roads and parking aisles without sidewalk evidence. Do not treat those earlier
examples as satisfying the current routing constraints.

### Sidewalk verification result

The original `yarkon-good` start does not currently yield a 10 km loop satisfying
all of the conservative sidewalk, no-repeat, and 80 m snapping constraints. The
planner reports that instead of relaxing a requirement.

A separately saved alternative starts at **32.1035782, 34.8201676**, approximately
457 m from that start. It is **10.017 km**, **87.9% inside mapped parks**, has no
repeated mapped path, and its entire geometry matches eligible pedestrian-path
ways with **zero road-way segments and zero unverified distance**. Access to this
alternative start is not part of the route and has not been verified. The local
GPX and JSON audit are under `local-data/yarkon-sidewalk-checked-alternative-10k*`.

## Offline route-search diagnosis

Run `.venv/bin/python tools/diagnose_route.py ROUTE.gpx OSM.json --km 10`
with a saved Overpass response to reproduce a request without network access.
It reports reference length, sidewalk audit, and generation outcome.

The local Yarkon snapshot on 2026-09-20 shows a separate map constraint:
`yarkon-good.gpx` is 13.63 km, and its start has only 0.97 km of reachable
cycle-capable paths under the sidewalk policy within the 80 m snap limit.
The reference includes excluded and unverified sections, so it cannot certify
a compliant 10 km route under this policy. The search fix does not bypass it
or silently move the start to the larger network roughly 457 m away.

## Route alternatives and unverified sections

Auto generation now offers up to three distinct routes in a comparison table.
Each shows distance and target deviation, park coverage, map-checked and
unverified kilometres/percentages, excluded distance, repeated distance, and
start offset. Select a row and choose **Use selected route** to load and export it.
Fewer alternatives are shown when the search finds fewer distinct loops.

Missing sidewalk tags on local residential, service, living-street, tertiary,
and unclassified roads are eligible only as **unverified** alternatives.
Missing evidence is not evidence that a route is unsafe; field verification
(such as the user's Yarkon reference) is separate from this map assessment.
Explicit no-sidewalk/access restrictions, major roads without sidewalk evidence,
and motorways remain excluded. No-repeat and distance requirements still apply.
Map-checked options rank first. Exported descriptions retain verification distances.
This alternative policy supersedes the strict-only auto behavior described above;
manual walking mode now also allows these clearly labelled unverified local
roads. The separate verified-only mode retains the strict policy.
