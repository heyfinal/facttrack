"""Orchestrator: render map + Excel + PDF for one project, in order."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .excel import render_xlsx
from .map import render_map
from .pdf import RenderResult, render_pdf
from .typst_pdf import render_typst_pdf


def build(project_id: str) -> dict[str, Path]:
    map_path = render_map(project_id)
    xlsx_path = render_xlsx(project_id)
    pdf_result: RenderResult = render_pdf(project_id, map_html_path=map_path)
    try:
        typst_result = render_typst_pdf(project_id)
        pdf_path = typst_result.pdf_path
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "Typst PDF render failed (%s); keeping WeasyPrint fallback", e
        )
        pdf_path = pdf_result.pdf_path

    # Per-tract deal memos — the boss-facing 1-pagers that synthesize the
    # dossier into BUY / NEGOTIATE / WALK. Failures are non-fatal; the main
    # report is the primary artifact.
    dealmemo_paths: list[Path] = []
    try:
        from facttrack.engine.dealmemo import render_dealmemos_for_all_tracts
        dealmemo_paths = render_dealmemos_for_all_tracts(project_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("deal-memo render skipped: %s", e)

    return {
        "map": map_path,
        "xlsx": xlsx_path,
        "html": pdf_result.html_path,
        "pdf": pdf_path,
        "dealmemos": dealmemo_paths,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the full FactTrack report artifact set")
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = build(args.project)
    print("Artifacts:")
    for k, v in out.items():
        print(f"  {k:<5}: {v}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
