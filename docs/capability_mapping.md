# 科研目标—能力—证据映射

复用 planning、property_plan 和 agent_capabilities。旧检查兼容；显式 `planning.py --tool-root <实际工具根>` 启用映射，读取该根的 agent_capabilities.py 元数据，不调用映射工具或准备后端。

## 阶段内 tool_steps

每个 stage 增加非空 tool_steps 列表。每步包含 command、purpose、inputs、outputs、completion_check、acceptance_check，及可选 validation_refs。

```json
{
  "command": "evidence",
  "purpose": "本步回答的问题",
  "inputs": [{"artifact": "source", "source_ref": "explicit-source.json", "kind": "metadata"}],
  "outputs": ["summary.json"],
  "completion_check": "怎样核对实际产物",
  "acceptance_check": "后续科学审阅要求",
  "validation_refs": []
}
```

命令必须存在于实际目录及 CLI 选项。结果附入口、支持范围、解释器、潜在副作用及条件；配置默认值不是真实调用证据，解释器候选不证明环境可用。

## 接续检查

- 已有来源：source_ref 明确指向工作区文件，可使用 JSON pointer；kind 显式选 structure/metadata/result。structure 只绑定来源字节，不验收结构。越界及 JSON 声明其他任务身份时拒绝。
- 计划上游：用 source_stage 替代 source_ref，必须是现有 dependency_plan_ref 的直接依赖；artifact 同时出现在上游 deliverables 和工具 outputs。required_contract_matches 显式列出须相等的 geometry/environment/paw/restart，空列表也须明确。身份缺失或不同阻断，循环/未知依赖拒绝。
- 计划输入保持 PLANNED_NOT_MATERIALIZED；原性质计划 blockers 保留在 dependency_limitations，不转为执行许可。
- 实际结果使用已有 vasp-result-record/v1；expected_identity 明确 execution_id、case_id、正整数 attempt，严格比较类型和值。核对 run_state=COMPLETED、result_state=COMPLETE_XML。required_acceptance_scope 沿用 COMPLETE_RESULT/COMPLETE_GEOMETRY/CONVERGED_GEOMETRY/RELAXED_GEOMETRY，几何用途还需 ionic_converged=true。这是技术完整性，不是科学接受。
- contract_identities 需与输入声明的四类合同一致。旧 record 缺字段按缺证据拒绝，不补写旧记录，不把解析成功视为对应关系。真实任务由路线明确同次结果/合同证据后选择既有取证或后续适配；本轮未建立普适历史结果导入。

## 上游科学接受依据

显式启用映射后，实际result输入还须声明scientific_decision_ref（工作区内完整文件）和scientific_decision_sha256（审阅时原文件身份）。缺少依据列UPSTREAM_SCIENTIFIC_DECISION_MISSING；文件缺失、改变、越界、声明其他任务或结果身份时阻断。该约束修正原映射仅检查技术完整性的缺口；未启用映射的原方案检查保持兼容，旧记录不重写。

每项输入的upstream_scientific_acceptance及每步upstream_acceptance_gaps分列技术来源与科学接受缺口。结果中即使写ACCEPTED也只是recorded_claim，不能代替依据。引用匹配只记REFERENCED_REQUIRES_ROUTE_VERIFICATION，scientific_acceptance始终NOT_VERIFIED_BY_TOOL；文本含义、接受范围、身份真实性和裁决权限仍须原路线核实。没有JSON身份的文本仅作已绑定参考，不据此证明接受了当前结果。计划上游尚未产出时列UPSTREAM_NOT_MATERIALIZED，不能据计划中的接受要求宣称已接受。

READY_FOR_USER_REVIEW只表示可以审阅声明和引用，不是科研接受、下游输入采用或执行授权。实际准备仍需原批准及scientific_decision_ref合同；本轮未代替路线裁决或扩大原准备入口。

## 验证证据和支持状态

validation_refs 只引用已有文件，不新增验收台账。非空时声明 validation_kind（ENGINEERING/LOCAL_BACKEND/REMOTE_BACKEND）与 validation_scope。输出绑定来源字节及已见 schema，明确“引用证据，未重跑或独立判定成功”。显式 engineering_fixture=true 不可标为后端验证。其他来源的真实、适用和成功仍需原证据及审阅。

support_state：UNAVAILABLE / API_PRESENT_NO_VALIDATION_EVIDENCE / API_PRESENT_WITH_REFERENCED_VALIDATION。最后一项只说明附有引用；没有验证证据也可出现在待审计划中，限制保持显式。

## 交付与权限

plan_check.json 启用时包含 capability_mapping；capabilities.json 保存实际目录快照，README 展示映射。始终 execution_authorized=false，不新建依赖状态机。工具根 metadata 入口须为维护者已核对的代码，不能执行外部下载的未知脚本。

工程验证覆盖旧入口、实际 metadata CLI、虚构命令、缺映射、产物和合同差异、循环、跨任务/缺失来源、结果身份/部分输出/未收敛及证据种类；不代表真实 backend 或科学接受。
