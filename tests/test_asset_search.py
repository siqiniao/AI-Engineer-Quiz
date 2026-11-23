"""Authenticated search and export tests with a synthetic, isolated catalog."""
import csv
import io
import json
import threading

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    from keepstack import db
    from keepstack.app import app
    from keepstack.config import config

    for attr, path in {
        "data_dir": tmp_path,
        "db_path": tmp_path / "keepstack.db",
        "blob_dir": tmp_path / "blobs",
        "thumb_dir": tmp_path / "thumbs",
        "cache_dir": tmp_path / "cache",
    }.items():
        monkeypatch.setattr(config, attr, path)
    monkeypatch.setattr(config, "ai_enabled", False)
    monkeypatch.setattr(config, "ocr_enabled", False)
    monkeypatch.setattr(db, "_local", threading.local())

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={
            "username": config.admin_user, "password": config.admin_password,
        })
        assert login.status_code == 200
        client.headers["Authorization"] = "Bearer " + login.json()["token"]
        collection = client.post("/api/collections", json={"name": "Campaign, 港口"})
        assert collection.status_code == 200
        collection_id = collection.json()["id"]
        sequence = 0

        def add(title, tags=(), *, member=True, status="active", media="image",
                created_at="2026-01-02T00:00:00Z", description=None):
            nonlocal sequence
            sequence += 1
            image = io.BytesIO()
            Image.new("RGB", (24, 24), (sequence, 70, 120)).save(image, "PNG")
            filename = f"港口,{sequence}.png"
            uploaded = client.post("/api/assets", data={"run_ai": "false"},
                                   files={"files": (filename, image.getvalue(), "image/png")})
            assert uploaded.status_code == 200, uploaded.text
            uuid = uploaded.json()["created"][0]["uuid"]
            path = f"/api/assets/{uuid}"
            assert client.patch(path, json={
                "title": title, "description": description, "status": status,
            }).status_code == 200
            for tag in tags:
                assert client.post(path + "/tags", json={"name": tag}).status_code == 200
            if member:
                assert client.post(f"/api/collections/{collection_id}/assets",
                                   json={"uuids": [uuid]}).status_code == 200
            with db.transaction() as conn:
                conn.execute("UPDATE assets SET created_at = ?, media_type = ? WHERE uuid = ?",
                             (created_at, media, uuid))
            return uuid

        yield client, collection_id, add
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()


def listed(client, params=()):
    response = client.get("/api/assets", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def exported(client, params=()):
    response = client.get("/api/assets/export.csv", params=params)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == 'attachment; filename="assets.csv"'
    assert response.headers["content-type"].startswith("text/csv")
    table = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"), newline=""))
    assert table.fieldnames == [
        "uuid", "title", "description", "filename", "media_type", "tags", "created_at",
    ]
    return list(table)


def test_combined_filters_page_and_export_in_both_orders(catalog):
    client, collection_id, add = catalog
    required = ("approved", "campaign", '海报, "港口"')
    title, description = 'Harbor, “港口”\n"approved"', '中文说明,"引号"\n第二行'
    tied = [add(title if i == 0 else f"Harbor {i}", (*required, "internal-review"),
                description=description if i == 0 else None) for i in range(5)]
    older = add("Harbor old", required, created_at="2026-01-01T00:00:00Z")
    newer = add("Harbor new", required, created_at="2026-01-03T00:00:00Z")
    add("Harbor private", (*required, "internal"))
    add("Harbor draft", (*required, "draft"))
    add("Harbor missing tag", ("approved", '海报, "港口"', "campaignable"))
    add("Harbor elsewhere", required, member=False)
    add("Harbor archived", required, status="archived")
    add("Harbor deleted", required, status="deleted")
    add("Harbor audio", required, media="audio")
    add("Mountain", required)

    filters = [
        ("q", '"harbor!"'), ("collection_id", collection_id), ("media_type", "image"),
        ("include_tags", " Approved "), ("include_tags", "approved"),
        ("include_tags", "campaign"), ("include_tags", required[2]),
        ("exclude_tags", "internal"), ("exclude_tags", "DRAFT"),
    ]
    for sort, expected in [
        ("oldest", [older, *tied, newer]), ("newest", [newer, *tied, older]),
    ]:
        params = [*filters, ("sort", sort)]
        actual = []
        for offset in (0, 3, 6, 9):
            page = listed(client, [*params, ("limit", 3), ("offset", offset)])
            assert page["total"] == len(expected)
            assert len(page["items"]) == max(0, min(3, len(expected) - offset))
            actual.extend(item["uuid"] for item in page["items"])
        assert actual == expected
        rows = exported(client, [*params, ("limit", 1), ("offset", 3)])
        assert [row["uuid"] for row in rows] == expected
        sample = next(row for row in rows if row["uuid"] == tied[0])
        assert sample["title"] == title
        assert sample["description"] == description
        assert sample["filename"] == "港口,1.png"
        assert sample["media_type"] == "image"
        assert sample["created_at"] == "2026-01-02T00:00:00Z"
        assert json.loads(sample["tags"]) == sorted((*required, "internal-review"))


def test_edits_update_fts_tag_filters_and_export(catalog):
    client, _, add = catalog
    uuid = add("Harbor", ("approved",), description="oldmarker")
    path = f"/api/assets/{uuid}"
    initial = {"q": "harbor oldmarker", "include_tags": "APPROVED"}
    assert listed(client, initial)["total"] == 1

    assert client.patch(path, json={
        "title": "Lighthouse", "description": '新说明, "aftermarker"\nsecond line',
    }).status_code == 200
    assert client.post(path + "/tags", json={"name": " NewCampaign "}).status_code == 200
    assert client.delete(path + "/tags/approved").status_code == 200

    for stale in ({"q": "harbor"}, {"q": "oldmarker"}, {"q": "approved"},
                  {"include_tags": "approved"}):
        assert listed(client, stale)["total"] == 0
        assert exported(client, stale) == []
    current = {"q": "lighthouse aftermarker newcampaign", "include_tags": "NewCampaign"}
    assert [item["uuid"] for item in listed(client, current)["items"]] == [uuid]
    row, = exported(client, current)
    assert row["uuid"] == uuid and row["title"] == "Lighthouse"
    assert row["description"] == '新说明, "aftermarker"\nsecond line'
    assert json.loads(row["tags"]) == ["newcampaign"]


def test_empty_special_inputs_pagination_and_authentication(catalog):
    client, _, add = catalog
    uuid = add("Harbor", ("approved",))
    assert [item["uuid"] for item in listed(client, {"q": " \t "})["items"]] == [uuid]
    for params in (
        {"q": '!"* , -'}, {"q": "does-not-exist"}, {"include_tags": "approve"},
        {"include_tags": "approved", "exclude_tags": "APPROVED"},
    ):
        assert listed(client, params)["items"] == []
        assert exported(client, params) == []
    assert listed(client, {"exclude_tags": "missing"})["total"] == 1
    for params in ({"limit": -1}, {"limit": 0}, {"limit": 201}, {"limit": "nan"},
                   {"offset": -1}, {"offset": "1.5"}, {"offset": 2**63}):
        assert client.get("/api/assets", params=params).status_code == 422
    del client.headers["Authorization"]
    for path in ("/api/assets", "/api/assets/export.csv"):
        assert client.get(path).status_code == 401
