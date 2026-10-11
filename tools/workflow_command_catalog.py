"""Public command registry; private preparation routes are not described here."""
from __future__ import annotations

from pathlib import Path


GENERIC_OPERATIONS = {
    "validate-bundle": (
        "approved_bundle_validation.py", "approved_bundle_validation",
        "Validate an explicit bundle envelope with no deployment or authority.",
        ["vasp-approved-bundle/v1"], "vasp-approved-bundle/v1", ["source_read"],
        "Generic policy rejects private approvals and Auto; a valid envelope never authorizes execution.",
    ),
    "preflight": (
        "vasp_executor_core.py", "vasp_executor_core",
        "Read-only preflight under generic policy.",
        ["explicit manifest and local inputs"], "vasp-executor-check/v1", ["source_read"],
        "No launcher is supplied or exposed; private profiles are disabled.",
    ),
    "postcheck": (
        "vasp_executor_core.py", "vasp_executor_core",
        "Read-only postcheck of explicit existing outputs.",
        ["explicit manifest, case and output contract"], "vasp-executor-check/v1", ["source_read"],
        "Program exit and convergence evidence remain separate; no scientific acceptance.",
    ),
    "evidence": (
        "tool_evidence.py", "tool_evidence",
        "Map one existing supported receipt to a compact evidence sidecar.",
        ["existing JSON receipt"], "vasp-tool-evidence/v1", ["source_read", "local_artifact_write"],
        "Never reruns a tool or reads calculation files beyond the supplied receipt.",
    ),
    "capabilities": (
        "agent_capabilities.py", "agent_capabilities",
        "Describe only installed generic commands and their declared effects.",
        ["source metadata"], "vasp-agent-capabilities/v1", ["source_read"],
        "Does not import deployment, configuration or execution backends.",
    ),
}
GENERIC_REQUIREMENTS = {
    "validate-bundle": "--bundle required; --workspace is explicit or defaults to current directory.",
    "preflight": "--manifest required; supplied input/case/requirements paths must be workspace-contained.",
    "postcheck": "--manifest and --case-dir required; output contract follows the existing executor schema.",
    "evidence": "--receipt is parser-required; --output is optional and must name a new file outside the source calculation directory. All supplied paths must be workspace-contained.",
    "capabilities": "No delegated arguments; metadata inspection only.",
}
GENERIC_DEPENDENCIES = {
    "validate-bundle": ("approved_bundle_validation.py", "vasp_profile_policy.py"),
    "preflight": ("vasp_executor_core.py", "vasp_contracts.py", "vasp_profile_policy.py", "progress_snapshot.py", "vasp_execution_state.py"),
    "postcheck": ("vasp_executor_core.py", "vasp_contracts.py", "vasp_profile_policy.py", "progress_snapshot.py", "vasp_execution_state.py"),
    "evidence": ("tool_evidence.py",),
    "capabilities": ("agent_capabilities.py", "workflow_command_catalog.py"),
}


def missing_files(command: str, tools_dir: str | Path) -> list[str]:
    return [name for name in GENERIC_DEPENDENCIES[command] if not (Path(tools_dir) / name).is_file()]


def command_names(scope: str = "generic", tools_dir: str | Path | None = None) -> list[str]:
    if scope != "generic":
        raise ValueError("Only generic commands are available in this registry")
    root = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    return [name for name in GENERIC_OPERATIONS if not missing_files(name, root)]
