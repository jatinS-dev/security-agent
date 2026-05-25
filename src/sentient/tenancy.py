from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .agent_registry import FileAgentRegistry
from .controller import InMemoryAgentController
from .models import EnforcementMode
from .policy import Policy, PolicyEngine
from .stores import FileApprovalStore, FileAuditStore
from .supervisor import SecuritySupervisor
from .verifiers import KeywordEvidenceVerifier, VerifierRegistry


@dataclass(frozen=True)
class TenantConfig:
    tenant_id: str
    policy_path: str
    audit_path: str
    approvals_path: str
    registry_path: str | None = None


class FileTenantRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._tenants = self._load()

    def get(self, tenant_id: str) -> TenantConfig | None:
        return self._tenants.get(tenant_id)

    def require(self, tenant_id: str) -> TenantConfig:
        config = self.get(tenant_id)
        if config is None:
            raise KeyError(f"Unknown tenant: {tenant_id}")
        return config

    def list(self) -> list[TenantConfig]:
        return list(self._tenants.values())

    def _load(self) -> dict[str, TenantConfig]:
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        tenants = data.get("tenants", data)
        if not isinstance(tenants, list):
            raise ValueError("Tenant registry must contain a tenants list.")
        configs: dict[str, TenantConfig] = {}
        for item in tenants:
            config = TenantConfig(
                tenant_id=item["tenant_id"],
                policy_path=item["policy_path"],
                audit_path=item.get("audit_path", f"logs/{item['tenant_id']}/audit.jsonl"),
                approvals_path=item.get(
                    "approvals_path",
                    f"logs/{item['tenant_id']}/approvals.jsonl",
                ),
                registry_path=item.get("registry_path"),
            )
            configs[config.tenant_id] = config
        return configs


class TenantSupervisorRouter:
    def __init__(
        self,
        registry: FileTenantRegistry,
        *,
        enforcement_mode: EnforcementMode | str = EnforcementMode.ENFORCE,
    ) -> None:
        self.registry = registry
        self.enforcement_mode = EnforcementMode(enforcement_mode)
        self._supervisors: dict[str, SecuritySupervisor] = {}

    def supervisor_for(self, tenant_id: str) -> SecuritySupervisor:
        if tenant_id not in self._supervisors:
            self._supervisors[tenant_id] = self._build_supervisor(
                self.registry.require(tenant_id)
            )
        return self._supervisors[tenant_id]

    def audit_path_for(self, tenant_id: str) -> Path:
        return Path(self.registry.require(tenant_id).audit_path)

    def _build_supervisor(self, config: TenantConfig) -> SecuritySupervisor:
        registry = (
            FileAgentRegistry.from_file(config.registry_path)
            if config.registry_path
            else None
        )
        return SecuritySupervisor(
            policy_engine=PolicyEngine(
                Policy.from_file(config.policy_path),
                verifier_registry=VerifierRegistry(KeywordEvidenceVerifier()),
                agent_registry=registry,
            ),
            controller=InMemoryAgentController(),
            enforcement_mode=self.enforcement_mode,
            audit_store=FileAuditStore(config.audit_path),
            approval_store=FileApprovalStore(config.approvals_path),
        )
