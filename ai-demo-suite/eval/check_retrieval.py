"""check_retrieval.py: retrieval-only check against the golden set (no Claude calls, no cost).
Usage:  python eval/check_retrieval.py          all questions, with hit rate and MRR
        python eval/check_retrieval.py Q025     one question, showing every chunk retrieved"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
from rag_core import retrieve, get_collection

with open(Path(__file__).parent / "golden_set.csv", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
pick = sys.argv[1:]
if pick:
    rows = [r for r in rows if r["id"] in pick]

collection = get_collection()
hits, rrs = [], []
for r in rows:
    gold = [g for g in r["source_chunk_ids"].split(";") if g]
    if not gold:
        continue                      # refusal questions have no gold chunks
    results = retrieve(collection, r["question"], 4)
    got = [h["id"] for h in results]
    pos = next((i for i, g in enumerate(got, 1) if g in gold), None)
    hits.append(1 if pos else 0)
    rrs.append(1 / pos if pos else 0)
    print(f"{r['id']:6} {'HIT  rank ' + str(pos) if pos else 'MISS      '}  {r['question'][:60]}")
    if pick:
        print("   gold:", gold)
        for i, h in enumerate(results, 1):
            print(f"\n   {i}. {h['id']}{'   <- gold' if h['id'] in gold else ''}")
            print("      " + h["text"][:300].replace("\n", " ") + " ...")

if hits:
    print(f"\n{len(hits)} questions: hit rate {sum(hits) / len(hits):.0%}, MRR {sum(rrs) / len(rrs):.2f}")