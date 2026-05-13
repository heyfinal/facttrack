"""Folium interactive tract map renderer."""
from __future__ import annotations

import logging
from pathlib import Path

import folium
from folium.plugins import MarkerCluster

from facttrack.config import PATHS, ensure_dirs
from facttrack.engine.context import load_project

log = logging.getLogger(__name__)


_STATUS_COLOR = {
    "ACTIVE":  "green",
    "INACTIVE": "orange",
    "PLUGGED": "red",
    "SHUT_IN": "blue",
}


def render_map(project_id: str) -> Path:
    ensure_dirs()
    ctx = load_project(project_id)

    # Centroid: average over tracts in project, fallback to East-TX center
    pts = [(t.centroid_lat, t.centroid_lon) for t in ctx.tracts if t.centroid_lat and t.centroid_lon]
    if pts:
        ctr_lat = sum(p[0] for p in pts) / len(pts)
        ctr_lon = sum(p[1] for p in pts) / len(pts)
    else:
        ctr_lat, ctr_lon = 31.55, -95.55  # East TX

    m = folium.Map(
        location=[ctr_lat, ctr_lon],
        zoom_start=12,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    # Tract markers
    for t in ctx.tracts:
        if t.centroid_lat and t.centroid_lon:
            popup_html = (
                f"<b>{t.label}</b><br>"
                f"County FIPS: {t.county_fips}<br>"
                f"Abstract: {t.abstract_no or '—'}<br>"
                f"Survey: {t.survey_name or '—'}<br>"
                f"Acres: {t.gross_acres or '—'}"
            )
            folium.Marker(
                location=[t.centroid_lat, t.centroid_lon],
                tooltip=t.label,
                popup=folium.Popup(popup_html, max_width=300),
                icon=folium.Icon(color="darkblue", icon="map-pin", prefix="fa"),
            ).add_to(m)

    # Well markers
    if ctx.wells:
        well_cluster = MarkerCluster(name="Wells").add_to(m)
        for w in ctx.wells:
            if not (w.surface_lat and w.surface_lon):
                continue
            op_name = ctx.operators_by_p5.get(w.operator_p5).name if (w.operator_p5 and w.operator_p5 in ctx.operators_by_p5) else "?"
            color = _STATUS_COLOR.get((w.status or "").upper(), "gray")
            popup_html = (
                f"<b>{w.lease_name} #{w.well_no}</b><br>"
                f"API: {w.api_no}<br>"
                f"Operator: {op_name}<br>"
                f"Status: {w.status}<br>"
                f"Spud: {w.spud_date}<br>"
                f"Completion: {w.completion_date}"
            )
            folium.CircleMarker(
                location=[w.surface_lat, w.surface_lon],
                radius=8,
                color=color,
                fill=True,
                fill_opacity=0.7,
                popup=folium.Popup(popup_html, max_width=300),
            ).add_to(well_cluster)

    folium.LayerControl(collapsed=False).add_to(m)

    out_dir = PATHS.reports / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "map.html"
    m.save(str(html_path))
    log.info("rendered map → %s", html_path)
    return html_path


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = render_map(args.project)
    print(f"Map: {path}")
