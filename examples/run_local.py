"""Run a labeled mock simulation and an A/B diff.

In-process by default, so it does not need the network or a running server:

    python examples/run_local.py

Against a running API (the worker, or PROCESS_JOBS_INLINE=true, must be up):

    python examples/run_local.py --base-url http://127.0.0.1:8741
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PARAGRAPH = (
    "The best trail mix for long hikes is almonds, pumpkin seeds, dried cranberries, and dark chocolate. "
    "Northwind packs this trail mix for long hikes at about 100 calories per ounce so it stays light. "
)

TRAIL_HTML = f"""<!doctype html>
<html><head>
<title>Best trail mix for long hikes | Northwind</title>
<meta name="description" content="The best trail mix for long hikes balances salt, carbs, and weight.">
<link rel="canonical" href="https://northwind.example/trail-mix">
<meta property="article:modified_time" content="2026-01-15">
<script type="application/ld+json">{{"@type":"Article","dateModified":"2026-01-15"}}</script>
</head>
<body><h1>Best trail mix for long hikes</h1><p>{PARAGRAPH * 4}</p></body></html>"""

DRAFT_HTML = """<!doctype html>
<html><head><title>Office plants</title></head>
<body><h1>Office plants</h1>
<p>The lobby fern needs water. This draft is not about hiking food.</p>
</body></html>"""

ON_TOPIC = "What is the best trail mix for long hikes?"
OFF_TOPIC = "What is the capital of France?"


def payload() -> dict:
    return {
        "site_url": "https://northwind.example",
        "pages": [{"url": "https://northwind.example/trail-mix", "html": TRAIL_HTML}],
        "questions": [ON_TOPIC, OFF_TOPIC],
        "n_runs": 5,
        "search_provider": "mock",
        "llm_provider": "mock",
        "embedding_provider": "mock",
        "fetch_competitors": True,
    }


def wait_for(get_json, simulation_id: str) -> dict:
    for _ in range(60):
        body = get_json(f"/v1/simulations/{simulation_id}")
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.5)
    raise SystemExit("Simulation did not finish within 30 seconds")


def print_report(body: dict, diff: dict | None) -> None:
    providers = body["providers"]
    print("AEO Simulator sample")
    print("answers_are_official_engine_results:", providers["answers_are_official_engine_results"])
    print(
        f"providers: search={providers['search']} llm={providers['llm']} "
        f"embeddings={providers['embeddings']} synthetic={providers['synthetic']}"
    )
    print("compared_against_live_web:", providers["compared_against_live_web"])
    for warning in body["warnings"]:
        print("warning:", warning)
    print()
    for question in body["questions"]:
        print(f"Q: {question['question']}")
        print(
            f"  diagnosis={question['diagnosis']}  "
            f"citation_share={question['simulated_citation_share']}  "
            f"mention_rate={question['simulated_mention_rate']}  "
            f"mean_position={question['mean_citation_position']}"
        )
        for reason in question["diagnosis_reasons"]:
            print(f"  reason: {reason}")
        checks = question.get("html_checks") or {}
        if checks:
            print(
                "  raw html:"
                f" h1={checks['has_h1']}"
                f" title={checks['has_title']}"
                f" meta={checks['has_meta_description']}"
                f" canonical={checks['has_canonical']}"
                f" text_in_raw_html={checks['text_in_raw_html']}"
                f" words={checks['word_count']}"
                f" updated={checks['has_updated_date']}"
                f" answer_first={checks['answer_first_summary']}"
            )
        if question["runs"]:
            sample = " ".join(question["runs"][0]["answer"].split())
            print("  sample answer:", sample[:280])
        print()
    if diff:
        print("A/B draft that removes the trail-mix answer")
        print(f"  citation share {diff['citation_share_before']} -> {diff['citation_share_after']} ({diff['citation_share_delta']:+})")
        print(f"  mention rate   {diff['mention_rate_before']} -> {diff['mention_rate_after']} ({diff['mention_rate_delta']:+})")
        print(f"  diagnosis      {diff['diagnosis_before']} -> {diff['diagnosis_after']}")
        print(f"  improved={diff['improved']}")
        for item in diff["regressions"]:
            print("  regression:", item)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Northwind mock AEO simulation")
    parser.add_argument("--base-url", default=None, help="API base URL. Omit to run in-process.")
    args = parser.parse_args()

    if args.base_url:
        import httpx

        client = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=120)
        created = client.post("/v1/simulations", json=payload())
        created.raise_for_status()
        body = wait_for(lambda path: client.get(path).json(), created.json()["id"])
        on_topic = next(item for item in body["questions"] if item["question"] == ON_TOPIC)
        draft = client.post(
            f"/v1/questions/{on_topic['id']}/ab",
            json={
                "change_type": "draft",
                "page_url": "https://northwind.example/trail-mix",
                "html": DRAFT_HTML,
            },
        )
        draft.raise_for_status()
        ab = draft.json()
        for _ in range(60):
            if ab["status"] in {"completed", "failed"}:
                break
            time.sleep(0.5)
            ab = client.get(f"/v1/ab/{ab['id']}").json()
        diff = ab.get("diff")
    else:
        from fastapi.testclient import TestClient

        from aeo.api import create_app
        from aeo.config import Settings

        database = Path(tempfile.mkdtemp(prefix="aeo-example-")) / "aeo.db"
        settings = Settings(
            database_url=f"sqlite:///{database}",
            search_provider="mock",
            llm_provider="mock",
            embedding_provider="mock",
            process_jobs_inline=True,
            crawl_delay_seconds=0,
            brave_api_key=None,
            openai_api_key=None,
        )
        app = create_app(settings)
        with TestClient(app) as client:
            created = client.post("/v1/simulations", json=payload())
            if created.status_code >= 400:
                raise SystemExit(created.text)
            body = created.json()
            on_topic = next(item for item in body["questions"] if item["question"] == ON_TOPIC)
            draft = client.post(
                f"/v1/questions/{on_topic['id']}/ab",
                json={
                    "change_type": "draft",
                    "page_url": "https://northwind.example/trail-mix",
                    "html": DRAFT_HTML,
                },
            )
            if draft.status_code >= 400:
                raise SystemExit(draft.text)
            diff = draft.json().get("diff")

    if body["status"] != "completed":
        print_report(body, diff)
        raise SystemExit(f"Simulation status is {body['status']}: {body.get('error')}")
    print_report(body, diff)
    on_topic = next(item for item in body["questions"] if item["question"] == ON_TOPIC)
    if not on_topic["simulated_citation_share"]:
        raise SystemExit("Expected the trail-mix page to be cited at least once in the mock runs")


if __name__ == "__main__":
    main()
