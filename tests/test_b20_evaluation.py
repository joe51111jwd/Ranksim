"""B20 review fixes in the evaluation path: what a `--final` number is called (F0), legacy final-run
rows and re-frozen copies (F17), and a damaged results.jsonl (F19). Synthetic pages and labels,
except where a test reads the repo's own docs."""

import hashlib
import re
from datetime import datetime
from pathlib import Path

import pytest

from aeo.ranksim.cli import main
from aeo.ranksim.snapshot import final_runs

REPO = Path(__file__).resolve().parents[1]


def _corpus(tmp_path, *, newline="\n"):
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import open_session

    db = f"sqlite:///{tmp_path / 'live.db'}"
    session = open_session(db)
    lines = ["query,url,position"]
    for q in range(8):
        for position in range(1, 4):
            url = f"https://site{q}{position}.example/page"
            words = " ".join(["dirt", "work", "software"][: 4 - position] * (60 // position))
            html = f"<html><body><main><h1>Dirt work</h1><p>{words} {'filler ' * 200}</p></main></body></html>"
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
            lines.append(f"dirt work software {q},{url},{position}")
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_bytes((newline.join(lines) + newline).encode())
    return db, serp


def _freeze(db, serp, out, *extra):
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(out), "--as-of", "2026-11-01", *extra])


# F0 -------------------------------------------------------------------------------------------

# A sentence about a --final run, the final split, or another same-snapshot number.
_SAME_SNAPSHOT = re.compile(
    r"--final|\bfinal[ -](?:split|run|number|queries)\b|\bsame[ -](?:snapshot|day)\b|\bholdout\b|\bheld[ -]out\b",
    re.IGNORECASE,
)
# "only a later snapshot (the ledger, or `--final` once) measures that": --final named as a later snapshot.
_FINAL_AS_LATER_SNAPSHOT = (
    re.compile(r"later snapshot[^.;]*(?:--final|\bfinal[ -](?:split|run)\b)", re.IGNORECASE),
    re.compile(r"(?:--final|\bfinal[ -](?:split|run)\b)[^.;]*\b(?:is|as|counts as) a later snapshot", re.IGNORECASE),
)
_ACCURACY = re.compile(r"\baccuracy\b(?!\.md)", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    """Sentences of a Markdown file. Wrapped lines are joined; list items, table rows and headings
    start a new block."""
    text = re.sub(r"\n[ \t]*(?:[-*|] |\d+\. |#+ )", "\n\n", text)
    found = []
    for block in re.split(r"\n\s*\n", text):
        block = re.sub(r"\s+", " ", block).strip()
        found.extend(part for part in re.split(r"(?<=[.!?])\s+(?=[A-Z`(\"'])", block) if part)
    return found


def _accuracy_claims(sentence: str) -> list[str]:
    """What in a sentence calls a --final, final-split or same-snapshot number accuracy. A negation
    ("not accuracy", "never as accuracy", a clause that starts with "never") is not a claim."""
    if not _SAME_SNAPSHOT.search(sentence):
        return []
    claims = [match.group(0) for pattern in _FINAL_AS_LATER_SNAPSHOT for match in pattern.finditer(sentence)]
    for match in _ACCURACY.finditer(sentence):
        before = sentence[: match.start()]
        clause = re.split(r"[;:]", before)[-1]
        if re.search(r"\b(?:not|never)(?:\s+as)?\s+$", before, re.IGNORECASE) or re.match(r"\s*(?:not|never|no)\b", clause, re.IGNORECASE):
            continue
        claims.append(sentence[max(0, match.start() - 40) : match.end()])
    return claims


def test_the_claim_check_flags_final_as_accuracy_and_passes_the_negations():
    for claim in (
        "On the labeled file it is same-snapshot agreement, not accuracy on unseen results; "
        "only a later snapshot (the ledger, or `--final` once) measures that.",
        "A `--final` run measures accuracy on unseen results.",
        "The final split gives an accuracy of 0.561.",
        "Held-out accuracy is 0.58.",
    ):
        assert _accuracy_claims(claim), claim
    for negation in (
        "These are same-snapshot numbers on the labeled file, not accuracy on unseen results.",
        "A `--final` number is a same-snapshot holdout, so it is reported as that and never as accuracy.",
        "Rules: never present a training, same-snapshot or test-tuned score as accuracy; score held-out data once.",
        "A `--final` number is a same-snapshot holdout, not accuracy.",
        "Only a forward check against a later snapshot (the ledger) measures accuracy on unseen results.",
    ):
        assert _accuracy_claims(negation) == [], negation


def test_no_doc_calls_a_final_run_or_a_same_snapshot_number_accuracy():
    """MISSION: a --final number is a same-snapshot holdout (held-out queries from the same frozen
    snapshot, same day as the training queries). Only a forward check on a later snapshot measures
    accuracy. No sentence in README.md or docs/*.md may say otherwise."""
    docs = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]
    assert REPO / "docs" / "accuracy.md" in docs
    claims = [(path.name, sentence) for path in docs for sentence in _sentences(path.read_text(encoding="utf-8")) if _accuracy_claims(sentence)]
    assert claims == []
    text = (REPO / "docs" / "accuracy.md").read_text(encoding="utf-8")
    assert "same-snapshot holdout, not accuracy" in text
    assert "\u2014" not in text and "\u2013" not in text


def test_a_final_run_says_it_is_a_same_snapshot_holdout_not_accuracy(tmp_path, capsys):
    db, serp = _corpus(tmp_path)
    _freeze(db, serp, tmp_path / "a")
    capsys.readouterr()
    main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "a"), "--final", "--bootstrap", "10"])
    out = capsys.readouterr().out
    assert "same-snapshot holdout, not accuracy" in out
    assert "later snapshot (the ledger)" in out
    assert [sentence for sentence in _sentences(out) if _accuracy_claims(sentence)] == []
    (run,) = final_runs(tmp_path / "a")
    assert any("same-snapshot holdout, not accuracy" in note for note in run["notes"])


# F17 ------------------------------------------------------------------------------------------


def test_a_legacy_row_blocks_a_refrozen_copy_without_reopening_its_snapshot(tmp_path, capsys):
    """87511c0 stored rs_final_runs rows under a key of the label file bytes, the cluster threshold
    and the final queries, with the snapshot folder. While that folder exists, the row blocks the
    same split re-frozen from LF labels or with another threshold, even if the old snapshot is never
    opened again. Rows are only added."""
    from aeo.ranksim.features import MODEL_FEATURES
    from aeo.ranksim.lambdamart import model_id
    from aeo.ranksim.snapshot import load_manifest, record_final, snapshot_final_key
    from aeo.ranksim.store import RsFinalRun, open_session

    db, serp_crlf = _corpus(tmp_path, newline="\r\n")
    _freeze(db, serp_crlf, tmp_path / "a")
    manifest = load_manifest(tmp_path / "a")
    split = manifest["split"]
    # What 87511c0 wrote for one --final run: a row under its key (formula written out here) and results.jsonl.
    old_key = hashlib.sha256(
        (f"{manifest['labels_sha256']}|{split['cluster_threshold']}|" + "\n".join(sorted(split["final_queries"]))).encode()
    ).hexdigest()
    model = model_id(list(MODEL_FEATURES))
    entry = {"model_id": model, "created_at": "2026-11-03T10:00:00+00:00", "final_key": old_key}
    session = open_session(db)
    try:
        session.add(
            RsFinalRun(
                final_key=old_key, model_id=model, snapshot_dir=str(tmp_path / "a"), summary=entry,
                created_at=datetime.fromisoformat(entry["created_at"]),
            )
        )
        session.commit()
    finally:
        session.close()
    record_final(tmp_path / "a", entry)
    lf = tmp_path / "serp-lf.csv"
    lf.write_bytes(serp_crlf.read_bytes().replace(b"\r\n", b"\n"))
    for name, source, extra in (("lf", lf, []), ("threshold", serp_crlf, ["--cluster-threshold", "0.5"])):
        capsys.readouterr()
        _freeze(db, source, tmp_path / name, *extra)
        assert load_manifest(tmp_path / name)["split"]["final_queries"] == split["final_queries"]
        assert f"Already scored on it: {model}" in capsys.readouterr().out
        with pytest.raises(SystemExit, match="already scored on this final split"):
            main(["--database", db, "benchmark", "--snapshot", str(tmp_path / name), "--final", "--bootstrap", "10"])
        assert final_runs(tmp_path / name) == []
    assert not (tmp_path / "final_runs.jsonl").exists()  # the refusal came from the database alone
    session = open_session(db)
    try:
        rows = sorted((row.final_key, row.model_id, row.snapshot_dir, row.created_at) for row in session.query(RsFinalRun))
    finally:
        session.close()
    new_key = snapshot_final_key(tmp_path / "a", manifest)
    stored_at = rows[0][3]
    assert sorted(rows) == sorted(
        [(old_key, model, str(tmp_path / "a"), stored_at), (new_key, model, str(tmp_path / "a"), stored_at)]
    )


# F19 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bad_line", "problem"),
    [
        (b'{"model_id": "lgbm-y", "crea', "it is not valid JSON"),
        (b'{"model_id": "lgbm-\xff"}', "it is not UTF-8 text"),
        (b'["lgbm-y"]', "it needs a model_id"),
        (b'{"created_at": "2026-11-01T00:00:00+00:00"}', "it needs a model_id"),
    ],
)
def test_a_damaged_results_file_names_the_file_and_line(tmp_path, capsys, bad_line, problem):
    from aeo.ranksim.snapshot import SnapshotError
    from aeo.ranksim.store import RsFinalRun, open_session

    db, serp = _corpus(tmp_path)
    _freeze(db, serp, tmp_path / "a")
    results = tmp_path / "a" / "results.jsonl"
    results.write_bytes(b'{"model_id": "lgbm-x", "created_at": "2026-11-01T00:00:00+00:00"}\n' + bad_line + b"\n")
    before = results.read_bytes()
    for extra in ([], ["--final"]):  # a dev run reads results.jsonl too, to register earlier final runs
        with pytest.raises(SystemExit) as caught:
            main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "a"), *extra, "--bootstrap", "10"])
        message = str(caught.value.code)
        assert f"Line 2 of {results} cannot be read: {problem}" in message
        assert "Fix that line by hand" in message
    with pytest.raises(SnapshotError, match=f"Line 2 of {re.escape(str(results))}"):
        final_runs(tmp_path / "a")
    assert results.read_bytes() == before  # never rewritten
    session = open_session(db)
    try:
        assert session.query(RsFinalRun).count() == 0  # nothing registered from a partly read file
    finally:
        session.close()
