"""rag_core.py: the RAG pipeline with no Streamlit code.
Used by eval/run_eval.py now, and by rag_app.py after the next refactor."""
import os
import time
from pathlib import Path

import anthropic
import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv
from groq import Groq
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

load_dotenv(Path(__file__).parent / ".env")
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

DOCS_PATH = Path(__file__).parent / "documents"
CHROMA_PATH = Path(__file__).parent / "chroma_db"

MODEL_IDS = {
    "Claude Haiku 4.5": "claude-haiku-4-5-20251001",
    "GPT OSS 20B": "openai/gpt-oss-20b",
    "Qwen 27B": "qwen/qwen3.8-27b",
}

CONFIG = {  # logged with every evaluation run
    "gen_model": "Claude Haiku 4.5",
    "temperature": 0,               # CHANGE 1: app uses the default (1.0) for Claude
    "embedding_model": "all-MiniLM-L6-v2",
    "chunk_size_chars": 800,
    "chunk_overlap_chars": 100,
    "top_k": 4,
    "distance": "chroma default (l2)",
    "regulator_boost": True,
    "prompt_version": "v1_unlabelled_chunks",
}


def load_documents():
    docs = []
    for file in DOCS_PATH.iterdir():
        try:
            if file.suffix == ".pdf":
                text = "".join(page.extract_text() or "" for page in PdfReader(str(file)).pages)
            elif file.suffix == ".txt":
                text = file.read_text(encoding="utf-8")
            else:
                continue
            if text.strip():
                docs.append({"source": file.name, "text": text})
        except Exception as e:
            print(f"Could not read {file.name}: {e}")
    return docs


def chunk_documents(docs):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800, chunk_overlap=100, separators=["\n\n", "\n", ". ", " "])
    chunks = []
    for doc in docs:
        for i, split in enumerate(splitter.split_text(doc["text"])):
            chunks.append({"id": f"{doc['source']}_{i}", "text": split, "source": doc["source"]})
    return chunks


def build_vector_store():
    chunks = chunk_documents(load_documents())
    if not chunks:
        return None, 0
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    try:
        client.delete_collection("fx_docs")
    except Exception:
        pass
    collection = client.create_collection(name="fx_docs", embedding_function=ef)
    for i in range(0, len(chunks), 50):
        batch = chunks[i:i + 50]
        collection.add(ids=[c["id"] for c in batch],
                       documents=[c["text"] for c in batch],
                       metadatas=[{"source": c["source"]} for c in batch])
    return collection, len(chunks)


_collection = None

def get_collection():
    global _collection
    if _collection is None:
        _collection, _ = build_vector_store()
    return _collection


def _to_hits(results):
    return [{"id": i, "text": d, "source": m["source"]}
            for i, d, m in zip(results["ids"][0], results["documents"][0], results["metadatas"][0])]


def retrieve(collection, query, n_results=4):
    """Same logic as the app, but returns chunk ids too (CHANGE 2)."""
    hits = _to_hits(collection.query(query_texts=[query], n_results=n_results))
    regulators = {
        "rbi": "What must banks obtain from clients before executing derivatives under RBI rules?",
        "apra": "What must banks obtain from clients before executing derivatives under APRA rules?",
        "mas": "What are the MAS FEAT requirements for AI governance?",
    }
    mentioned = [reg for reg in regulators if reg in query.lower()]   # substring match, bug kept for baseline
    if len(mentioned) > 1:
        seen = {h["id"] for h in hits}
        for reg in mentioned:
            for h in _to_hits(collection.query(query_texts=[regulators[reg]], n_results=3)):
                if h["id"] not in seen:
                    hits.append(h)
                    seen.add(h["id"])
    return hits


def build_prompt(context, question):
    return f"""You are an FX and treasury advisory assistant for a corporate bank.
Answer the question using ONLY the information provided in the context below.
If the answer is not in the context, say clearly: "I cannot find this information in the available documents."
Do not use your general training knowledge. Ground every statement in the provided context.
Keep your answer concise and professional, under 250 words.

CONTEXT:
{context}

QUESTION:
{question}

ANSWER:"""


def generate(prompt, model_choice="Claude Haiku 4.5"):
    start = time.time()
    if model_choice == "Claude Haiku 4.5":
        r = anthropic_client.messages.create(
            model=MODEL_IDS[model_choice], max_tokens=600, extra_body={"temperature": 0},
            messages=[{"role": "user", "content": prompt}])
        text = r.content[0].text
    else:
        r = groq_client.chat.completions.create(
            model=MODEL_IDS[model_choice], max_tokens=600, temperature=0.1,
            messages=[{"role": "user", "content": prompt}])
        text = r.choices[0].message.content
    return text, round(time.time() - start, 2)


def ask(question, history=None, model_choice="Claude Haiku 4.5", n_results=4):
    """One question through the pipeline. history is accepted but unused (the app has no memory)."""
    hits = retrieve(get_collection(), question, n_results)
    context = "\n\n---\n\n".join(h["text"] for h in hits)
    answer, elapsed = generate(build_prompt(context, question), model_choice)
    return {"question": question, "standalone_query": question, "answer": answer,
            "contexts": hits, "latency_ms": int(elapsed * 1000)}