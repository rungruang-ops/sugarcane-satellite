from click.testing import CliRunner

from canesat.cli import main


def test_help_lists_commands():
    out = CliRunner().invoke(main, ["--help"]).output
    for cmd in ("ingest", "detect", "plot", "db", "export", "preview", "alerts"):
        assert cmd in out


def test_ingest_requires_target(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid")
    r = CliRunner().invoke(main, ["ingest", "--start", "2025-01-01", "--end", "2025-02-01"])
    assert r.exit_code != 0 and "--plot-id" in r.output


def test_toml_config_overrides(tmp_path):
    from canesat.cli import Ctx

    p = tmp_path / "c.toml"
    p.write_text(
        "[anomaly]\ngap_threshold = -0.2\nharvest_months = [1, 2]\n"
        "[ingest]\nmin_clear_fraction = 0.7\n"
    )
    c = Ctx(str(p))
    assert c.anomaly_cfg().gap_threshold == -0.2
    assert c.anomaly_cfg().harvest_months == (1, 2)
    assert c.ingest_cfg().min_clear_fraction == 0.7


def test_example_threshold_file_is_valid():
    from pathlib import Path

    from canesat.cli import Ctx

    c = Ctx(str(Path(__file__).parents[1] / "config" / "thresholds.example.toml"))
    assert c.anomaly_cfg().cooldown_days == 14
    assert c.ingest_cfg().ring_outer_m == 2500.0
