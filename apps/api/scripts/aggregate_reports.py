"""Combine junit + evaluation reports into one machine-readable summary with explicit gates.

Usage: python -m scripts.aggregate_reports --junit reports/junit.xml --agent reports/agent.json
           --rag reports/rag.json [--e2e reports/e2e.json] --out reports/summary.json

Exit code 0 only when every gate passes. A report that is missing is reported as `missing` and
fails its gate: absence of evidence is never treated as success."""
import argparse
import json
import sys
import xml.etree.ElementTree as ET  # noqa: S405 - parses our own CI output
from pathlib import Path


def summarize_junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return {"tests": total, "failed": failed, "skipped": skipped, "passed": total - failed - skipped}


def summarize_playwright(path: Path) -> dict:
    data = json.loads(path.read_text())
    stats = data.get("stats", {})
    return {"tests": stats.get("expected", 0) + stats.get("unexpected", 0) + stats.get("flaky", 0),
            "passed": stats.get("expected", 0), "failed": stats.get("unexpected", 0),
            "flaky": stats.get("flaky", 0), "skipped": stats.get("skipped", 0),
            "started_at": stats.get("startTime")}  # lets a reader spot a stale report


def build_summary(junit: dict | None, agent: dict | None, rag: dict | None,
                  e2e: dict | None) -> dict:
    gates: dict[str, bool] = {}
    gates["backend_tests_pass"] = bool(junit) and junit["failed"] == 0 and junit["tests"] > 0
    if agent is not None:
        gates["agent_no_critical_failures"] = not agent.get("critical_failures")
        gates["agent_all_scenarios_pass"] = not agent.get("failures")
    else:
        gates["agent_no_critical_failures"] = gates["agent_all_scenarios_pass"] = False
    if rag is not None:
        m = rag["metrics"]
        gates["rag_no_unsupported_answers"] = m["unsupported_answer_rate"] == 0
        # near-miss questions are a known weakness of lexical retrieval; the tolerance is explicit
        # and documented in evals/README.md rather than hidden by deleting the hard questions
        gates["rag_near_miss_unsupported_at_most_0.15"] = m.get("near_miss_unsupported_rate", 1) <= 0.15
        gates["rag_hit_at_k_at_least_0.9"] = (m.get(f"hit_at_{rag['config']['top_k']}") or 0) >= 0.9
    else:
        gates["rag_no_unsupported_answers"] = gates["rag_hit_at_k_at_least_0.9"] = False
        gates["rag_near_miss_unsupported_at_most_0.15"] = False
    if e2e is not None:
        gates["web_e2e_pass"] = e2e["failed"] == 0 and e2e["tests"] > 0
    return {
        "backend_tests": junit or "missing",
        "web_e2e": e2e if e2e is not None else "not_run",
        "agent_eval": ({"metrics": agent["metrics"], "by_metric": agent["by_metric"],
                        "failures": agent["failures"], "git_sha": agent["run"]["git_sha"],
                        "dataset": agent["dataset"]} if agent else "missing"),
        "rag_eval": ({"metrics": rag["metrics"], "failures": rag["failures"],
                      "dataset": rag["dataset"], "config": rag["config"]} if rag else "missing"),
        "gates": gates, "passed": all(gates.values()),
    }


def _load(path: Path | None, loader):
    return loader(path) if path and path.exists() else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--junit", type=Path)
    p.add_argument("--agent", type=Path)
    p.add_argument("--rag", type=Path)
    p.add_argument("--e2e", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)
    summary = build_summary(
        _load(a.junit, summarize_junit),
        _load(a.agent, lambda x: json.loads(x.read_text())),
        _load(a.rag, lambda x: json.loads(x.read_text())),
        _load(a.e2e, summarize_playwright))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(summary, indent=2))
    for name, ok in summary["gates"].items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
