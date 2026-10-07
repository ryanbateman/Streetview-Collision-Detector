(function () {
  "use strict";
  var PAGE_SIZE = 20;
  var NARROW_QUERY = "(max-width: 699px)";
  var HIGHLIGHT_MS = 2000;
  var map = __SVCD_MAP__;
  var cluster = __SVCD_CLUSTER__;

  var data = [];
  try {
    var blob = document.getElementById("svcd-data");
    data = JSON.parse(blob ? blob.textContent : "[]");
  } catch (err) {
    console.error("svcd: could not read candidate data", err);
  }
  if (!Array.isArray(data)) data = [];
  data.sort(function (a, b) { return a.rank - b.rank; });

  var byRank = new Map();
  var markers = new Map();
  data.forEach(function (c, i) {
    c.bucket = Math.min(2, Math.floor((i * 3) / data.length));
    byRank.set(c.rank, c);
  });

  var drawer = document.getElementById("svcd-drawer");
  var toggleBtn = document.getElementById("svcd-toggle");
  var hideBtn = document.getElementById("svcd-hide");
  var list = document.getElementById("svcd-list");
  var filterInput = document.getElementById("svcd-filter");
  var prevBtn = document.getElementById("svcd-prev");
  var nextBtn = document.getElementById("svcd-next");
  var pageLabel = document.getElementById("svcd-page");
  var countLabel = document.getElementById("svcd-count");
  var statusLabel = document.getElementById("svcd-status");
  var defaultStatus = statusLabel.textContent;

  var filtered = data;
  var page = 1;
  var activeRank = null;
  var markerTimer = null;

  // Only https links are rendered; anything else (javascript:, data:, http:) is shown as text.
  function safeUrl(url) {
    return typeof url === "string" && url.startsWith("https://") ? url : null;
  }

  function displayName(c) {
    return c.name || c.placeKey || "Unnamed place";
  }

  // All user-provided text goes through textContent; no HTML parsing of data.
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function externalLink(url, text) {
    var a = el("a", null, text);
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    return a;
  }

  function plural(n, word) {
    return n + " " + word + (n === 1 ? "" : "s");
  }

  function visitsText(c) {
    return plural(c.visitCount, "visit");
  }

  function dwellText(c) {
    return Math.round(c.dwellMinutes) + " min daylight dwell";
  }

  function bestPano(c) {
    var panos = c.panos || [];
    for (var i = 0; i < panos.length; i++) {
      if (safeUrl(panos[i].url)) return panos[i];
    }
    return null;
  }

  function isNarrow() {
    return window.matchMedia(NARROW_QUERY).matches;
  }

  function buildPopup(c) {
    var root = el("div", "svcd-popup");
    root.appendChild(el("b", null, displayName(c)));
    root.appendChild(el("div", null, "Month: " + c.month));
    root.appendChild(el("div", "svcd-odds", c.odds));
    root.appendChild(el("div", null, visitsText(c) + ", " + dwellText(c)));
    var ul = el("ul");
    (c.panos || []).forEach(function (p) {
      var li = el("li");
      var url = safeUrl(p.url);
      li.appendChild(url ? externalLink(url, "Open panorama") : el("span", null, "Panorama (no link)"));
      li.appendChild(document.createTextNode(" " + (p.date || "date unknown") + ", " + Math.round(p.distanceM) + " m"));
      ul.appendChild(li);
    });
    root.appendChild(ul);
    return root;
  }

  data.forEach(function (c) {
    if (!isFinite(c.lat) || !isFinite(c.lng)) return;
    var pin = el("span", "svcd-b" + c.bucket);
    var icon = L.divIcon({
      className: "svcd-pin",
      html: pin,
      iconSize: [18, 18],
      iconAnchor: [9, 9],
      popupAnchor: [0, -9]
    });
    var marker = L.marker([c.lat, c.lng], { icon: icon, title: displayName(c) + " (" + c.month + ")" });
    marker.bindPopup(function () { return buildPopup(c); }, { maxWidth: 300 });
    marker.on("click", function () { selectRank(c.rank, { scroll: true }); });
    markers.set(c.rank, marker);
    cluster.addLayer(marker);
  });

  function setOpen(open) {
    document.body.classList.toggle("svcd-open", open);
    toggleBtn.setAttribute("aria-expanded", String(open));
    drawer.inert = !open;
    map.invalidateSize();
    setTimeout(function () { map.invalidateSize(); }, 250);
  }

  function restartAnimation(node, className) {
    node.classList.remove(className);
    void node.offsetWidth;
    node.classList.add(className);
  }

  function buildItem(c) {
    var li = el("li", "svcd-item");
    li.id = "svcd-item-" + c.rank;
    li.tabIndex = 0;
    li.dataset.rank = String(c.rank);
    if (c.rank === activeRank) li.classList.add("svcd-active");

    var title = el("div", "svcd-title");
    title.appendChild(el("span", "svcd-dot svcd-b" + c.bucket));
    var rank = el("a", "svcd-rank", "#" + c.rank);
    rank.href = "#rank-" + c.rank;
    rank.title = "Link to this entry";
    rank.addEventListener("click", function (ev) {
      ev.preventDefault();
      history.replaceState(null, "", "#rank-" + c.rank);
      selectRank(c.rank, { scroll: false });
    });
    title.appendChild(rank);
    title.appendChild(el("span", "svcd-name", displayName(c)));
    li.appendChild(title);

    var meta1 = el("div", "svcd-meta");
    meta1.appendChild(document.createTextNode(c.month + " · "));
    meta1.appendChild(el("span", "svcd-odds", c.odds));
    li.appendChild(meta1);
    li.appendChild(el("div", "svcd-meta", visitsText(c) + " · " + dwellText(c)));

    var links = el("div", "svcd-links");
    var show = el("button", null, "Show on map");
    show.type = "button";
    show.addEventListener("click", function () { showOnMap(c); });
    links.appendChild(show);
    var best = bestPano(c);
    if (best) {
      links.appendChild(externalLink(safeUrl(best.url), "Panorama"));
    } else {
      links.appendChild(el("span", "svcd-more", "No panorama link"));
    }
    var panoCount = (c.panos || []).length;
    if (panoCount > 1) {
      var more = el("span", "svcd-more", "+" + (panoCount - 1) + " more");
      more.title = "The map popup lists every panorama";
      links.appendChild(more);
    }
    li.appendChild(links);

    li.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && ev.target === li) {
        ev.preventDefault();
        showOnMap(c);
      }
    });
    return li;
  }

  function pageCount() {
    return Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  }

  function renderList() {
    var pages = pageCount();
    page = Math.min(Math.max(1, page), pages);
    list.textContent = "";
    if (!data.length) {
      list.appendChild(el("li", "svcd-empty", "No candidates found. Nothing to show on the map."));
    } else if (!filtered.length) {
      list.appendChild(el("li", "svcd-empty", "No candidates match the filter."));
    }
    filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).forEach(function (c) {
      list.appendChild(buildItem(c));
    });
    pageLabel.textContent = "Page " + page + " of " + pages;
    prevBtn.disabled = page <= 1;
    nextBtn.disabled = page >= pages;
    countLabel.textContent = data.length ? plural(data.length, "candidate") : "No candidates";
    statusLabel.textContent = filtered.length === data.length
      ? defaultStatus
      : filtered.length + " of " + data.length + " match the filter.";
  }

  function applyFilter(text) {
    var q = String(text).trim().toLowerCase();
    filtered = !q ? data : data.filter(function (c) {
      return displayName(c).toLowerCase().includes(q) || String(c.month).toLowerCase().includes(q);
    });
    page = 1;
    renderList();
  }

  function selectRank(rank, opts) {
    var c = byRank.get(rank);
    if (!c) return false;
    var idx = filtered.indexOf(c);
    if (idx < 0) {
      filterInput.value = "";
      applyFilter("");
      idx = filtered.indexOf(c);
    }
    activeRank = rank;
    var wanted = Math.floor(idx / PAGE_SIZE) + 1;
    var item = document.getElementById("svcd-item-" + rank);
    if (wanted !== page || !item) {
      page = wanted;
      renderList();
      item = document.getElementById("svcd-item-" + rank);
    } else {
      list.querySelectorAll(".svcd-active").forEach(function (n) { n.classList.remove("svcd-active"); });
      item.classList.add("svcd-active");
    }
    if (item) {
      restartAnimation(item, "svcd-flash");
      setTimeout(function () { item.classList.remove("svcd-flash"); }, HIGHLIGHT_MS);
      if (opts && opts.scroll) item.scrollIntoView({ block: "nearest" });
    }
    return true;
  }

  function highlightMarker(marker) {
    var node = marker.getElement ? marker.getElement() : null;
    if (!node) return;
    restartAnimation(node, "svcd-pin-hl");
    clearTimeout(markerTimer);
    markerTimer = setTimeout(function () { node.classList.remove("svcd-pin-hl"); }, HIGHLIGHT_MS);
  }

  function showOnMap(c) {
    selectRank(c.rank, { scroll: true });
    var marker = markers.get(c.rank);
    if (!marker) return;
    if (isNarrow()) setOpen(false);
    if (!map.hasLayer(cluster)) map.addLayer(cluster);
    map.setView([c.lat, c.lng], 17, { animate: false });
    cluster.zoomToShowLayer(marker, function () {
      marker.openPopup();
      highlightMarker(marker);
    });
  }

  function fromHash() {
    var m = /^#rank-(\d+)$/.exec(window.location.hash);
    var c = m ? byRank.get(Number(m[1])) : null;
    if (!c) return false;
    showOnMap(c);
    return true;
  }

  toggleBtn.addEventListener("click", function () { setOpen(true); filterInput.focus(); });
  hideBtn.addEventListener("click", function () { setOpen(false); toggleBtn.focus(); });
  filterInput.addEventListener("input", function () { applyFilter(filterInput.value); });
  prevBtn.addEventListener("click", function () { page -= 1; renderList(); });
  nextBtn.addEventListener("click", function () { page += 1; renderList(); });
  window.addEventListener("hashchange", fromHash);
  var narrowMedia = window.matchMedia(NARROW_QUERY);
  if (narrowMedia.addEventListener) {
    narrowMedia.addEventListener("change", function (ev) { setOpen(!ev.matches); });
  }

  setOpen(!isNarrow());
  renderList();
  if (!fromHash() && markers.size) {
    map.fitBounds(cluster.getBounds(), { padding: [30, 30], maxZoom: 16 });
  }
})();
