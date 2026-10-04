"""NDVI ingestion for one plot: STAC search -> per-scene plot + ring statistics.

The approach is the one proven in the Khon Kaen exploratory script (khonkaen-ndvi/fetch.py):
windowed COG reads of B04/B08/SCL from Planetary Computer, BOA offset removal, SCL classes
4/5 kept, and a 'likely cane' mask (WorldCover 2021 cropland AND NDVI > 0.6 in early Dec).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
from shapely.geometry.base import BaseGeometry

from .config import IngestConfig
from .geometry import PlotGrid, build_plot_grid, utm_epsg_for
from .ndvi import boa_offset, clear_mask, compute_ndvi, likely_cane_mask, zonal_stats
from .stac import SceneRef, StacClient, grid_footprint_lonlat, read_reprojected, read_window
from .stac import select_scenes as _select_scenes

log = logging.getLogger(__name__)


@dataclass
class Observation:
    """One plot x one scene. Mirrors the ndvi_observations table (ingest columns)."""

    item_id: str
    obs_date: date
    acquired_at: str
    mgrs_tile: str | None
    cloud_cover: float | None
    processing_baseline: str | None
    median_ndvi: float | None
    p10_ndvi: float | None
    p90_ndvi: float | None
    n_px: int
    n_clear_px: int
    clear_fraction: float
    is_clear: bool
    flags: list[str] = field(default_factory=list)
    ring_median: float | None = None
    ring_mad: float | None = None
    ring_n_px: int | None = None
    ring_clear_fraction: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["obs_date"] = self.obs_date.isoformat()
        return d


@dataclass
class CaneMask:
    mask: np.ndarray
    ref_item_id: str
    n_cane_ring_px: int
    n_ring_px: int


def compute_observation(
    scene: SceneRef,
    pg: PlotGrid,
    cane: np.ndarray | None,
    red: np.ndarray,
    nir: np.ndarray,
    scl: np.ndarray,
    cfg: IngestConfig,
) -> Observation:
    """Pure part of per-scene processing (no I/O) — unit tested with synthetic arrays."""
    ndvi = compute_ndvi(red, nir, boa_offset(scene.processing_baseline))
    clear = clear_mask(scl, cfg.clear_classes, cfg.cloud_classes, cfg.cloud_dilate_px)
    st = zonal_stats(ndvi, clear, pg.plot_mask, cfg.min_clear_fraction, cfg.min_clear_px)
    flags = list(pg.flags)
    if not st.is_clear:
        flags.append("low_clear_fraction" if st.n_clear_px else "no_clear_pixels")
    obs = Observation(
        item_id=scene.item_id,
        obs_date=scene.obs_date,
        acquired_at=scene.acquired_at.isoformat(),
        mgrs_tile=scene.mgrs_tile,
        cloud_cover=scene.cloud_cover,
        processing_baseline=scene.processing_baseline,
        median_ndvi=st.median,
        p10_ndvi=st.p10,
        p90_ndvi=st.p90,
        n_px=st.n_px,
        n_clear_px=st.n_clear_px,
        clear_fraction=round(st.clear_fraction, 4),
        is_clear=st.is_clear,
        flags=flags,
    )
    if cane is not None:
        ring_zone = pg.ring_mask & cane
        rs = zonal_stats(ndvi, clear, ring_zone, 0.0, cfg.ring_min_clear_px)
        obs.ring_n_px = rs.n_clear_px
        obs.ring_clear_fraction = round(rs.clear_fraction, 4)
        if rs.n_clear_px >= cfg.ring_min_clear_px:
            obs.ring_median, obs.ring_mad = rs.median, rs.mad
        else:
            obs.flags.append("ring_too_few_pixels")
    return obs


def _round(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(float(x), nd)


class PlotIngestor:
    """Runs ingestion for a plot geometry. I/O is injectable for tests."""

    def __init__(
        self,
        cfg: IngestConfig | None = None,
        stac: StacClient | None = None,
        cache_dir: Path | None = None,
        reader: Callable[[str, object], np.ndarray] = read_window,
        reprojector: Callable[[str, object], np.ndarray] = read_reprojected,
    ):
        self.cfg = cfg or IngestConfig()
        self._stac = stac
        self.cache_dir = cache_dir
        self.reader = reader
        self.reprojector = reprojector

    @property
    def stac(self) -> StacClient:
        if self._stac is None:
            self._stac = StacClient(self.cfg.stac_url)
        return self._stac

    def plot_grid(self, geom: BaseGeometry, epsg: int) -> PlotGrid:
        c = self.cfg
        return build_plot_grid(
            geom, epsg, c.inner_buffer_m, c.min_clear_px, c.ring_gap_m, c.ring_outer_m
        )

    # ------------------------------------------------------------- cane mask
    def _cache_path(self, geom: BaseGeometry, epsg: int) -> Path | None:
        if not self.cache_dir:
            return None
        c = self.cfg
        key = hashlib.sha1(
            geom.wkb
            + f"{epsg}|{c.cane_ref_window}|{c.cane_ndvi_threshold}|{c.ring_outer_m}"
            f"|{c.ring_gap_m}|{c.worldcover_year}".encode()
        ).hexdigest()[:16]
        return self.cache_dir / f"cane_mask_{key}.npz"

    def cane_mask(self, geom: BaseGeometry, pg: PlotGrid) -> CaneMask:
        path = self._cache_path(geom, pg.grid.epsg)
        if path and path.exists():
            z = np.load(path)
            return CaneMask(z["mask"], str(z["ref_item_id"]), int(z["n_cane"]), int(z["n_ring"]))
        c = self.cfg
        start, end = c.cane_ref_window.split("/")
        window_ll = grid_footprint_lonlat(pg.grid)
        refs = [
            s for s in self.stac.sentinel2(geom, start, end, max_cloud=60) if s.epsg == pg.grid.epsg
        ]
        refs = sorted(_select_scenes(refs, window_ll, pg.grid.epsg), key=lambda s: s.cloud_cover)
        if not refs:
            raise LookupError(f"no reference scene for cane mask in {c.cane_ref_window}")
        best: tuple[float, SceneRef, np.ndarray, np.ndarray] | None = None
        for s in refs[:4]:
            scl = self.reader(s.hrefs["SCL"], pg.grid)
            clr = clear_mask(scl, c.clear_classes, c.cloud_classes, c.cloud_dilate_px)
            frac = float(clr[pg.ring_mask].mean())
            if best is None or frac > best[0]:
                best = (frac, s, scl, clr)
            if frac >= 0.95:
                break
        assert best is not None
        frac, ref, _, clr = best
        red = self.reader(ref.hrefs["B04"], pg.grid)
        nir = self.reader(ref.hrefs["B08"], pg.grid)
        ndvi = compute_ndvi(red, nir, boa_offset(ref.processing_baseline))
        wc = self.reprojector(self.stac.worldcover_href(geom, c.worldcover_year), pg.grid)
        mask = likely_cane_mask(wc, ndvi, clr, c.cane_ndvi_threshold)
        cm = CaneMask(mask, ref.item_id, int((mask & pg.ring_mask).sum()), int(pg.ring_mask.sum()))
        log.info(
            "cane mask from %s: ring clear %.2f, likely-cane %d / %d ring px",
            ref.item_id,
            frac,
            cm.n_cane_ring_px,
            cm.n_ring_px,
        )
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                path,
                mask=mask,
                ref_item_id=ref.item_id,
                n_cane=cm.n_cane_ring_px,
                n_ring=cm.n_ring_px,
            )
        return cm

    # ------------------------------------------------------------- scenes
    def find_scenes(self, geom: BaseGeometry, start: date | str, end: date | str) -> list[SceneRef]:
        c = geom.centroid
        epsg = utm_epsg_for(c.x, c.y)
        pg = self.plot_grid(geom, epsg)
        scenes = self.stac.sentinel2(geom, start, end, self.cfg.max_scene_cloud)
        return _select_scenes(scenes, grid_footprint_lonlat(pg.grid), epsg)

    def ingest(
        self,
        geom: BaseGeometry,
        start: date | str,
        end: date | str,
        skip_item_ids: Iterable[str] = (),
        workers: int = 8,
        use_ring: bool = True,
    ) -> tuple[list[Observation], dict]:
        """Return observations (one per acquisition date) plus run metadata."""
        scenes = self.find_scenes(geom, start, end)
        skip = set(skip_item_ids)
        todo = [s for s in scenes if s.item_id not in skip and s.epsg]
        grids: dict[int, PlotGrid] = {}
        canes: dict[int, CaneMask | None] = {}

        def prep(epsg: int) -> tuple[PlotGrid, CaneMask | None]:
            if epsg not in grids:
                grids[epsg] = self.plot_grid(geom, epsg)
                canes[epsg] = self.cane_mask(geom, grids[epsg]) if use_ring else None
            return grids[epsg], canes[epsg]

        for epsg in sorted({s.epsg for s in todo if s.epsg}):
            prep(epsg)

        def work(s: SceneRef) -> Observation | dict:
            pg, cm = grids[s.epsg], canes[s.epsg]
            try:
                red = self.reader(s.hrefs["B04"], pg.grid)
                nir = self.reader(s.hrefs["B08"], pg.grid)
                scl = self.reader(s.hrefs["SCL"], pg.grid)
            except Exception as e:  # network hiccup: report, don't abort the batch
                return {"item_id": s.item_id, "error": f"{type(e).__name__}: {e}"[:300]}
            return compute_observation(s, pg, cm.mask if cm else None, red, nir, scl, self.cfg)

        with ThreadPoolExecutor(max(1, workers)) as ex:
            results = list(ex.map(work, todo))
        obs = [r for r in results if isinstance(r, Observation)]
        errors = [r for r in results if isinstance(r, dict)]
        for o in obs:
            for k in ("median_ndvi", "p10_ndvi", "p90_ndvi", "ring_median", "ring_mad"):
                setattr(o, k, _round(getattr(o, k)))
        meta = {
            "n_scenes_found": len(scenes),
            "n_scenes_processed": len(todo),
            "n_skipped_existing": len(scenes) - len(todo),
            "n_errors": len(errors),
            "errors": errors,
            "cane_masks": {
                str(e): (
                    {
                        "ref_item_id": cm.ref_item_id,
                        "likely_cane_ring_px": cm.n_cane_ring_px,
                        "ring_px": cm.n_ring_px,
                    }
                    if cm
                    else None
                )
                for e, cm in canes.items()
            },
            "plot_px": {str(e): int(pg.plot_mask.sum()) for e, pg in grids.items()},
        }
        return sorted(obs, key=lambda o: o.obs_date), meta
