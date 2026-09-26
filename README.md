\# AI Portfolio — Shantanu Dasgupta



Senior AI and Digital Transformation Leader | Financial Services | APAC



Three cloud AI certifications (Google Cloud, Microsoft, AWS) completed in 2026. 

This repository contains demonstration tools, study resources, and working AI 

prototypes built across that learning programme.



\---



\## Live Demos



| Demo | Description | Link |

|------|-------------|-------|

| AI Model Comparison Dashboard | Compare four AI models on FX and treasury prompts | Coming soon |

| FX Advisory RAG Assistant | Grounded regulatory document Q\&A with source attribution | Coming soon |



\---



\## Repository Structure



\### ai-demo-suite/

Two Streamlit applications demonstrating enterprise AI capabilities in financial services.



\*\*app.py — AI Model Comparison Dashboard\*\*

\- Compares Claude Haiku 4.5, GPT OSS 120B, GPT OSS 20B, and Qwen 3.8 27B simultaneously

\- Five preset prompts covering FX hedging, RAG vs fine-tuning, hallucination risk, responsible AI, and multi-agent governance

\- Temperature control, response time metrics, token counts

\- Stack: Anthropic API + Groq API + Streamlit



\*\*rag\_app.py — FX Advisory RAG Assistant\*\*

\- Answers FX, hedging, and treasury regulation questions grounded in real regulatory documents

\- Source documents: APRA CPS 226, FX Global Code, RBI FX Directions, MAS FEAT Principles, FX Products Guide, Hedging Policy

\- Source attribution on every answer, hallucination prevention, multi-regulator retrieval

\- Stack: LangChain + ChromaDB + sentence-transformers + Anthropic API + Groq API + Streamlit



\### trade-finance-ai/

Python AI assistant for trade finance operations covering the full LC lifecycle.



\- SWIFT MT700 and MT760 mechanics across 14 linked tables

\- LC lifecycle, discrepancy checking, compliance screening, FX hedging, settlement, fund utilisation

\- Mermaid journey diagrams and analytics dashboard

\- All data synthetic — no real bank transaction data

\- Stack: Python + DuckDB + Streamlit + Anthropic API



\### stack-fluency-quiz/

Interactive web tool for learning AI vendor architecture stacks.



\- Covers 27 AI vendors across frontier labs, hyperscalers, frameworks, and India-specific players

\- Teach-and-test format organised by architecture layer

\- Single HTML file — open in any browser, no installation required



\### ab731-resources/

Study materials for Microsoft AB-731 AI Transformation Leader certification.



\- AB731\_Master\_Reference.docx — 34 concepts covering fine-tuning vs RAG, Copilot products, Microsoft Foundry, exam traps, disambiguation tables

\- Practice quiz PDFs from preparation sessions



\### aws-aif-c01-study/

Study documents for AWS Certified AI Practitioner AIF-C01 certification.



\- Seven domain study documents covering all five exam domains

\- 450 practice questions completed at 96.4% average accuracy

\- AWS AIF-C01 passed August 2026



\---



\## Setup — AI Demo Suite



```bash

cd ai-demo-suite

python -m venv venv

venv\\Scripts\\activate        # Windows

pip install -r requirements.txt

```



Create a `.env` file with your API keys:

GROQ\_API\_KEY=your\_groq\_key\_here

ANTHROPIC\_API\_KEY=your\_anthropic\_key\_here

Run the comparison dashboard:

```bash

streamlit run app.py

```



Run the RAG assistant:

```bash

streamlit run rag\_app.py --server.port 8502

```



\---



\## Setup — Trade Finance AI



```bash

cd trade-finance-ai

python -m venv venv

venv\\Scripts\\activate        # Windows

pip install -r requirements.txt

```



Create a `.env` file:

ANTHROPIC\_API\_KEY=your\_anthropic\_key\_here



Run:

```bash

streamlit run app.py

```



\---



\## Certifications



| Certification | Provider | Date |

|---------------|----------|------|

| Generative AI Leader | Google Cloud | June 2026 |

| AB-731 AI Transformation Leader | Microsoft | June 2026 |

| AIF-C01 AI Practitioner | AWS | August 2026 |



\---



\## Connect



\[LinkedIn](https://www.linkedin.com/in/shantanu-dasgupta)

