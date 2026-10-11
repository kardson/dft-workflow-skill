"""Read-only catalog of the generic command surface included in public tools."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent

from workflow_command_catalog import (
    GENERIC_DEPENDENCIES,
    GENERIC_OPERATIONS,
    GENERIC_REQUIREMENTS,
    command_names,
    missing_files,
)


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _expression(node):
    return ast.unparse(node) if node is not None else None


def parser_arguments(path: Path) -> list[dict]:
    """Extract argparse fields without importing any command backend."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        names = [_literal(arg) for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        if not names:
            continue
        kw = {item.arg: item.value for item in node.keywords if item.arg}
        action = _literal(kw.get("action")) if "action" in kw else None
        kind = "flag" if action in {"store_true", "store_false"} else "string"
        if "type" in kw:
            kind = ast.unparse(kw["type"]).split(".")[-1]
        result.append({
            "names": names,
            "required": (_literal(kw.get("required")) is True) or (not names[0].startswith("-") and _literal(kw.get("nargs")) not in ("?", "*")),
            "type": kind,
            "choices": _literal(kw.get("choices")) if "choices" in kw else None,
            "nargs": _literal(kw.get("nargs")) if "nargs" in kw else None,
            "action": _literal(kw.get("action")) if "action" in kw else None,
            "default": _literal(kw.get("default")) if "default" in kw else None,
            "default_expression": _expression(kw.get("default")),
            "source": str(path),
            "line": node.lineno,
        })
    result.sort(key=lambda item: item["line"])
    return result


def catalog(*, scope="generic", tools_dir=None) -> dict:
    if scope != "generic":
        raise ValueError("Only the generic command catalog is available in this package")
    root = Path(tools_dir) if tools_dir is not None else TOOLS
    names = command_names("generic", root)
    entries = []
    for name in names:
        filename, _module, summary, files, schema, effects, failure = GENERIC_OPERATIONS[name]
        source = root / filename
        params = [] if name == "capabilities" else parser_arguments(source)
        if name in {"preflight", "postcheck"}:
            unique = {}
            for param in params:
                if param["names"] == ["mode"]:
                    continue
                unique.setdefault(tuple(param["names"]), param)
            params = list(unique.values())
            if name == "postcheck":
                for param in params:
                    if "--case-dir" in param["names"]:
                        param["required"] = True
        entries.append(dict(
            name=name,
            cli=["vasp_core_cli.py", name],
            entry_required=True,
            entry_mode_choices_from_parser=names,
            delegated_parser=str(source),
            delegated_parameters=params,
            invocation_requirements=GENERIC_REQUIREMENTS[name],
            interpreter=sys.executable,
            required_files=files,
            supported_scope=summary,
            output_schema=schema,
            dependencies=list(GENERIC_DEPENDENCIES[name]),
            scope="generic",
            effects={key: key in effects for key in ("source_read", "local_artifact_write", "remote_tool_call", "remote_compute_control")},
            effect_semantics="possible effects of this CLI operation; not effects observed in a prior invocation",
            failure_semantics=failure,
            scientific_acceptance="NOT_AUTHORIZED",
        ))
    unavailable = [
        {"name": name, "missing_files": missing_files(name, root)}
        for name in GENERIC_OPERATIONS
        if name not in names
    ]
    return dict(
        schema="vasp-agent-capabilities/v1",
        source=str(root / "vasp_core_cli.py"),
        catalog_method="Public generic command registry plus AST parser metadata; no backend imports",
        scope="generic",
        commands=entries,
        unavailable_commands=unavailable,
    )


def main() -> int:
    print(json.dumps(catalog(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
