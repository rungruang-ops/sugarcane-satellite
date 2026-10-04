"""Command-line interface: `canesat --help`."""

from __future__ import annotations

import csv
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

import click

from .config import AnomalyConfig, IngestConfig, Settings, ingest_config_from, load_toml_config


def _json_default(o: Any) -> Any:
    if isinstance(o, date):
        return o.isoformat()
    if hasattr(o, "__float__"):
        return float(o)
    return str(o)


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default)


class Ctx:
    def __init__(self, config_path: str | None):
        self.settings = Settings()
        self.toml = load_toml_config(config_path) if config_path else {}

    def conn(self):
        from . import db

        if not self.settings.database_url:
            raise click.UsageError("DATABASE_URL is not set (see .env.example)")
        return db.connect(self.settings.database_url)

    def ingest_cfg(self) -> IngestConfig:
        return ingest_config_from(self.toml.get("ingest"))

    def anomaly_cfg(self, conn=None) -> AnomalyConfig:
        from . import db

        cfg = AnomalyConfig()
        if conn is not None:
            cfg = cfg.merged(db.get_setting(conn, "anomaly") or {})
        return cfg.merged(self.toml.get("anomaly", {}))


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    help="TOML file with [ingest] / [anomaly] overrides.",
)
@click.option("-v", "--verbose", is_flag=True)
@click.pass_context
def main(ctx: click.Context, config_path: str | None, verbose: bool) -> None:
    """canesat — Sentinel-2 NDVI pipeline + neighbour anomaly alerts for sugarcane plots."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ctx.obj = Ctx(config_path)


# ------------------------------------------------------------------ db
@main.group("db")
def db_group() -> None:
    """Database commands."""


@db_group.command("migrate")
@click.pass_obj
def db_migrate(c: Ctx) -> None:
    """Apply pending SQL migrations."""
    from . import db

    with c.conn() as conn:
        applied = db.migrate(conn)
    click.echo(f"applied: {applied or 'nothing (up to date)'}")


# ------------------------------------------------------------------ plots
@main.group("plot")
def plot_group() -> None:
    """Register and list plots."""


@plot_group.command("add")
@click.argument("name")
@click.option(
    "--geojson",
    type=click.Path(exists=True, dir_okay=False),
    help="GeoJSON Polygon/MultiPolygon/Feature (EPSG:4326).",
)
@click.option(
    "--center",
    nargs=2,
    type=float,
    metavar="LAT LON",
    help="Square plot around a point instead of --geojson.",
)
@click.option("--size-m", type=float, default=1000.0, show_default=True)
@click.option("--tambon", "tambon_code")
@click.option("--cane-type", type=click.Choice(["plant", "ratoon", "unknown"]), default="unknown")
@click.option("--planting-date", type=click.DateTime(["%Y-%m-%d"]))
@click.option("--last-harvest-date", type=click.DateTime(["%Y-%m-%d"]))
@click.option("--ratoon-no", type=int)
@click.option("--owner-user-id", type=int)
@click.option("--group-id", type=int)
@click.pass_obj
def plot_add(
    c: Ctx,
    name,
    geojson,
    center,
    size_m,
    tambon_code,
    cane_type,
    planting_date,
    last_harvest_date,
    ratoon_no,
    owner_user_id,
    group_id,
) -> None:
    """Register a plot."""
    from shapely.geometry import mapping

    from . import db
    from .geometry import geom_from_geojson, square_around

    if bool(geojson) == bool(center):
        raise click.UsageError("give exactly one of --geojson or --center")
    geom = (
        geom_from_geojson(Path(geojson).read_text()) if geojson else square_around(*center, size_m)
    )
    with c.conn() as conn:
        pid = db.insert_plot(
            conn,
            name,
            mapping(geom),
            tambon_code=tambon_code,
            cane_type=cane_type,
            planting_date=planting_date.date() if planting_date else None,
            last_harvest_date=last_harvest_date.date() if last_harvest_date else None,
            ratoon_no=ratoon_no,
            owner_user_id=owner_user_id,
            group_id=group_id,
        )
    click.echo(pid)


@plot_group.command("list")
@click.pass_obj
def plot_list(c: Ctx) -> None:
    from . import db

    with c.conn() as conn:
        for r in db.list_plots(conn):
            click.echo(
                f"{r['id']}\t{r['name']}\t{r['area_rai']} rai\t"
                f"{r['lat']:.4f},{r['lon']:.4f}\t{r['cane_type']}\t{r['tambon_code'] or ''}"
            )


# ------------------------------------------------------------------ ingest
def _ingest_one(c: Ctx, conn, ingestor, row, start, end, workers, force, use_ring) -> dict:
    from . import db
    from .geometry import geom_from_geojson

    geom = geom_from_geojson(row["geojson"])
    skip = set() if force else db.ingested_item_ids(conn, row["id"])
    obs, meta = ingestor.ingest(geom, start, end, skip, workers=workers, use_ring=use_ring)
    db.upsert_observations(conn, row["id"], obs)
    meta.pop("errors", None) if not meta.get("n_errors") else None
    return {
        "plot_id": row["id"],
        "name": row["name"],
        "stored": len(obs),
        "clear": sum(o.is_clear for o in obs),
        **meta,
    }


@main.command("ingest")
@click.option("--plot-id", "plot_ids", type=int, multiple=True)
@click.option("--all", "all_plots", is_flag=True, help="Batch mode: every active plot.")
@click.option("--start", required=True, help="YYYY-MM-DD")
@click.option("--end", required=True, help="YYYY-MM-DD")
@click.option("--workers", type=int, default=8, show_default=True)
@click.option("--force", is_flag=True, help="Re-process scenes already stored.")
@click.option("--no-ring", is_flag=True, help="Skip the cane-pixel ring baseline.")
@click.pass_obj
def ingest_cmd(c: Ctx, plot_ids, all_plots, start, end, workers, force, no_ring) -> None:
    """Fetch Sentinel-2 L2A scenes and store per-plot NDVI statistics."""
    from . import db
    from .ingest import PlotIngestor

    if bool(plot_ids) == all_plots:
        raise click.UsageError("give --plot-id (one or more) or --all")
    ingestor = PlotIngestor(c.ingest_cfg(), cache_dir=c.settings.cache_dir)
    with c.conn() as conn:
        rows = db.list_plots(conn) if all_plots else [db.get_plot(conn, p) for p in plot_ids]
        if any(r is None for r in rows):
            raise click.UsageError("unknown plot id")
        out = []
        for row in rows:
            try:
                out.append(
                    _ingest_one(c, conn, ingestor, row, start, end, workers, force, not no_ring)
                )
            except Exception as e:  # keep batch going
                out.append({"plot_id": row["id"], "error": f"{type(e).__name__}: {e}"})
            click.echo(_dump(out[-1]))
    if any("error" in o for o in out):
        sys.exit(1)


# ------------------------------------------------------------------ detect
@main.command("detect")
@click.option("--plot-id", "plot_ids", type=int, multiple=True)
@click.option("--all", "all_plots", is_flag=True)
@click.pass_obj
def detect_cmd(c: Ctx, plot_ids, all_plots) -> None:
    """Evaluate stored observations and create alerts."""
    from .detect import run_detect

    if bool(plot_ids) == all_plots:
        raise click.UsageError("give --plot-id (one or more) or --all")
    with c.conn() as conn:
        cfg = c.anomaly_cfg(conn)
        summary = run_detect(conn, list(plot_ids) or None, cfg)
    click.echo(_dump(summary))


@main.command("alerts")
@click.option("--plot-id", type=int)
@click.pass_obj
def alerts_cmd(c: Ctx, plot_id) -> None:
    """List alerts."""
    from . import db

    with c.conn() as conn:
        click.echo(_dump(db.list_alerts(conn, plot_id)))


# ------------------------------------------------------------------ export
EXPORT_COLS = [
    "obs_date",
    "item_id",
    "mgrs_tile",
    "scene_cloud",
    "median_ndvi",
    "p10_ndvi",
    "p90_ndvi",
    "n_px",
    "n_clear_px",
    "clear_fraction",
    "is_clear",
    "flags",
    "ring_median",
    "ring_mad",
    "ring_n_px",
    "nb_source",
    "nb_median",
    "gap_nb",
    "z_nb",
    "hist_mean",
    "hist_n_years",
    "z_hist",
    "status",
]


@main.command("export")
@click.option("--plot-id", type=int, required=True)
@click.option(
    "--out",
    type=click.Path(dir_okay=False),
    required=True,
    help="*.csv (observations) or *.json (plot + observations + alerts)",
)
@click.option("--clear-only", is_flag=True)
@click.pass_obj
def export_cmd(c: Ctx, plot_id, out, clear_only) -> None:
    """Export a plot's time series (and alerts) to CSV or JSON."""
    from . import db

    with c.conn() as conn:
        plot = db.get_plot(conn, plot_id)
        rows = db.fetch_observations(conn, [plot_id], clear_only=clear_only)
        alerts = db.list_alerts(conn, plot_id)
    recs = [{k: r.get(k) for k in EXPORT_COLS} for r in rows]
    for r in recs:
        r["flags"] = ";".join(r["flags"] or [])
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".csv":
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=EXPORT_COLS)
            w.writeheader()
            w.writerows(recs)
    else:
        plot = {k: v for k, v in plot.items() if k != "geojson"} | {
            "geometry": json.loads(plot["geojson"])
        }
        path.write_text(
            _dump({"plot": plot, "observations": recs, "alerts": alerts}) + "\n", encoding="utf-8"
        )
    click.echo(f"wrote {len(recs)} observations, {len(alerts)} alerts -> {path}")


# ------------------------------------------------------------------ preview (no DB)
@main.command("preview")
@click.option("--center", nargs=2, type=float, metavar="LAT LON", required=True)
@click.option("--size-m", type=float, default=1000.0, show_default=True)
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--workers", type=int, default=8, show_default=True)
@click.option("--out", type=click.Path(dir_okay=False))
@click.pass_obj
def preview_cmd(c: Ctx, center, size_m, start, end, workers, out) -> None:
    """Ingest + evaluate a square box without a database (ring baseline + prior years)."""
    from .baseline import PlotInfo
    from .detect import evaluate_plots
    from .geometry import square_around
    from .ingest import PlotIngestor

    geom = square_around(*center, size_m)
    obs, meta = PlotIngestor(c.ingest_cfg(), cache_dir=c.settings.cache_dir).ingest(
        geom, start, end, workers=workers
    )
    rows = [
        o.to_dict() | {"obs_date": o.obs_date, "scene_id": i}
        for i, o in enumerate(obs)
        if o.is_clear
    ]
    cfg = c.anomaly_cfg()
    res = evaluate_plots({0: PlotInfo(0)}, {0: rows}, {}, cfg)[0]
    status = {ev.point.ref: ev for ev in res.evaluations}
    series = []
    for i, o in enumerate(obs):
        d = o.to_dict()
        ev = status.get(i) if o.is_clear else None
        d.update(
            gap_nb=ev and ev.gap_nb,
            z_nb=ev and ev.z_nb,
            z_hist=ev and ev.z_hist,
            status=ev.status if ev else "not_clear",
        )
        series.append(d)
    result = {
        "center": center,
        "size_m": size_m,
        "start": start,
        "end": end,
        "meta": meta,
        "observations": series,
        "alerts": [a.__dict__ | {"ref": None} for a in res.alerts],
    }
    text = _dump(result)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
    click.echo(text if not out else f"wrote {out}")


if __name__ == "__main__":
    main()
