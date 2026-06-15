# # """
# # utils/llm_client.py
# # Groq API client for two tasks:
# #   1. parse_order()    — extracts structured fields from a plain-English order request
# #   2. copilot_query()  — answers natural-language questions about plant state,
# #                         now augmented with RAG retrieval from company spec documents

# # Key fixes applied vs v1:
# #   - max_tokens raised from 600 → 1500 (prevents mid-sentence truncation)
# #   - RAG context injected before every copilot answer so the AI can cite real
# #     Apex Precast spec numbers (slump ranges, W/C limits, NCR triggers, etc.)
# #   - Copilot system prompt updated to reference the company documents explicitly
# #   - Full per-batch QA readings (slump_mm, wc_ratio, strength) now reachable
# #     from the batch-level details in context, enabling precise failure explanations
# # """

# # import json
# # from pathlib import Path
# # from groq import Groq

# # # RAG retrieval — no external ML needed (TF-IDF, pure Python)
# # from utils.rag import retrieve_relevant_chunks

# # # Path to company spec documents relative to project root
# # DOCS_DIR = str(Path(__file__).parent.parent / "data" / "docs")


# # def get_client(api_key: str) -> Groq:
# #     return Groq(api_key=api_key)


# # # ─────────────────────────────────────────────
# # # 1. ORDER PARSER  (unchanged from v1)
# # # ─────────────────────────────────────────────
# # ORDER_PARSE_SYSTEM = """
# # You are an order intake assistant for Apex Precast, an Irish precast concrete manufacturer.
# # Your job is to extract structured order information from plain English requests sent by site managers or contractors.

# # Always respond with valid JSON and nothing else. No markdown, no explanation.

# # Extract these fields:
# # - customer: company or site name (string)
# # - product_type: one of [Hollow-Core Slab, Precast Beam (I-Section), Box Culvert, Precast Column, Retaining Wall Panel, Double-T Slab, Manhole Ring, Bridge Parapet] — infer best match
# # - grade: concrete grade, one of [C30, C40, C50] — default C40 if not specified
# # - volume_m3: total volume in cubic metres (number) — if given in loads or pieces try to infer
# # - delivery_date: ISO date string YYYY-MM-DD — if "today" use today's date, if "tomorrow" use tomorrow
# # - delivery_time: HH:MM string — default "08:00" if not specified
# # - county: Irish county for delivery (string, capitalise properly)
# # - notes: any special instructions (string, empty string if none)

# # If a field cannot be determined, use null.
# # """

# # def parse_order(api_key: str, raw_text: str, today_str: str) -> dict:
# #     """
# #     Takes a free-text order request and returns structured JSON.
# #     today_str is passed so the LLM can resolve 'today'/'tomorrow' correctly.
# #     """
# #     client = get_client(api_key)
# #     user_message = f"Today's date is {today_str}.\n\nParse this order request:\n\n{raw_text}"

# #     response = client.chat.completions.create(
# #         model="llama-3.3-70b-versatile",
# #         messages=[
# #             {"role": "system", "content": ORDER_PARSE_SYSTEM},
# #             {"role": "user",   "content": user_message}
# #         ],
# #         temperature=0.1,
# #         max_tokens=400
# #     )

# #     raw = response.choices[0].message.content.strip()
# #     raw = raw.replace("```json", "").replace("```", "").strip()
# #     return json.loads(raw)


# # # ─────────────────────────────────────────────
# # # 2. PLANT INTELLIGENCE COPILOT  (RAG-augmented)
# # # ─────────────────────────────────────────────

# # COPILOT_SYSTEM = """
# # You are the Plant Intelligence Assistant for Apex Precast, Athlone, Co. Westmeath.

# # You have access to TWO sources of information, both provided in the user message:

# # SOURCE 1 — LIVE PLANT DATA (JSON)
# # Current inventory levels, active orders, dispatch assignments, truck status, and QA summaries.
# # This data is live and specific to today's operation.

# # SOURCE 2 — COMPANY SPECIFICATION DOCUMENTS (document excerpts)
# # Apex Precast's own QA specification (QA-SPEC-001), mix design manuals, and operating procedures.
# # These define the official acceptance criteria: slump ranges by grade, W/C ratio limits, NCR triggers,
# # admixture protocols, and failure investigation procedures.

# # HOW TO USE BOTH SOURCES:
# # When a question involves specific batch data (e.g. "why is BCH-2025-2002 failing"), look up that
# # batch in the live data, find its QA readings (slump_mm, water_cement_ratio, 28day_strength_mpa),
# # and then compare those readings against the spec document criteria to explain precisely why it failed.

# # When a question involves thresholds or limits (e.g. "what's the slump range for C40"), answer
# # directly from the spec document — do not make up numbers.

# # When a question involves counts, totals, or rankings (e.g. "which truck has most deliveries"),
# # use the pre-computed values in the live data — never count items in a list yourself.

# # When a question asks how many batches can be produced for any grade (C30, C40, C50), the answer
# # is in the pre-computed "batch_capacity_by_grade" field. Read the "batches_possible" value and
# # "limiting_material" directly from there. Do NOT re-derive this from the raw consumption rates —
# # the Python computation is already done and guaranteed correct. Your job is to explain the answer
# # clearly, not to recalculate it.

# # RESPONSE STYLE:
# # Be concise and operational — the person asking is a plant manager, not a student.
# # Give the direct answer first, then the supporting detail.
# # Cite specific numbers (e.g. "slump was 252mm, limit is 180mm for C30 — that's a high slump failure").
# # Keep responses under 200 words unless the question genuinely needs a full breakdown.
# # Never say "I don't know" when the answer is in either source — look carefully in both.
# # """


# # def copilot_query(api_key: str, question: str, context: dict) -> str:
# #     """
# #     RAG-augmented copilot query.

# #     The retrieval step happens before the LLM call:
# #       1. Score all document chunks in data/docs/ against the question using TF-IDF
# #       2. Inject the top 3 most relevant chunks into the prompt as SOURCE 2
# #       3. The LLM then has both live plant data AND spec document context

# #     This means questions like "why is this batch failing" now get an answer that
# #     cites the actual Apex Precast spec threshold, not the model's generic knowledge.

# #     max_tokens is 1500 (up from 600) to prevent mid-sentence truncation on
# #     calculation-heavy answers like batch count breakdowns.
# #     """
# #     client = get_client(api_key)

# #     # ── RAG: retrieve relevant spec document chunks ───────────────────
# #     # retrieve_relevant_chunks returns an empty string if no chunks score
# #     # above the minimum threshold, so it's safe to always call it.
# #     relevant_docs = retrieve_relevant_chunks(question, DOCS_DIR, top_k=4)

# #     # ── Assemble the full prompt ──────────────────────────────────────
# #     context_str = json.dumps(context, indent=2, default=str)

# #     if relevant_docs:
# #         doc_section = f"""
# # ---
# # SOURCE 2 — COMPANY SPECIFICATION DOCUMENTS (retrieved for this question):
# # {relevant_docs}
# # ---"""
# #     else:
# #         doc_section = "\n(No specification documents were retrieved as relevant for this question.)"

# #     user_message = f"""
# # SOURCE 1 — LIVE PLANT DATA:
# # {context_str}
# # {doc_section}

# # Question from plant manager:
# # {question}
# # """

# #     response = client.chat.completions.create(
# #         model="llama-3.3-70b-versatile",
# #         messages=[
# #             {"role": "system", "content": COPILOT_SYSTEM},
# #             {"role": "user",   "content": user_message}
# #         ],
# #         temperature=0.3,
# #         max_tokens=1500   # Raised from 600 — prevents truncation on detailed answers
# #     )

# #     return response.choices[0].message.content.strip()

# """
# utils/llm_client.py — Groq-only LLM client

# Two functions, two models, no backend switching:
#   parse_order()    → llama-3.1-8b-instant  (500k tokens/day — simple extraction)
#   copilot_query()  → llama-3.3-70b-versatile (100k tokens/day — complex reasoning)
# """

# import json
# import os
# from pathlib import Path
# from groq import Groq

# try:
#     from .rag import retrieve_relevant_chunks
# except ImportError:
#     from utils.rag import retrieve_relevant_chunks

# try:
#     from .token_tracker import log_usage
# except ImportError:
#     try:
#         from utils.token_tracker import log_usage
#     except ImportError:
#         def log_usage(response, call_type="unknown"):
#             pass

# DOCS_DIR = str(Path(__file__).parent.parent / "data" / "docs")

# GROQ_MODEL_8B  = "llama-3.1-8b-instant"        # 500k TPD — used for parse_order
# GROQ_MODEL_70B = "llama-3.3-70b-versatile"      # 100k TPD — used for copilot_query


# # ─────────────────────────────────────────────────────────────────────────────
# # ORDER PARSER  — uses 8B model
# # ─────────────────────────────────────────────────────────────────────────────

# ORDER_PARSE_SYSTEM = """
# You are an order intake assistant for Apex Precast, an Irish precast concrete manufacturer.
# Extract structured order information from plain English requests.

# Always respond with valid JSON and nothing else. No markdown, no explanation, no preamble.

# Extract these fields:
# - customer: company or site name (string)
# - product_type: one of [Hollow-Core Slab, Precast Beam (I-Section), Box Culvert, Precast Column, Retaining Wall Panel, Double-T Slab, Manhole Ring, Bridge Parapet]
# - grade: one of [C30, C40, C50] — default C40 if not specified
# - volume_m3: total volume in cubic metres (number)
# - delivery_date: ISO date YYYY-MM-DD — resolve "today", "tomorrow", "Thursday" etc.
# - delivery_time: HH:MM — default "08:00" if not specified
# - county: Irish county for delivery
# - notes: special instructions (empty string if none)

# Use null for any field that cannot be determined.
# """


# def parse_order(api_key: str, raw_text: str, today_str: str) -> dict:
#     """
#     Parses a free-text order into structured JSON.
#     Uses llama-3.1-8b-instant — 500k tokens/day, same accuracy as 70B for extraction.
#     """
#     client = Groq(api_key=api_key)

#     response = client.chat.completions.create(
#         model=GROQ_MODEL_8B,
#         messages=[
#             {"role": "system", "content": ORDER_PARSE_SYSTEM},
#             {"role": "user",   "content": f"Today is {today_str}.\n\nParse this order:\n\n{raw_text}"}
#         ],
#         temperature=0.1,
#         max_tokens=400,
#     )

#     log_usage(response, call_type="parse_order")

#     content = response.choices[0].message.content.strip()
#     content = content.replace("```json", "").replace("```", "").strip()
#     # Some models add preamble before the JSON — trim to the first {
#     brace = content.find("{")
#     if brace > 0:
#         content = content[brace:]

#     return json.loads(content)


# # ─────────────────────────────────────────────────────────────────────────────
# # PLANT INTELLIGENCE COPILOT  — uses 70B model
# # ─────────────────────────────────────────────────────────────────────────────

# COPILOT_SYSTEM = """
# You are the Plant Intelligence Assistant for Apex Precast, Athlone, Co. Westmeath.

# You have TWO sources of information in the user message:

# SOURCE 1 — LIVE PLANT DATA (JSON): inventory, orders, dispatch, trucks, QA.
# SOURCE 2 — COMPANY SPEC DOCUMENTS: Apex Precast QA specification derived from EN 206-1.

# RULES:
# - For batch failure questions: find the batch in live data, read its slump_mm and
#   water_cement_ratio, compare against the spec document thresholds, explain precisely.
# - For threshold/limit questions: answer from the spec document, cite the exact number.
# - For counts and rankings: use the pre-computed values in the data, never count lists yourself.
# - For batch capacity questions: read batches_possible and limiting_material from
#   batch_capacity_by_grade — do NOT recalculate from raw rates.

# STYLE: Direct answer first, then supporting detail. Under 200 words unless a full
# breakdown is genuinely needed. Cite specific numbers.
# """


# def copilot_query(api_key: str, question: str, context: dict) -> str:
#     """
#     RAG-augmented plant copilot.
#     Uses llama-3.3-70b-versatile — 100k tokens/day, needed for complex reasoning.
#     """
#     client = Groq(api_key=api_key)

#     relevant_docs = retrieve_relevant_chunks(question, DOCS_DIR, top_k=4)
#     context_str   = json.dumps(context, indent=2, default=str)

#     doc_section = (
#         f"\n---\nSOURCE 2 — COMPANY SPEC DOCUMENTS:\n{relevant_docs}\n---"
#         if relevant_docs
#         else "\n(No spec documents retrieved for this question.)"
#     )

#     response = client.chat.completions.create(
#         model=GROQ_MODEL_70B,
#         messages=[
#             {"role": "system", "content": COPILOT_SYSTEM},
#             {"role": "user",   "content":
#                 f"SOURCE 1 — LIVE PLANT DATA:\n{context_str}"
#                 f"{doc_section}"
#                 f"\n\nQuestion: {question}"}
#         ],
#         temperature=0.3,
#         max_tokens=1500,
#     )

#     log_usage(response, call_type="copilot_query")
#     return response.choices[0].message.content.strip()

"""
utils/llm_client.py — Groq-only LLM client

Two functions, two models, no backend switching:
  parse_order()    → llama-3.1-8b-instant  (500k tokens/day — simple extraction)
  copilot_query()  → llama-3.3-70b-versatile (100k tokens/day — complex reasoning)
"""

import json
import os
from pathlib import Path
from groq import Groq

try:
    from .rag import retrieve_relevant_chunks
except ImportError:
    from utils.rag import retrieve_relevant_chunks

try:
    from .token_tracker import log_usage
except ImportError:
    try:
        from utils.token_tracker import log_usage
    except ImportError:
        def log_usage(response, call_type="unknown"):
            pass

DOCS_DIR = str(Path(__file__).parent.parent / "data" / "docs")

GROQ_MODEL_8B  = "llama-3.1-8b-instant"        # 500k TPD — used for parse_order
GROQ_MODEL_70B = "llama-3.3-70b-versatile"      # 100k TPD — used for copilot_query


# ─────────────────────────────────────────────────────────────────────────────
# ORDER PARSER  — uses 8B model
# ─────────────────────────────────────────────────────────────────────────────

ORDER_PARSE_SYSTEM = """
You are an order intake assistant for Apex Precast, an Irish precast concrete manufacturer.
Extract structured order information from plain English requests.

Always respond with valid JSON and nothing else. No markdown, no explanation, no preamble.

Extract these fields:
- customer: company or site name (string)
- product_type: one of [Hollow-Core Slab, Precast Beam (I-Section), Box Culvert, Precast Column, Retaining Wall Panel, Double-T Slab, Manhole Ring, Bridge Parapet]
- grade: one of [C30, C40, C50] — default C40 if not specified
- volume_m3: total volume in cubic metres (number)
- delivery_date: ISO date YYYY-MM-DD — resolve day names using the "Upcoming named days" lookup table provided in the date context. NEVER calculate dates yourself. Simply look up the day name in the table and copy the corresponding YYYY-MM-DD value exactly.
- delivery_time: HH:MM — default "08:00" if not specified
- county: Irish county for delivery
- notes: special instructions (empty string if none)

Use null for any field that cannot be determined.
"""


def parse_order(api_key: str, raw_text: str, today_str: str) -> dict:
    """
    Parses a free-text order into structured JSON.
    Uses llama-3.1-8b-instant — 500k tokens/day, same accuracy as 70B for extraction.
    """
    client = Groq(api_key=api_key)

    response = client.chat.completions.create(
        model=GROQ_MODEL_8B,
        messages=[
            {"role": "system", "content": ORDER_PARSE_SYSTEM},
            {"role": "user",   "content": f"{today_str}\n\nParse this order:\n\n{raw_text}"}
        ],
        temperature=0.1,
        max_tokens=400,
    )

    log_usage(response, call_type="parse_order")

    content = response.choices[0].message.content.strip()
    content = content.replace("```json", "").replace("```", "").strip()
    # Some models add preamble before the JSON — trim to the first {
    brace = content.find("{")
    if brace > 0:
        content = content[brace:]

    return json.loads(content)


# ─────────────────────────────────────────────────────────────────────────────
# PLANT INTELLIGENCE COPILOT  — uses 70B model
# ─────────────────────────────────────────────────────────────────────────────

COPILOT_SYSTEM = """
You are the Plant Intelligence Assistant for Apex Precast, Athlone, Co. Westmeath.

You have TWO sources of information in the user message:

SOURCE 1 — LIVE PLANT DATA (JSON): inventory, orders, dispatch, trucks, QA.
SOURCE 2 — COMPANY SPEC DOCUMENTS: Apex Precast QA specification derived from EN 206-1.

RULES:
- For batch failure questions: find the batch in live data, read its slump_mm and
  water_cement_ratio, compare against the spec document thresholds, explain precisely.
- For threshold/limit questions: answer from the spec document, cite the exact number.
- For counts and rankings: use the pre-computed values in the data, never count lists yourself.
- For batch capacity questions: read batches_possible and limiting_material from
  batch_capacity_by_grade — do NOT recalculate from raw rates.
- For slump risk questions: read directly from slump_risk_summary in the live data.
  Never scan dispatch_assignments yourself to find slump risk — the pre-computed
  summary is guaranteed complete, whereas scanning a list yourself is not.

STYLE: Direct answer first, then supporting detail. Under 200 words unless a full
breakdown is genuinely needed. Cite specific numbers.
"""


def copilot_query(api_key: str, question: str, context: dict) -> str:
    """
    RAG-augmented plant copilot.
    Uses llama-3.3-70b-versatile — 100k tokens/day, needed for complex reasoning.
    """
    client = Groq(api_key=api_key)

    relevant_docs = retrieve_relevant_chunks(question, DOCS_DIR, top_k=2)
    context_str   = json.dumps(context, indent=2, default=str)

    doc_section = (
        f"\n---\nSOURCE 2 — COMPANY SPEC DOCUMENTS:\n{relevant_docs}\n---"
        if relevant_docs
        else "\n(No spec documents retrieved for this question.)"
    )

    response = client.chat.completions.create(
        model=GROQ_MODEL_8B,
        messages=[
            {"role": "system", "content": COPILOT_SYSTEM},
            {"role": "user",   "content":
                f"SOURCE 1 — LIVE PLANT DATA:\n{context_str}"
                f"{doc_section}"
                f"\n\nQuestion: {question}"}
        ],
        temperature=0.3,
        max_tokens=1500,
    )

    log_usage(response, call_type="copilot_query")
    return response.choices[0].message.content.strip()

