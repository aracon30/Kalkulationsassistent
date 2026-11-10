from app.services.prefetch import Prefetcher


def test_prefetch_reads_in_parallel_and_falls_back_on_errors(tmp_path):
    from tests.factory import make_heta_r05_kalkulation
    from app.config import get_settings
    from app.services.excel_importer import extract_workbook, load_profiles
    f = tmp_path / "SE-99950.26_Kalkulation.xlsx"
    make_heta_r05_kalkulation(f, "SE-99950/26")
    missing = str(tmp_path / "fehlt.xlsx")
    plan = [(1, "excel", str(f), "angebotskalkulation"), (2, "excel", missing, "angebotskalkulation")]
    with Prefetcher(plan, str(get_settings().profiles_dir), workers=2) as p:
        ex = p.get(1, lambda: None)
        assert ex is not None and ex.profile_id == extract_workbook(f, load_profiles(get_settings().profiles_dir), "angebotskalkulation").profile_id
        assert p.get(2, lambda: "fallback") == "fallback"   # Fehler im Worker -> erneut im Hauptprozess
        assert p.get(3, lambda: "nicht geplant") == "nicht geplant"
