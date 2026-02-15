"""Sync bergfrid_state.json to/from a GitHub Gist for persistence across deploys.

Requires two env vars:
  GITHUB_GIST_TOKEN  — a GitHub personal access token (classic) with 'gist' scope
  GITHUB_GIST_ID     — the Gist ID (create one manually first, can be secret)

The Gist stores the state JSON as a file named 'bergfrid_state.json'.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

log = logging.getLogger("bergfrid.gist_sync")

GIST_API = "https://api.github.com/gists"
GIST_FILENAME = "bergfrid_state.json"


class GistSync:
    def __init__(self, token: str, gist_id: str):
        self.token = token
        self.gist_id = gist_id

    def _request(self, url: str, method: str = "GET",
                 data: Optional[bytes] = None) -> Optional[dict]:
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Bergfrid-Bot/1.0",
        }
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            log.warning("Gist API %s %s -> %d", method, url, e.code)
            return None
        except Exception as e:
            log.warning("Gist API error: %s", e)
            return None

    def pull(self) -> Optional[Dict[str, Any]]:
        """Fetch state from Gist. Returns parsed dict or None."""
        resp = self._request(f"{GIST_API}/{self.gist_id}")
        if not resp:
            return None
        files = resp.get("files", {})
        f = files.get(GIST_FILENAME)
        if not f:
            log.info("Gist %s: fichier '%s' absent.", self.gist_id, GIST_FILENAME)
            return None
        try:
            data = json.loads(f["content"])
            if isinstance(data, dict):
                log.info("Gist pull: OK (last_id=%s).", data.get("last_id", "?"))
                return data
        except (json.JSONDecodeError, KeyError) as e:
            log.warning("Gist pull: contenu invalide: %s", e)
        return None

    def push(self, state: Dict[str, Any]) -> bool:
        """Push state to Gist. Returns True on success."""
        payload = json.dumps({
            "files": {
                GIST_FILENAME: {
                    "content": json.dumps(state, ensure_ascii=False, indent=2)
                }
            }
        }).encode("utf-8")
        resp = self._request(
            f"{GIST_API}/{self.gist_id}", method="PATCH", data=payload
        )
        if resp:
            log.debug("Gist push: OK.")
            return True
        return False
