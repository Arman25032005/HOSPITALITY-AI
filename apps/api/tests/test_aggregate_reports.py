import json

from scripts import aggregate_reports as ag

GOOD_AGENT = {"critical_failures": [], "failures": [], "metrics": {}, "by_metric": {},
              "run": {"git_sha": "x"}, "dataset": {}}
GOOD_RAG = {"metrics": {"unsupported_answer_rate": 0.0, "near_miss_unsupported_rate": 0.1,
                        "hit_at_5": 0.96}, "failures": [],
            "dataset": {}, "config": {"top_k": 5}}
JUNIT_OK = {"tests": 10, "failed": 0, "skipped": 0, "passed": 10}


def test_all_green_passes():
    s = ag.build_summary(JUNIT_OK, GOOD_AGENT, GOOD_RAG, None)
    assert s["passed"] and s["web_e2e"] == "not_run"


def test_missing_reports_fail_their_gates():
    s = ag.build_summary(None, None, None, None)
    assert not s["passed"] and not any(s["gates"].values())
    assert s["backend_tests"] == "missing"


def test_each_gate_can_fail_independently():
    assert not ag.build_summary({**JUNIT_OK, "failed": 1}, GOOD_AGENT, GOOD_RAG, None)["passed"]
    assert not ag.build_summary(JUNIT_OK, {**GOOD_AGENT, "critical_failures": ["p01"]},
                                GOOD_RAG, None)["passed"]
    bad_rag = {**GOOD_RAG, "metrics": {**GOOD_RAG["metrics"], "unsupported_answer_rate": 0.1}}
    assert not ag.build_summary(JUNIT_OK, GOOD_AGENT, {**GOOD_RAG, "metrics": {
        **GOOD_RAG["metrics"], "near_miss_unsupported_rate": 0.3}}, None)["passed"]
    assert not ag.build_summary(JUNIT_OK, GOOD_AGENT, bad_rag, None)["passed"]
    weak = {**GOOD_RAG, "metrics": {**GOOD_RAG["metrics"], "hit_at_5": 0.5}}
    assert not ag.build_summary(JUNIT_OK, GOOD_AGENT, weak, None)["passed"]
    assert not ag.build_summary(JUNIT_OK, GOOD_AGENT, GOOD_RAG, {"tests": 3, "failed": 1})["passed"]


def test_parsers_and_cli(tmp_path):
    (tmp_path / "j.xml").write_text(
        '<testsuites><testsuite tests="5" failures="1" errors="1" skipped="1"/></testsuites>')
    assert ag.summarize_junit(tmp_path / "j.xml") == {"tests": 5, "failed": 2, "skipped": 1,
                                                      "passed": 2}
    (tmp_path / "e.json").write_text(json.dumps({"stats": {"expected": 4, "unexpected": 1,
                                                           "flaky": 0, "skipped": 0}}))
    assert ag.summarize_playwright(tmp_path / "e.json")["failed"] == 1
    out = tmp_path / "summary.json"
    rc = ag.main(["--junit", str(tmp_path / "j.xml"), "--out", str(out)])
    assert rc == 1 and json.loads(out.read_text())["passed"] is False
