"""Shared Feeder /api/requests aggregation — the single source of truth used by both
scripts/feeder_enrich.py (post-run enrichment) and ringer.py's live /live-model route.

aggregate_rows() reproduces feeder_enrich's historical aggregation exactly (guarded by
scripts/checks/phase4_enrich_check.sh). latest_served() picks the model that ACTUALLY
served a session — the last success row, since a session can carry failover rows and
rows[-1] may be a later error. fetch_session_rows() reads Feeder (or a fixture) and
tolerates both response shapes. Stdlib only; never raises on network/parse errors.
"""
from __future__ import annotations

import json
import statistics
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FEEDER_BASE = "http://localhost:3001"


def aggregate_rows(rows: list[dict]) -> dict:
    """Summarise a session's request rows. Byte-compatible with feeder_enrich's block
    (minus the 'sessions' list, which the caller adds). Success == status == 'success'."""
    all_rows = list(rows)
    all_rows_sorted = sorted(all_rows, key=lambda r: r.get("created_at", ""))
    success_rows = [r for r in all_rows_sorted if r.get("status") == "success"]

    served_by_key: dict[tuple, dict] = {}
    served_order: list[tuple] = []
    for row in success_rows:
        key = (row.get("platform"), row.get("model_id"))
        if key not in served_by_key:
            served_by_key[key] = {"calls": 0, "output_tokens": 0}
            served_order.append(key)
        served_by_key[key]["calls"] += 1
        served_by_key[key]["output_tokens"] += row.get("output_tokens", 0)

    served = [
        {"platform": k[0], "model_id": k[1],
         "calls": served_by_key[k]["calls"], "output_tokens": served_by_key[k]["output_tokens"]}
        for k in served_order
    ]

    failovers = 0
    prev_key = None
    for row in success_rows:
        key = (row.get("platform"), row.get("model_id"))
        if prev_key is not None and key != prev_key:
            failovers += 1
        prev_key = key

    errors_429 = sum(1 for r in all_rows if str(r.get("status")) == "429")
    latency_values = [r.get("latency_ms", 0) for r in all_rows if "latency_ms" in r]
    latency_ms_total = sum(latency_values)
    latency_ms_p50 = statistics.median(latency_values) if latency_values else 0

    return {
        "served": served,
        "failovers": failovers,
        "mixed_models": len(served) > 1,
        "requests": len(all_rows),
        "errors_429": errors_429,
        "latency_ms_total": latency_ms_total,
        "latency_ms_p50": latency_ms_p50,
    }


def latest_served(rows: list[dict]) -> dict | None:
    """The row whose model actually served this session = the LAST success row.
    None when there are no success rows yet (still churning / pending)."""
    last = None
    for row in rows:
        if row.get("status") == "success":
            last = row
    return last


def fetch_session_rows(session_id: str, feeder_base: str = FEEDER_BASE,
                       timeout: int = 5, fixture: str | None = None) -> list[dict]:
    """Return the request rows for a session. Reads `fixture` (a JSON file) if given,
    else GETs {feeder_base}/api/requests?session_id=. Accepts a bare array OR
    {"requests":[...]}. Returns [] on any error, empty body, or 200-with-[] — never raises.
    Localhost is trusted tokenless, so no Authorization header is needed."""
    try:
        if fixture is not None:
            with open(fixture, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            url = f"{feeder_base}/api/requests?session_id={session_id}"
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, json.JSONDecodeError, ValueError):
        return []
    if isinstance(data, dict):
        rows = data.get("requests", [])
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    return rows if isinstance(rows, list) else []
