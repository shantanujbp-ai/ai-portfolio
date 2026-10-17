"""search_chunks.py: find every chunk containing a word or phrase.
Usage:  python search_chunks.py ferr
Results are saved to eval/search_results.txt"""
import sys
from pathlib import Path
from rag_core import load_documents, chunk_documents

term = " ".join(sys.argv[1:]).lower()
matches = [c for c in chunk_documents(load_documents()) if term in c["text"].lower()]

out = Path(__file__).parent / "eval" / "search_results.txt"
with open(out, "w", encoding="utf-8") as f:
    for c in matches:
        f.write("=" * 70 + "\n")
        f.write(c["id"] + "\n\n")
        f.write(c["text"] + "\n\n")

print(f"{len(matches)} chunk(s) contain '{term}'. Saved to {out}")