"""MAP stage: render ranked collisions as CSV, a standalone HTML table and a folium map."""
import html
import logging as log
from pathlib import Path

import folium
from folium import plugins
from folium.plugins import HeatMap

from svcd.match import rankCollisions
from svcd.models import Collision
from svcd.storage import writeCsv

DEFAULT_CENTRE = [52.52, 13.405]
DEFAULT_ZOOM = 5

_PAGE_STYLE = """
body { font-family: system-ui, sans-serif; margin: 16px; color: #222; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: left; }
th { background: #f4f4f4; cursor: pointer; user-select: none; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
"""

# Click a header to re-sort; rows arrive pre-sorted by score.
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

_COLUMNS = ["rank", "name", "month", "panoDate", "visitCount", "dwellMinutes", "distanceM", "link"]


def _displayName(collision: Collision) -> str:
    return collision.name or collision.placeKey


def _safeText(text: str) -> str:
    """HTML-escape, and also neutralise backticks and '$' because folium embeds popups and
    tooltips inside JavaScript template literals."""
    return html.escape(text, quote=True).replace("`", "&#96;").replace("$", "&#36;")


def _anchor(url: str, escapeAmpersands: bool = True) -> str:
    """Panorama link. The map popup keeps raw '&' (browsers accept it) so the URL stays greppable."""
    href = html.escape(url, quote=True) if escapeAmpersands else url.replace('"', "%22").replace("<", "%3C")
    return f'<a href="{href}" target="_blank" rel="noopener">Open panorama</a>'


def _tableRow(rank: int, c: Collision) -> str:
    cells = [
        f'<td class="num">{rank}</td>',
        f"<td>{html.escape(_displayName(c))}</td>",
        f"<td>{html.escape(c.month)}</td>",
        f"<td>{html.escape(c.panoDate)}</td>",
        f'<td class="num">{c.visitCount}</td>',
        f'<td class="num">{round(c.dwellMinutes)}</td>',
        f'<td class="num">{round(c.distanceM)}</td>',
        f'<td data-v="{rank}">{_anchor(c.url)}</td>',
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def _renderHtml(ranked: list[Collision]) -> str:
    if ranked:
        header = "".join(f"<th>{name}</th>" for name in _COLUMNS)
        rows = "\n".join(_tableRow(i, c) for i, c in enumerate(ranked, start=1))
        body = (
            f"<p>{len(ranked)} collisions, ranked by score.</p>\n"
            f"<table>\n<thead><tr>{header}</tr></thead>\n<tbody>\n{rows}\n</tbody>\n</table>\n"
            f"<script>{_SORT_SCRIPT}</script>"
        )
    else:
        body = "<p>No collisions found.</p>"
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>Street View Collisions</title>\n<style>{_PAGE_STYLE}</style>\n</head>\n"
        f"<body>\n<h1>Street View Collisions</h1>\n{body}\n</body>\n</html>\n"
    )


def writeTable(collisions: list[Collision], csvPath: Path, htmlPath: Path) -> None:
    """Write the ranked collisions as CSV and as a standalone HTML table."""
    ranked = rankCollisions(collisions)
    writeCsv(csvPath, ranked, cls=Collision)
    htmlPath = Path(htmlPath)
    htmlPath.parent.mkdir(parents=True, exist_ok=True)
    htmlPath.write_text(_renderHtml(ranked), encoding="utf-8")
    log.info(f"Wrote {len(ranked)} collisions to {csvPath} and {htmlPath}")


def _popupHtml(c: Collision) -> str:
    return (
        f"<b>{_safeText(_displayName(c))}</b><br/>"
        f"Month: {html.escape(c.month)}<br/>"
        f"Visits: {c.visitCount}<br/>"
        f"Dwell: {round(c.dwellMinutes)} min<br/>"
        f"Distance: {round(c.distanceM)} m<br/>"
        f"{_anchor(c.url, escapeAmpersands=False)}"
    )


def _heatWeights(collisions: list[Collision]) -> list[float]:
    """Scores normalised to 0.1..1.0 so the weakest collision still shows; all-equal scores map to 1.0."""
    scores = [c.score for c in collisions]
    low, high = min(scores), max(scores)
    if high - low <= 1e-12:
        return [1.0] * len(scores)
    return [0.1 + 0.9 * (s - low) / (high - low) for s in scores]


def buildMap(collisions: list[Collision], path: Path) -> None:
    """Folium map with a marker cluster (one marker per collision) and a score heatmap."""
    collisions = list(collisions)
    if collisions:
        centre = [
            sum(c.panoLat for c in collisions) / len(collisions),
            sum(c.panoLng for c in collisions) / len(collisions),
        ]
        mapObj = folium.Map(location=centre, zoom_start=12)
    else:
        mapObj = folium.Map(location=DEFAULT_CENTRE, zoom_start=DEFAULT_ZOOM)

    markerCluster = plugins.MarkerCluster(name="Collisions", overlay=True, control=True)
    for c in collisions:
        folium.Marker(
            location=[c.panoLat, c.panoLng],
            popup=folium.Popup(_popupHtml(c), max_width=300),
            tooltip=_safeText(f"{_displayName(c)} ({c.month})"),
        ).add_to(markerCluster)
    markerCluster.add_to(mapObj)

    if collisions:
        heatData = [[c.panoLat, c.panoLng, w] for c, w in zip(collisions, _heatWeights(collisions))]
        HeatMap(heatData, name="Heatmap").add_to(mapObj)

    folium.LayerControl().add_to(mapObj)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mapObj.save(str(path))
    log.info(f"Wrote map with {len(collisions)} markers to {path}")
