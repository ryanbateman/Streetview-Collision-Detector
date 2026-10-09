(function () {
  "use strict";
  var PAGE_SIZE = 20;
  var NARROW_QUERY = "(max-width: 699px)";
  var HIGHLIGHT_MS = 2000;
  var STORE_KEY = "svcd-checked";
  var BACKUP_KEY = "svcd-checked.bak";
  var EXPORT_NAME = "svcd-checked.json";
  var IMPORT_MAX_BYTES = 5 * 1024 * 1024;
  var SOURCE_LABELS = { google: "Google", user: "User photo" };
  var NO_STORAGE = "Checked marks cannot be saved in this browser; export them before closing the page.";
  var CORRUPT_NOTE = "Saved checks could not be read; a backup was kept as " + BACKUP_KEY;
  var CORRUPT_NO_BACKUP = "Saved checks could not be read, and no backup could be kept.";
  var MASKED_NAME = "Unknown location";
  var MASKED_CREDIT = "Contributor hidden";
  var lastBackup = null;  // raw value most recently copied to BACKUP_KEY, so it is copied once

  // ---------------------------------------------------------------- checked store (pure helpers)

  // {checkKey: ISO time}. Null prototype, so keys such as "__proto__" are plain data.
  function emptyStore() { return Object.create(null); }

  function hasOwn(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }

  // A store is a plain object of string to string. Anything else (array, string, null) is rejected
  // whole; inside an object, entries whose value is not a string are dropped and the rest kept.
  function validStore(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    var out = emptyStore();
    var keys = Object.keys(value);
    for (var i = 0; i < keys.length; i++) {
      var v = value[keys[i]];
      if (typeof keys[i] === "string" && typeof v === "string") out[keys[i]] = v;
    }
    return out;
  }

  // Parse a raw stored string. corrupt is true when it is not JSON or not an object.
  function parseStore(raw) {
    if (!raw) return { store: emptyStore(), corrupt: false };
    var store = null;
    try {
      store = validStore(JSON.parse(raw));
    } catch (err) {
      store = null;
    }
    return store ? { store: store, corrupt: false } : { store: emptyStore(), corrupt: true };
  }

  // A copy of base plus every incoming key base lacks; existing timestamps are kept.
  function mergeStore(base, incoming) {
    var out = emptyStore();
    var added = 0;
    Object.keys(base).forEach(function (key) { out[key] = base[key]; });
    Object.keys(incoming).forEach(function (key) {
      if (!hasOwn(out, key)) { out[key] = incoming[key]; added += 1; }
    });
    return { store: out, added: added };
  }

  // Read the store from storage, or null when storage cannot be read at all. A corrupt value is
  // copied to BACKUP_KEY (once per distinct value) and reported, and an empty store is returned.
  function loadChecked(storage) {
    var raw = null;
    try {
      raw = storage.getItem(STORE_KEY);
    } catch (err) {
      return null;
    }
    var parsed = parseStore(raw);
    if (parsed.corrupt && raw !== lastBackup) {
      lastBackup = raw;
      try {
        storage.setItem(BACKUP_KEY, raw);
        note(CORRUPT_NOTE);
      } catch (err) {
        console.error("svcd: could not back up unreadable checked marks", err);
        note(CORRUPT_NO_BACKUP);
      }
    }
    return parsed.store;
  }

  // The name shown for a candidate. Without a name the place key is shown (a path:lat,lng key, bare
  // coordinates or a placeId), which sharing mode replaces so a screenshot does not give it away.
  function displayName(c, sharing) {
    if (c.name) return c.name;
    if (sharing) return MASKED_NAME;
    return c.placeKey || "Unnamed place";
  }

  // A credit line as shown. Sharing mode hides a contributor's name; a Google credit is kept.
  function displayCredit(credit, sharing) {
    if (typeof credit !== "string" || !credit.trim()) return null;
    if (sharing && !/google/i.test(credit)) return MASKED_CREDIT;
    return credit;
  }

  // The text filter always matches the real name, so turning sharing mode on does not change the list.
  function textMatches(c, q) {
    return !q || displayName(c, false).toLowerCase().includes(q) || String(c.month).toLowerCase().includes(q);
  }

  function sourceMatches(c, source) {
    return source === "all" || c.source === source;
  }

  function checkedMatches(c, mode, store) {
    if (mode === "all") return true;
    return (mode === "checked") === hasOwn(store, c.checkKey);
  }

  // The drawer list predicate: text, provenance and checked filters all apply.
  function matchesFilters(c, f, store) {
    return textMatches(c, f.text) && sourceMatches(c, f.source) && checkedMatches(c, f.checked, store);
  }

  // Exposed for tests (tests/test_drawer_js.py); the page itself does not use this.
  window.__svcd = {
    validStore: validStore, parseStore: parseStore, loadChecked: loadChecked, mergeStore: mergeStore,
    matchesFilters: matchesFilters, displayName: displayName, displayCredit: displayCredit,
    STORE_KEY: STORE_KEY, BACKUP_KEY: BACKUP_KEY
  };

  var map = __SVCD_MAP__;
  var cluster = __SVCD_CLUSTER__;
  var heat = __SVCD_HEAT__;

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
  var iconGrey = new Map();  // rank -> whether the marker currently has the grey (checked) icon
  data.forEach(function (c) {
    if (!SOURCE_LABELS.hasOwnProperty(c.source)) c.source = "google";
    if (typeof c.checkKey !== "string" || !c.checkKey) c.checkKey = c.placeKey + "|" + c.month + "|" + c.source;
    byRank.set(c.rank, c);
  });

  var drawer = document.getElementById("svcd-drawer");
  var toggleBtn = document.getElementById("svcd-toggle");
  var hideBtn = document.getElementById("svcd-hide");
  var list = document.getElementById("svcd-list");
  var filterInput = document.getElementById("svcd-filter");
  var filterBox = document.getElementById("svcd-filter-box");
  var filterSummary = document.getElementById("svcd-filter-summary");
  var sourceSelect = document.getElementById("svcd-source-filter");
  var checkedSelect = document.getElementById("svcd-checked-filter");
  var prevBtn = document.getElementById("svcd-prev");
  var nextBtn = document.getElementById("svcd-next");
  var pageLabel = document.getElementById("svcd-page");
  var countLabel = document.getElementById("svcd-count");
  var statusLabel = document.getElementById("svcd-status");
  var noteLabel = document.getElementById("svcd-note");
  var exportBtn = document.getElementById("svcd-export");
  var importBtn = document.getElementById("svcd-import");
  var importFile = document.getElementById("svcd-import-file");
  var clearBtn = document.getElementById("svcd-clear");
  var sharingBox = document.getElementById("svcd-sharing");
  var sharingBadge = document.getElementById("svcd-sharing-badge");
  var sharingInfo = document.getElementById("svcd-sharing-info");
  var sharingHelp = document.getElementById("svcd-sharing-help");
  if (!drawer) return;  // no drawer markup (the test harness): helpers only
  var defaultStatus = statusLabel.textContent;

  var filtered = data;
  var page = 1;
  var activeRank = null;
  var markerTimer = null;
  var filters = { text: "", source: "all", checked: "all" };
  var shownOnMap = new Set();  // ranks whose markers are in the cluster
  var sharing = false;  // sharing mode masks unnamed places and contributor credits; never persisted

  // ---------------------------------------------------------------- checked state

  // Accessing window.localStorage itself throws in some browsers when site data is blocked.
  function getStorage() {
    try {
      return window.localStorage;
    } catch (err) {
      return null;
    }
  }

  function readStore() {
    var storage = getStorage();
    return storage ? loadChecked(storage) : null;
  }

  function saveChecked() {
    try {
      getStorage().setItem(STORE_KEY, JSON.stringify(checked));
    } catch (err) {
      note(NO_STORAGE);
    }
  }

  // Re-read the store just before writing, so marks made in another tab since this page loaded
  // are kept, then apply one change to the fresh copy and save it. change either edits the store
  // it is given in place or returns a replacement.
  function updateStore(change) {
    var base = readStore() || checked;
    checked = change(base) || base;
    saveChecked();
  }

  var checked = readStore();
  if (!checked) {
    note(NO_STORAGE);
    checked = emptyStore();
  }

  function isChecked(c) { return hasOwn(checked, c.checkKey); }

  function checkedCount() {
    var n = 0;
    data.forEach(function (c) { if (isChecked(c)) n += 1; });
    return n;
  }

  function note(text) {
    if (noteLabel) noteLabel.textContent = text || "";
  }

  // ---------------------------------------------------------------- helpers

  // Only https links are rendered; anything else (javascript:, data:, http:) is shown as text.
  function safeUrl(url) {
    return typeof url === "string" && url.startsWith("https://") ? url : null;
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

  function badge(source) {
    var s = SOURCE_LABELS.hasOwnProperty(source) ? source : "google";
    var node = el("span", "svcd-badge svcd-src-" + s, SOURCE_LABELS[s]);
    node.title = s === "user" ? "Photosphere uploaded by a Maps contributor" : "Google's own Street View imagery";
    return node;
  }

  function panoSource(c, p) {
    return SOURCE_LABELS.hasOwnProperty(p.source) ? p.source : c.source;
  }

  // Credit line for a user photo as shown (masked in sharing mode), or null; shown as text only.
  function creditText(c, p) {
    if (panoSource(c, p) !== "user") return null;
    return displayCredit(p.copyright, sharing);
  }

  function shownName(c) {
    return displayName(c, sharing);
  }

  function markerTitle(c) {
    return shownName(c) + " (" + c.month + ")";
  }

  function plural(n, word) {
    return n + " " + word + (n === 1 ? "" : "s");
  }

  function visitsText(c) {
    return plural(c.visitCount, "visit");
  }

  function dwellText(c) {
    return Math.round(c.dwellMinutes) + " min in daylight";
  }

  // The middle dot between parts of a line, as a muted separator.
  function sep() {
    return el("span", "svcd-sep", "\u00b7");
  }

  // Month, then a middle dot, then the odds ("2022-11", dot, "about 1 in 18").
  function oddsLine(c) {
    var line = el("div", "svcd-line");
    line.appendChild(el("span", "svcd-month", c.month));
    line.appendChild(sep());
    line.appendChild(el("span", "svcd-odds", c.odds));
    return line;
  }

  // Visits and daylight dwell, plus a contributor credit when there is one (already masked).
  function metaLine(c, credit) {
    var meta = el("div", "svcd-meta");
    meta.appendChild(el("span", null, visitsText(c)));
    meta.appendChild(sep());
    meta.appendChild(el("span", null, dwellText(c)));
    if (credit) {
      meta.appendChild(sep());
      meta.appendChild(el("span", "svcd-credit", credit));
    }
    return meta;
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

  function checkBox(c, className) {
    var label = el("label", "svcd-check");
    var box = el("input");
    box.type = "checkbox";
    box.className = className;
    box.dataset.rank = String(c.rank);
    box.checked = isChecked(c);
    box.addEventListener("change", function () { setChecked(c, box.checked); });
    label.appendChild(box);
    label.appendChild(document.createTextNode("Checked"));
    return label;
  }

  // ---------------------------------------------------------------- markers

  function buildPopup(c) {
    var root = el("div", "svcd-popup");
    var head = el("div", "svcd-popup-head");
    var name = shownName(c);
    var heading = el("h2", null, name);
    heading.title = name;
    head.appendChild(heading);
    head.appendChild(badge(c.source));
    root.appendChild(head);
    root.appendChild(oddsLine(c));
    root.appendChild(metaLine(c, null));
    var ul = el("ul", "svcd-panos");
    (c.panos || []).forEach(function (p) {
      // One line per panorama: link, date, distance. The heading badge gives the source, which all
      // of a candidate's panoramas share; a contributor credit (user photos only) goes underneath.
      var li = el("li");
      var row = el("div", "svcd-pano-row");
      var url = safeUrl(p.url);
      row.appendChild(url ? externalLink(url, "Open panorama") : el("span", null, "Panorama (no link)"));
      row.appendChild(el("span", null, p.date || "date unknown"));
      row.appendChild(el("span", null, Math.round(p.distanceM) + " m"));
      li.appendChild(row);
      var credit = creditText(c, p);
      if (credit) li.appendChild(el("div", "svcd-credit", credit));
      ul.appendChild(li);
    });
    root.appendChild(ul);
    root.appendChild(checkBox(c, "svcd-popup-check"));
    return root;
  }

  function markerIcon(c) {
    var pin = el("span", isChecked(c) ? "svcd-grey" : "svcd-pin-dot");
    return L.divIcon({
      className: "svcd-pin",
      html: pin,
      iconSize: [18, 18],
      iconAnchor: [9, 9],
      popupAnchor: [0, -9]
    });
  }

  data.forEach(function (c) {
    if (!isFinite(c.lat) || !isFinite(c.lng)) return;
    var marker = L.marker([c.lat, c.lng], { icon: markerIcon(c), title: markerTitle(c) });
    marker.bindPopup(function () { return buildPopup(c); }, { maxWidth: 340, minWidth: 280 });
    marker.on("click", function () { selectRank(c.rank, { scroll: true }); });
    markers.set(c.rank, marker);
    iconGrey.set(c.rank, isChecked(c));
  });

  // ---------------------------------------------------------------- filters

  function matchesText(c) { return textMatches(c, filters.text); }

  function matchesSource(c) { return sourceMatches(c, filters.source); }

  function matchesChecked(c) { return checkedMatches(c, filters.checked, checked); }

  // Markers and heat follow the provenance and checked filters; the text filter only narrows the list.
  function onMap(c) {
    return matchesSource(c) && matchesChecked(c);
  }

  function syncMapLayers() {
    var add = [];
    var remove = [];
    markers.forEach(function (marker, rank) {
      var want = onMap(byRank.get(rank));
      if (want && !shownOnMap.has(rank)) { add.push(marker); shownOnMap.add(rank); }
      if (!want && shownOnMap.has(rank)) { remove.push(marker); shownOnMap.delete(rank); }
    });
    if (remove.length) cluster.removeLayers(remove);
    if (add.length) cluster.addLayers(add);
    if (heat) {
      heat.setLatLngs(data.filter(function (c) {
        return onMap(c) && isFinite(c.lat) && isFinite(c.lng);
      }).map(function (c) { return [c.lat, c.lng, c.heat]; }));
    }
  }

  function setSelect(select, value) {
    if (select) select.value = value;
  }

  // A collapsed filter with text in it still says so on its summary line.
  function syncFilterSummary() {
    var on = !!filters.text;
    var text = on ? "Filter: active" : "Filter";
    if (filterSummary.textContent !== text) filterSummary.textContent = text;
    filterSummary.classList.toggle("svcd-filter-on", on);
  }

  // Recompute the visible list. A filter change goes back to page 1; a checked toggle keeps the page.
  function refresh(resetPage) {
    filtered = data.filter(function (c) { return matchesFilters(c, filters, checked); });
    if (resetPage) page = 1;
    syncMapLayers();
    renderList();
  }

  // ---------------------------------------------------------------- drawer

  function setOpen(open) {
    if (!open) setHelpOpen(false);
    document.body.classList.toggle("svcd-open", open);
    toggleBtn.setAttribute("aria-expanded", String(open));
    drawer.inert = !open;
    map.invalidateSize();
    setTimeout(function () { map.invalidateSize(); }, 250);
  }

  // The sharing mode info popover under its "i" button.
  function helpOpen() {
    return !!sharingHelp && !sharingHelp.hidden;
  }

  function setHelpOpen(open) {
    if (!sharingInfo || !sharingHelp) return;
    sharingHelp.hidden = !open;
    sharingInfo.setAttribute("aria-expanded", String(open));
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
    if (isChecked(c)) li.classList.add("svcd-checked");

    var row = el("div", "svcd-row");
    var rank = el("a", "svcd-rank", "#" + c.rank);
    rank.href = "#rank-" + c.rank;
    rank.title = "Link to this entry";
    rank.addEventListener("click", function (ev) {
      ev.preventDefault();
      history.replaceState(null, "", "#rank-" + c.rank);
      selectRank(c.rank, { scroll: false });
    });
    row.appendChild(rank);
    var name = shownName(c);
    var nameNode = el("span", "svcd-name", name);
    nameNode.title = name;
    row.appendChild(nameNode);
    row.appendChild(badge(c.source));
    li.appendChild(row);

    var body = el("div", "svcd-body");
    body.appendChild(oddsLine(c));
    var best = bestPano(c);
    body.appendChild(metaLine(c, best ? creditText(c, best) : null));

    var links = el("div", "svcd-links");
    var show = el("button", null, "Show on map");
    show.type = "button";
    show.addEventListener("click", function () { showOnMap(c); });
    links.appendChild(show);
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
    links.appendChild(checkBox(c, "svcd-item-check"));
    body.appendChild(links);
    li.appendChild(body);

    li.addEventListener("keydown", function (ev) {
      if (ev.target !== li) return;
      if (ev.key === "Enter") {
        ev.preventDefault();
        showOnMap(c);
      } else if (ev.key === " " || ev.key === "Spacebar") {
        ev.preventDefault();
        setChecked(c, !isChecked(c));
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
      list.appendChild(el("li", "svcd-empty", "No candidates match the filters."));
    }
    filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).forEach(function (c) {
      list.appendChild(buildItem(c));
    });
    pageLabel.textContent = "Page " + page + " of " + pages;
    prevBtn.disabled = page <= 1;
    nextBtn.disabled = page >= pages;
    // Set only on change, so the live region does not announce the same count after every redraw.
    var count = data.length
      ? plural(data.length, "candidate") + ", " + checkedCount() + " checked"
      : "No candidates";
    if (countLabel.textContent !== count) countLabel.textContent = count;
    statusLabel.textContent = filtered.length === data.length
      ? defaultStatus
      : filtered.length + " of " + data.length + " match the filters.";
    syncFilterSummary();
  }

  // Record or clear one checked mark, then update the list, the open popup and the marker.
  function setChecked(c, value) {
    var focused = document.activeElement;
    var hadFocus = focused && list.contains(focused);
    // The list is rebuilt below; when the entry's own checkbox had focus, focus goes back to the
    // rebuilt checkbox rather than to the list item.
    var onBox = hadFocus && focused.classList.contains("svcd-item-check");
    var focusIndex = -1;
    if (hadFocus) {
      var items = Array.prototype.slice.call(list.querySelectorAll(".svcd-item"));
      focusIndex = items.indexOf(focused.closest(".svcd-item"));
    }
    updateStore(function (store) {
      if (!value) {
        delete store[c.checkKey];
      } else if (!hasOwn(store, c.checkKey)) {
        store[c.checkKey] = new Date().toISOString();
      }
    });
    refreshAllMarks();
    if (hadFocus) {
      var target = document.getElementById("svcd-item-" + c.rank);
      if (!target) {
        var rest = list.querySelectorAll(".svcd-item");
        target = rest[Math.min(focusIndex, rest.length - 1)] || null;
      }
      var box = target && onBox ? target.querySelector(".svcd-item-check") : null;
      if (box) {
        box.focus();
      } else if (target) {
        target.focus();
      }
    }
  }

  // After any change to the store (here or in another tab), bring marker icons, open popups,
  // counts and the list in line with it. Only markers whose checked state changed get a new icon.
  function refreshAllMarks() {
    markers.forEach(function (marker, rank) {
      var c = byRank.get(rank);
      var grey = isChecked(c);
      if (iconGrey.get(rank) !== grey) {
        marker.setIcon(markerIcon(c));
        iconGrey.set(rank, grey);
      }
    });
    document.querySelectorAll(".svcd-popup-check").forEach(function (box) {
      var c = byRank.get(Number(box.dataset.rank));
      if (c) box.checked = isChecked(c);
    });
    refresh(false);
  }

  // Sharing mode changed: re-render names and credits in the current list page, marker tooltips and
  // any open popup. Filters, page, checked marks and the map view stay as they are.
  function setSharing(value) {
    sharing = !!value;
    if (sharingBox) sharingBox.checked = sharing;
    if (sharingBadge) sharingBadge.hidden = !sharing;
    markers.forEach(function (marker, rank) {
      var title = markerTitle(byRank.get(rank));
      marker.options.title = title;
      var node = marker.getElement ? marker.getElement() : null;
      if (node) node.title = title;
      if (marker.isPopupOpen && marker.isPopupOpen()) marker.getPopup().update();
    });
    var scroll = list.scrollTop;
    renderList();
    list.scrollTop = scroll;
  }

  function selectRank(rank, opts) {
    var c = byRank.get(rank);
    if (!c) return false;
    var idx = filtered.indexOf(c);
    if (idx < 0) {
      // Clear only the filters that hide this entry.
      if (!matchesText(c)) { filterInput.value = ""; filters.text = ""; }
      if (!matchesSource(c)) { filters.source = "all"; setSelect(sourceSelect, "all"); }
      if (!matchesChecked(c)) { filters.checked = "all"; setSelect(checkedSelect, "all"); }
      refresh(true);
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
    if (!marker || !shownOnMap.has(c.rank)) return;
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

  // ---------------------------------------------------------------- export / import

  function exportChecked() {
    var fresh = readStore();
    if (fresh) checked = fresh;
    var text = JSON.stringify(checked, null, 2);
    var url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    var a = el("a");
    a.href = url;
    a.download = EXPORT_NAME;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    note("Exported " + plural(Object.keys(checked).length, "checked mark") + ".");
  }

  function importChecked(file) {
    if (!file) return;
    if (file.size > IMPORT_MAX_BYTES) {
      note("Import failed: the file is too large.");
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      var incoming = null;
      try {
        incoming = validStore(JSON.parse(String(reader.result)));
      } catch (err) {
        incoming = null;
      }
      if (!incoming) {
        note("Import failed: expected a JSON object of check keys to timestamps.");
        return;
      }
      var added = 0;
      updateStore(function (store) {
        var merged = mergeStore(store, incoming);
        added = merged.added;
        return merged.store;
      });
      refreshAllMarks();
      note("Imported " + plural(added, "new checked mark") + ".");
    };
    reader.onerror = function () { note("Import failed: the file could not be read."); };
    reader.readAsText(file);
  }

  function clearChecked() {
    var fresh = readStore();
    if (fresh) checked = fresh;
    var n = Object.keys(checked).length;
    if (!n) { note("Nothing to clear."); return; }
    if (!window.confirm("Clear all " + plural(n, "checked mark") + "? Export them first to keep a copy.")) return;
    updateStore(function () { return emptyStore(); });
    refreshAllMarks();
    note("Cleared all checked marks.");
  }

  // ---------------------------------------------------------------- wiring

  toggleBtn.addEventListener("click", function () { setOpen(true); filterSummary.focus(); });
  hideBtn.addEventListener("click", function () { setOpen(false); toggleBtn.focus(); });
  filterInput.addEventListener("input", function () {
    filters.text = String(filterInput.value).trim().toLowerCase();
    refresh(true);
  });
  // Opening the filter puts the cursor in the text box.
  filterBox.addEventListener("toggle", function () { if (filterBox.open) filterInput.focus(); });
  sourceSelect.addEventListener("change", function () { filters.source = sourceSelect.value; refresh(true); });
  checkedSelect.addEventListener("change", function () { filters.checked = checkedSelect.value; refresh(true); });
  exportBtn.addEventListener("click", exportChecked);
  importBtn.addEventListener("click", function () { importFile.value = ""; importFile.click(); });
  importFile.addEventListener("change", function () { importChecked(importFile.files && importFile.files[0]); });
  clearBtn.addEventListener("click", clearChecked);
  if (sharingBox) sharingBox.addEventListener("change", function () { setSharing(sharingBox.checked); });
  if (sharingInfo) sharingInfo.addEventListener("click", function () { setHelpOpen(!helpOpen()); });
  // The popover closes on Escape or on a click anywhere outside it and its button.
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape" || !helpOpen()) return;
    setHelpOpen(false);
    if (drawer.contains(document.activeElement)) sharingInfo.focus();
  });
  document.addEventListener("click", function (ev) {
    if (!helpOpen() || sharingInfo.contains(ev.target) || sharingHelp.contains(ev.target)) return;
    setHelpOpen(false);
  });
  prevBtn.addEventListener("click", function () { page -= 1; renderList(); });
  nextBtn.addEventListener("click", function () { page += 1; renderList(); });
  window.addEventListener("hashchange", fromHash);
  // Another tab changed the checked marks (key null means its storage was cleared): reload them.
  window.addEventListener("storage", function (ev) {
    if (ev.key !== null && ev.key !== STORE_KEY) return;
    var fresh = readStore();
    if (!fresh) return;
    checked = fresh;
    refreshAllMarks();
  });
  var narrowMedia = window.matchMedia(NARROW_QUERY);
  if (narrowMedia.addEventListener) {
    narrowMedia.addEventListener("change", function (ev) { setOpen(!ev.matches); });
  }

  // Browsers may restore form values on reload; start from the defaults so the list matches.
  setSelect(sourceSelect, "all");
  setSelect(checkedSelect, "all");
  filterInput.value = "";
  if (sharingBox) sharingBox.checked = false;  // sharing mode is per view: off after every reload
  if (sharingBadge) sharingBadge.hidden = true;
  setOpen(!isNarrow());
  refresh(true);
  if (!fromHash() && markers.size) {
    map.fitBounds(cluster.getBounds(), { padding: [30, 30], maxZoom: 16 });
  }
})();
