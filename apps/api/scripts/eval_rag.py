"""Retrieval/answer evaluation against a versioned dataset. No LLM involved.

Usage: python -m scripts.eval_rag [--dataset evals/rag/dataset_v1.json] [--out PATH]
Requires a seeded database. Writes a machine-readable JSON report.
"""
import argparse
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionLocal
from app.models import Hotel
from app.rag.answer import answer_question
from app.rag.embeddings import get_embedder
from app.rag.retrieval import search
from app.rag.vectorstore import get_vector_store

ROOT = Path(__file__).resolve().parent.parent


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       text=True).strip()
    except Exception:
        return "unknown"


def _is_expected(p, item) -> bool:
    return p.title == item["expect_doc"] and item.get("expect_heading", "") in p.heading


def run(dataset_path: Path, out: Path | None) -> dict:
    ds = json.loads(dataset_path.read_text())
    settings = get_settings()
    results, k = [], settings.rag_top_k
    with SessionLocal() as db:
        hotel = db.scalar(select(Hotel).where(Hotel.slug == "demo-grand"))
        if hotel is None:
            raise SystemExit("Seed the database first: python -m scripts.seed")
        embedder, store = get_embedder(settings), get_vector_store(settings, db)
        for item in ds["items"]:
            common = dict(org_id=hotel.organization_id, hotel_id=hotel.id,
                          audience=item["audience"], embedder=embedder, store=store)
            passages = search(db, query=item["question"], top_k=k, **common)
            ans = answer_question(db, settings=settings, question=item["question"], **common)
            row = {"id": item["id"], "answerable": item["answerable"], "abstained": ans.abstained,
                   "near_miss": bool(item.get("near_miss"))}
            if item["answerable"]:
                ranks = [i for i, p in enumerate(passages, 1) if _is_expected(p, item)]
                rank = ranks[0] if ranks else None
                text = ans.text.lower()
                row.update(
                    rank=rank, hit_at_1=rank == 1, hit_at_k=rank is not None,
                    precision_at_k=round(sum(_is_expected(p, item) for p in passages)
                                         / max(len(passages), 1), 3),
                    cited_expected=any(c.title == item["expect_doc"] and
                                       item.get("expect_heading", "") in c.heading
                                       for c in ans.citations),
                    facts_present=all(f.lower() in text for f in item["must_contain"]))
            results.append(row)

    ans_rows = [r for r in results if r["answerable"]]
    # near-miss questions share generic words with real sections; they are reported separately
    # because lexical retrieval can only partly separate them from answerable paraphrases
    un_rows = [r for r in results if not r["answerable"] and not r["near_miss"]]
    nm_rows = [r for r in results if not r["answerable"] and r["near_miss"]]

    def frac(rows, key):
        return round(sum(1 for r in rows if r[key]) / len(rows), 3) if rows else None

    good = [r for r in ans_rows if not r["abstained"] and r["cited_expected"] and r["facts_present"]]
    report = {
        "dataset": {"path": str(dataset_path.relative_to(ROOT)), "version": ds["version"],
                    "answerable": len(ans_rows), "unanswerable": len(un_rows),
                    "near_miss_unanswerable": len(nm_rows)},
        "config": {"embedder": embedder.name, "vector_store": settings.vector_store_provider,
                   "top_k": k, "min_coverage": settings.rag_min_coverage,
                   "min_cosine": settings.rag_min_cosine, "hybrid": True},
        "run": {"git_sha": _git_sha(), "timestamp": datetime.now(UTC).isoformat(),
                "python": platform.python_version()},
        "metrics": {
            "hit_at_1": frac(ans_rows, "hit_at_1"),
            f"hit_at_{k}": frac(ans_rows, "hit_at_k"),
            f"precision_at_{k}": round(
                sum(r["precision_at_k"] for r in ans_rows) / len(ans_rows), 3),
            "mrr": round(sum(1 / r["rank"] for r in ans_rows if r["rank"]) / len(ans_rows), 3),
            "answer_cites_expected": frac(ans_rows, "cited_expected"),
            "answer_contains_facts": frac(ans_rows, "facts_present"),
            "end_to_end_correct": round(len(good) / len(ans_rows), 3),
            "false_abstain_rate": frac(ans_rows, "abstained"),
            "correct_abstain_rate": frac(un_rows, "abstained"),
            "unsupported_answer_rate": round(1 - (frac(un_rows, "abstained") or 0), 3),
            "near_miss_unsupported_rate": round(1 - (frac(nm_rows, "abstained") or 0), 3),
        },
        "failures": [r["id"] for r in ans_rows if r not in good]
                    + [r["id"] for r in un_rows + nm_rows if not r["abstained"]],
        "items": results,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(ROOT / "evals/rag/dataset_v1.json"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rep = run(Path(a.dataset), Path(a.out) if a.out else None)
    print(json.dumps({k: rep[k] for k in ("dataset", "config", "metrics", "failures")}, indent=2))
    sys.exit(0)
