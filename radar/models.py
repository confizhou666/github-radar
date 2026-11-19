from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RepoItem:
    repo_name: str  # canonical owner/repo (original case)
    url: str
    description: str = ""
    language: Optional[str] = None
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    created_at: str = ""
    pushed_at: str = ""
    sources: List[str] = field(default_factory=list)
    discussion_url: str = ""
    title_cn: str = ""
    desc_cn: str = ""
    ai_highlight: str = ""
    category_tag: str = ""
    discovered_at: str = ""

    @property
    def canonical_key(self) -> str:
        return self.repo_name.strip().lower()
