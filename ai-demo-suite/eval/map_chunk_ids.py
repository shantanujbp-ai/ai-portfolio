"""map_chunk_ids.py : fills the source_chunk_ids column of golden_set.csv
by finding each evidence quote in the current chunks.

Run from the ai-demo-suite folder:   python eval/map_chunk_ids.py
Re-run whenever documents or chunk settings change.

Matching ignores spaces, line breaks and capitals, so PDF line breaks
and stray spaces ("2 0 per cent") do not stop a match. Several quotes
in one row are separated by ||. A quote split across two neighbouring
chunks is found as a pair and both ids are recorded."""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
from rag_core import load_documents, chunk_documents  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_set.csv"


def norm(text):
    text = (text.replace("\u2019", "'").replace("\u2018", "'")
                .replace("\u201c", '"').replace("\u201d", '"'))
    return re.sub(r"\s+", "", text).lower()


def prepare(chunks):
    out = []
    for c in chunks:
        source, idx = c["id"].rsplit("_", 1)
        out.append({"id": c["id"], "source": source, "idx": int(idx), "norm": norm(c["text"])})
    return out


def find(quote, chunks):
    q = norm(quote)
    hits = [c["id"] for c in chunks if q in c["norm"]]
    if hits:
        return hits, "found"
    words = quote.split()
    if len(words) < 4:
        return [], "NOT FOUND"
    half = len(words) // 2
    first, second = norm(" ".join(words[:half])), norm(" ".join(words[half:]))
    a = [c for c in chunks if first in c["norm"]]
    b = [c for c in chunks if second in c["norm"]]
    pairs = [(x["id"], y["id"]) for x in a for y in b
             if x["source"] == y["source"] and y["idx"] == x["idx"] + 1]
    if pairs:
        return [i for p in pairs for i in p], "found (split across two chunks)"
    return [], "NOT FOUND"


def main():
    chunks = prepare(chunk_documents(load_documents()))
    with open(GOLDEN, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    problems = 0
    for row in rows:
        quotes = [q.strip() for q in row["evidence_quote"].split("||") if q.strip()]
        if not quotes:
            row["source_chunk_ids"] = ""
            print(f"{row['id']:6} no quote (refusal question)")
            continue
        allowed = {s.strip() for s in row["source_files"].split(";") if s.strip()}
        pool = [c for c in chunks if not allowed or c["source"] in allowed]
        ids = []
        for q in quotes:
            found, status = find(q, pool)
            if not found:
                problems += 1
                print(f"{row['id']:6} NOT FOUND: {q[:70]}")
            elif "split" in status:
                print(f"{row['id']:6} split quote matched {found}")
            ids.extend(found)
        row["source_chunk_ids"] = ";".join(dict.fromkeys(ids))
        if ids:
            print(f"{row['id']:6} {row['source_chunk_ids']}")

    with open(GOLDEN, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print()
    print(f"{len(rows)} rows processed; {problems} quote(s) not found.")
    if problems:
        print("Check each NOT FOUND quote against the document text and correct it in golden_set.csv.")


if __name__ == "__main__":
    main()
