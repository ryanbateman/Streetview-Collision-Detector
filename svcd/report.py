"""MAP stage: render ranked candidates as CSV, a standalone HTML table and a folium map.

The map page is built from folium (map, tile layers, marker cluster, heat layer, layer control)
plus a side drawer whose markup, CSS and JS live in svcd/templates and are inlined into the
saved file, so the output stays a single standalone HTML page.
"""
import csv
import html
import json
import logging as log
import math
import warnings
from pathlib import Path
from typing import Any

import folium
from branca.element import MacroElement
from folium import plugins
from folium.plugins import HeatMap
from folium.template import Template

from svcd.models import SOURCE_GOOGLE, SOURCE_USER, Candidate

DEFAULT_CENTRE = [52.52, 13.405]
DEFAULT_ZOOM = 5
DEFAULT_TILES = "Esri.WorldStreetMap"
TEMPLATES = Path(__file__).parent / "templates"

CSV_COLUMNS = [
    "rank", "placeKey", "name", "month", "lat", "lng", "kind", "probability", "oneIn", "coverage",
    "visibility", "visitCount", "dwellMinutes", "panoCount", "panoUrls", "source", "checkKey",
]

# Badge text per provenance; the drawer JS uses the same words.
SOURCE_LABELS = {SOURCE_GOOGLE: "Google", SOURCE_USER: "User photo"}

_PAGE_STYLE = """
body { font-family: system-ui, sans-serif; margin: 16px; color: #222; background: #fff; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f4f4f4; cursor: pointer; user-select: none; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.links .pano { display: block; margin-bottom: 2px; }
.badge { display: inline-block; font-size: 11px; font-weight: 600; line-height: 1.4; padding: 0 5px;
  border: 1px solid; border-radius: 3px; white-space: nowrap; }
.badge.google { color: #1a5aa8; border-color: #9dbbe3; background: #eef4fc; }
.badge.user { color: #7a4a00; border-color: #e3c08a; background: #fdf3e2; }
.credit { color: #5c6670; font-size: 12px; margin-left: 4px; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e9ec; background: #1d2126; }
  th { background: #262b31; }
  th, td { border-color: #363c44; }
  a { color: #7fb2ff; }
  .badge.google { color: #a9cbff; border-color: #3d5a80; background: #1f2b3a; }
  .badge.user { color: #f2c98a; border-color: #7a5a2a; background: #33281a; }
  .credit { color: #a0a9b3; }
}
"""

# Click a header to re-sort; rows arrive pre-sorted by rank.
_SORT_SCRIPT = """
document.querySelectorAll("th").forEach(function (th, col) {
  th.addEventListener("click", function () {
    var body = th.closest("table").tBodies[0];
    var asc = th.dataset.dir !== "asc";
    th.dataset.dir = asc ? "asc" : "desc";
    var rows = Array.from(body.rows);
    rows.sort(function (a, b) {
      var x = a.cells[col].dataset.v || a.cells[col].textContent;
      var y = b.cells[col].dataset.v || b.cells[col].textContent;
      var nx = parseFloat(x), ny = parseFloat(y);
      var cmp = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : x.localeCompare(y);
      return asc ? cmp : -cmp;
    });
    rows.forEach(function (r) { body.appendChild(r); });
  });
});
"""

_TABLE_HEADERS = ["rank", "name", "source", "month", "odds", "visits", "daylight dwell (min)", "panoramas"]


def oneIn(probability: float) -> str:
    """Probability as plain odds: "1 in 12"; N above 100 is rounded to 2 significant figures.

    From 0.5 up, "1 in N" stops being informative, so it is a percentage instead: "50%", "90%".
    """
    if not probability or probability <= 0 or math.isnan(probability):
        return "very unlikely"
    if probability >= 0.5:
        return f"{round(min(probability, 1.0) * 100)}%"
    n = 1.0 / probability
    if n > 100:
        digits = math.floor(math.log10(n)) + 1
        n = round(n, 2 - digits)
    return f"1 in {max(2, int(round(n))):,}"


def oddsText(probability: float) -> str:
    """oneIn with "about" in front: "about 1 in 12", "about 50%", or "very unlikely"."""
    text = oneIn(probability)
    return text if text == "very unlikely" else f"about {text}"


def _displayName(candidate: Candidate) -> str:
    return candidate.name or candidate.placeKey


def _safeText(text: str) -> str:
    """HTML-escape, and also neutralise backticks and '$' because folium embeds popups and
    tooltips inside JavaScript template literals."""
    return html.escape(text, quote=True).replace("`", "&#96;").replace("$", "&#36;")


def _safeUrl(url: Any) -> str | None:
    """Only https URLs become links; anything else (javascript:, data:, http:) is dropped."""
    return url if isinstance(url, str) and url.startswith("https://") else None


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _sortedPanos(candidate: Candidate) -> list[dict[str, Any]]:
    """Best panorama (highest probability) first."""
    return sorted(candidate.panos or [], key=lambda p: -_finite(p.get("probability")))


def _ranked(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=lambda c: c.rank)


def _source(value: Any, default: str = SOURCE_GOOGLE) -> str:
    """A known provenance value, else the default (unknown imagery counts as Google)."""
    return value if value in SOURCE_LABELS else default


def _panoSource(c: Candidate, p: dict[str, Any]) -> str:
    """A panorama's own source when it has one, else its candidate's."""
    return _source(p.get("source"), _source(c.source))


def _copyright(p: dict[str, Any]) -> str | None:
    value = p.get("copyright")
    return value if isinstance(value, str) and value.strip() else None


def _checkKey(c: Candidate) -> str:
    """Stable id for the map's checked state; derived when an older record lacks one."""
    return c.checkKey or f"{c.placeKey}|{c.month}|{_source(c.source)}"


# ---------------------------------------------------------------- table

def _csvRow(c: Candidate) -> list[Any]:
    panos = _sortedPanos(c)
    return [
        c.rank, c.placeKey, c.name or "", c.month, c.lat, c.lng, c.kind, c.probability, oneIn(c.probability),
        c.coverage, c.visibility, c.visitCount, c.dwellMinutes, len(panos),
        " ".join(str(p.get("url") or "") for p in panos if p.get("url")),
        _source(c.source), _checkKey(c),
    ]


def _badge(source: str) -> str:
    return f'<span class="badge {source}">{SOURCE_LABELS[source]}</span>'


def _tableRow(c: Candidate) -> str:
    links = []
    for p in _sortedPanos(c):
        url = _safeUrl(p.get("url"))
        if not url:
            continue
        source = _panoSource(c, p)
        link = f'{_badge(source)} <a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">Open</a>'
        credit = _copyright(p)
        if source == SOURCE_USER and credit:
            link += f' <span class="credit">{_safeText(credit)}</span>'
        links.append(f'<span class="pano">{link}</span>')
    source = _source(c.source)
    cells = [
        f'<td class="num">{c.rank}</td>',
        f"<td>{_safeText(_displayName(c))}</td>",
        f'<td data-v="{source}">{_badge(source)}</td>',
        f"<td>{_safeText(c.month)}</td>",
        f'<td data-v="{c.rank}">{_safeText(oddsText(c.probability))}</td>',
        f'<td class="num">{c.visitCount}</td>',
        f'<td class="num">{round(c.dwellMinutes)}</td>',
        f'<td class="links" data-v="{len(links)}">{"".join(links)}</td>',
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def _renderHtml(ranked: list[Candidate]) -> str:
    if ranked:
        header = "".join(f"<th>{name}</th>" for name in _TABLE_HEADERS)
        rows = "\n".join(_tableRow(c) for c in ranked)
        body = (
            f"<p>{len(ranked)} candidates, ranked by the chance that a Street View camera caught you.</p>\n"
            f"<table>\n<thead><tr>{header}</tr></thead>\n<tbody>\n{rows}\n</tbody>\n</table>\n"
            f"<script>{_SORT_SCRIPT}</script>"
        )
    else:
        body = "<p>No candidates found.</p>"
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>Street View Candidates</title>\n<style>{_PAGE_STYLE}</style>\n</head>\n"
        f"<body>\n<h1>Street View Candidates</h1>\n{body}\n</body>\n</html>\n"
    )


def writeTable(candidates: list[Candidate], csvPath: Path, htmlPath: Path) -> None:
    """Write the ranked candidates as CSV and as a standalone sortable HTML table."""
    ranked = _ranked(candidates)
    csvPath, htmlPath = Path(csvPath), Path(htmlPath)
    csvPath.parent.mkdir(parents=True, exist_ok=True)
    with csvPath.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(_csvRow(c) for c in ranked)
    htmlPath.parent.mkdir(parents=True, exist_ok=True)
    htmlPath.write_text(_renderHtml(ranked), encoding="utf-8")
    log.info(f"Wrote {len(ranked)} candidates to {csvPath} and {htmlPath}")


# ---------------------------------------------------------------- map

def _heatWeights(candidates: list[Candidate]) -> list[float]:
    """Probabilities normalised to 0.1..1.0 so the weakest candidate still shows; all-equal maps to 1.0."""
    scores = [c.probability for c in candidates]
    low, high = min(scores), max(scores)
    if high - low <= 1e-12:
        return [1.0] * len(scores)
    return [0.1 + 0.9 * (s - low) / (high - low) for s in scores]


def _panoRecord(c: Candidate, p: dict[str, Any]) -> dict[str, Any]:
    return {
        "panoId": str(p.get("panoId") or ""),
        "date": str(p.get("date") or ""),
        "url": _safeUrl(p.get("url")),
        "lat": _finite(p.get("lat")),
        "lng": _finite(p.get("lng")),
        "distanceM": _finite(p.get("distanceM")),
        "probability": _finite(p.get("probability")),
        "source": _panoSource(c, p),
        "copyright": _copyright(p),
    }


def _candidateRecord(c: Candidate, heat: float) -> dict[str, Any]:
    return {
        "rank": c.rank,
        "placeKey": c.placeKey,
        "name": c.name,
        "month": c.month,
        "lat": _finite(c.lat),
        "lng": _finite(c.lng),
        "kind": c.kind,
        "probability": _finite(c.probability),
        "oneIn": oneIn(c.probability),
        "odds": oddsText(c.probability),
        "visitCount": c.visitCount,
        "dwellMinutes": round(_finite(c.dwellMinutes), 1),
        "panos": [_panoRecord(c, p) for p in _sortedPanos(c)],
        "source": _source(c.source),
        "checkKey": _checkKey(c),
        "heat": _finite(heat, 1.0),  # heat-layer weight, so the JS can rebuild the layer when filtering
    }


def _dataBlob(ranked: list[Candidate]) -> str:
    """Candidates as JSON that is safe inside a <script> element and inside JS template literals."""
    weights = _heatWeights(ranked) if ranked else []
    records = [_candidateRecord(c, w) for c, w in zip(ranked, weights)]
    text = json.dumps(records, ensure_ascii=True, allow_nan=False)
    text = text.replace("</", "<\\/").replace("<!--", "<\\u0021--")
    return text.replace("`", "\\u0060").replace("$", "\\u0024")


def _readTemplate(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


class _DrawerScript(MacroElement):
    """Emits the drawer JS into the map's script section, after the cluster group is defined."""

    _template = Template("{% macro script(this, kwargs) %}\n{{ this.code }}\n{% endmacro %}")

    def __init__(self, code: str):
        super().__init__()
        self._name = "SvcdDrawer"
        self.code = code


def _countText(count: int) -> str:
    if count == 0:
        return "No candidates"
    return f"{count} candidate" + ("" if count == 1 else "s")


def _addDrawer(mapObj: folium.Map, markerCluster: plugins.MarkerCluster, heat: HeatMap | None,
               ranked: list[Candidate]) -> None:
    root = mapObj.get_root()
    root.header.add_child(folium.Element(f"<style>\n{_readTemplate('drawer.css')}\n</style>"), name="svcd_css")
    empty = "" if ranked else '<li class="svcd-empty">No candidates found. Nothing to show on the map.</li>'
    drawerHtml = (
        _readTemplate("drawer.html")
        .replace("__SVCD_COUNT_TEXT__", _countText(len(ranked)))
        .replace("__SVCD_COUNT__", str(len(ranked)))
        .replace("__SVCD_EMPTY__", empty)
    )
    root.html.add_child(folium.Element(drawerHtml), name="svcd_drawer")
    root.html.add_child(
        folium.Element(f'<script type="application/json" id="svcd-data">{_dataBlob(ranked)}</script>'),
        name="svcd_data",
    )
    code = (
        _readTemplate("drawer.js")
        .replace("__SVCD_MAP__", mapObj.get_name())
        .replace("__SVCD_CLUSTER__", markerCluster.get_name())
        .replace("__SVCD_HEAT__", heat.get_name() if heat is not None else "null")
    )
    mapObj.add_child(_DrawerScript(code))


def buildMap(candidates: list[Candidate], path: Path) -> None:
    """Folium map with clustered candidate markers, a probability heat layer and a ranked side drawer.

    The drawer filters by text, provenance (Google or user photo) and checked state. Checked marks
    live in the browser's localStorage under "svcd-checked", keyed by Candidate.checkKey, so they
    survive regenerating the map.
    """
    ranked = _ranked(candidates)
    if ranked:
        centre = [sum(c.lat for c in ranked) / len(ranked), sum(c.lng for c in ranked) / len(ranked)]
        mapObj = folium.Map(location=centre, zoom_start=12, tiles=None)
    else:
        mapObj = folium.Map(location=DEFAULT_CENTRE, zoom_start=DEFAULT_ZOOM, tiles=None)
    # OpenStreetMap's tile server refuses requests that arrive without a Referer header, which is
    # what happens when the saved HTML is opened as a local file. Esri's street map needs neither a
    # Referer nor a key, so it is the default; Carto (currently keyless but folium warns it may need
    # a key) and OSM (works when the file is served over http) stay selectable in the layer control.
    folium.TileLayer(tiles=DEFAULT_TILES, name="Esri World Street Map").add_to(mapObj)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        folium.TileLayer(tiles="CartoDB Positron", name="CartoDB Positron", show=False).add_to(mapObj)
    folium.TileLayer(tiles="OpenStreetMap", name="OpenStreetMap", show=False).add_to(mapObj)

    # Markers are created by drawer.js so each one can be looked up by rank; folium only defines
    # the cluster group and pulls in the Leaflet.markercluster JS and CSS.
    markerCluster = plugins.MarkerCluster(name="Candidates", overlay=True, control=True)
    markerCluster.add_to(mapObj)

    # drawer.js replaces the heat points whenever the provenance or checked filter changes.
    heat = None
    if ranked:
        heatData = [[c.lat, c.lng, w] for c, w in zip(ranked, _heatWeights(ranked))]
        heat = HeatMap(heatData, name="Heatmap")
        heat.add_to(mapObj)

    folium.LayerControl().add_to(mapObj)
    _addDrawer(mapObj, markerCluster, heat, ranked)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mapObj.save(str(path))
    log.info(f"Wrote map with {len(ranked)} candidates to {path}")
