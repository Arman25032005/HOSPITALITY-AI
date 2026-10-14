"""Optional RAGAS evaluation (faithfulness, answer relevancy, context precision/recall).

RAGAS scores with an LLM judge, so this needs the `ragas` package AND a configured judge model
(LLM_PROVIDER + credentials). It has NOT been run against a real judge in this repository: the
default CI/dev setup has no LLM, and numbers must never be reported unless it actually ran.
When prerequisites are missing it exits with code 3 and a clear message instead of guessing.

Usage: python -m scripts.eval_ragas [--out evals/reports/ragas_....json]
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "evals/rag/dataset_v1.json"
REFERENCES = ROOT / "evals/rag/references_v1.json"


def build_samples(items: list[dict], references: dict[str, str], retrieve) -> list[dict]:
    """Pure dataset builder. `retrieve(item) -> (answer_text, [context strings])`.

    Only answerable items with a hand-written reference are scored: abstention behaviour on
    unanswerable questions is measured deterministically by scripts.eval_rag instead."""
    samples = []
    for item in items:
        if not item.get("answerable") or item["id"] not in references:
            continue
        answer, contexts = retrieve(item)
        samples.append({"id": item["id"], "user_input": item["question"],
                        "response": answer, "retrieved_contexts": list(contexts),
                        "reference": references[item["id"]]})
    return samples


def prerequisites() -> str | None:
    """Return a human-readable reason RAGAS cannot run, or None."""
    from app.config import get_settings

    if importlib.util.find_spec("ragas") is None:
        return "the `ragas` package is not installed (pip install ragas)"
    s = get_settings()
    if s.llm_provider == "none" or not (s.llm_api_key or s.llm_base_url):
        return "no judge LLM configured (set LLM_PROVIDER and LLM_API_KEY/LLM_BASE_URL)"
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    reason = prerequisites()
    if reason:
        print(f"RAGAS evaluation skipped: {reason}. No scores were produced.", file=sys.stderr)
        return 3
    # pragma: no cover - requires a live judge model, never exercised in this repository's CI
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.session import SessionLocal
    from app.models import Hotel
    from app.rag.answer import answer_question
    from app.rag.embeddings import get_embedder
    from app.rag.retrieval import search
    from app.rag.vectorstore import get_vector_store

    ds = json.loads(DATASET.read_text())
    refs = json.loads(REFERENCES.read_text())["references"]
    settings = get_settings()
    with SessionLocal() as db:
        hotel = db.scalar(select(Hotel).where(Hotel.slug == "demo-grand"))
        embedder, store = get_embedder(settings), get_vector_store(settings, db)

        def retrieve(item):
            common = dict(org_id=hotel.organization_id, hotel_id=hotel.id,
                          audience=item["audience"], embedder=embedder, store=store)
            ctx = [p.text for p in search(db, query=item["question"], top_k=settings.rag_top_k,
                                          **common)]
            ans = answer_question(db, settings=settings, question=item["question"], **common)
            return ans.text, ctx

        samples = build_samples(ds["items"], refs, retrieve)
    from ragas import EvaluationDataset, evaluate
    from ragas.metrics import context_precision, context_recall, faithfulness

    result = evaluate(EvaluationDataset.from_list(samples),
                      metrics=[faithfulness, context_precision, context_recall])
    report = {"judge": settings.llm_model, "samples": len(samples),
              "scores": result.to_pandas().drop(columns=["user_input", "response",
                                                         "retrieved_contexts", "reference"],
                                                errors="ignore").mean(numeric_only=True).to_dict()}
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
