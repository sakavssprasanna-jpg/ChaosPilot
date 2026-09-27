from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

@dataclass
class DocumentChunk:
    chunk_id: str
    document: str
    section: str
    text: str
    source_type: str  # 'runbook', 'architecture', 'postmortem', 'doc'
    token_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class EvidenceCitation:
    source_type: str
    document: str
    section: str
    chunk_id: str
    score: float
    text_snippet: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class GitEvidenceCitation:
    source_type: str
    commit: str
    author: str
    date: str
    message: str
    files_changed: List[str]
    diff: str
    relevance: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class EvidenceBundle:
    query: str
    documents: List[EvidenceCitation] = field(default_factory=list)
    git_evidence: List[GitEvidenceCitation] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "documents": [d.to_dict() for d in self.documents],
            "git_evidence": [g.to_dict() for g in self.git_evidence],
            "sources": self.sources,
            "timestamp": self.timestamp
        }
