"""ChaosPilot Local Document Retrieval and Evidence Package."""
from rag.models import DocumentChunk, EvidenceCitation, GitEvidenceCitation, EvidenceBundle
from rag.document_indexer import DocumentIndexer
from rag.retriever import retrieve, retrieve_multi, BM25Retriever
from rag.coordinator import EvidenceCoordinator, coordinate_evidence, diagnose_evidence

__all__ = [
    "DocumentChunk",
    "EvidenceCitation",
    "GitEvidenceCitation",
    "EvidenceBundle",
    "DocumentIndexer",
    "retrieve",
    "retrieve_multi",
    "BM25Retriever",
    "EvidenceCoordinator",
    "coordinate_evidence",
    "diagnose_evidence"
]
