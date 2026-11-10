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


def _corpus(tmp_path, *, newline="\n", dated=False):
    """Eight queries of three labeled pages each. With ``dated``, each query has its own observed_on day."""
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import open_session

    db = f"sqlite:///{tmp_path / 'live.db'}"
    session = open_session(db)
    lines = ["query,url,position" + (",observed_on" if dated else "")]
    for q in range(8):
        for position in range(1, 4):
            url = f"https://site{q}{position}.example/page"
            words = " ".join(["dirt", "work", "software"][: 4 - position] * (60 // position))
            html = f"<html><body><main><h1>Dirt work</h1><p>{words} {'filler ' * 200}</p></main></body></html>"
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
            lines.append(f"dirt work software {q},{url},{position}" + (f",2026-10-{20 + q}" if dated else ""))
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
# Only the word "accuracy": "accurate" would flag correct sentences such as EXPERIMENTS.md's
# "looked accurate in-sample ... but was right on 114 of 217 held-out cases".
_ACCURACY = re.compile(r"\baccuracy\b(?!\.md)", re.IGNORECASE)
# A negation that governs the "accuracy" right after it, in the same clause: "not accuracy", "does not
# measure accuracy", "cannot be called accuracy", "No same-snapshot number is accuracy". At most three
# words in between, with no comma, colon, semicolon or bracket and no "but", "so", "yet", "and", "that",
# "which" or "who". Those words end the negated part: in "never seen and measures accuracy" the "never"
# governs "seen" only. A past participle (a word ending in -ed, -en or -wn) right before a verb such as
# "is", "gives" or "measures" ends it too: in "queries that were never seen give accuracy" the "never"
# governs "seen", not "give accuracy". "Or" does not end it, so "is not dev agreement or accuracy" stays
# a negation. "Not only" and "not just" do not negate.
_NEGATED = re.compile(
    r"\b(?:not|never|no|nor|cannot|\w+n['\u2019]t)\b(?!\s+(?:only|just|merely|simply)\b)"
    r"(?![\w\s`'\u2019-]*\b(?!(?:often|even)\b)\w+(?:ed|en|wn)"
    r"\s+(?:is|are|was|were|gives?|gave|measures?|measured|yields?|yielded)\b)"
    r"(?:\s+(?!(?:but|so|yet|and|that|which|who)\b)[\w`'\u2019-]+){0,3}\s+$",
    re.IGNORECASE,
)
# The rule's own wording: "never present a training, same-snapshot or test-tuned score as accuracy".
_NEVER_PRESENT_AS = re.compile(
    r"\bnever (?:present|report|call|show)\b[^.;:]{0,80}\b(?:score|number|figure|result)s?\s+as\s+$", re.IGNORECASE
)


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
    """Where a sentence about a --final run, the final split or another same-snapshot number uses
    "accuracy" without a negation that governs that word (see ``_NEGATED``), or names
    --final as the later snapshot. A pattern check for plain wording, not a parser: it can miss a
    claim phrased around these words. It can also flag a correct sentence such as "is not one that
    measures accuracy"; reword that as "does not measure accuracy"."""
    if not _SAME_SNAPSHOT.search(sentence):
        return []
    claims = [match.group(0) for pattern in _FINAL_AS_LATER_SNAPSHOT for match in pattern.finditer(sentence)]
    for match in _ACCURACY.finditer(sentence):
        before = sentence[: match.start()]
        if _NEGATED.search(before) or _NEVER_PRESENT_AS.search(before):
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
        # A sentence that opens with a negation and then makes the claim (B20 review).
        "Not only dev agreement, a `--final` run is the accuracy number to report.",
        "No leakage, so the --final number is accuracy on unseen results.",
        "Never tuned on it, the final split measures accuracy.",
        "Not just same-snapshot agreement: a `--final` run gives accuracy on unseen results.",
        "It is not dev agreement; the final split is accuracy.",
        "A `--final` run is not just agreement but accuracy.",
        "A `--final` run is not agreement but accuracy.",
        "Never report the dev number, report the final split as accuracy.",
        "The --final run (E12) gives the model's true accuracy: 0.561.",
        "E12's final split, 0.561 pairwise, is our accuracy estimate.",
        "Final-split accuracy was 0.561.",
        "Run --final once to get a real accuracy figure.",
        "A same-snapshot holdout is accuracy on unseen data.",
        # A negation in an earlier phrase or clause does not negate the accuracy (B21 review).
        "A `--final` run scores queries the model has never seen and measures accuracy on unseen results.",
        "E12's final split was never tuned and gives accuracy of 0.561.",
        "The final split is not leaked and measures accuracy.",
        "The final split is not leaked which gives accuracy.",
        "The final split uses queries never seen that give accuracy on unseen results.",
        "Held-out queries that were never seen give accuracy on unseen results.",
        "A `--final` run on queries never seen measures accuracy.",
    ):
        assert _accuracy_claims(claim), claim
    for negation in (
        "These are same-snapshot numbers on the labeled file, not accuracy on unseen results.",
        "A `--final` number is a same-snapshot holdout, so it is reported as that and never as accuracy.",
        "Rules: never present a training, same-snapshot or test-tuned score as accuracy; score held-out data once.",
        "A `--final` number is a same-snapshot holdout, not accuracy.",
        "Only a forward check against a later snapshot (the ledger) measures accuracy on unseen results.",
        "The final split is a same-snapshot holdout; it is not accuracy.",
        "Held-out agreement is not accuracy.",
        "No same-snapshot number is accuracy.",
        # Common negations a correct future sentence may use (B20 review).
        "A --final run does not measure accuracy.",
        "The --final number cannot be called accuracy.",
        "A `--final` number isn't accuracy.",
        "A --final run is neither dev agreement nor accuracy.",
        # Negations next to "and", a relative clause or a participle (B21 review).
        "A `--final` number is a same-snapshot holdout and is not accuracy.",
        "A `--final` number is a holdout that is not accuracy.",
        "A `--final` run gives a number that cannot be called accuracy.",
        "A `--final` number has never been called accuracy.",
        "A `--final` number is not seen as accuracy.",
        "Held-out queries that were never seen give agreement, not accuracy.",
        "A `--final` number is not dev agreement or accuracy.",
        "A `--final` run does not even give accuracy.",
    ):
        assert _accuracy_claims(negation) == [], negation


def test_no_doc_calls_a_final_run_or_a_same_snapshot_number_accuracy():
    """MISSION: a --final number is a same-snapshot holdout (held-out queries from the same frozen
    label file as the dev queries). Only a forward check on a later snapshot measures accuracy. No
    sentence in README.md or docs/*.md may call it accuracy in the plain wording ``_accuracy_claims``
    checks."""
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


def test_the_final_note_states_only_what_holds_when_queries_have_different_dates(tmp_path, capsys):
    """A label file can give each query its own observed_on date, and freeze keeps it. The --final
    note and docs/accuracy.md must not say the final queries were collected on the same day as the
    dev queries: the tool never checks that. They come from the same frozen label file (B20 review)."""
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.snapshot import load_manifest

    db, serp = _corpus(tmp_path, dated=True)
    _freeze(db, serp, tmp_path / "a")
    split = load_manifest(tmp_path / "a")["split"]
    days = {row.query: row.observed_on for row in load_serp_file(tmp_path / "a" / "labels.csv")}
    assert split["final_queries"] and not {days[q] for q in split["final_queries"]} & {days[q] for q in split["dev_queries"]}
    capsys.readouterr()
    main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "a"), "--final", "--bootstrap", "10"])
    out = capsys.readouterr().out
    assert "same-snapshot holdout, not accuracy" in out
    assert "The final queries come from the same frozen label file as the dev queries." in out
    assert not re.search(r"same[ -]day", out, re.IGNORECASE)
    (run,) = final_runs(tmp_path / "a")
    assert not any(re.search(r"same[ -]day", note, re.IGNORECASE) for note in run["notes"])
    text = (REPO / "docs" / "accuracy.md").read_text(encoding="utf-8")
    final_sentences = [sentence for sentence in _sentences(text) if re.search(r"--final|\bfinal queries\b", sentence)]
    assert final_sentences
    assert [sentence for sentence in final_sentences if re.search(r"same[ -]day", sentence, re.IGNORECASE)] == []


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
