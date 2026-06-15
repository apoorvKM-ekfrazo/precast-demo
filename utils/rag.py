"""
utils/rag.py — Lightweight Document Retrieval for the Plant Intelligence Copilot

Why no ChromaDB or sentence-transformers here?
-----------------------------------------------
For a demo/pilot with a handful of company documents, a full vector database is
architectural overkill and adds significant setup friction (model downloads, memory
overhead, first-run latency). Instead we use a TF-IDF inspired scoring approach:

  TF (Term Frequency)   = how often does a query word appear in this chunk?
  IDF (Inverse Doc Freq) = how rare is this word across all chunks? (rare = more informative)
  Score = sum of TF × IDF for each query word in each chunk

This is exactly how search engines worked before neural embeddings, and for a domain
with consistent terminology (slump, W/C ratio, NCR, C40, batch ID) it works very well
because the vocabulary is specialised and non-ambiguous.

When to upgrade to ChromaDB/embeddings:
  - When the document corpus grows beyond ~50 pages
  - When users ask paraphrase-heavy questions (e.g. "concrete too runny" instead of "high slump")
  - When cross-document synthesis becomes important

For now this is the right tool: zero extra dependencies, <50ms retrieval, explainable results.
"""

import math
import re
from pathlib import Path
from typing import List, Tuple, Dict


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT LOADING AND CHUNKING
# ─────────────────────────────────────────────────────────────────────────────

def _tokenise(text: str) -> List[str]:
    """
    Lowercase and split text into tokens.  We keep numbers (critical for
    concrete specs: "40 MPa", "0.45", "90 minutes") and strip punctuation.
    """
    text = text.lower()
    tokens = re.findall(r'\b[\w.]+\b', text)
    return tokens


def load_and_chunk_documents(docs_dir: str, chunk_size: int = 10) -> List[Dict]:
    """
    Loads all .txt files from docs_dir and splits them into overlapping chunks.

    Why chunks of ~15 lines?  Too large (the whole document) and the relevant
    sentence gets diluted by irrelevant text, hurting precision.  Too small
    (1 sentence) and we lose the surrounding context that makes an answer useful.
    15 lines (roughly 200-300 words) gives the LLM enough context to form a
    complete, grounded answer.

    We use 3-line overlap between chunks so that sentences near a chunk boundary
    don't get split and lose their context.
    """
    chunks = []
    docs_path = Path(docs_dir)

    for filepath in sorted(docs_path.glob("*.txt")):
        lines = filepath.read_text(encoding="utf-8").splitlines()
        # Remove pure separator lines (===, ---, empty)
        lines = [l for l in lines if l.strip() and not set(l.strip()) <= {"=", "-", "~"}]

        step    = chunk_size - 2   # 2-line overlap (smaller chunks need less overlap)
        for i in range(0, len(lines), step):
            chunk_lines = lines[i : i + chunk_size]
            chunk_text  = "\n".join(chunk_lines).strip()
            if len(chunk_text) < 50:   # skip tiny trailing chunks
                continue
            chunks.append({
                "id":     f"{filepath.stem}:{i}",
                "source": filepath.name,
                "text":   chunk_text,
                "tokens": _tokenise(chunk_text),
            })

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# IDF COMPUTATION  (computed once over the corpus)
# ─────────────────────────────────────────────────────────────────────────────

def compute_idf(chunks: List[Dict]) -> Dict[str, float]:
    """
    For each unique token, compute log(N / df) where:
      N  = total number of chunks
      df = number of chunks containing this token

    Rare tokens (appear in few chunks) get a high IDF and dominate scoring.
    Common tokens like 'the', 'is', 'for' appear everywhere and get near-zero IDF.
    This is why we don't need a stopword list — IDF handles it naturally.
    """
    N  = len(chunks)
    df: Dict[str, int] = {}

    for chunk in chunks:
        for token in set(chunk["tokens"]):  # set() to count each token once per chunk
            df[token] = df.get(token, 0) + 1

    idf = {token: math.log((N + 1) / (count + 1)) for token, count in df.items()}
    return idf


# ─────────────────────────────────────────────────────────────────────────────
# SCORING AND RETRIEVAL
# ─────────────────────────────────────────────────────────────────────────────

def score_chunk(query_tokens: List[str], chunk: Dict, idf: Dict[str, float]) -> float:
    """
    TF-IDF score: for each query token present in the chunk, add TF × IDF.

    TF is normalised by chunk length to avoid favouring longer chunks that simply
    contain more words (and therefore more query-word matches by chance).
    """
    chunk_len     = len(chunk["tokens"]) or 1
    token_counts  = {}
    for t in chunk["tokens"]:
        token_counts[t] = token_counts.get(t, 0) + 1

    score = 0.0
    for qt in query_tokens:
        if qt in token_counts:
            tf    = token_counts[qt] / chunk_len   # normalised TF
            score += tf * idf.get(qt, 0.0)         # TF × IDF contribution

    return score


def retrieve_relevant_chunks(
    query: str,
    docs_dir: str,
    top_k: int = 3,
    min_score: float = 0.001,
) -> str:
    """
    Main retrieval function called from llm_client.py.

    Takes the plant manager's question, scores all document chunks against it,
    and returns the top_k most relevant chunks formatted as a single string
    ready to inject into the LLM prompt.

    Returns an empty string if no chunks score above min_score (this prevents
    the LLM from seeing an empty or irrelevant context block).
    """
    chunks = load_and_chunk_documents(docs_dir)
    if not chunks:
        return ""

    idf           = compute_idf(chunks)
    query_tokens  = _tokenise(query)

    scored = [
        (score_chunk(query_tokens, chunk, idf), chunk)
        for chunk in chunks
    ]
    scored.sort(key=lambda x: x[0], reverse=True)

    # Filter out chunks that didn't match any query terms meaningfully
    top_chunks = [(s, c) for s, c in scored[:top_k] if s >= min_score]

    if not top_chunks:
        return ""

    parts = []
    for rank, (score, chunk) in enumerate(top_chunks, 1):
        parts.append(
            f"[Document excerpt {rank} — Source: {chunk['source']}]\n{chunk['text']}"
        )

    return "\n\n".join(parts)
