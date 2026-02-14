import os
import json
import logging
from typing import Any, Dict, List

log = logging.getLogger("bergfrid.state")


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class StateStore:
    """
    Schema:
    {
      "last_id": "...",
      "etag": "...",
      "modified": ...,
      "sent": { "discord": [...], "telegram": [...] }
    }
    """
    def __init__(self, path: str, sent_ring_max: int = 250):
        self.path = path
        self.sent_ring_max = sent_ring_max

    def _empty_state(self) -> Dict[str, Any]:
        return {
            "last_id": None,
            "etag": None,
            "modified": None,
            "sent": {"discord": [], "telegram": [], "twitter": []},
        }

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            log.info("Fichier state %s absent, initialisation.", self.path)
            return self._empty_state()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("state n'est pas un dict")
            data.setdefault("last_id", None)
            data.setdefault("etag", None)
            data.setdefault("modified", None)
            data.setdefault("sent", {"discord": [], "telegram": []})
            data["sent"].setdefault("discord", [])
            data["sent"].setdefault("telegram", [])
            data["sent"].setdefault("twitter", [])
            return data
        except json.JSONDecodeError as e:
            log.error("Fichier state %s corrompu (JSON invalide): %s. Reinitialisation.", self.path, e)
            return self._empty_state()
        except (OSError, ValueError) as e:
            log.error("Erreur lecture state %s: %s. Reinitialisation.", self.path, e)
            return self._empty_state()

    def save(self, state: Dict[str, Any]) -> None:
        sent = state.get("sent", {})
        for k in ("discord", "telegram", "twitter"):
            lst = sent.get(k, [])
            if isinstance(lst, list) and len(lst) > self.sent_ring_max:
                sent[k] = lst[-self.sent_ring_max:]
        state["sent"] = sent
        try:
            _atomic_write_json(self.path, state)
        except OSError as e:
            log.error("Impossible de sauvegarder state dans %s: %s", self.path, e)

    @staticmethod
    def sent_has(state: Dict[str, Any], platform: str, entry_id: str) -> bool:
        return entry_id in (state.get("sent", {}).get(platform, []) or [])

    def sent_add(self, state: Dict[str, Any], platform: str, entry_id: str) -> None:
        state.setdefault("sent", {}).setdefault(platform, [])
        lst: List[str] = state["sent"][platform]
        if entry_id not in lst:
            lst.append(entry_id)
        if len(lst) > self.sent_ring_max:
            state["sent"][platform] = lst[-self.sent_ring_max:]
