"""RAG knowledge retrieval.

Primary: Gemini multilingual embeddings (works for Hindi/Telugu questions over English docs).
Fallback: local TF-IDF (no API needed) so the agent never loses its knowledge base.
"""
import hashlib
import json
import re

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import llm
from .config import EMBED_CACHE, GEMINI_EMBED_MODEL, KNOWLEDGE_DIR


class KnowledgeBase:
    def __init__(self):
        self.chunks = []      # [{"id", "source", "title", "text"}]
        self.tfidf = None
        self.tfidf_matrix = None
        self.embeddings = None
        self.mode = "tfidf"

    # ----- indexing -----
    def load(self):
        self.chunks = []
        for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
            self.chunks.extend(self._split(path.name, path.read_text(encoding="utf-8")))
        texts = [f"{c['title']}. {c['text']}" for c in self.chunks]
        self.tfidf = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True)
        self.tfidf_matrix = self.tfidf.fit_transform(texts)
        self.embeddings = self._load_or_build_embeddings(texts)
        self.mode = "gemini-embeddings" if self.embeddings is not None else "tfidf"
        print(f"[RAG] {len(self.chunks)} chunks indexed, mode={self.mode}")

    @staticmethod
    def _split(source, text):
        """Split markdown by '## ' headings; each section is one chunk."""
        out = []
        for i, section in enumerate(re.split(r"\n(?=## )", text)):
            lines = section.strip().splitlines()
            if not lines:
                continue
            title = lines[0].lstrip("# ").strip()
            body = " ".join(l.strip() for l in lines[1:] if l.strip())
            if body:
                out.append({"id": f"{source}#{i}", "source": source, "title": title, "text": body})
        return out

    def _load_or_build_embeddings(self, texts):
        if not llm.available():
            return None
        key = hashlib.md5((GEMINI_EMBED_MODEL + "||".join(texts)).encode()).hexdigest()
        try:
            if EMBED_CACHE.exists():
                cache = json.loads(EMBED_CACHE.read_text())
                if cache.get("key") == key:
                    return np.array(cache["vectors"])
            vectors = llm.embed(texts, task="RETRIEVAL_DOCUMENT")
            EMBED_CACHE.write_text(json.dumps({"key": key, "vectors": vectors}))
            return np.array(vectors)
        except Exception as e:  # rate limit / network: degrade gracefully
            print(f"[RAG] embeddings unavailable ({e.__class__.__name__}); using TF-IDF")
            return None

    # ----- retrieval -----
    def search(self, query, k=3):
        if not query.strip() or not self.chunks:
            return []
        scores = None
        if self.embeddings is not None:
            try:
                q = np.array(llm.embed([query], task="RETRIEVAL_QUERY")[0])
                scores = self.embeddings @ q / (np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(q) + 1e-9)
            except Exception:
                scores = None
        if scores is None:
            scores = cosine_similarity(self.tfidf.transform([query]), self.tfidf_matrix)[0]
            if scores.max() < 0.05:  # e.g. non-English query in TF-IDF mode -> give overview chunks
                return [dict(c, score=0.0) for c in self.chunks[:k]]
        top = np.argsort(scores)[::-1][:k]
        return [dict(self.chunks[i], score=round(float(scores[i]), 3)) for i in top]


kb = KnowledgeBase()
