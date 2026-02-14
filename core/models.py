from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    url: str
    summary: str
    tags: List[str]
    author: str
    category: str
    published_at: Optional[datetime]  # UTC si possible
    social_summary: str = ""  # texte court pour Twitter/réseaux sociaux
    image_url: str = ""  # URL image article (media:content / enclosure)
    source: str = "Bergfrid"
