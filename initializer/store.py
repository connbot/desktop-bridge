"""Non-secret operation ledger. API tokens and password plaintext must never enter it."""

import json
import sqlite3
import time
from pathlib import Path

SAFE_FIELDS = {
    "id",
    "owner_id",
    "owner_login",
    "installation_id",
    "repo_name",
    "repo_id",
    "repo_url",
    "branch",
    "commit",
    "mode",
    "stage",
    "error",
    "uncertain",
    "created_at",
    "updated_at",
    "account_id",
    "zone_id",
    "zone_name",
    "hostname",
    "tunnel_id",
    "dns_id",
    "run_id",
    "run_url",
    "run_attempt",
    "expires_at",
    "origin",
    "last_sequence",
    "last_seen",
    "secret_written",
    "workflow_blob",
    "scenario",
    "cf_token_written",
    "key_id",
    "public_key",
    "failed_stage",
    "previous_run_id",
    "dispatch_at",
    "dispatch_nonce",
}


class Store:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, owner TEXT, data TEXT)"
        )
        self.db.commit()
        if path != ":memory:":
            Path(path).chmod(0o600)

    def save(self, op):
        if set(op) - SAFE_FIELDS:
            raise ValueError("Unexpected ledger field; do not persist credentials")
        op["updated_at"] = time.time()
        self.db.execute(
            "INSERT OR REPLACE INTO operations VALUES (?, ?, ?)",
            (op["id"], str(op["owner_id"]), json.dumps(op)),
        )
        self.db.commit()

    def get(self, op_id, owner_id=None):
        row = self.db.execute("SELECT owner, data FROM operations WHERE id=?", (op_id,)).fetchone()
        if not row or (owner_id is not None and row[0] != str(owner_id)):
            return None
        return json.loads(row[1])

    def latest(self, owner_id):
        rows = self.db.execute(
            "SELECT data FROM operations WHERE owner=?", (str(owner_id),)
        ).fetchall()
        return max((json.loads(r[0]) for r in rows), key=lambda x: x["created_at"], default=None)

    def all(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM operations")]
