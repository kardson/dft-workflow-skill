# 来源导航和恢复

复用 task_checkpoint.py / task_resume.py 及任务卡现有 CONTINUATION 区，不建立新的任务状态机。空白起点见[导航模板](../templates/task_navigation.template.json)，由 agent 维护，用户无需填写 JSON。

## 权威来源

| 内容 | 来源角色 | 注意 |
|---|---|---|
| 目标与未决问题 | research | 原澄清记录及明确用户答复 |
| 方法与选择 | plan | 已批准方案，计划值不是实际运行值 |
| 授权依据 | approval | 原答复/批准文件；角色标签和文件存在不证明有效授权 |
| 实际包与身份 | preparation / execution | 实际文件、manifest 及审计；历史包不证明当前运行 |
| 时间限定观察 | observation | 观测时间和采集范围，不当作心跳 |
| 分析事实 | facts | 绑定同次原始输出及实际解析范围 |
| 解释与接受 | review | 负责人的明确科学判断 |
| 未决问题 | blockers | 指向实际缺口记录 |

冲突时回到各类原始来源核实，不让摘要覆盖批准、实际输入或原始输出。缺少的角色保留未决事项，不伪造占位来源。不要求对所有历史任务补齐。

## 使用

在原 checkpoint 参数上增加 `--navigation <导航JSON>`。导航列 workspace-relative 文件路径；不接受越界引用。JSON 来源存在 task_id/identity.task_id 时必须与任务一致；没有身份的文献/文本仅作为显式引用，不获得任务事实地位。来源角色只导航，checker 不评判批准或科学有效性。

checkpoint 将导航和逐项来源绑定到既有 evidence，导航进入同一继续记录；`--task-card` 仅更新原任务 CONTINUATION 区。导航中的下一步是历史建议，不替代 resume 的安全 next_action。resume 仍始终返回 execution_authorized=false。旧继续记录不必添加导航。

只在 task_transition、authority_change、delivery、new_evidence、blocker_change、responsibility_change 时生成新的继续记录。新记录保留工具写入时间、责任人、未决项和下一步；保留旧记录，不覆盖证据。未决科学问题继续由路线裁决。

恢复时先运行原 task_resume.py。任何绑定文件缺失或改变时 status=BLOCKED、next_action=RECONCILE_EVIDENCE；导航复制内容与绑定源不符同样阻断。原任务卡的旧导航仅供定位，不代替实时检查。更新任务卡前也核实继续记录来源未变。

## 验收范围

工程验证包括任务卡其他内容保留、导航引用变更/缺失、跨任务/越界拒绝、旧记录读取，以及历史下一步不产生新执行授权。生产记录接入仍由原路线写入者选择；本工具不主动扫描或更新科研任务。
