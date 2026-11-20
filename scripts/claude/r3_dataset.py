"""Round 3 modelling table: one row per (list, page) with history-free features.

A list is one site-restricted search (one site, one search). The label is the
REAL position of the page inside that list. Every feature comes from
scripts/claude/r3_features.py (search text, URL, the page's own stored HTML) and,
when the models are installed, scripts/claude/r3_semantic.py (free pretrained
relevance models run on the same text). No rank history, no Google title or snippet.

  build_table(labels, df_table=None) -> (rows DataFrame, df_table)

``df_table`` is the document-frequency table BM25 needs. It is built once from the
training pages and then frozen with the model, so scoring a held-out list never
depends on which other held-out pages exist.
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import r3_data, r3_features as F, r3_pages  # noqa: E402
from scripts.claude.r2_samesite_pairs import Corpus, coverage, ident, page_fields, query_terms, toks  # noqa: E402


class FrozenCorpus(Corpus):
    """BM25 statistics from a saved table (document count, document frequencies, average field lengths)."""

    def __init__(self, table: dict):
        self.n = int(table["n"])
        self.df = Counter({term: int(count) for term, count in table["df"].items()})
        self.avg = {name: float(value) for name, value in table["avg"].items()}


def make_df_table(pages: dict[str, dict]) -> dict:
    corpus = Corpus({key: page_fields(page) for key, page in pages.items()})
    return {"n": corpus.n, "df": dict(corpus.df), "avg": dict(corpus.avg)}


def load_lists(labels: list[str]) -> pd.DataFrame:
    frames = [r3_data.load_label(label) for label in labels]
    frames = [frame for frame in frames if not frame.empty]
    lists = pd.concat(frames, ignore_index=True)
    # Keep results on the list's own site only, and rank them 1..n inside the list.
    lists = lists[lists["url_site"] == lists["site"].map(lambda site: r3_features_site(site))].copy()
    lists["list_id"] = lists["label"] + "|" + lists["q"]
    lists["rank"] = lists.groupby("list_id")["position"].rank(method="first").astype(int)
    return lists.reset_index(drop=True)


def r3_features_site(site: str) -> str:
    from aeo.ranksim.site_history import site_key

    return site_key("https://" + site + "/")


def home_keys(sites: set[str]) -> dict[str, list[str]]:
    from aeo.ranksim.site_history import url_key

    return {site: [url_key(f"https://{site}/"), url_key(f"https://www.{site}/")] for site in sites}


def semantic_features(lists: pd.DataFrame, pages: dict[str, dict]) -> pd.DataFrame | None:
    """Cross-encoder and embedding scores per (search, page). None when the models are not installed."""
    try:
        from scripts.claude import r3_semantic as S
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    try:
        S.ensure_models()
    except Exception:  # noqa: BLE001
        return None
    usable = lists[lists["key"].isin(pages)]
    combos = usable[["query", "key"]].drop_duplicates()
    title_text = {key: F.page_text_for_title(page) for key, page in pages.items()}
    passage_text = {key: (S.passages(page.get("main_text") or "", words=200, stride=100, max_passages=12) or [title_text[key]]) for key, page in pages.items()}
    records = []
    queries = sorted(set(combos["query"]))
    query_vec = dict(zip(queries, S.embed(queries, is_query=True), strict=True))
    all_title = sorted({title_text[key] for key in combos["key"]})
    title_vec = dict(zip(all_title, S.embed(all_title), strict=True))
    all_pass = sorted({text for key in set(combos["key"]) for text in passage_text[key]})
    pass_vec = dict(zip(all_pass, S.embed(all_pass), strict=True))
    for query, group in combos.groupby("query", sort=True):
        keys = list(group["key"])
        ce_title = S.cross_scores(query, [title_text[key] for key in keys])
        flat = [(key, text) for key in keys for text in passage_text[key]]
        ce_flat = S.cross_scores(query, [text for _key, text in flat])
        by_key: dict[str, list[float]] = {}
        for (key, _text), score in zip(flat, ce_flat, strict=True):
            by_key.setdefault(key, []).append(float(score))
        qv = query_vec[query]
        for key, title_score in zip(keys, ce_title, strict=True):
            scores = sorted(by_key[key], reverse=True)
            emb_pass = [float(np.dot(qv, pass_vec[text])) for text in passage_text[key]]
            records.append(
                {
                    "query": query,
                    "key": key,
                    "ce_title": float(title_score),
                    "ce_body_max": scores[0],
                    "ce_body_top3": float(np.mean(scores[:3])),
                    "emb_title": float(np.dot(qv, title_vec[title_text[key]])),
                    "emb_body_max": max(emb_pass),
                }
            )
    return pd.DataFrame.from_records(records)


def build_table(labels: list[str], df_table: dict | None = None, with_semantic: bool = True) -> tuple[pd.DataFrame, dict]:
    lists = load_lists(labels)
    sites = set(lists["site"])
    homes = home_keys(sites)
    wanted = set(lists["key"]) | {key for keys in homes.values() for key in keys}
    pages = r3_pages.load_pages(wanted)
    listed_pages = {key: page for key, page in pages.items() if key in set(lists["key"])}
    if df_table is None:
        df_table = make_df_table(listed_pages)
    corpus = FrozenCorpus(df_table)
    static = {key: F.static_page(page, page["fetch_url"]) for key, page in listed_pages.items()}
    lists["usable"] = lists["key"].isin(listed_pages)

    records = []
    for list_id, group in lists.groupby("list_id", sort=True):
        site, query = group["site"].iloc[0], group["query"].iloc[0]
        home = next((pages[key] for key in homes[site] if key in pages), None)
        home_links = {ident(link) for link, _text in (home.get("links") or [])} if home else None
        outgoing = {key: {ident(link) for link, _text in (listed_pages[key].get("links") or [])} for key in group["key"] if key in listed_pages}
        # Anchor text of same-site links that point at each URL, from the stored home page and the other listed pages.
        anchors: dict[str, list[str]] = {}
        sources = [(None, home)] if home else []
        sources += [(key, listed_pages[key]) for key in group["key"] if key in listed_pages]
        for source_key, source_page in sources:
            for link, text in source_page.get("links") or []:
                if text:
                    anchors.setdefault(ident(link), []).append((source_key, text))
        terms = query_terms(query)
        idf_sum = sum(corpus.idf(term) for term in terms) or 1.0
        flags = F.query_flags(query)
        for record in group.itertuples(index=False):
            row = {
                "list_id": list_id,
                "label": record.label,
                "q": record.q,
                "site": site,
                "query": query,
                "key": record.key,
                "url": record.url,
                "position": int(record.position),
                "rank": int(record.rank),
                "list_len": int(len(group)),
                "usable": bool(record.usable),
            }
            if record.usable:
                st = static[record.key]
                lex = F.lexical_features(query, st, corpus)
                lex["bm25f_norm"] = lex["bm25f"] / idf_sum
                lex["bm25_body_norm"] = lex["bm25_body"] / idf_sum
                row.update(lex)
                for name in F.URL_FEATURES + F.SHAPE_FEATURES:
                    if name in st:
                        row[name] = st[name]
                own = ident(record.url)
                row["in_list_links"] = float(sum(1 for other, links in outgoing.items() if other != record.key and own in links))
                row["in_list_link_share"] = row["in_list_links"] / max(1, len(outgoing) - 1)
                row["home_linked"] = float(own in home_links) if home_links is not None else float("nan")
                anchor_tokens = [token for source_key, text in anchors.get(own, []) if source_key != record.key for token in toks(text)]
                row["anchor_cov"] = coverage(anchor_tokens, terms)
                row["has_anchor"] = float(bool(anchor_tokens))
                row.update(flags)
            records.append(row)
    table = pd.DataFrame.from_records(records)
    if with_semantic:
        sem = semantic_features(lists, listed_pages)
        if sem is not None and len(sem):
            table = table.merge(sem, on=["query", "key"], how="left")
    return table, df_table


def pair_table(table: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """One row per ordered pair of usable pages in a list: feature differences (higher page minus lower page)."""
    usable = table[table["usable"]]
    out = []
    for list_id, group in usable.groupby("list_id", sort=True):
        group = group.sort_values("rank")
        values = group[features].to_numpy(dtype=float)
        ranks = group["rank"].to_numpy()
        keys = group["key"].to_numpy()
        meta = group.iloc[0]
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                row = {
                    "list_id": list_id,
                    "site": meta["site"],
                    "query": meta["query"],
                    "key_hi": keys[i],
                    "key_lo": keys[j],
                    "rank_hi": int(ranks[i]),
                    "rank_lo": int(ranks[j]),
                }
                diff = values[i] - values[j]
                row.update({name: (0.0 if not math.isfinite(d) else float(d)) for name, d in zip(features, diff, strict=True)})
                out.append(row)
    return pd.DataFrame.from_records(out)
