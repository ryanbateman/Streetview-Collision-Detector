"""Behavioural tests for the pure helpers in svcd/templates/drawer.js, run under Node.

The harness loads drawer.js in a vm context with stub window, document, localStorage and L. With
no drawer markup the script stops after exposing its helpers on window.__svcd.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
if NODE is None:
    pytest.skip("node is not on PATH", allow_module_level=True)

DRAWER_JS = Path(__file__).resolve().parent.parent / "svcd" / "templates" / "drawer.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function makeStorage(initial, failing) {
  const items = new Map(Object.entries(initial || {}));
  return {
    writes: 0,
    getItem(key) { if (failing) throw new Error("blocked"); return items.has(key) ? items.get(key) : null; },
    setItem(key, value) { if (failing) throw new Error("blocked"); this.writes += 1; items.set(key, String(value)); },
    removeItem(key) { items.delete(key); },
  };
}

const noteLabel = { textContent: "" };
const window = { localStorage: makeStorage(), addEventListener() {}, matchMedia() { return { matches: false }; } };
const document = { getElementById(id) { return id === "svcd-note" ? noteLabel : null; } };
const context = { window, document, console, L: {} };
vm.createContext(context);
const source = fs.readFileSync(process.argv[2], "utf8").replace(/__SVCD_[A-Z]+__/g, "null");
vm.runInContext(source, context, { filename: "drawer.js" });
const h = window.__svcd;

const plain = (store) => store === null ? null : Object.assign({}, store);
const out = {};

// validStore
out.mixed = plain(h.validStore({ "a|2020-01|google": "t1", b: 5, c: null, d: { x: 1 }, e: "t2", f: ["t"] }));
out.rejected = [[["a", "t"]], "str", null, 42, true].map((v) => h.validStore(v));
const proto = h.validStore(JSON.parse('{"__proto__": "t0", "k": "v"}'));
out.protoKeys = Object.keys(proto).sort();
out.protoValue = proto["__proto__"];
out.protoIsNull = Object.getPrototypeOf(proto) === null;
out.objectPrototypeClean = Object.keys(Object.prototype).length === 0 && ({}).k === undefined
  && vm.runInContext("Object.keys(Object.prototype).length === 0 && ({}).k === undefined", context);

// mergeStore (import)
const base = h.validStore({ a: "2024-01-01T00:00:00Z" });
const merged = h.mergeStore(base, h.validStore({ a: "2025-06-06T00:00:00Z", b: "2025-07-07T00:00:00Z" }));
out.merged = plain(merged.store);
out.mergedAdded = merged.added;
out.baseUnchanged = plain(base);
const protoMerge = h.mergeStore(h.validStore({}), h.validStore(JSON.parse('{"__proto__": "t1"}')));
out.protoMerge = { keys: Object.keys(protoMerge.store), added: protoMerge.added, clean: ({}).t1 === undefined };

// matchesFilters: text + provenance + checked compose
const c = { name: "Cafe Alpha", placeKey: "pk", month: "2021-05", source: "user", checkKey: "pk|2021-05|user" };
const checkedStore = h.validStore({ "pk|2021-05|user": "t" });
const emptyStore = h.validStore({});
const f = (text, source, checked) => ({ text, source, checked });
out.filters = [
  h.matchesFilters(c, f("", "all", "all"), emptyStore),
  h.matchesFilters(c, f("alpha", "user", "checked"), checkedStore),
  h.matchesFilters(c, f("alpha", "google", "all"), checkedStore),
  h.matchesFilters(c, f("beta", "all", "all"), checkedStore),
  h.matchesFilters(c, f("2021-05", "all", "checked"), checkedStore),
  h.matchesFilters(c, f("alpha", "user", "unchecked"), checkedStore),
  h.matchesFilters(c, f("", "user", "unchecked"), emptyStore),
  h.matchesFilters(c, f("", "user", "checked"), emptyStore),
];

// loadChecked: corrupt values are backed up once and reported; partial values keep good entries
const corrupt = makeStorage({ [h.STORE_KEY]: "{not json" });
out.corruptStore = plain(h.loadChecked(corrupt));
out.corruptBackup = corrupt.getItem(h.BACKUP_KEY);
out.corruptNote = noteLabel.textContent;
h.loadChecked(corrupt);
out.corruptWrites = corrupt.writes;
noteLabel.textContent = "";
const notObject = makeStorage({ [h.STORE_KEY]: '"just a string"' });
out.notObjectStore = plain(h.loadChecked(notObject));
out.notObjectBackup = notObject.getItem(h.BACKUP_KEY);
out.notObjectNote = noteLabel.textContent;
noteLabel.textContent = "";
const partial = makeStorage({ [h.STORE_KEY]: '{"a": "t1", "b": 2, "c": "t3"}' });
out.partialStore = plain(h.loadChecked(partial));
out.partialBackup = partial.getItem(h.BACKUP_KEY);
out.partialNote = noteLabel.textContent;
out.missingStore = plain(h.loadChecked(makeStorage()));
out.blockedStore = h.loadChecked(makeStorage({}, true));

// sharing mode: displayName masks unnamed places (the key is shown), displayCredit masks contributors
const pathPlace = { name: null, placeKey: "path:52.5163,13.3777" };
const coordPlace = { placeKey: "52.51631,13.37773" };
const namedPlace = { name: "Fixture Park", placeKey: "52.51631,13.37773" };
out.namesSharing = [pathPlace, coordPlace, namedPlace].map((p) => h.displayName(p, true));
out.namesPlain = [pathPlace, coordPlace, namedPlace].map((p) => h.displayName(p, false));
out.creditsSharing = ["\u00a9 Fixture Contributor 3", "\u00a9 Google", "Image: GOOGLE LLC", "", null]
  .map((credit) => h.displayCredit(credit, true));
out.creditsPlain = ["\u00a9 Fixture Contributor 3", "\u00a9 Google"].map((credit) => h.displayCredit(credit, false));

process.stdout.write(JSON.stringify(out));
"""

CORRUPT_NOTE = "Saved checks could not be read; a backup was kept as svcd-checked.bak"


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    harness = tmp_path_factory.mktemp("drawerjs") / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run([NODE, str(harness), str(DRAWER_JS)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_valid_store_keeps_valid_entries_and_drops_invalid_ones(results):
    assert results["mixed"] == {"a|2020-01|google": "t1", "e": "t2"}


def test_valid_store_rejects_arrays_strings_and_other_non_objects(results):
    assert results["rejected"] == [None, None, None, None, None]


def test_valid_store_keeps_proto_as_a_plain_key_without_pollution(results):
    assert results["protoKeys"] == ["__proto__", "k"]
    assert results["protoValue"] == "t0"
    assert results["protoIsNull"] is True
    assert results["objectPrototypeClean"] is True
    assert results["protoMerge"] == {"keys": ["__proto__"], "added": 1, "clean": True}


def test_import_merge_keeps_existing_timestamps(results):
    assert results["merged"] == {"a": "2024-01-01T00:00:00Z", "b": "2025-07-07T00:00:00Z"}
    assert results["mergedAdded"] == 1
    assert results["baseUnchanged"] == {"a": "2024-01-01T00:00:00Z"}


def test_filter_predicate_composes_text_provenance_and_checked(results):
    assert results["filters"] == [True, True, False, False, True, False, True, False]


def test_load_checked_backs_up_a_corrupt_store_once_and_starts_empty(results):
    assert results["corruptStore"] == {}
    assert results["corruptBackup"] == "{not json"
    assert results["corruptNote"] == CORRUPT_NOTE
    assert results["corruptWrites"] == 1  # the second load of the same value does not copy again
    assert results["notObjectStore"] == {}
    assert results["notObjectBackup"] == '"just a string"'
    assert results["notObjectNote"] == CORRUPT_NOTE


def test_load_checked_keeps_valid_entries_of_a_partly_invalid_store(results):
    assert results["partialStore"] == {"a": "t1", "c": "t3"}
    assert results["partialBackup"] is None
    assert results["partialNote"] == ""


def test_load_checked_handles_missing_and_blocked_storage(results):
    assert results["missingStore"] == {}
    assert results["blockedStore"] is None


def test_display_name_masks_unnamed_place_keys_only_in_sharing_mode(results):
    assert results["namesSharing"] == ["Unknown location", "Unknown location", "Fixture Park"]
    assert results["namesPlain"] == ["path:52.5163,13.3777", "52.51631,13.37773", "Fixture Park"]


def test_display_credit_masks_contributors_but_never_google(results):
    assert results["creditsSharing"] == ["Contributor hidden", "\u00a9 Google", "Image: GOOGLE LLC", None, None]
    assert results["creditsPlain"] == ["\u00a9 Fixture Contributor 3", "\u00a9 Google"]
