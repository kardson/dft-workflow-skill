# Linux SSH 工作站适配（验证中）

当前适配实现固定胞 fresh/warm 包的独立部署、工作站准备和监督器启动。已在真实 Linux/SSH/tmux 工作站完成一次最小 fresh VASP 测试的生成、审计、部署、赝势装配和启动，并观察到真实进程进入主循环。同一次测试也已完成终态核对、固定清单回收和真实 XML 解析；参数解释仍须单独审阅。warm 路径没有本次真实宿主验收。静态监督附件部署尚未接入此适配器。

调用 tools/ssh_deployment.py --help。三个动作 deploy、prepare、launch 分别需要私有 JSON 请求，--output 为新的本地回执路径。失败保留回执和远端现场，未知状态禁止自动重试。用户不填写 JSON，由 agent 根据真实批准和实际包维护。

Deploy 请求包含 candidate、preparation、target、files、authority、audit_context。target 为 host、port、directory、python；目录绑定原 execution manifest。files 必须等于包内完整上传清单。audit_context 包含 plan_path、approval_path、approved_manifest、workspace、tool_root、python，重新核对原批准并执行准备审计。authority 的 schema=dft-deployment-authority/v1，包含相同 target、全部 files_sha256、preparation_sha256 和真实 user_confirmation.reference/quote。传输保留原关闭 gate，不复制许可赝势和重启大文件。

Prepare/launch 请求包含 deployment（实际部署回执）和 authority。authority schema 分别为 dft-prepare-authority/v1、dft-launch-authority/v1，包含原 target/files_sha256、task_id/unit_id/execution_id 组成的 identity、实际 runtime_gate.json 的 gate_sha256、原 tmux_session 对应的 session、真实 user_confirmation.reference/quote。

本适配器不打开 gate。负责用户/科学审阅者在另行批准的工作站步骤中设置原运行器所要求的权限；不能因为部署成功就将 user_authorized 或 sol_inputs_accepted 设为 true。gate 的计划、检查清单和身份由原运行器复核。工作站 prepare 复用 practical_runtime.prepare，处理既有赝势组装和 restart 契约；launch 才请求 tmux 启动 launch_supervisor.sh。两者独占 claim，失败不得自动删除 claim 或重试。

Launch 回执仅表示启动请求已发送；接下来使用 execution.py 读取一次真实启动证据，确认推进后结束本轮。后续一次性 progress_evidence 查询，终态使用 recovery.py 预算和固定清单回收，再按 analysis.py 批准绑定分析。没有准备证据时不能跳过 prepare；不在真实工作站验证前声称已贯通。

已在真实 Linux/Python/tmux 中验证独立目录的合成监督器失败与待审阅路径；另一次两原子 VASP 工程测试已通过真实 prepare/launch 并进入主循环。初始化证据与电子步推进、收敛和科学接受分别记录；尚未形成电子步证据时，交付可显示 INITIALIZED_PENDING_PROGRESS，完整状态继续保留 UNKNOWN。[验证范围](validation_status.md)。

## 受限工程测试

默认运行器仍拒绝与其他 VASP/MPI 作业共存。另有明确批准才能使用的 `environment.bounded_validation` 工程测试 profile：绑定 task_id、unit_id、remote_batch_dir 及授权引用，仅允许单 MPI rank、单线程，CPU 配额 10%、整组内存 2 GiB、nice 19、600 秒墙钟和 30 秒停止宽限。逻辑 CPU 和独立 service 名称须按实际环境批准；共享逻辑 CPU 不代表独占或零干扰。资源不足或超时会失败，不能自动放大预算或重启。

这一路径需要可用的 systemd user service 和 cgroup v2。运行器检查实际 cgroup、CPU/内存限额、进程亲和、优先级及整组终止配置；仅写配置不能通过。MPI 参数限定为不覆盖继承亲和的无绑定布局；所有计算子进程仍受 CPUAffinity 约束。适配器使用包内独立 `.agent_tmux.sock`，并阻止该独立 server 自动把 pane 迁移到其他 cgroup，pane 恢复正常用户总线。未修改其他会话或系统配置。

该 profile 已完成资源探针、实际 MPI 和 VASP 进程继承核验；同一测试已完成终态回收解析，尚未完成超时故障注入。它是小型工程验证选项，不是通用资源调度器。用户无需填写字段，agent 根据已确认方案和真实主机证据维护；不支持的宿主保留缺项，不静默取消限制。

## 完整终态输出的接收

完整 OUTCAR 会回显普通赝势元数据，进度片段的保守过滤不直接用于整份文件。Agent 可在已批准回收中显式选择 `recovery.py ingest --full-outputs --preparation`：原准备记录必须绑定实际输入和执行清单，终态身份、传输预算、源路径、完整偏移和读前后稳定性都要通过；VASP 版本、原子/电子数、主体计算结构及结束计时也需匹配。原始字节私有保存，不删段。疑似实际赝势数据段仍拒绝，POTCAR/WAVECAR/CHGCAR 文件仍不在回收白名单。默认进度片段规则不变；不能把这一分支用于任意命名为 OUTCAR 的数据或缺少完整结束结构的中断输出。

参数对照读取实际 case 的 INCAR，并区分 OUTCAR 赋值与说明文字。分号分隔的赋值逐项读取，真正重复冲突保留；参数未批准、仅有输入回显或打印精度不足时仍为 UNKNOWN/REVIEW。机械后检通过不等于参数已获科学接受。
