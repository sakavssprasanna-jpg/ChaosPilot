import math
import re
from typing import Dict, List, Optional, Set, Tuple

from rag.document_indexer import DocumentIndexer
from rag.models import DocumentChunk, EvidenceCitation

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "he", "in", "is", "it", "its", "of", "on", "that", "the",
    "to", "was", "were", "will", "with"
}

def tokenize(text: str) -> List[str]:
    """Tokenizes and normalizes text into alphanumeric words, ignoring common stopwords."""
    words = re.findall(r"\b[a-zA-Z0-9_\-\.]+\b", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]

class BM25Retriever:
    """Robust local lexical retriever implementing BM25 ranking, phrase boosting, and metadata preservation."""

    def __init__(self, indexer: Optional[DocumentIndexer] = None, k1: float = 1.5, b: float = 0.75):
        self.indexer = indexer or DocumentIndexer()
        self.k1 = k1
        self.b = b
        self._chunks: List[DocumentChunk] = []
        self._doc_lens: List[int] = []
        self._avg_doc_len: float = 0.0
        self._doc_freqs: Dict[str, int] = {}
        self._term_freqs: List[Dict[str, int]] = []
        self.refresh()

    def refresh(self):
        """Builds or refreshes term index and inverted document frequencies."""
        self._chunks = self.indexer.get_chunks()
        if not self._chunks:
            return

        self._doc_lens = []
        self._term_freqs = []
        self._doc_freqs = {}

        total_len = 0
        for chunk in self._chunks:
            combined_text = f"{chunk.section} {chunk.text}"
            tokens = tokenize(combined_text)
            doc_len = len(tokens)
            self._doc_lens.append(doc_len)
            total_len += doc_len

            tf: Dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self._term_freqs.append(tf)

            for unique_token in tf.keys():
                self._doc_freqs[unique_token] = self._doc_freqs.get(unique_token, 0) + 1

        self._avg_doc_len = total_len / len(self._chunks) if self._chunks else 1.0

    def _idf(self, term: str) -> float:
        n = len(self._chunks)
        df = self._doc_freqs.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        source_type: Optional[str] = None
    ) -> List[EvidenceCitation]:
        """Retrieves top-k evidence citations ranked by BM25 relevance score."""
        if not self._chunks:
            self.refresh()

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        clean_query = query.lower().strip()
        scored_results: List[Tuple[float, DocumentChunk]] = []

        for idx, chunk in enumerate(self._chunks):
            if source_type and chunk.source_type != source_type:
                continue

            tf_map = self._term_freqs[idx]
            doc_len = self._doc_lens[idx]
            bm25_score = 0.0

            for q_term in query_tokens:
                if q_term not in tf_map:
                    continue
                tf = tf_map[q_term]
                idf = self._idf(q_term)
                num = tf * (self.k1 + 1.0)
                denom = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / self._avg_doc_len))
                bm25_score += idf * (num / denom)

            if bm25_score <= 0.0:
                continue

            # Heading match boost
            section_tokens = set(tokenize(chunk.section))
            heading_matches = sum(1 for qt in query_tokens if qt in section_tokens)
            if heading_matches > 0:
                bm25_score += heading_matches * 1.2

            # Exact phrase match boost
            chunk_lower = chunk.text.lower()
            if clean_query in chunk_lower or clean_query in chunk.section.lower():
                bm25_score *= 1.5

            scored_results.append((bm25_score, chunk))

        # Sort descending by score
        scored_results.sort(key=lambda x: x[0], reverse=True)

        if not scored_results:
            return []

        # Normalize top score to scale ~0.0 to 1.0
        max_score = scored_results[0][0]
        citations: List[EvidenceCitation] = []
        for raw_score, chunk in scored_results[:top_k]:
            normalized_score = round(min(1.0, raw_score / (max_score + 1e-6)), 4)
            # Short preview snippet
            snippet = chunk.text.strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."

            citations.append(EvidenceCitation(
                source_type=chunk.source_type,
                document=chunk.document,
                section=chunk.section,
                chunk_id=chunk.chunk_id,
                score=normalized_score,
                text_snippet=snippet
            ))

        return citations

    def retrieve_multi(self, queries: List[str], top_k: int = 5) -> List[EvidenceCitation]:
        """Issues multiple queries, merges and deduplicates results with frequency reinforcement."""
        aggregated: Dict[str, Tuple[EvidenceCitation, int, float]] = {}

        for q in queries:
            results = self.retrieve(q, top_k=top_k * 2)
            for item in results:
                cid = item.chunk_id
                if cid not in aggregated:
                    aggregated[cid] = (item, 1, item.score)
                else:
                    prev_item, count, max_score = aggregated[cid]
                    new_score = max(max_score, item.score) + 0.05
                    aggregated[cid] = (prev_item, count + 1, min(1.0, new_score))

        # Rank by aggregated score
        ranked = sorted(aggregated.values(), key=lambda x: x[2], reverse=True)

        final_citations: List[EvidenceCitation] = []
        for item, count, score in ranked[:top_k]:
            item.score = round(score, 4)
            final_citations.append(item)

        return final_citations

# Global singleton helper
_default_retriever: Optional[BM25Retriever] = None

def get_retriever() -> BM25Retriever:
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = BM25Retriever()
    return _default_retriever

def retrieve(query: str, top_k: int = 5, source_type: Optional[str] = None) -> List[EvidenceCitation]:
    """Top-level convenience function for keyword/lexical retrieval."""
    return get_retriever().retrieve(query, top_k=top_k, source_type=source_type)

def retrieve_multi(queries: List[str], top_k: int = 5) -> List[EvidenceCitation]:
    """Top-level convenience function for multi-query retrieval."""
    return get_retriever().retrieve_multi(queries, top_k=top_k)
