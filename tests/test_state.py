import os
import json
import pytest
from core.state import StateStore


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / "test_state.json")


@pytest.fixture
def store(state_file):
    return StateStore(state_file, sent_ring_max=5)


# ── load ──────────────────────────────────────────────────────

class TestStateStoreLoad:
    def test_load_missing_file(self, store):
        state = store.load()
        assert state["last_id"] is None
        assert state["sent"]["discord"] == []
        assert state["sent"]["telegram"] == []

    def test_load_valid_file(self, store, state_file):
        data = {
            "last_id": "abc",
            "etag": "xyz",
            "modified": None,
            "sent": {"discord": ["a", "b"], "telegram": []},
        }
        with open(state_file, "w") as f:
            json.dump(data, f)
        state = store.load()
        assert state["last_id"] == "abc"
        assert state["sent"]["discord"] == ["a", "b"]

    def test_load_corrupted_json(self, store, state_file):
        with open(state_file, "w") as f:
            f.write("{invalid json")
        state = store.load()
        assert state["last_id"] is None  # falls back to empty

    def test_load_not_a_dict(self, store, state_file):
        with open(state_file, "w") as f:
            json.dump([1, 2, 3], f)
        state = store.load()
        assert state["last_id"] is None

    def test_load_fills_missing_keys(self, store, state_file):
        with open(state_file, "w") as f:
            json.dump({"last_id": "x"}, f)
        state = store.load()
        assert state["etag"] is None
        assert "discord" in state["sent"]


# ── save ──────────────────────────────────────────────────────

class TestStateStoreSave:
    def test_save_creates_file(self, store, state_file):
        state = store.load()
        state["last_id"] = "test123"
        store.save(state)
        assert os.path.exists(state_file)
        with open(state_file) as f:
            data = json.load(f)
        assert data["last_id"] == "test123"

    def test_save_truncates_ring_buffer(self, store, state_file):
        state = store.load()
        state["sent"]["discord"] = [f"id_{i}" for i in range(20)]
        store.save(state)
        with open(state_file) as f:
            data = json.load(f)
        assert len(data["sent"]["discord"]) == 5  # sent_ring_max=5

    def test_save_atomic_no_tmp_left(self, store, state_file):
        state = store.load()
        store.save(state)
        assert not os.path.exists(f"{state_file}.tmp")


# ── sent_has / sent_add ───────────────────────────────────────

class TestSentOperations:
    def test_sent_has_empty(self):
        state = {"sent": {"discord": [], "telegram": []}}
        assert not StateStore.sent_has(state, "discord", "abc")

    def test_sent_has_found(self):
        state = {"sent": {"discord": ["abc", "def"], "telegram": []}}
        assert StateStore.sent_has(state, "discord", "abc")

    def test_sent_add(self, store):
        state = {"sent": {"discord": [], "telegram": []}}
        store.sent_add(state, "discord", "new_id")
        assert "new_id" in state["sent"]["discord"]

    def test_sent_add_no_duplicate(self, store):
        state = {"sent": {"discord": ["abc"], "telegram": []}}
        store.sent_add(state, "discord", "abc")
        assert state["sent"]["discord"].count("abc") == 1

    def test_sent_add_respects_ring_max(self, store):
        state = {"sent": {"discord": [f"id_{i}" for i in range(5)], "telegram": []}}
        store.sent_add(state, "discord", "new_id")
        assert len(state["sent"]["discord"]) <= 5
        assert "new_id" in state["sent"]["discord"]
