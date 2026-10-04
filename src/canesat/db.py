"""PostgreSQL/PostGIS access (psycopg 3) and a tiny plain-SQL migration runner."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date
from importlib import resources
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .baseline import PlotInfo
from .ingest import Observation


def connect(url: str, **kwargs: Any) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row, **kwargs)


# ------------------------------------------------------------------ migrations
def migration_files() -> list[tuple[str, str]]:
    pkg = resources.files("canesat") / "migrations"
    files = sorted(p for p in pkg.iterdir() if p.name.endswith(".sql"))
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations in filename order. Returns names applied."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for name, sql in migration_files():
        if name in done:
            continue
        with conn.transaction():
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
        applied.append(name)
    return applied


# ------------------------------------------------------------------ plots
def insert_plot(
    conn: psycopg.Connection,
    name: str,
    geom_geojson: dict[str, Any],
    *,
    owner_user_id: int | None = None,
    group_id: int | None = None,
    tambon_code: str | None = None,
    cane_type: str = "unknown",
    planting_date: date | None = None,
    ratoon_no: int | None = None,
    last_harvest_date: date | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO plots (name, geom, area_rai, owner_user_id, group_id, tambon_code,
                           cane_type, planting_date, ratoon_no, last_harvest_date)
        SELECT %(name)s, g, round((ST_Area(g::geography) / 1600.0)::numeric, 2),
               %(owner)s, %(group)s, %(tambon)s, %(cane_type)s, %(planting)s, %(ratoon)s,
               %(harvest)s
        FROM (SELECT ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%(geom)s), 4326)) AS g) s
        RETURNING id
        """,
        {
            "name": name,
            "geom": json.dumps(geom_geojson),
            "owner": owner_user_id,
            "group": group_id,
            "tambon": tambon_code,
            "cane_type": cane_type,
            "planting": planting_date,
            "ratoon": ratoon_no,
            "harvest": last_harvest_date,
        },
    ).fetchone()
    conn.commit()
    return int(row["id"])


_PLOT_COLS = """id, name, tambon_code, cane_type, planting_date, ratoon_no, last_harvest_date,
    area_rai, ST_AsGeoJSON(geom) AS geojson, ST_Y(ST_Centroid(geom)) AS lat,
    ST_X(ST_Centroid(geom)) AS lon"""


def get_plot(conn: psycopg.Connection, plot_id: int) -> dict[str, Any] | None:
    return conn.execute(f"SELECT {_PLOT_COLS} FROM plots WHERE id = %s", (plot_id,)).fetchone()


def list_plots(conn: psycopg.Connection, include_archived: bool = False) -> list[dict[str, Any]]:
    where = "" if include_archived else "WHERE archived_at IS NULL"
    return list(conn.execute(f"SELECT {_PLOT_COLS} FROM plots {where} ORDER BY id"))


def plot_info(row: dict[str, Any]) -> PlotInfo:
    return PlotInfo(
        plot_id=int(row["id"]),
        cane_type=row.get("cane_type") or "unknown",
        planting_date=row.get("planting_date"),
        last_harvest_date=row.get("last_harvest_date"),
        tambon_code=row.get("tambon_code"),
    )


def plot_distances(
    conn: psycopg.Connection, plot_id: int, radius_m: float
) -> list[tuple[int, float]]:
    """Other active plots within ``radius_m`` (geodesic, edge-to-edge)."""
    rows = conn.execute(
        """
        SELECT o.id, ST_Distance(p.geom::geography, o.geom::geography) AS dist
        FROM plots p JOIN plots o ON o.id <> p.id AND o.archived_at IS NULL
        WHERE p.id = %s AND ST_DWithin(p.geom::geography, o.geom::geography, %s)
        """,
        (plot_id, radius_m),
    )
    return [(int(r["id"]), float(r["dist"])) for r in rows]


# ------------------------------------------------------------------ scenes / observations
def upsert_scene(conn: psycopg.Connection, o: Observation) -> int:
    row = conn.execute(
        """
        INSERT INTO scenes (item_id, acquired_at, mgrs_tile, cloud_cover, processing_baseline)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (item_id) DO UPDATE SET processed_at = now()
        RETURNING id
        """,
        (o.item_id, o.acquired_at, o.mgrs_tile, o.cloud_cover, o.processing_baseline),
    ).fetchone()
    return int(row["id"])


def ingested_item_ids(conn: psycopg.Connection, plot_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT s.item_id FROM ndvi_observations o JOIN scenes s ON s.id = o.scene_id"
        " WHERE o.plot_id = %s",
        (plot_id,),
    )
    return {r["item_id"] for r in rows}


_OBS_COLS = (
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
    "ring_clear_fraction",
)


def upsert_observations(conn: psycopg.Connection, plot_id: int, obs: Iterable[Observation]) -> int:
    n = 0
    with conn.transaction():
        for o in obs:
            sid = upsert_scene(conn, o)
            cols = ", ".join(_OBS_COLS)
            ph = ", ".join(["%s"] * len(_OBS_COLS))
            upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in _OBS_COLS)
            conn.execute(
                f"INSERT INTO ndvi_observations (plot_id, scene_id, obs_date, {cols})"
                f" VALUES (%s, %s, %s, {ph})"
                f" ON CONFLICT (plot_id, scene_id) DO UPDATE SET {upd}",
                (plot_id, sid, o.obs_date, *[getattr(o, c) for c in _OBS_COLS]),
            )
            n += 1
    conn.commit()  # persist per plot so a long batch keeps finished plots
    return n


def fetch_observations(
    conn: psycopg.Connection, plot_ids: Sequence[int] | None = None, clear_only: bool = False
) -> list[dict[str, Any]]:
    where, params = [], []
    if plot_ids is not None:
        where.append("o.plot_id = ANY(%s)")
        params.append(list(plot_ids))
    if clear_only:
        where.append("o.is_clear")
    sql = (
        "SELECT o.*, s.item_id, s.cloud_cover AS scene_cloud, s.mgrs_tile FROM ndvi_observations o"
        " JOIN scenes s ON s.id = o.scene_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY o.plot_id, o.obs_date"
    )
    return list(conn.execute(sql, params))


def update_evaluation(
    conn: psycopg.Connection, plot_id: int, scene_id: int, ev: dict[str, Any]
) -> None:
    conn.execute(
        """
        UPDATE ndvi_observations SET nb_source = %(nb_source)s, nb_median = %(nb_median)s,
            nb_mad = %(nb_mad)s, nb_n = %(nb_n)s, gap_nb = %(gap_nb)s, z_nb = %(z_nb)s,
            hist_mean = %(hist_mean)s, hist_n_years = %(hist_n_years)s, z_hist = %(z_hist)s,
            status = %(status)s, evaluated_at = now()
        WHERE plot_id = %(plot_id)s AND scene_id = %(scene_id)s
        """,
        {**ev, "plot_id": plot_id, "scene_id": scene_id},
    )


# ------------------------------------------------------------------ alerts / settings
def previous_alerts(conn: psycopg.Connection, plot_id: int) -> list[tuple[str, date]]:
    rows = conn.execute(
        "SELECT type, obs_date FROM alerts WHERE plot_id = %s AND status <> 'suppressed'",
        (plot_id,),
    )
    return [(r["type"], r["obs_date"]) for r in rows]


def insert_alert(
    conn: psycopg.Connection,
    plot_id: int,
    type_: str,
    severity: str,
    obs_date: date,
    z_score: float | None,
    gap: float | None,
    details: dict[str, Any],
    status: str = "pending",
) -> int | None:
    row = conn.execute(
        """
        INSERT INTO alerts (plot_id, type, severity, status, obs_date, z_score, gap, details)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (plot_id, type, obs_date) WHERE plot_id IS NOT NULL DO NOTHING
        RETURNING id
        """,
        (plot_id, type_, severity, status, obs_date, z_score, gap, Jsonb(details)),
    ).fetchone()
    return int(row["id"]) if row else None


def list_alerts(conn: psycopg.Connection, plot_id: int | None = None) -> list[dict[str, Any]]:
    if plot_id is None:
        return list(conn.execute("SELECT * FROM alerts ORDER BY obs_date, id"))
    return list(
        conn.execute("SELECT * FROM alerts WHERE plot_id = %s ORDER BY obs_date, id", (plot_id,))
    )


def get_setting(conn: psycopg.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: psycopg.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (%s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        (key, Jsonb(value)),
    )
    conn.commit()
