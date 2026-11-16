"""导出服务：报告 CSV 与运行清单 JSON。

报告、清单与页面共用 store 里的同一份匹配组与取舍轨迹，不另起口径。
"""
from __future__ import annotations

from ...errors import DomainError
from ..m6_diff import differences
from . import csv_out, manifest_out


def _reconciliation(store, run: dict, settings) -> dict:
    return differences.reconcile(store.records(run["run_id"]), store.matches(run["run_id"]), settings)


def _groups(store, run_id: str) -> list[dict]:
    confirmed = {item["match_id"] for item in store.confirmations(run_id)}
    groups = store.matches(run_id)
    for group in groups:
        group["confirmed"] = group["match_id"] in confirmed
    return groups


def report(store, run: dict | None, settings) -> str:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 404)
    result = _reconciliation(store, run, settings)
    return csv_out.report_text(
        store.records(run["run_id"]),
        _groups(store, run["run_id"]),
        result,
        store.match_decisions(run["run_id"]),
    )


def manifest(store, run: dict | None, settings) -> str:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 404)
    result = _reconciliation(store, run, settings)
    result["counts"]["confirmed_groups"] = len(store.confirmations(run["run_id"]))
    result["counts"]["dropped_candidates"] = sum(
        1 for item in store.match_decisions(run["run_id"]) if item["decision"] == "dropped"
    )
    return manifest_out.manifest_text(run, result)


def summary(store, run: dict | None, settings) -> dict:
    if not run:
        return {"run_id": None, "counts": {}, "totals": {}}
    result = _reconciliation(store, run, settings)
    counts = dict(result["counts"])
    counts["confirmed_groups"] = len(store.confirmations(run["run_id"]))
    counts["dropped_candidates"] = sum(
        1 for item in store.match_decisions(run["run_id"]) if item["decision"] == "dropped"
    )
    return {
        "run_id": run["run_id"],
        "counts": counts,
        "totals": {key: format(value, "f") for key, value in result["totals"].items()},
    }
