"""Offline-only, explicit fake providers. Never imported by the live adapter."""

import base64

from nacl.public import PrivateKey

from .providers import ProviderError


class MockProviders:
    def __init__(self):
        self.private_key = PrivateKey.generate()
        self.calls = []
        self.runs = {}
        self.secrets = {}
        self.scenario = "success"
        self.next_run_id = 987

    async def identity(self, token):
        return {"id": 42, "login": "preview-user"}

    async def installation(self, token, user):
        if self.scenario == "authorization_expired":
            raise ProviderError("authorization_expired")
        return 101

    async def create_repository(self, op, user_token):
        self.calls.append("create_repository")
        if self.scenario in {"template_not_enabled", "repository_name_taken", "permission_denied"}:
            raise ProviderError(self.scenario)
        if self.scenario == "create_unknown":
            raise ProviderError("provider_unavailable", uncertain=True)
        return {
            "repo_id": 123,
            "repo_url": "https://github.com/preview-user/" + op["repo_name"],
            "branch": "main",
        }

    async def repository_token(self, op):
        return "mock-repository-token"

    async def verify_repository(self, op, token):
        if self.scenario == "actions_disabled":
            raise ProviderError("actions_disabled")
        return "a" * 40

    async def public_key(self, op, token):
        return {
            "key_id": "mock-key",
            "key": base64.b64encode(bytes(self.private_key.public_key)).decode(),
        }

    async def write_secret(self, op, token, name, encrypted, key_id):
        self.calls.append("write_secret:" + name)
        self.secrets[name] = encrypted

    async def zones(self, token):
        if self.scenario == "no_zones":
            return []
        return [{"id": "zone-1", "name": "example.com", "account_id": "account-1"}]

    async def check_hostname(self, op, token):
        if self.scenario == "hostname_taken":
            raise ProviderError("hostname_taken")

    async def create_tunnel(self, op, token):
        self.calls.append("create_tunnel")
        return {"id": "tunnel-1"}

    async def configure_tunnel(self, op, token):
        self.calls.append("create_dns")
        return "dns-1"

    async def verify_tunnel(self, op, token):
        return None

    async def tunnel_token(self, op, token):
        return "mock-connector-token"

    async def dispatch(self, op, token):
        self.calls.append("dispatch")
        if self.scenario == "dispatch_unknown":
            raise ProviderError("dispatch_result_unknown", uncertain=True)
        if self.scenario == "launch_failed":
            raise ProviderError("permission_denied")
        run_id = self.next_run_id
        self.next_run_id += 1
        self.runs[run_id] = {
            "id": run_id,
            "status": "in_progress",
            "head_sha": "a" * 40,
            "run_attempt": 1,
            "event": "workflow_dispatch",
        }
        return {
            "run_id": run_id,
            "run_url": f"https://github.com/preview-user/example/actions/runs/{run_id}",
        }

    async def find_dispatched_run(self, op, token):
        return None

    async def get_run(self, op, token):
        return self.runs[op["run_id"]]

    async def cancel(self, op, token):
        self.calls.append("cancel")
        self.runs[op["run_id"]]["status"] = "completed"
        self.runs[op["run_id"]]["conclusion"] = "cancelled"
