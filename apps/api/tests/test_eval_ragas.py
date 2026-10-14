import json

from scripts import eval_ragas as er


def test_build_samples_only_scores_answerable_items_with_references():
    items = [{"id": "a", "question": "Q1?", "answerable": True},
             {"id": "b", "question": "Q2?", "answerable": False},
             {"id": "c", "question": "Q3?", "answerable": True}]
    out = er.build_samples(items, {"a": "ref A"}, lambda item: ("ans " + item["id"], ["ctx1", "ctx2"]))
    assert out == [{"id": "a", "user_input": "Q1?", "response": "ans a",
                    "retrieved_contexts": ["ctx1", "ctx2"], "reference": "ref A"}]


def test_every_answerable_dataset_item_has_a_reference():
    ds = json.loads(er.DATASET.read_text())["items"]
    refs = json.loads(er.REFERENCES.read_text())["references"]
    assert {i["id"] for i in ds if i["answerable"]} == set(refs)


def test_skips_cleanly_without_a_judge_model(capsys):
    assert er.prerequisites() is not None  # no ragas / no LLM in the default environment
    assert er.main([]) == 3
    assert "No scores were produced" in capsys.readouterr().err
