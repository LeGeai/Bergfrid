from abc import ABC, abstractmethod
from typing import Dict, Any

from core.models import Article


class Publisher(ABC):
    name: str

    @abstractmethod
    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        ...
