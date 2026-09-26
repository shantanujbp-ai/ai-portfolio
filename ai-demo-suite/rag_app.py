import streamlit as st
from groq import Groq
import anthropic
from dotenv import load_dotenv
import os
import time
from pathlib import Path

# Document loading
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Vector store
import chromadb
from chromadb.utils import embedding_functions

load_dotenv()
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

st.set_page_config(
    page_title="FX Advisory RAG Assistant",
    page_icon="📋",
    layout="wide"
)

DOCS_PATH = Path("documents")
CHROMA_PATH = Path("chroma_db")

# ── Document loading ──────────────────────────────────────────────────────────

def load_documents():
    docs = []
    for file in DOCS_PATH.iterdir():
        if file.suffix == ".pdf":
            try:
                reader = PdfReader(str(file))
                text = ""
                for page in reader.pages:
                    text += page.extract_text() or ""
                if text.strip():
                    docs.append({"source": file.name, "text": text})
            except Exception as e:
                st.warning(f"Could not read {file.name}: {e}")
        elif file.suffix == ".txt":
            try:
                text = file.read_text(encoding="utf-8")
                docs.append({"source": file.name, "text": text})
            except Exception as e:
                st.warning(f"Could not read {file.name}: {e}")
    return docs


def chunk_documents(docs):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        separators=["\n\n", "\n", ". ", " "]
    )
    chunks = []
    for doc in docs:
        splits = splitter.split_text(doc["text"])
        for i, split in enumerate(splits):
            chunks.append({
                "id": f"{doc['source']}_{i}",
                "text": split,
                "source": doc["source"]
            })
    return chunks


@st.cache_resource(show_spinner="Loading and indexing documents...")
def build_vector_store():
    docs = load_documents()
    if not docs:
        return None, 0
    chunks = chunk_documents(docs)

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    # Clear and rebuild collection
    try:
        client.delete_collection("fx_docs")
    except Exception:
        pass

    collection = client.create_collection(
        name="fx_docs",
        embedding_function=ef
    )

    # Add in batches
    batch_size = 50
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        collection.add(
            ids=[c["id"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[{"source": c["source"]} for c in batch]
        )

    return collection, len(chunks)


def retrieve_context(collection, query, n_results=4):
    # Standard retrieval
    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )
    chunks = results["documents"][0]
    sources = [m["source"] for m in results["metadatas"][0]]

    # Check if query mentions multiple regulators — if so do targeted retrieval
    query_lower = query.lower()
    regulators = {
        "rbi": "What must banks obtain from clients before executing derivatives under RBI rules?",
        "apra": "What must banks obtain from clients before executing derivatives under APRA rules?",
        "mas": "What are the MAS FEAT requirements for AI governance?",
    }

    mentioned = [reg for reg in regulators if reg in query_lower]

    if len(mentioned) > 1:
        # Do a targeted query per regulator and add unique chunks
        seen_ids = set(chunks)
        for reg in mentioned:
            targeted = collection.query(
                query_texts=[regulators[reg]],
                n_results=3
            )
            for chunk, meta in zip(
                targeted["documents"][0],
                targeted["metadatas"][0]
            ):
                if chunk not in seen_ids:
                    chunks.append(chunk)
                    sources.append(meta["source"])
                    seen_ids.add(chunk)

    context = "\n\n---\n\n".join(chunks)
    return context, list(dict.fromkeys(sources))


# ── LLM calls ────────────────────────────────────────────────────────────────

def build_prompt(context, question):
    return f"""You are an FX and treasury advisory assistant for a corporate bank.
Answer the question using ONLY the information provided in the context below.
If the answer is not in the context, say clearly: "I cannot find this information in the available documents."
Do not use your general training knowledge. Ground every statement in the provided context.
Keep your answer concise and professional — under 250 words.

CONTEXT:
{context}

QUESTION:
{question}

ANSWER:"""


def get_claude_response(prompt):
    start = time.time()
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    elapsed = round(time.time() - start, 2)
    return response.content[0].text, elapsed


def get_groq_response(prompt, model_id):
    start = time.time()
    response = groq_client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.1
    )
    elapsed = round(time.time() - start, 2)
    return response.choices[0].message.content, elapsed


# ── UI ────────────────────────────────────────────────────────────────────────

st.title("📋 FX Advisory RAG Assistant")
st.markdown("**Grounded responses from regulatory documents and treasury policy**")
st.markdown("---")

# Build vector store
collection, chunk_count = build_vector_store()

if collection is None:
    st.error("No documents found in the documents folder. Please add documents and restart.")
    st.stop()

# Sidebar
with st.sidebar:
    st.header("📂 Knowledge Base")
    st.success(f"Documents indexed: {chunk_count} chunks")
    st.markdown("**Source documents:**")
    for f in sorted(DOCS_PATH.iterdir()):
        size_kb = round(f.stat().st_size / 1024)
        st.markdown(f"- {f.name} ({size_kb} KB)")

    st.markdown("---")
    st.header("⚙️ Settings")
    model_choice = st.radio(
        "Model:",
        ["Claude Haiku 4.5", "GPT OSS 20B", "Qwen 27B"],
        index=0
    )
    n_chunks = st.slider("Context chunks retrieved:", 2, 6, 4,
                         help="More chunks = more context but slower")
    show_context = st.checkbox("Show retrieved context", value=False)

    st.markdown("---")
    st.markdown("**How RAG works here:**")
    st.markdown("""
    1. Your question is embedded as a vector
    2. Most similar document chunks retrieved
    3. Chunks injected into model prompt
    4. Model answers ONLY from retrieved content
    5. Sources shown so you can verify
    """)

# Preset questions
PRESETS = {
    "Select a question...": "",
    "What must banks obtain from clients before executing derivatives under RBI and APRA rules?":
        "What must banks obtain from clients before executing derivatives under RBI and APRA rules?",
    "What are the APRA margining requirements for non-centrally cleared derivatives?":
        "What are the APRA margining requirements for non-centrally cleared derivatives?",
    "What FX hedging instruments are permitted for retail users under RBI guidelines?":
        "What FX hedging instruments are permitted for retail users under RBI guidelines?",
    "What documentation is required before executing a derivative transaction?":
        "What documentation is required before executing a derivative transaction?",
    "What approval limits apply to FX hedging transactions?":
        "What approval limits apply to FX hedging transactions?",
    "What does MAS FEAT say about accountability for AI decisions?":
        "What does MAS FEAT say about accountability for AI decisions?",
    "What is a Non-Deliverable Forward and when is it used?":
        "What is a Non-Deliverable Forward and when is it used?",
    "What are the AI governance requirements for FX advisory systems?":
        "What are the AI governance requirements for FX advisory systems?"
}

preset = st.selectbox("Preset questions:", list(PRESETS.keys()))
default_q = PRESETS[preset]

question = st.text_area(
    "Ask a question about FX, hedging, or treasury regulation:",
    value=default_q,
    height=80,
    placeholder="e.g. What are the collateral requirements under APRA CPS 226?"
)

col1, col2 = st.columns([1, 4])
with col1:
    ask_button = st.button("Ask", type="primary", use_container_width=True)

if ask_button and question:
    st.markdown("---")

    # Retrieve context
    with st.spinner("Retrieving relevant document chunks..."):
        context, sources = retrieve_context(collection, question, n_chunks)

    # Show sources
    st.markdown("**Sources retrieved:**")
    source_cols = st.columns(len(sources))
    for i, src in enumerate(sources):
        with source_cols[i]:
            st.info(f"📄 {src}")

    # Show context if requested
    if show_context:
        with st.expander("Retrieved context (what the model sees)"):
            st.text(context)

    # Build grounded prompt
    prompt = build_prompt(context, question)

    # Get response
    with st.spinner(f"Generating grounded response from {model_choice}..."):
        try:
            if model_choice == "Claude Haiku 4.5":
                response, elapsed = get_claude_response(prompt)
            elif model_choice == "GPT OSS 20B":
                response, elapsed = get_groq_response(prompt, "openai/gpt-oss-20b")
            else:
                response, elapsed = get_groq_response(prompt, "qwen/qwen3.8-27b")
        except Exception as e:
            st.error(f"Error: {str(e)}")
            st.stop()

    # Display response
    st.markdown("### Response")
    st.markdown(
        f"""<div style='
            background-color: #f8f9fa;
            padding: 24px;
            border-radius: 8px;
            border-left: 6px solid #1B3A6B;
            font-size: 15px;
            line-height: 1.7;
        '>{response}</div>""",
        unsafe_allow_html=True
    )
    st.markdown(f"Response time: `{elapsed}s`  |  Model: `{model_choice}`  |  Chunks used: `{n_chunks}`")

    # Hallucination check note
    st.markdown("---")
    st.info(
        "**Grounding note:** This response was generated using only content retrieved "
        "from the documents listed above. The model was instructed not to use general "
        "training knowledge. If the answer says 'I cannot find this information' — "
        "that is the RAG system working correctly, not a failure."
    )

elif ask_button and not question:
    st.warning("Please enter a question.")

st.markdown("---")
st.markdown(
    "<div style='text-align:center; color:#888; font-size:12px;'>"
    "FX Advisory RAG Assistant | Built with LangChain + ChromaDB + Streamlit | "
    "Retrieval-Augmented Generation Demo"
    "</div>",
    unsafe_allow_html=True
)