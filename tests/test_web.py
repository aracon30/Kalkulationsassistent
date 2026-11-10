import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def client(env):
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def imported(client, offer_base):
    from app.services.jobs import manager
    manager.start("scan", str(offer_base), wait=True)
    manager.start("import", None, wait=True)
    return offer_base


def test_pages_render_empty(client):
    for url in ["/", "/import", "/dokumente", "/angebote", "/pruefung", "/pruefung/protokoll", "/aehnlichkeit", "/simulation", "/einstellungen"]:
        r = client.get(url)
        assert r.status_code == 200, url
        assert "HETA" in r.text


def test_full_flow_in_browser(client, imported):
    from app.database import new_session
    from app.models import Document, Offer
    r = client.get("/")
    assert "Importierte Angebote" in r.text
    r = client.get("/angebote?produkttyp=Kerzenfilter")
    assert "SE-99901.26" in r.text
    s = new_session()
    offer = s.scalars(select(Offer)).one()
    drawing = s.scalars(select(Document).where(Document.file_name == "KF-9-301.11-005.pdf")).one()
    s.close()
    r = client.get(f"/angebote/{offer.id}")
    assert r.status_code == 200
    assert "Pos. 1" in r.text and "Pos. 2" in r.text and "Testkunde Chemie GmbH" in r.text
    assert "B33" in r.text  # Quellenangabe (Zelle) im eingebetteten Quellen-JSON
    png = client.get(f"/dokumente/{drawing.id}/seite/1.png?zoom=1")
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/dokumente/{drawing.id}/seite/99.png").status_code == 404
    pdf = client.get(f"/dokumente/{drawing.id}/datei")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get("/aehnlichkeit?product_type=Kerzenfilter&dn=300").text.count("SE-99901.26") >= 1
    r = client.get("/import/jobs/2/bericht.csv")
    assert r.status_code == 200 and "Angebotsnummer" in r.text


def test_review_correction_is_logged(client, imported):
    from app.database import new_session
    from app.models import Correction, ReviewIssue
    s = new_session()
    issue = s.scalars(select(ReviewIssue).where(ReviewIssue.status == "offen")).first()
    s.close()
    r = client.post(f"/pruefung/{issue.id}", data={"action": "freigeben", "user_name": "Prüfer"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/pruefung/{issue.id}", data={"action": "freigeben", "user_name": ""}, follow_redirects=False)
    s = new_session()
    assert s.get(ReviewIssue, issue.id).status == "erledigt"
    assert s.scalars(select(Correction)).first().user_name == "Prüfer"
    s.close()


def test_review_correction_updates_target(client, imported):
    from app.database import new_session
    from app.models import Correction, OfferItem, ReviewIssue
    s = new_session()
    item = s.scalars(select(OfferItem).where(OfferItem.position_number == 1)).one()
    issue = ReviewIssue(code="test", message="Test", offer_id=item.offer_id, offer_item_id=item.id)
    s.add(issue); s.commit()
    iid, item_id = issue.id, item.id
    s.close()
    r = client.post(f"/pruefung/{iid}", data={"action": "korrigieren", "field_name": "weight", "new_value": "1.234,5",
                                              "reason": "Wiegeprotokoll", "user_name": "Prüfer"}, follow_redirects=False)
    assert r.status_code == 303
    s = new_session()
    assert s.get(OfferItem, item_id).weight == 1234.5
    c = s.scalars(select(Correction).where(Correction.field_name == "weight")).one()
    assert c.old_value == "410.0" and c.new_value == "1234.5" and c.reason == "Wiegeprotokoll"
    s.close()


def test_simulation_api(client):
    r = client.post("/api/simulation/berechnen", json={"values": {"material_cost": 1000, "manufacturing_hours": 10}})
    assert r.status_code == 200
    body = r.json()
    assert body["sales_price"] > 0 and body["scheme_validated"] is False


def test_cross_origin_post_blocked(client):
    r = client.post("/import/start", data={"mode": "neu"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_untrusted_host_rejected(client):
    r = client.get("/", headers={"Host": "evil.example"})
    assert r.status_code == 400


def test_documents_only_by_id(client):
    assert client.get("/dokumente/999/datei").status_code == 404
    assert client.get("/dokumente/..%2F..%2Fetc%2Fpasswd/datei").status_code in (404, 422)


def test_scan_invalid_dir_shows_error(client, tmp_path):
    r = client.post("/import/scan", data={"base_dir": str(tmp_path / "fehlt")}, follow_redirects=True)
    assert "nicht gefunden" in r.text


def test_backup(client):
    r = client.post("/einstellungen/backup")
    assert r.status_code == 200 and r.content[:15] == b"SQLite format 3"
