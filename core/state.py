import os
import json
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("bergfrid.state")


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _init_gist_sync() -> Optional[Any]:
    """Create GistSync if env vars are set, else None."""
    token = os.environ.get("GITHUB_GIST_TOKEN", "")
    gist_id = os.environ.get("GITHUB_GIST_ID", "")
    if token and gist_id:
        from core.gist_sync import GistSync
        return GistSync(token, gist_id)
    return None


class StateStore:
    """
    Schema:
    {
      "last_id": "...",
      "etag": "...",
      "modified": ...,
      "sent": { "discord": [...], "telegram": [...], ... }
    }
    """
    PLATFORMS = ("discord", "telegram", "twitter", "mastodon", "bluesky")

    def __init__(self, path: str, sent_ring_max: int = 250):
        self.path = path
        self.sent_ring_max = sent_ring_max
        self._gist = _init_gist_sync()
        if self._gist:
            log.info("Gist sync: ACTIVE (gist_id=%s).", self._gist.gist_id)
        self._save_counter = 0

    def _empty_state(self) -> Dict[str, Any]:
        return {
            "last_id": None,
            "etag": None,
            "modified": None,
            "sent": {p: [] for p in self.PLATFORMS},
        }

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        data.setdefault("last_id", None)
        data.setdefault("etag", None)
        data.setdefault("modified", None)
        data.setdefault("sent", {})
        for p in self.PLATFORMS:
            data["sent"].setdefault(p, [])
        return data

    def load(self) -> Dict[str, Any]:
        # Try local file first
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("state n'est pas un dict")
                return self._normalize(data)
            except json.JSONDecodeError as e:
                log.error("State local corrompu: %s", e)
            except (OSError, ValueError) as e:
                log.error("Erreur lecture state local: %s", e)

        # Local missing/corrupt -> try Gist
        if self._gist:
            log.info("Tentative de recuperation depuis Gist...")
            gist_data = self._gist.pull()
            if gist_data:
                state = self._normalize(gist_data)
                # Persist locally
                try:
                    _atomic_write_json(self.path, state)
                except OSError:
                    pass
                return state

        log.info("Fichier state absent et Gist indisponible, initialisation.")
        return self._empty_state()

    def save(self, state: Dict[str, Any]) -> None:
        sent = state.get("sent", {})
        for k in self.PLATFORMS:
            lst = sent.get(k, [])
            if isinstance(lst, list) and len(lst) > self.sent_ring_max:
                sent[k] = lst[-self.sent_ring_max:]
        state["sent"] = sent
        try:
            _atomic_write_json(self.path, state)
        except OSError as e:
            log.error("Impossible de sauvegarder state dans %s: %s", self.path, e)

        # Push to Gist every 5 saves (avoid excessive API calls)
        if self._gist:
            self._save_counter += 1
            if self._save_counter >= 5:
                self._save_counter = 0
                self._gist.push(state)

    def force_gist_push(self, state: Dict[str, Any]) -> None:
        """Force an immediate push to Gist (e.g. after seed)."""
        if self._gist:
            self._gist.push(state)

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
