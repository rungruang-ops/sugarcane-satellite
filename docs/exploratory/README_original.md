# Khon Kaen sugarcane NDVI (Sentinel-2 L2A, Oct 2023 – Oct 2026)
- khonkaen_sugarcane_ndvi.png: chart (Thai labels, Sarabun font)
- khonkaen_sugarcane_ndvi_sentinel2.csv: per-date mean NDVI for each 1x1 km box (probable-cane pixels / other cropland / all cropland)
- modis_mod13q1_crosscheck.csv: MODIS MOD13Q1 16-day NDVI (ORNL DAAC), central 5x5 px (~1.25 km), reliability 0-1 only
- raw_scenes.csv: every Sentinel-2 scene read, including cloudy/rejected ones
- areas.json: box coordinates, WorldCover class %, mask fractions
- scan.py (candidate selection), fetch.py (S2 extraction), modis.py, make_chart.py
Method: Planetary Computer sentinel-2-l2a, B04/B08 10 m, BOA offset -1000 (baseline>=4), SCL classes 4/5 kept, dates with >=60% valid pixels.
"Probable cane" = ESA WorldCover 2021 cropland AND NDVI>0.6 on 2025-12-01 (after rice harvest, before cane crushing). Not a verified cane map.
