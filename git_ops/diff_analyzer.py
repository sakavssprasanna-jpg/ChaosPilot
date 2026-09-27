from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import git

from rag.models import GitEvidenceCitation
from services.config import BASE_DIR

class GitDiffAnalyzer:
    """Safe, read-only Git repository inspection and diff analyzer.
    
    Zero mutation: Performs only read operations. Never alters branch, HEAD, index, or working tree.
    """

    def __init__(self, repo_path: Optional[Path] = None):
        target_path = Path(repo_path or BASE_DIR).resolve()
        self.repo_path = target_path
        self.repo: Optional[git.Repo] = None
        self.available = False

        try:
            # Strictly do NOT search parent directories
            repo = git.Repo(str(target_path), search_parent_directories=False)
            resolved_root = Path(repo.working_tree_dir).resolve()

            # Reject if resolved working_tree_dir is not within the ChaosPilot project root
            if resolved_root != target_path:
                raise ValueError(
                    f"Git repository root {resolved_root} is outside ChaosPilot workspace {target_path}"
                )

            self.repo = repo
            self.available = True
        except Exception:
            self.repo = None
            self.available = False

    def get_recent_commits(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Extracts recent commits, commit metadata, changed files, and diff patches."""
        if not self.available or not self.repo:
            return []

        commits_data: List[Dict[str, Any]] = []
        try:
            commits = list(self.repo.iter_commits(max_count=limit))
        except Exception:
            return []

        for commit in commits:
            author_str = f"{commit.author.name} <{commit.author.email}>" if commit.author else "Unknown"
            date_str = commit.committed_datetime.astimezone(timezone.utc).isoformat()
            message = commit.message.strip()
            hexsha = commit.hexsha

            files_changed = []
            diff_text = ""

            try:
                if commit.parents:
                    parent = commit.parents[0]
                    diff_items = parent.diff(commit, create_patch=True)
                else:
                    diff_items = commit.diff(git.NULL_TREE, create_patch=True)

                diff_parts = []
                for d in diff_items:
                    file_path = d.b_path or d.a_path
                    if file_path:
                        files_changed.append(file_path)
                    if d.diff:
                        # Safely decode diff bytes
                        try:
                            decoded = d.diff.decode("utf-8", errors="replace")
                            diff_parts.append(f"--- {d.a_path}\n+++ {d.b_path}\n{decoded}")
                        except Exception:
                            pass
                diff_text = "\n".join(diff_parts)
                # Keep diff bounded to 3000 chars
                if len(diff_text) > 3000:
                    diff_text = diff_text[:2990] + "\n... [diff truncated]"
            except Exception:
                pass

            commits_data.append({
                "commit": hexsha,
                "commit_short": hexsha[:8],
                "author": author_str,
                "date": date_str,
                "message": message,
                "files_changed": files_changed,
                "diff": diff_text
            })

        return commits_data

    def search_commits(self, terms: List[str], limit: int = 5) -> List[GitEvidenceCitation]:
        """Searches recent commits for occurrences of diagnostic terms. Never manufactures relevance."""
        if not self.available or not terms:
            return []

        recent = self.get_recent_commits(limit=limit * 2)
        norm_terms = [t.lower() for t in terms if len(t) > 2]

        scored: List[Tuple[float, Dict[str, Any]]] = []

        for item in recent:
            score = 0.0
            msg_lower = item["message"].lower()
            diff_lower = item["diff"].lower()
            files_lower = " ".join(item["files_changed"]).lower()

            for term in norm_terms:
                if term in msg_lower:
                    score += 1.5
                if term in files_lower:
                    score += 1.0
                if term in diff_lower:
                    score += 0.8

            # Strictly only include commits with positive relevance score
            if score > 0.0:
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: List[GitEvidenceCitation] = []
        for s, item in scored[:limit]:
            results.append(GitEvidenceCitation(
                source_type="git",
                commit=item["commit_short"],
                author=item["author"],
                date=item["date"],
                message=item["message"],
                files_changed=item["files_changed"],
                diff=item["diff"],
                relevance=round(min(1.0, s / 3.0), 3)
            ))

        return results

    def get_uncommitted_changes(self) -> List[Dict[str, Any]]:
        """Inspects working directory for uncommitted changes (read-only)."""
        if not self.available or not self.repo:
            return []

        changes = []
        try:
            diffs = self.repo.index.diff(None, create_patch=True)
            for d in diffs:
                decoded = d.diff.decode("utf-8", errors="replace") if d.diff else ""
                changes.append({
                    "file": d.b_path or d.a_path,
                    "change_type": d.change_type,
                    "diff": decoded
                })
        except Exception:
            pass
        return changes

_git_analyzer_instance: Optional[GitDiffAnalyzer] = None

def get_git_analyzer() -> GitDiffAnalyzer:
    global _git_analyzer_instance
    if _git_analyzer_instance is None:
        _git_analyzer_instance = GitDiffAnalyzer()
    return _git_analyzer_instance
