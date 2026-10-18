# 接入回执与异常交付

Agent 复用 environment_check.py 的 tools/workstation 原始报告，调用 tools/project_setup.py（先读取 --help）汇总完整清单。报告缺失时所有相应项目保留 UNVERIFIED。输出目录必须是私有工作区的新目录，保留 setup.json 和 README.md。

--capabilities 接收以能力名称为键的 JSON 对象，模式 single 不需要委派能力；subagent 要求 spawn；paired 要求 thread_identity、message。每项包含 state 和非空 basis。basis 必须引用实际宿主接口观察或已授权验证的依据，不能把用户偏好写成能力证明；不要为了验证消息能力未经许可发消息。程序检查记录结构，语义真实性由 agent 核对。缺证说明缺口，不自动切换模式。

回执只表示已有日期的环境与能力证据，不是当前连接探测或计算授权。环境变化时补查，简单进度请求跳过接入流程。Agent 用通俗语言总结可用项、缺项、影响与下一步，不能只交 JSON。

遇到 BLOCKED/PARTIAL/FAILED/UNKNOWN，用 tools/issue_report.py 输出统一问题报告。--report JSON 包含 schema=dft-issue-report/v1、task_id、status 和非空 issues。每项含 reason、impact、agent_next_action、user_decision 及 evidence_refs 列表。没有需要用户决定的内容时明确写“暂无”；未取得证据时列表为空，不伪造引用。该工具检查格式并渲染，不验证引用内容，不执行补救动作。原始回执仍保持原样。

接入回执存在缺项时，入口自动生成 issues.json 和 ISSUES.md，包含原因、影响、agent 下一步和用户需决定的事项；setup.json 保留完整原始检查汇总。选择新目录，已有输出不会覆盖。宿主能力 basis 需要非空文字依据，null、布尔值或空对象不能证明支持。
