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
    for url in ["/", "/import", "/dokumente", "/angebote", "/pruefung", "/pruefung/protokoll", "/aehnlichkeit", "/anfragen", "/einstellungen"]:
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


def test_material_page_and_csv(client, env):
    from tests.factory import make_heta_r05_kalkulation
    from app.services.jobs import manager
    folder = env / "M" / "SE-99907.26-X"
    make_heta_r05_kalkulation(folder / "SE-99907.26_Kalkulation.xlsx", "SE-99907/26")
    manager.start("scan", str(env / "M"), wait=True)
    manager.start("import", None, wait=True)
    r = client.get("/material")
    assert r.status_code == 200 and "Rohr" in r.text and "Ø323,9 x 4,5" in r.text
    r = client.get("/material?kategorie=Rohr&ansicht=zeilen")
    assert "Behältermantel" in r.text and "Behälterflansch" not in r.text
    r = client.get("/material/export.csv?kategorie=Flansch")
    assert r.status_code == 200 and "DN300 PN16" in r.text and "Behältermantel" not in r.text
    r = client.get("/angebote/1")
    assert "Materialauszug" in r.text


def test_offer_page_with_text_row_in_material_list(client, env):
    """Regression: Hinweistext im Materialauszug führte zu Internal Server Error (Summe über None)."""
    import openpyxl
    from tests.factory import make_heta_r05_kalkulation
    from app.services.jobs import manager
    from app.database import new_session
    from app.models import MaterialLine
    p = make_heta_r05_kalkulation(env / "R" / "SE-99908.26-X" / "SE-99908.26_Kalkulation.xlsx", "SE-99908/26")
    wb = openpyxl.load_workbook(p)
    wb["KF-300"].insert_rows(6)
    wb["KF-300"]["B6"] = "Hinweis: Preise netto"
    wb.save(p)
    manager.start("scan", str(env / "R"), wait=True)
    manager.start("import", None, wait=True)
    r = client.get("/angebote/1")
    assert r.status_code == 200 and "Materialauszug" in r.text
    s = new_session()
    assert not s.query(MaterialLine).filter(MaterialLine.description.like("Hinweis%")).all()
    s.close()


def test_unexpected_error_shows_german_page(env, monkeypatch):
    from app.main import create_app
    from app.routers import dashboard
    monkeypatch.setattr(dashboard, "collect_kpis", lambda db: 1 / 0)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        r = c.get("/")
    assert r.status_code == 500 and "ZeroDivisionError" in r.text and "app.log" in r.text


def test_service_view_and_offer_page(client, env):
    from tests.factory import make_service_calc
    from app.services.jobs import manager
    make_service_calc(env / "SV" / "DE-99941.26" / "DE-99941.26_Kalkulation Service.xlsx", "DE-99941/26", abroad=True)
    manager.start("scan", str(env / "SV"), wait=True)
    manager.start("import", None, wait=True)
    r = client.get("/material?ansicht=service")
    assert r.status_code == 200 and "Service Experte" in r.text and "Ausland" in r.text and "DE-99941.26" in r.text
    r = client.get("/angebote/1")
    assert "Serviceeinsatz" in r.text and "Wartung" in r.text


def test_review_list_hides_info_by_default(client, imported):
    from app.database import session_scope
    from app.models import ReviewIssue
    with session_scope() as s:
        s.add(ReviewIssue(code="testhinweis", message="NUR-INFO-XYZ", severity="info"))
        s.add(ReviewIssue(code="testwarnung", message="WARN-XYZ", severity="warnung"))
    r = client.get("/pruefung")
    assert "WARN-XYZ" in r.text and "NUR-INFO-XYZ" not in r.text and "testhinweis (1 Hinweise)" in r.text
    assert "NUR-INFO-XYZ" in client.get("/pruefung?stufe=alle").text
    assert "NUR-INFO-XYZ" in client.get("/pruefung?code=testhinweis").text
