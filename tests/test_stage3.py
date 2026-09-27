import json
import os
from pathlib import Path
import pytest

from git_ops.diff_analyzer import GitDiffAnalyzer, get_git_analyzer
from rag.coordinator import EvidenceCoordinator, coordinate_evidence, diagnose_evidence
from rag.document_indexer import DocumentIndexer
from rag.models import DocumentChunk, EvidenceBundle, EvidenceCitation, GitEvidenceCitation
from rag.retriever import BM25Retriever, retrieve, retrieve_multi
from services.config import BASE_DIR, LOGS_DIR

def test_knowledge_documents_discovered():
    """Verify all real markdown knowledge documents under knowledge/ are discovered and indexed."""
    indexer = DocumentIndexer()
    chunks = indexer.index_all()

    assert len(chunks) >= 10, f"Expected at least 10 chunks, got {len(chunks)}"

    indexed_docs = {c.document.replace("\\", "/") for c in chunks}
    assert "knowledge/runbooks/payment_timeout.md" in indexed_docs
    assert "knowledge/runbooks/gateway_504.md" in indexed_docs
    assert "knowledge/architecture/service_topology.md" in indexed_docs
    assert "knowledge/architecture/payment_service.md" in indexed_docs
    assert "knowledge/postmortems/payment_timeout_incident.md" in indexed_docs

def test_chunks_preserve_source_metadata():
    """Verify every chunk preserves document path, section heading, chunk ID, and source type."""
    indexer = DocumentIndexer()
    chunks = indexer.get_chunks()

    valid_source_types = {"runbook", "architecture", "postmortem", "doc"}

    for chunk in chunks:
        assert isinstance(chunk.chunk_id, str) and len(chunk.chunk_id) > 3
        assert chunk.document.startswith("knowledge/")
        assert isinstance(chunk.section, str) and len(chunk.section) > 0
        assert chunk.source_type in valid_source_types
        assert isinstance(chunk.text, str) and len(chunk.text.strip()) > 0
        assert chunk.token_count > 0

def test_no_unsupported_postgresql_claims_in_knowledge_base():
    """Verify that knowledge documents do not fabricate claims about PostgreSQL, max_conn, or pool starvation."""
    indexer = DocumentIndexer()
    chunks = indexer.get_chunks()

    forbidden_patterns = [
        "postgresql",
        "max_conn=20",
        "max_conn",
        "database connection pool starvation",
        "database connection pool exhaustion"
    ]

    for chunk in chunks:
        chunk_lower = chunk.text.lower()
        for forbidden in forbidden_patterns:
            assert forbidden not in chunk_lower, (
                f"Unsupported claim '{forbidden}' detected in {chunk.document} [{chunk.section}]"
            )

def test_retrieval_returns_relevant_payment_timeout_evidence():
    """Verify lexical retrieval returns payment timeout runbook as top evidence for timeout queries."""
    results = retrieve("payment timeout 504 gateway", top_k=3)

    assert len(results) > 0
    top_result = results[0]
    assert isinstance(top_result, EvidenceCitation)
    assert top_result.score > 0.0

    # Top result should be from payment_timeout.md or gateway_504.md
    assert "payment_timeout.md" in top_result.document or "gateway_504.md" in top_result.document
    assert top_result.section in [
        "Overview", "Failure Signature & Symptoms",
        "Root Cause Diagnosis", "Safe Remediation Procedure",
        "Timeout Budget & SLA"
    ]

def test_ranking_is_deterministic():
    """Verify ranking produces identical scores and chunk order across repeated executions."""
    retriever = BM25Retriever()
    query = "controlled artificial delay timeout failure"

    run1 = retriever.retrieve(query, top_k=5)
    run2 = retriever.retrieve(query, top_k=5)

    assert len(run1) == len(run2)
    for c1, c2 in zip(run1, run2):
        assert c1.chunk_id == c2.chunk_id
        assert c1.score == c2.score
        assert c1.section == c2.section

def test_duplicate_chunks_are_removed_in_multi_query():
    """Verify multi-query retrieval merges and deduplicates chunks by chunk_id."""
    queries = [
        "payment service timeout",
        "order service payment timeout 504",
        "payment failure recovery procedure"
    ]
    results = retrieve_multi(queries, top_k=6)

    chunk_ids = [c.chunk_id for c in results]
    assert len(chunk_ids) == len(set(chunk_ids)), "Duplicate chunk IDs found in multi-query results"

def test_multiple_diagnostic_queries_work():
    """Verify multiple diagnostic queries combine evidence from different operational areas."""
    queries = [
        "gateway 504 upstream timeout",
        "payment service delay timeout"
    ]
    results = retrieve_multi(queries, top_k=5)

    assert len(results) >= 2
    docs_retrieved = {r.document for r in results}
    assert len(docs_retrieved) >= 2

def test_git_root_resolves_to_chaospilot_workspace():
    """Verify GitDiffAnalyzer resolves explicitly to ChaosPilot root and never uses parent repository."""
    analyzer = GitDiffAnalyzer()
    assert analyzer.available is True
    assert analyzer.repo is not None

    working_tree = Path(analyzer.repo.working_tree_dir).resolve()
    expected_root = Path(BASE_DIR).resolve()
    assert working_tree == expected_root
    assert working_tree.name == "RAG"
    assert "VAJAYA" != working_tree.name  # Ensure it is not the user home directory

def test_git_analyzer_refuses_outside_repositories():
    """Verify GitDiffAnalyzer strictly rejects repository roots outside ChaosPilot workspace."""
    parent_dir = Path(BASE_DIR).parent.resolve()
    analyzer = GitDiffAnalyzer(repo_path=parent_dir)
    # Must refuse parent repo
    assert analyzer.available is False
    assert analyzer.repo is None

def test_unrelated_parent_commits_cannot_appear():
    """Verify only ChaosPilot commits exist in project history; no SheetPilot or Code Mentor commits."""
    analyzer = GitDiffAnalyzer()
    assert analyzer.available is True

    commits = analyzer.get_recent_commits(limit=10)
    assert len(commits) > 0

    for c in commits:
        msg = c["message"].lower()
        assert "sheetpilot" not in msg
        assert "code mentor" not in msg
        # Commit should reflect real ChaosPilot evolutionary work
        assert any(k in msg for k in ["chaospilot", "telemetry", "incident", "target environment", "retrieval"])

def test_empty_or_relevant_git_evidence_handled_correctly():
    """Verify Git term search does not manufacture relevance and returns empty list when no terms match."""
    analyzer = GitDiffAnalyzer()
    assert analyzer.available is True

    # 1. Nonexistent terms must return empty list (no manufactured relevance)
    empty_results = analyzer.search_commits(["tachyon", "warp", "fluxcapacitor"], limit=5)
    assert len(empty_results) == 0

    # 2. Relevant term present in ChaosPilot commits
    matched = analyzer.search_commits(["telemetry"], limit=5)
    assert len(matched) > 0
    assert any("telemetry" in m.message.lower() for m in matched)

def test_incident_snapshot_generates_diagnostic_queries():
    """Verify diagnose_evidence inspects real incident snapshot and produces relevant diagnostic citations."""
    sample_snapshot = {
        "timestamp": 1790508943.0,
        "timestamp_iso": "2026-09-27T11:35:43.084472+00:00",
        "overall_status": "degraded",
        "service_health": {
            "payment-service": {
                "status": "degraded",
                "details": {"fault_active": True, "fault_mode": "timeout"}
            },
            "order-service": {"status": "healthy"},
            "gateway": {"status": "healthy"}
        },
        "metrics_summary": {
            "gateway": {
                "status_distribution": {"200": 5, "504": 5},
                "timeout_count": 5,
                "error_rate": 0.5
            }
        },
        "fault_status": {
            "active": True,
            "mode": "timeout",
            "description": "Controlled payment-processing timeout fault"
        }
    }

    bundle = diagnose_evidence(sample_snapshot)
    assert isinstance(bundle, EvidenceBundle)
    assert len(bundle.documents) > 0
    assert any("payment_timeout.md" in d.document for d in bundle.documents)
    assert len(bundle.sources) > 0

def test_evidence_bundle_does_not_fabricate_git_root_cause():
    """Verify that evidence bundle does not claim a Git commit caused the fault when no commits match."""
    sample_snapshot = {
        "timestamp": 1790508943.0,
        "overall_status": "degraded",
        "service_health": {
            "payment-service": {
                "status": "degraded",
                "details": {"fault_active": True, "fault_mode": "timeout"}
            }
        },
        "metrics_summary": {"gateway": {"timeout_count": 5}},
        "fault_status": {
            "active": True,
            "mode": "timeout",
            "description": "Controlled payment-processing timeout fault"
        }
    }

    bundle = diagnose_evidence(sample_snapshot)
    assert isinstance(bundle, EvidenceBundle)
    # Document evidence is found
    assert len(bundle.documents) > 0
    # No false git causation: if git commits don't match the specific fault mode, no false citation is forced
    for g in bundle.git_evidence:
        assert g.relevance > 0.0

def test_nonexistent_evidence_not_fabricated():
    """Verify that querying completely unrelated/fictional concepts returns zero fabricated matches."""
    retriever = BM25Retriever()
    results = retriever.retrieve("antimatter flux capacitor hyperspace warp tachyon drive", top_k=5)
    assert len(results) == 0, "Retriever fabricated evidence for non-existent concept!"
