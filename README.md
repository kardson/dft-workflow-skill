# DFT Workflow Toolkit

帮助科研人员把材料计算的**研究问题、计算输入、运行证据和结果解释**连接起来。当前计算工具主要面向 **VASP**。

如果你已有结构、计算文件或结果，希望知道下一步需要补什么证据、检查哪些设置、怎样把任务交给同事或 AI 助手，可以从这里开始。首次试用只需要 Python，使用随仓库提供的模拟示例；真实研究还需要你自己的计算软件、合法赝势，以及负责方法与结果判断的研究者。

**快速入口：** [第一次试用](#第一次试用) · [开始真实任务](#开始真实任务) · [读懂结果](#读懂结果) · [工具与安装](#工具与安装)

## 它能帮我做什么？

| 你遇到的问题 | 工具提供的帮助 | 仍需研究者判断的内容 |
|---|---|---|
| 不知道下一步缺什么 | 按明确的目标和已有证据列出计算依赖、前提和缺口 | 科学问题、方法和需要的精度 |
| 担心输入不一致 | 在支持范围内生成候选输入，检查实际设置、结构和来源 | 模型是否合适、参数是否有依据 |
| 想知道计算进展 | 整理已有输出里的步数、力、计时和缺失证据 | 是否需要进一步取证或处理异常 |
| 计算结束了，但不确定能否使用 | 核对输出和指定的收敛要求，保留警告与冲突 | 结果适用于哪个科学问题 |
| 希望同事或 AI 助手接手 | 保存明确的任务、证据路径、限制和下一步 | 哪些动作已获批准、何时需要重新决定 |

只想读已有输出或做一次检查，可以直接选择对应工具。你不必先建立一套完整的计算管理系统。

## 第一次试用

这一步练习的是“发现计算计划缺少什么”，不需要安装 VASP 或科学分析包。

### 1. 获取工具

安装 Python **3.10 或更新版本**，并获取本仓库。Python 3.10+ 是源码语法要求；已有安装验证主要来自 Windows AMD64 / Python 3.12.14，其他环境的验证范围见[兼容记录](docs/compatibility-clean-science.json)。

如果已有 Git，可运行：

```sh
git clone https://github.com/kardson/dft-workflow-toolkit.git
cd dft-workflow-toolkit
python --version
```

也可以从 GitHub 下载仓库文件。下面的命令都从工具库根目录执行。

### 2. 运行一个模拟规划

Windows PowerShell：

```powershell
$toolkitRoot = (Get-Location).Path
$trialWorkspace = Join-Path (Split-Path $toolkitRoot -Parent) 'synthetic-first-plan'
if (Test-Path -LiteralPath $trialWorkspace) { throw '请选择新目录，保留已有工作' }
New-Item -ItemType Directory -Path $trialWorkspace | Out-Null
Copy-Item -LiteralPath (Join-Path $toolkitRoot 'examples/synthetic-property-request.json') -Destination (Join-Path $trialWorkspace 'request.json')
$env:DFT_WORKSPACE_ROOT = $trialWorkspace
python -B (Join-Path $toolkitRoot 'tools/property_plan.py') --request (Join-Path $trialWorkspace 'request.json') --output-dir (Join-Path $trialWorkspace 'plan')
```

示例请求见 [synthetic-property-request.json](examples/synthetic-property-request.json)。新文件保存在工具库旁边的独立目录里，已有同名目录时停止，方便保留此前的工作。

<details>
<summary>Linux / macOS 对应命令</summary>

```sh
toolkit_root="$PWD"
trial_workspace="$(dirname "$toolkit_root")/synthetic-first-plan"
if [ -e "$trial_workspace" ]; then
  echo '请选择新目录，保留已有工作'
else
  mkdir "$trial_workspace" &&
  cp "$toolkit_root/examples/synthetic-property-request.json" "$trial_workspace/request.json" &&
  DFT_WORKSPACE_ROOT="$trial_workspace" python -B "$toolkit_root/tools/property_plan.py" --request "$trial_workspace/request.json" --output-dir "$trial_workspace/plan"
fi
```

这是相同示例的命令写法；Linux/macOS 完整工具套件尚未验证。

</details>

### 3. 看懂输出

打开新目录中的三个文件：

| 文件 | 看什么 |
|---|---|
| `plan/property_plan.json` | 计划状态和计算依赖 |
| `plan/blockers.json` | 尚未具备的来源、方法或证据 |
| `plan/dependency_table.csv` | 各步骤之间的依赖，可用表格软件查看 |

**预期结果是 `BLOCKED`，命令正常退出。** 这个模拟请求故意缺少科学合同：工具成功发现了缺口，因此目前不具备继续计算的条件。示例不会生成 VASP 输入或启动计算。

你第一次试用的目标，是能找到缺口并解释下一步需要补什么。真实研究中，应补充实际依据后再作判断；不要删除缺口或把模拟数值当作研究参数。

## 开始真实任务

先把下面几件事用自然语言写清楚。你可以交给同事或 AI 助手，由他们整理成工具要求的文件格式。

- **研究问题：** 想回答什么？已有证据为什么还不够？
- **来源材料：** 结构、实际输入输出、文献或已批准方案在哪里？
- **科学方案：** 模型、方法、关键参数、约束和比较基准由谁确定？哪些尚未确定？
- **成本与停止点：** 预计资源、时间和预算是多少？遇到什么情况先停下来判断？
- **允许动作与交付：** 本次允许读取、准备、分析、查询还是运行？最后交付什么证据？

例如：

> 我想比较两种候选结构在同一方法下的稳定性。来源和已有结果在私有工作区中。请先核对可比条件，列出证据缺口，并给出需要我决定的事项。本次允许本地读取和规划，交付一份计划与依据。

### 用一张任务卡保持清楚

在你的私有研究工作区中维护一张当前任务卡，技术细节链接到原始规格和结果，避免在多处复制“当前状态”。

| 项目 | 写什么 |
|---|---|
| 研究问题 | 本次只回答哪一个问题 |
| 当前阶段 | 尚未准备、已准备、已启动、结果已返回或已完成科学审阅 |
| 证据与时间 | 实际输入或主输出在哪里；最后核实是什么时候 |
| 下一步与负责人 | 一个明确动作、执行人及允许范围 |
| 结论与限制 | 做完能说明什么，还有哪些不确定性 |

科学方案需要由负责研究的人裁决；用户对执行动作的批准应写明范围。准备完成、工具检查通过或一项旧任务的批准，均不能自动扩展成新的运行或参数扫描。

真实研究文件放在独立的**私有工作区**。规划等工具用 `DFT_WORKSPACE_ROOT` 指定工作区；其他工具按各自 `--help` 接受路径。环境变量不会自动切换当前目录，建议传入绝对路径。结构、计算输出、主机配置和密钥保存在自己的环境中。

进一步说明：[私有工作区](docs/private_workspace.md)、[协作流程](docs/workflow.md)、[任务记录模板](templates/task_record.template.json)、[交接模板](templates/handoff.template.md)。

## 读懂结果

先区分计算的执行状态和结果的科学用途：

| 看到的记录 | 可以说明什么 |
|---|---|
| 输入已准备 | 候选文件到位；还不能说明计算已经运行 |
| 已启动 / `RUNNING` | 在记录的观察时间有启动或运行证据；历史快照不保证现在仍在运行 |
| 正常退出 / 退出码 0 | 对应程序或检查完成；仍需核对输出、收敛条件和警告 |
| `BLOCKED` | 当前前提不足；查看具体缺口，工具本身可能已正常完成 |
| `UNKNOWN` / `PARTIAL` | 证据缺失或不完整，暂时无法作完整判断 |
| 结果已接受 | 应有研究者给出的依据、限定用途和仍然存在的限制 |

一份有用的结果报告，应该让读者能回答：**原本想知道什么、实际观察到什么、依据在哪里、结论可信到什么程度、下一步由谁决定。**

比较能量、结构或电子性质时，需要核对方法、赝势、数值设置、参照和相关收敛条件。工具可以记录这些事实，具体物理解释仍由研究者负责。输入和文件完整性检查通过，也不能单独证明科学结论成立。

## 工具与安装

第一次规划试用和基础契约、证据检查只依赖 Python 标准库。根据实际任务再安装科学或分析依赖。

| 用途 | 入口 / 依赖 |
|---|---|
| 目标依赖规划、参数提案检查 | `tools/property_plan.py`、`tools/parameter_advice.py` |
| 实际输入及执行前后检查 | `tools/vasp_core_cli.py`、`tools/vasp_executor.py` |
| 当前支持范围内的候选输入生成 | `tools/vasp_input_generator.py` |
| 本地进度和运行证据 | `tools/progress_snapshot.py`、`tools/progress_evidence.py` |
| 结果记录、几何比较和附加分析 | `tools/result_registry.py`、`tools/scientific_helpers.py`、`tools/analysis_addons.py` |
| 工具回执、指定字段核验和观察指标 | `tools/tool_evidence.py`、`tools/evidence_verifier.py`、`tools/workflow_metrics.py` |

通用检查入口提供 `validate-bundle`、`preflight`、`postcheck`、`evidence` 和 `capabilities`，没有准备或提交计算的命令。各入口都有明确支持范围；缺少的科学选择不会自动填成批准参数。

<details>
<summary>查看工具能力和可选安装步骤</summary>

```sh
python -B tools/vasp_core_cli.py --help
python -B tools/vasp_core_cli.py capabilities
python -B tools/property_plan.py --help
```

需要 ASE / pymatgen 等几何或科学解析功能时，在工具库之外建立自己的隔离环境，再使用该环境的 Python 从工具库根目录安装：

```sh
python -m pip install -r requirements-science.txt
```

需要相应 DOS、HDF5 或附加分析功能时，再按需安装 `requirements-analysis.txt`。部分功能还要求特定输出文件，MacroDensity 需另外取得和核对。

已记录的科学安装组合为 **Windows AMD64 / Python 3.12.14**。复现步骤和具体依赖版本见[环境复现说明](docs/reproduce-clean-science.md)与[兼容记录](docs/compatibility-clean-science.json)。记录的是特定环境和工具快照，不代表所有操作系统或依赖版本已兼容。

VASP 的配置参照版本为 **6.5.1**，VASPKIT 集成记录参照版本为 **1.5.1**。计算软件、授权赝势和工作站调用配置由使用者自行提供。

静态交付、操作计划和作业监督还有 `static_delivery_builder.py`、`vasp_operations.py`、`job_watch.py` 等入口。它们需要自己的部署配置和明确授权，使用前阅读帮助与协作流程。

</details>

## 让 AI 助手参与

把 [AGENTS.md](AGENTS.md) 和当前任务卡交给助手，指明来源、允许动作、验收要求和停止条件。可以让助手把自然语言整理成 JSON、执行本地检查、说明证据缺口并解释结果。

助手应使用实际输入和主输出，保留未知项。提交、传输、重启和持续监控须遵守你指定的范围；远端配置需要在你的环境中适配。

## 验证、贡献与许可

本仓库包含合成测试。已有安装和合成验证提供有限的工具证据；真实诊断覆盖、真实科研效率收益和外部首次使用者体验仍有未验证部分。因此，目前不能据此承诺科研节时或诊断准确率。

<details>
<summary>开发者验证命令和证据说明</summary>

安装需要的科学依赖后，从仓库根目录运行：

```sh
python -B -m unittest discover -s tools -p 'test_*.py'
python -B -m unittest discover -s tests -p 'test_*.py'
python -B scripts/check_public_release.py --require-repository
```

两组测试覆盖不同内容，不能只运行一组便声称完整验证。测试使用构造数据，不运行 VASP 或连接工作站；实际计数和跳过原因以对应工具快照的记录为准。

相关证据：
[诊断范围](docs/diagnostic-validation-scope.json)、
[诊断补充](docs/diagnostic-validation-supplement.json)、
[字段核验说明](docs/evidence-verifier.md)、
[工作流观察合同](docs/workflow-observation-contract.json)、
[合成试用证据](docs/workflow-evidence-supplement.json)。

</details>

你可以通过 issue 或 PR 提交通用改进。维护者在维护源中合入，再按公开清单生成发布；`release_manifest.json` 记录公开文件和完整性摘要。贡献中请使用合成数据，真实研究内容和配置保留在私有工作区。

**本工具库尚未指定许可证。** 使用与再分发前请向维护者确认授权；第三方软件遵守各自许可，见 [THIRD_PARTY.md](THIRD_PARTY.md)。

更多说明：[协作与证据边界](docs/workflow.md) · [公开内容与贡献要求](docs/publication_policy.md)。
