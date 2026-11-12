"""Check scientific-plan completeness against fixed types; never choose parameters.

References and declared choices are checked, not scientific validity. Multi-stage
dependency planning reuses the existing property_plan receipt instead of building
a second dependency graph here. No preparation, execution or approval occurs.
"""
from __future__ import annotations
import argparse
import json
import math
import hashlib
import subprocess
import sys
from pathlib import Path

from intake import existing, text


def catalog():
    return json.loads(Path(__file__).with_name("plan_templates.json").read_text(encoding="utf-8"))["templates"]


def strings(value):
    return isinstance(value, list) and bool(value) and all(text(part) for part in value)


def capability_mapping(plan, root, capabilities, dependency=None):
    """Check explicit mappings against current metadata; never invoke mapped tools."""
    if capabilities.get('schema') != 'vasp-agent-capabilities/v1':
        raise ValueError('Unsupported capabilities catalog')
    root = Path(root).resolve()
    commands = {row['name']: row for row in capabilities['commands']}
    stages = {stage['id']: stage for stage in plan['stages']}
    nodes = {node['stage_id']: node for node in (dependency or {}).get('nodes', [])}
    issues, mapped = [], []
    if nodes:
        visiting, visited = set(), set()
        def visit(key):
            if key in visiting: raise ValueError('Dependency plan contains a cycle')
            if key in visited: return
            if key not in nodes: raise ValueError('Dependency plan references an unknown stage')
            visiting.add(key)
            for parent in nodes[key].get('depends_on', []): visit(parent)
            visiting.remove(key); visited.add(key)
        for key in nodes: visit(key)

    def read_ref(ref):
        if not text(ref):
            raise ValueError('Explicit source reference required')
        name, _, pointer = ref.partition('#')
        path = (root / name).resolve(strict=True); path.relative_to(root)
        if not path.is_file():
            raise ValueError('Source is not a file')
        raw = path.read_bytes()
        value = json.loads(raw.decode('utf-8-sig')) if path.suffix.lower() == '.json' else None
        if pointer:
            if not pointer.startswith('/') or value is None:
                raise ValueError('JSON pointer required')
            for token in pointer[1:].split('/'):
                token = token.replace('~1', '/').replace('~0', '~')
                value = value[int(token)] if isinstance(value, list) else value[token]
        return value, {'ref': ref, 'sha256': hashlib.sha256(raw).hexdigest()}

    for stage_id, stage in stages.items():
        steps = stage.get('tool_steps')
        if not isinstance(steps, list) or not steps:
            issues.append(stage_id + ':CAPABILITY_MAPPING_MISSING')
            continue
        for index, step in enumerate(steps):
            label = stage_id + ':' + str(index)
            if not isinstance(step, dict):
                issues.append(label + ':INVALID_TOOL_STEP'); continue
            command = commands.get(step.get('command'))
            if command is not None and step.get('command') not in command.get('entry_mode_choices_from_parser', []):
                command = None
            problems, inputs, evidence, acceptance_gaps = [], [], [], []
            if command is None:
                problems.append('CAPABILITY_UNAVAILABLE')
            for field in ('purpose', 'completion_check', 'acceptance_check'):
                if not text(step.get(field)): problems.append('MISSING:' + field)
            if not strings(step.get('outputs')): problems.append('OUTPUTS_MISSING')
            if not isinstance(step.get('inputs'), list) or not step['inputs']:
                problems.append('INPUTS_MISSING')
            for item in step.get('inputs', []) if isinstance(step.get('inputs'), list) else []:
                try:
                    if not isinstance(item, dict) or not text(item.get('artifact')):
                        raise ValueError('Input artifact required')
                    parent = item.get('source_stage')
                    if parent is not None:
                        if item.get('source_ref') is not None:
                            raise ValueError('Choose planned stage or actual source')
                        if parent not in stages or parent not in nodes.get(stage_id, {}).get('depends_on', []):
                            raise ValueError('Input stage is not an explicit dependency')
                        upstream = stages[parent]
                        produced = {output for tool in upstream.get('tool_steps', []) for output in tool.get('outputs', [])}
                        if item['artifact'] not in upstream.get('deliverables', []) or item['artifact'] not in produced:
                            raise ValueError('Upstream artifact not promised')
                        matches = item.get('required_contract_matches')
                        if not isinstance(matches, list) or any(key not in ('geometry', 'environment', 'paw', 'restart') for key in matches):
                            raise ValueError('Explicit required_contract_matches list required')
                        for key in matches:
                            expected = nodes[stage_id].get('contract_identities', {}).get(key)
                            if not text(expected) or expected != nodes[parent].get('contract_identities', {}).get(key):
                                raise ValueError('Planned dependency contract differs: ' + key)
                        pending_acceptance = {'status': 'UPSTREAM_NOT_MATERIALIZED',
                                              'scientific_acceptance': 'NOT_VERIFIED_BY_TOOL',
                                              'route_verification_required': True}
                        inputs.append({**item, 'state': 'PLANNED_NOT_MATERIALIZED',
                                       'upstream_scientific_acceptance': pending_acceptance})
                        acceptance_gaps.append({'artifact': item['artifact'], 'source_stage': parent,
                                                **pending_acceptance})
                    else:
                        value, binding = read_ref(item.get('source_ref'))
                        if isinstance(value, dict):
                            identity = value.get('identity', value)
                            if value.get('task_id') not in (None, plan['task_id']):
                                raise ValueError('Source task differs')
                            if isinstance(identity, dict) and identity.get('task_id') not in (None, plan['task_id']):
                                raise ValueError('Source identity task differs')
                        if item.get('kind') not in ('structure', 'metadata', 'result'):
                            raise ValueError('Explicit input kind required')
                        acceptance = None
                        if item.get('kind') == 'result' or isinstance(value, dict) and value.get('schema') == 'vasp-result-record/v1':
                            if not isinstance(value, dict): raise ValueError('Result needs JSON evidence')
                            expected = item.get('expected_identity')
                            actual = value.get('identity', value)
                            if not isinstance(expected, dict) or not isinstance(actual, dict):
                                raise ValueError('Result identity required')
                            if (not text(expected.get('execution_id')) or not text(expected.get('case_id'))
                                    or type(expected.get('attempt')) is not int or expected['attempt'] < 1):
                                raise ValueError('Explicit execution/case/positive attempt required')
                            for key in ('execution_id', 'case_id', 'attempt'):
                                if expected.get(key) is None or type(expected[key]) is not type(actual.get(key)) or expected[key] != actual.get(key):
                                    raise ValueError('Result identity differs: ' + key)
                            if value.get('schema') != 'vasp-result-record/v1':
                                raise ValueError('Unsupported result evidence schema')
                            scope = item.get('required_acceptance_scope')
                            scopes = {'COMPLETE_RESULT', 'COMPLETE_GEOMETRY', 'CONVERGED_GEOMETRY', 'RELAXED_GEOMETRY'}
                            if scope not in scopes or value.get('run_state') != 'COMPLETED' or value.get('result_state') != 'COMPLETE_XML':
                                raise ValueError('Required result completeness not established')
                            if scope != 'COMPLETE_RESULT' and value.get('ionic_converged') is not True:
                                raise ValueError('Required converged geometry not established')
                            contracts = item.get('contract_identities')
                            if not isinstance(contracts, dict): raise ValueError('Result contracts required')
                            for key in ('geometry', 'environment', 'paw', 'restart'):
                                if not text(contracts.get(key)) or contracts[key] != value.get('contract_identities', {}).get(key):
                                    raise ValueError('Result contract differs: ' + key)
                            acceptance = {'status': 'MISSING_DECISION_EVIDENCE',
                                          'recorded_claim': value.get('scientific_acceptance'),
                                          'result_identity': {key: actual[key] for key in ('execution_id', 'case_id', 'attempt')},
                                          'scientific_acceptance': 'NOT_VERIFIED_BY_TOOL',
                                          'route_verification_required': True}
                            decision_ref = item.get('scientific_decision_ref')
                            decision_sha = item.get('scientific_decision_sha256')
                            if decision_ref is None and decision_sha is None:
                                problems.append('UPSTREAM_SCIENTIFIC_DECISION_MISSING')
                            else:
                                if not text(decision_ref) or '#' in decision_ref:
                                    raise ValueError('Explicit whole-file scientific decision reference required')
                                if (not isinstance(decision_sha, str) or len(decision_sha) != 64
                                        or any(char not in '0123456789abcdef' for char in decision_sha)):
                                    raise ValueError('Explicit scientific decision SHA-256 required')
                                decision, decision_binding = read_ref(decision_ref)
                                if decision_sha != decision_binding['sha256']:
                                    raise ValueError('Scientific decision evidence changed')
                                if isinstance(decision, dict):
                                    decision_identity = decision.get('identity', {})
                                    if (decision.get('task_id') not in (None, plan['task_id'])
                                            or isinstance(decision_identity, dict)
                                            and decision_identity.get('task_id') not in (None, plan['task_id'])):
                                        raise ValueError('Scientific decision task differs')
                                    declared_identity = decision.get('identity', decision)
                                    if isinstance(declared_identity, dict):
                                        for key in ('execution_id', 'case_id', 'attempt'):
                                            if (key in declared_identity
                                                    and (type(declared_identity[key]) is not type(actual[key])
                                                         or declared_identity[key] != actual[key])):
                                                raise ValueError('Scientific decision result identity differs: ' + key)
                                acceptance.update(status='REFERENCED_REQUIRES_ROUTE_VERIFICATION',
                                                  evidence=decision_binding)
                            acceptance_gaps.append({'artifact': item['artifact'], 'source_ref': item['source_ref'],
                                                    **acceptance})
                        bound_input = {**item, **binding, 'state': 'SOURCE_BOUND_CONTENT_CHECKED'}
                        if acceptance is not None:
                            bound_input['upstream_scientific_acceptance'] = acceptance
                        inputs.append(bound_input)
                except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
                    problems.append('INPUT_EVIDENCE:' + str(error))
            refs = step.get('validation_refs', [])
            if not isinstance(refs, list):
                problems.append('INVALID_VALIDATION_REFS'); refs = []
            for ref in refs:
                try:
                    value, binding = read_ref(ref)
                    kind, scope = step.get('validation_kind'), step.get('validation_scope')
                    if kind not in ('ENGINEERING', 'LOCAL_BACKEND', 'REMOTE_BACKEND') or not text(scope):
                        raise ValueError('Validation evidence kind and scope must be explicit')
                    if isinstance(value, dict) and value.get('engineering_fixture') is True and kind != 'ENGINEERING':
                        raise ValueError('Engineering fixture cannot prove backend validation')
                    evidence.append({**binding, 'kind': kind, 'scope': scope,
                                     'observed_schema': value.get('schema') if isinstance(value, dict) else None,
                                     'semantics': 'Explicitly referenced existing evidence; relevance and success require review, not rerun or independently verified here'})
                except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
                    problems.append('VALIDATION_EVIDENCE:' + str(error))
            support = ('UNAVAILABLE' if command is None else
                       'API_PRESENT_NO_VALIDATION_EVIDENCE' if not evidence else 'API_PRESENT_WITH_REFERENCED_VALIDATION')
            mapped.append({'stage_id': stage_id, 'step': index, 'command': step.get('command'),
                           'purpose': step.get('purpose'), 'support_state': support, 'inputs': inputs,
                           'outputs': step.get('outputs'), 'completion_check': step.get('completion_check'),
                           'acceptance_check': step.get('acceptance_check'), 'validation_evidence': evidence,
                           'upstream_acceptance_gaps': acceptance_gaps,
                           'tool': {key: command.get(key) for key in ('cli', 'supported_scope', 'interpreter', 'effects', 'effect_conditions', 'invocation_requirements')} if command else None,
                           'blockers': problems})
            issues.extend(label + ':' + problem for problem in problems)
    return {'schema': 'dft-plan-capability-mapping/v1', 'stages': mapped, 'blockers': issues,
            'dependency_limitations': (dependency or {}).get('blockers', []),
            'configured_backend': capabilities.get('configured_vaspkit_backend'),
            'execution_authorized': False, 'scientific_acceptance': 'NOT_AUTHORIZED',
            'scope': 'Explicit declarations and source contents only; planned inputs remain unmaterialized and no mapped tool ran'}


def validate(plan, root, *, capabilities=None):
    if plan.get("schema") != "dft-calculation-plan/v1" or not text(plan.get("task_id")):
        raise ValueError("Explicit schema and task ID required")
    blockers = []
    for name in ("research_question", "why_needed", "method_rationale", "precision_and_comparability",
                 "interpretation_limits", "execution_scope", "handoff_point"):
        if not text(plan.get(name)):
            blockers.append("MISSING:" + name)
    if not existing(root, plan.get("intake_ref")):
        blockers.append("INTAKE_REFERENCE_MISSING")
    else:
        intake = json.loads((Path(root) / plan["intake_ref"]).read_text(encoding="utf-8-sig"))
        if (intake.get("schema") != "dft-research-intake-check/v1" or intake.get("task_id") != plan["task_id"]
                or intake.get("status") != "READY_FOR_PLAN_DISCUSSION" or intake.get("blockers")):
            blockers.append("INTAKE_NOT_CONFIRMED_OR_WRONG_TASK")
    if not strings(plan.get("stop_conditions")):
        blockers.append("STOP_CONDITIONS_MISSING")
    if not strings(plan.get("method_evidence_refs")) or not all(existing(root, ref) for ref in plan.get("method_evidence_refs", [])):
        blockers.append("METHOD_EVIDENCE_MISSING")
    resources = plan.get("resources", {})
    if not text(resources.get("description")) or not text(resources.get("basis")):
        blockers.append("RESOURCE_SCOPE_OR_BASIS_MISSING")
    cost = plan.get("cost", {})
    if cost.get("state") not in {"ESTIMATED", "UNKNOWN"} or not text(cost.get("basis")):
        blockers.append("COST_BASIS_MISSING")
    elif cost["state"] == "ESTIMATED":
        bounds = cost.get("wall_hours_range")
        if (not isinstance(bounds, list) or len(bounds) != 2 or
                any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds)
                or not 0 <= bounds[0] <= bounds[1] or not text(cost.get("uncertainty"))):
            blockers.append("COST_RANGE_OR_UNCERTAINTY_INVALID")
        if not strings(cost.get("evidence_refs")) or not all(existing(root, ref) for ref in cost.get("evidence_refs", [])):
            blockers.append("COST_ESTIMATE_EVIDENCE_MISSING")
    elif cost.get("wall_hours_range") is not None:
        blockers.append("UNKNOWN_COST_MUST_NOT_CLAIM_A_RANGE")
    templates, stages, seen = catalog(), plan.get("stages", []), set()
    if not isinstance(stages, list) or not stages:
        raise ValueError("An explicit nonempty calculation list is required")
    for stage in stages:
        key = stage.get("id")
        if not text(key) or key in seen:
            raise ValueError("Unique explicit stage IDs required")
        seen.add(key)
        template = templates.get(stage.get("template"))
        if template is None:
            blockers.append(key + ":UNVALIDATED_TEMPLATE")
            continue
        if stage.get("external_field") is not False or stage.get("fixed_cell") is not True or stage.get("soc") is not False:
            blockers.append(key + ":OUTSIDE_VALIDATED_SCOPE")
        for name in ("objective", "settings_basis", "completion_criteria"):
            if not text(stage.get(name)):
                blockers.append(key + ":MISSING:" + name)
        if not existing(root, stage.get("model_source_ref")):
            blockers.append(key + ":MODEL_SOURCE_MISSING")
        choices = stage.get("choices", {})
        for name in template["required_choices"]:
            if not text(choices.get(name)):
                blockers.append(key + ":CHOICE_REQUIRED:" + name)
        checks = stage.get("acceptance_checks", {})
        for name in template["required_checks"]:
            if not text(checks.get(name)):
                blockers.append(key + ":CHECK_REQUIRED:" + name)
        if not strings(stage.get("deliverables")) or not set(template["expected_outputs"]).issubset(stage.get("deliverables", [])):
            blockers.append(key + ":OUTPUT_CONTRACT_INCOMPLETE")
    dependency = None
    if len(stages) > 1:
        ref = plan.get("dependency_plan_ref")
        if not existing(root, ref):
            blockers.append("EXISTING_DEPENDENCY_PLAN_REQUIRED")
        else:
            dependency = json.loads((Path(root) / ref).read_text(encoding="utf-8-sig"))
            nodes = dependency.get("nodes", [])
            if (dependency.get("schema") != "vasp-property-plan/v1" or len(nodes) != len(stages)
                    or {node.get("stage_id") for node in nodes} != seen or dependency.get("execution_authorized") is not False):
                blockers.append("DEPENDENCY_PLAN_STAGE_IDENTITY_MISMATCH")
            # Its BLOCKED state can describe not-yet-prepared contracts. Preserve
            # it as a limitation; never turn it into execution authorization.
    mapping = capability_mapping(plan, root, capabilities, dependency) if capabilities is not None else None
    if mapping:
        blockers.extend(mapping['blockers'])
    result = {"schema": "dft-calculation-plan-check/v1", "task_id": plan["task_id"],
            "status": "NEEDS_PLAN_COMPLETION" if blockers else "READY_FOR_USER_REVIEW",
            "blockers": blockers, "stage_count": len(stages),
            "scientific_acceptance": "NOT_AUTHORIZED", "execution_authorized": False,
            "scope": "Completeness only; scientific method, source meaning and user approval require actual review"}
    if mapping is not None:
        result['capability_mapping'] = mapping
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tool-root", type=Path, help="opt-in mapping against current local capabilities metadata")
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("Preserve previous outputs; use a new directory")
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    capabilities = None
    if args.tool_root is not None:
        child = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(args.tool_root.resolve() / 'agent_capabilities.py')],
                               capture_output=True, text=True, encoding='utf-8', timeout=20, check=True)
        capabilities = json.loads(child.stdout)
    result = validate(plan, args.workspace, capabilities=capabilities)
    lines = ["# 计算方案", "", "状态：" + result["status"], ""]
    for name, label in (("research_question", "研究问题"), ("why_needed", "为什么需要计算"),
                        ("method_rationale", "方案依据"), ("precision_and_comparability", "精度与比较条件"),
                        ("interpretation_limits", "适用范围"), ("execution_scope", "拟批准范围"), ("handoff_point", "交回节点")):
        lines.append("- " + label + "：" + str(plan.get(name) or "待明确"))
    lines.extend(["", "资源：" + str(plan.get("resources", {}).get("description") or "待明确"),
                  "成本：" + json.dumps(plan.get("cost", {}), ensure_ascii=False),
                  "", "停止条件：" + json.dumps(plan.get("stop_conditions", []), ensure_ascii=False),
                  "", "方法依据文件：" + json.dumps(plan.get("method_evidence_refs", []), ensure_ascii=False),
                  "", "依赖计划：" + str(plan.get("dependency_plan_ref") or "单项计算；未声明多阶段依赖"),
                  "多阶段依赖的准备缺项以原计划为准，本报告不打开执行授权。", "", "## 计算清单与技术附录", ""])
    for stage in plan["stages"]:
        lines.extend(["### " + stage["id"], "", "目标：" + str(stage.get("objective")), "",
                      "```json", json.dumps(stage, ensure_ascii=False, indent=2), "```", ""])
    lines.extend(["## 待补充", ""] + ["- " + blocker for blocker in result["blockers"]])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "plan_check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if capabilities is not None:
        (args.output_dir / 'capabilities.json').write_text(json.dumps(capabilities, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        lines.extend(['', '## 目标—能力—证据映射', '', '```json', json.dumps(result['capability_mapping'], ensure_ascii=False, indent=2), '```'])
    (args.output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if result["blockers"]:
        from issue_report import write_issue
        write_issue(args.output_dir, plan.get("task_id"), "BLOCKED", result["blockers"],
                    "方案尚不完整，不能作为准备与执行依据。", "整理缺失选择、方法依据和成本不确定性。", "确认科学选择、资源与停止条件。", ["plan_check.json"])
    print(json.dumps({"status": result["status"], "stage_count": result["stage_count"]}))
    return 1 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
