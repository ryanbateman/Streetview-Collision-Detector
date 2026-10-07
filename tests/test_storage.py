from datetime import datetime, timezone

from svcd.models import Collision, Visit
from svcd.storage import appendJsonl, readJsonl, writeCsv, writeJsonl


def makeVisit(**overrides) -> Visit:
    base = dict(
        placeId="ChIJtest", name="Cafe", address="1 Test St", lat=52.51631, lng=13.37773,
        start=datetime(2022, 8, 31, 22, 0, tzinfo=timezone.utc),
        end=datetime(2022, 9, 1, 1, 30, tzinfo=timezone.utc), source="semantic",
    )
    base.update(overrides)
    return Visit(**base)


def test_jsonl_roundtrip_preserves_datetimes_and_none(tmp_path):
    rows = [makeVisit(), makeVisit(placeId=None, name=None, address=None)]
    assert writeJsonl(tmp_path / "v.jsonl", rows) == 2
    assert readJsonl(tmp_path / "v.jsonl", Visit) == rows


def test_naive_datetimes_are_treated_as_utc(tmp_path):
    naive = makeVisit(start=datetime(2022, 8, 1, 12, 0), end=datetime(2022, 8, 1, 13, 0))
    writeJsonl(tmp_path / "v.jsonl", [naive])
    back = readJsonl(tmp_path / "v.jsonl", Visit)[0]
    assert back.start == datetime(2022, 8, 1, 12, 0, tzinfo=timezone.utc)


def test_append_and_missing_file(tmp_path):
    path = tmp_path / "v.jsonl"
    assert readJsonl(path, Visit) == []
    writeJsonl(path, [makeVisit()])
    appendJsonl(path, [makeVisit(name="Second")])
    assert [v.name for v in readJsonl(path, Visit)] == ["Cafe", "Second"]


def test_csv_header_matches_dataclass(tmp_path):
    collision = Collision(
        placeKey="k", name="Cafe", month="2022-08", panoId="p", panoDate="2022-08",
        panoLat=52.5, panoLng=13.4, distanceM=12.3, visitCount=2, dwellMinutes=90.0,
        score=5.1, url="https://www.google.com/maps/@?api=1&map_action=pano&pano=p",
    )
    writeCsv(tmp_path / "c.csv", [collision])
    header = (tmp_path / "c.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == [f.name for f in __import__("dataclasses").fields(Collision)]
    writeCsv(tmp_path / "empty.csv", [], cls=Collision)
    assert (tmp_path / "empty.csv").read_text(encoding="utf-8").strip() == header


def test_truncated_last_line_is_skipped_with_warning(tmp_path, caplog):
    path = tmp_path / "v.jsonl"
    writeJsonl(path, [makeVisit(), makeVisit(name="Second")])
    text = path.read_text(encoding="utf-8")
    path.write_text(text + '{"placeId": "ChIJcut", "na', encoding="utf-8")
    with caplog.at_level("WARNING"):
        rows = readJsonl(path, Visit)
    assert [v.name for v in rows] == ["Cafe", "Second"]
    assert "unreadable line 3" in caplog.text


def test_missing_fields_from_older_files_take_dataclass_defaults(tmp_path):
    import json
    from svcd.models import Candidate
    path = tmp_path / "old.jsonl"
    record = {k: v for k, v in json.loads(json.dumps(
        __import__("svcd.storage", fromlist=["toRecord"]).toRecord(makeVisit()))).items()
        if k not in ("kind", "semanticType", "importance")}
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    visit = readJsonl(path, Visit)[0]
    assert (visit.kind, visit.semanticType, visit.importance) == ("place", None, None)
    candidate = Candidate(rank=1, placeKey="k", name=None, month="2022-08", lat=1.0, lng=2.0, kind="place",
                          probability=0.1, coverage=0.2, visibility=0.3, visitCount=1, dwellMinutes=5.0)
    writeJsonl(path, [candidate])
    assert readJsonl(path, Candidate)[0].panos == []
    (tmp_path / "nopanos.jsonl").write_text(
        json.dumps({k: v for k, v in __import__("svcd.storage", fromlist=["toRecord"]).toRecord(candidate).items()
                    if k != "panos"}) + "\n", encoding="utf-8")
    assert readJsonl(tmp_path / "nopanos.jsonl", Candidate)[0].panos == []
