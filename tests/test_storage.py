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
    assert header.split(",")[0] == "placeKey" and header.split(",")[-1] == "url"
    writeCsv(tmp_path / "empty.csv", [], cls=Collision)
    assert (tmp_path / "empty.csv").read_text(encoding="utf-8").strip() == header
