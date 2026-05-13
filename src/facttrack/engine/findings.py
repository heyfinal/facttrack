"""Curative finding objects + emitter + DB persistence."""
from __future__ import annotations

import decimal
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from facttrack.db import cursor

log = logging.getLogger(__name__)


class _JSONFallback(json.JSONEncoder):
    """Encode types psycopg2 returns that stdlib JSON cannot handle."""

    def default(self, o: Any) -> Any:
        if isinstance(o, decimal.Decimal):
            return float(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


def _jsonify(value: Any) -> str:
    return json.dumps(value, cls=_JSONFallback)


@dataclass
class Finding:
    rule_id: str
    severity: str  # critical | high | medium | low
    confidence_score: float
    title: str
    description: str
    suggested_action: str
    tract_id: int | None = None
    lease_id: int | None = None
    assignee_level: str | None = None  # junior_landman | senior_landman | attorney_referral | operator_action
    dollar_impact_low: float | None = None
    dollar_impact_high: float | None = None
    deadline: date | None = None
    related_events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def severity_rank(s: str) -> int:
    return _SEVERITY_RANK.get(s, 0)


@dataclass
class FindingEmitter:
    project_id: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, f: Finding) -> None:
        self.findings.append(f)


def persist_findings(project_id: str, findings: list[Finding]) -> int:
    """Replace prior open findings for this project + persist the new set."""
    if not findings:
        log.info("no findings to persist for project %s", project_id)
        # Still clear prior open items so reruns don't keep stale data
        with cursor(dict_rows=False) as cur:
            cur.execute(
                "DELETE FROM curative_item WHERE project_id = %s AND status IN ('open', 'in_progress', 'awaiting_doc')",
                (project_id,),
            )
        return 0

    with cursor(dict_rows=False) as cur:
        cur.execute(
            "DELETE FROM curative_item WHERE project_id = %s AND status IN ('open', 'in_progress', 'awaiting_doc')",
            (project_id,),
        )
        n = 0
        for f in findings:
            cur.execute(
                """
                INSERT INTO curative_item (
                    project_id, tract_id, lease_id, rule_id, severity, confidence_score,
                    dollar_impact_low, dollar_impact_high, title, description,
                    suggested_action, assignee_level, status, deadline,
                    related_events, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s::jsonb, %s::jsonb)
                """,
                (
                    project_id, f.tract_id, f.lease_id, f.rule_id, f.severity, f.confidence_score,
                    f.dollar_impact_low, f.dollar_impact_high, f.title, f.description,
                    f.suggested_action, f.assignee_level, f.deadline,
                    _jsonify(f.related_events), _jsonify(f.metadata),
                ),
            )
            n += 1
    log.info("persisted %d findings for project %s", n, project_id)
    return n


def rank_findings(findings: list[Finding]) -> list[Finding]:
    """Order by severity (desc) then confidence (desc) then rule_id (stable)."""
    return sorted(
        findings,
        key=lambda f: (severity_rank(f.severity), f.confidence_score),
        reverse=True,
    )
