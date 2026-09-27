import hashlib
import os
from pathlib import Path
import re
from typing import Dict, List, Optional

from rag.models import DocumentChunk
from services.config import BASE_DIR

KNOWLEDGE_DIR = BASE_DIR / "knowledge"

def slugify(text: str) -> str:
    """Converts a string to a clean deterministic slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_-]+", "_", text)

def detect_source_type(path_str: str) -> str:
    """Classifies document type based on directory path."""
    normalized = path_str.replace("\\", "/").lower()
    if "runbooks" in normalized:
        return "runbook"
    elif "architecture" in normalized:
        return "architecture"
    elif "postmortems" in normalized:
        return "postmortem"
    return "doc"

class DocumentIndexer:
    """Discovers, parses, and indexes Markdown knowledge documents into structured chunks."""

    def __init__(self, knowledge_dir: Optional[Path] = None):
        self.knowledge_dir = knowledge_dir or KNOWLEDGE_DIR
        self._chunks: List[DocumentChunk] = []

    def parse_markdown_file(self, file_path: Path) -> List[DocumentChunk]:
        """Parses a markdown document into section-based chunks preserving headings."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return []

        rel_path = file_path.relative_to(BASE_DIR).as_posix()
        source_type = detect_source_type(rel_path)
        doc_slug = slugify(file_path.stem)

        chunks: List[DocumentChunk] = []
        lines = content.split("\n")
        
        current_section = "Introduction"
        current_lines: List[str] = []
        chunk_idx = 0

        for line in lines:
            # Check for header
            header_match = re.match(r"^(#{1,3})\s+(.*)$", line)
            if header_match:
                # Flush previous section chunk if non-empty
                if current_lines:
                    text_block = "\n".join(current_lines).strip()
                    if text_block:
                        chunk_id = f"{doc_slug}::{slugify(current_section)}::{chunk_idx}"
                        chunks.append(DocumentChunk(
                            chunk_id=chunk_id,
                            document=rel_path,
                            section=current_section,
                            text=text_block,
                            source_type=source_type,
                            token_count=len(text_block.split())
                        ))
                        chunk_idx += 1
                    current_lines = []
                current_section = header_match.group(2).strip()
            else:
                current_lines.append(line)

        # Flush final section
        if current_lines:
            text_block = "\n".join(current_lines).strip()
            if text_block:
                chunk_id = f"{doc_slug}::{slugify(current_section)}::{chunk_idx}"
                chunks.append(DocumentChunk(
                    chunk_id=chunk_id,
                    document=rel_path,
                    section=current_section,
                    text=text_block,
                    source_type=source_type,
                    token_count=len(text_block.split())
                ))

        return chunks

    def index_all(self) -> List[DocumentChunk]:
        """Scans the knowledge directory and indexes all Markdown files."""
        if not self.knowledge_dir.exists():
            return []

        all_chunks: List[DocumentChunk] = []
        for root, _, files in os.walk(self.knowledge_dir):
            for file in sorted(files):
                if file.endswith(".md"):
                    full_path = Path(root) / file
                    chunks = self.parse_markdown_file(full_path)
                    all_chunks.extend(chunks)

        self._chunks = all_chunks
        return all_chunks

    def get_chunks(self) -> List[DocumentChunk]:
        """Returns indexed chunks, re-indexing if cache is empty."""
        if not self._chunks:
            self.index_all()
        return self._chunks
