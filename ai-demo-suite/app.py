import streamlit as st
from groq import Groq
import anthropic
from dotenv import load_dotenv
import os
import time

load_dotenv()
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

st.set_page_config(
    page_title="AI Model Comparison | FX Advisory",
    page_icon="🏦",
    layout="wide"
)

st.title("🏦 AI Model Comparison Dashboard")
st.markdown("**Compare how four leading AI models respond to the same FX and treasury prompt**")
st.markdown("---")

MODELS = {
    "Claude Haiku 4.5": {
        "id": "claude-haiku-4-5-20251001",
        "provider": "anthropic",
        "description": "Anthropic Claude — fast, precise, strong compliance and governance reasoning",
        "color": "#CC785C"
    },
    "GPT OSS 120B": {
        "id": "openai/gpt-oss-120b",
        "provider": "groq",
        "description": "OpenAI OSS — largest open model, comprehensive structured responses",
        "color": "#1B3A6B"
    },
    "GPT OSS 20B": {
        "id": "openai/gpt-oss-20b",
        "provider": "groq",
        "description": "OpenAI OSS — efficient mid-size model, fast responses",
        "color": "#1A5F6B"
    },
    "Qwen 3.8 27B": {
        "id": "qwen/qwen3.8-27b",
        "provider": "groq",
        "description": "Alibaba Qwen — strong multilingual reasoning, good analytical depth",
        "color": "#1A6B3A"
    }
}

SYSTEM_PROMPT = """You are a treasury and FX advisory assistant for a large corporate bank.
You provide concise, accurate responses about FX markets, hedging strategies,
and treasury products. Always ground your responses in established market practice.
Keep responses under 200 words. Do not use currency symbols — write INR and USD instead."""

PRESET_PROMPTS = {
    "Select a preset prompt...": "",
    "FX Hedging Strategy": "A corporate client has USD 5 million in receivables due in 90 days and is concerned about USD/INR depreciation. What hedging options should we discuss with them?",
    "RAG vs Fine-tuning": "What is the difference between RAG and fine-tuning for a bank deploying GenAI for treasury advisory? When would you use each?",
    "AI Hallucination Risk": "What is the biggest AI risk for a bank deploying a GenAI assistant for FX product recommendations, and how should it be mitigated?",
    "Responsible AI in Credit": "How should a bank address bias in a credit scoring model that shows 30% lower approval rates for SME clients versus large corporates with identical risk profiles?",
    "Multi-Agent Governance": "A bank wants to deploy an autonomous AI agent to book FX forwards on behalf of clients. What governance controls must be in place before go-live?"
}

with st.sidebar:
    st.header("Configuration")
    st.markdown("**Models being compared:**")
    for name, config in MODELS.items():
        st.markdown(f"- **{name}** — {config['description']}")
    st.markdown("---")
    st.markdown("**About this demo**")
    st.markdown("""
    Built using:
    - Anthropic API (Claude)
    - Groq API (open-source models)
    - Streamlit interface
    st.markdown("---")
    st.markdown("**Known model behaviours:**")
    st.info(
        "GPT OSS 120B may refuse certain governance questions due to safety filter calibration. "
        "GPT OSS 20B may occasionally produce empty responses on complex outputs. "
        "No model has real-time data — never trust rate quotes from any model."
    )
    Minimal cost to run
    """)
    st.markdown("---")
    temperature = st.slider("Temperature", 0.0, 1.0, 0.3, 0.1,
                            help="Lower = more consistent. Higher = more creative.")

preset = st.selectbox("Quick prompts:", list(PRESET_PROMPTS.keys()))

if preset != "Select a preset prompt...":
    default_prompt = PRESET_PROMPTS[preset]
else:
    default_prompt = ""

prompt = st.text_area(
    "Enter your FX or treasury question:",
    value=default_prompt,
    height=100,
    placeholder="e.g. What are the key risks of deploying AI for FX advisory in a regulated bank?"
)

col_left, col_right = st.columns([1, 4])
with col_left:
    run_button = st.button("Compare Models", type="primary", use_container_width=True)
with col_right:
    st.markdown(" ")


def get_groq_response(model_id, prompt, temperature):
    start = time.time()
    response = groq_client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature,
        max_tokens=800
    )
    elapsed = round(time.time() - start, 2)
    content = response.choices[0].message.content
    tokens = response.usage.completion_tokens
    return content, elapsed, tokens


def get_anthropic_response(model_id, prompt):
    start = time.time()
    response = anthropic_client.messages.create(
        model=model_id,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    elapsed = round(time.time() - start, 2)
    content = response.content[0].text
    tokens = response.usage.output_tokens
    return content, elapsed, tokens


def clean_response(text):
    return text.replace("₹", "INR").replace("\u20b9", "INR")


if run_button and prompt:
    st.markdown("---")
    st.markdown("### Model Responses")

    for idx, (model_name, config) in enumerate(MODELS.items()):
        st.markdown(f"#### {model_name}")
        st.markdown(f"*{config['description']}*")
        with st.spinner(f"Querying {model_name}..."):
            try:
                if config["provider"] == "anthropic":
                    response, elapsed, tokens = get_anthropic_response(
                        config["id"], prompt
                    )
                else:
                    response, elapsed, tokens = get_groq_response(
                        config["id"], prompt, temperature
                    )
                response = clean_response(response)
                if not response.strip():
                        response = "Model returned an empty response. Please try again."
                st.markdown(
                    f"""<div style='
                        background-color: #f8f9fa;
                        padding: 20px;
                        border-radius: 8px;
                        border-left: 6px solid {config["color"]};
                        font-size: 14px;
                        line-height: 1.6;
                        margin-bottom: 8px;
                    '>{response}</div>""",
                    unsafe_allow_html=True
                )
                st.markdown(f"Response time: `{elapsed}s`   Tokens: `{tokens}`")
            except Exception as e:
                st.error(f"Error querying {model_name}: {str(e)}")
        st.markdown("---")

    st.markdown("### What to observe")
    obs_col1, obs_col2, obs_col3 = st.columns(3)
    with obs_col1:
        st.info("**Accuracy:** Do all four reach the same conclusion? Where do they diverge?")
    with obs_col2:
        st.info("**Tone:** Which model sounds most appropriate for regulated financial services?")
    with obs_col3:
        st.info("**Hallucination risk:** Does any model cite specific figures without grounding?")

elif run_button and not prompt:
    st.warning("Please enter a prompt or select a preset.")

st.markdown("---")
st.markdown(
    "<div style='text-align:center; color:#888; font-size:12px;'>"
    "Built by Shantanu Dasgupta | AI Certifications Series | Anthropic + Groq | Open-source stack"
    "</div>",
    unsafe_allow_html=True
)