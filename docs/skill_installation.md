# 安装 VASP DFT workflow skill

本项目公开仓库根目录就是 `vasp-dft-workflow` skill 包。安装时保留完整仓库内容，让 `SKILL.md`、UI metadata、工具、参考文档和模板继续位于同一目录。

## 从 GitHub 安装

请 agent 从 `https://github.com/kardson/dft-workflow-toolkit` 安装 `vasp-dft-workflow` skill。使用本机 skill-installer 时，GitHub 仓库根目录必须显式指定 skill 名称：

```text
repo: kardson/dft-workflow-toolkit
path: .
name: vasp-dft-workflow
```

如果安装器无法直接从仓库根目录安装，可让 agent 将整个仓库克隆到 `skills/vasp-dft-workflow/`。不要只复制 `SKILL.md`；skill 会从自身绝对根目录读取 `tools/`、`docs/` 和 `templates/`。

安装后可直接用自然语言提出研究任务，也可显式调用 `$vasp-dft-workflow`。不要把研究工作目录放在安装目录中；工具或脚本调用时从 skill 根解析资源路径，并把用户工作区绝对路径传给工具。路径含空格时应正确引用。

## 运行要求

Skill 本身不包含 VASP、赝势、计算资源或所有 Python 依赖。真实运行需要用户自己的合法 VASP 部署、适用输入、计算资源、部署适配和相关依赖。请先由 agent 核验实际环境；skill 和公开工具帮助管理流程与证据，不保证计算收敛或科学结论正确。

## 首次项目接入与自动使用

安装后，支持自动技能选择的 agent 可以根据 VASP 需求识别本技能，无需每次点名；具体选择仍取决于宿主 agent。为稳定使用，可说：

> 请把这个研究项目接入 vasp-dft-workflow，今后 VASP 相关任务默认使用它，保留已有项目规则。

Agent 先确认工作区并读取已有 AGENTS.md。已有等效规则就复用；否则展示拟加入内容，在获得该项目文件的修改授权后追加以下独立段落。用户上述接入请求已明确授权时不重复确认。无文件时创建；文件只读、规则冲突或用户拒绝时说明情况，继续当前任务，不强行修改。不要改个人全局规则或其他项目。

```markdown
## VASP workflow skill

For VASP research tasks in this project, use the installed
`vasp-dft-workflow` skill automatically without requiring the user to name it.
Locate and read its SKILL.md through the host's installed skill catalog.
Enter at the relevant step; a progress request uses the one-time read-only
status route and does not restart the full workflow. Preserve existing
scientific ownership, project rules and execution authorization boundaries.
Do not apply this routing to unrelated tasks. If the skill is unavailable,
report the gap and follow existing project instructions without claiming
that the skill was loaded.
```

合并前后检查原有内容保留、段落只出现一次。项目接入不授予计算、远端写入或持续监控权限。更新或移动 skill 后通过技能目录定位，不在项目规则写死某台机器的安装路径。

## 选择协作模式

首次接入时也可选择单 agent、按需 subagent 或固定双线程；已有模式直接沿用，后续无需重复选择。模型偏好单独配置，宿主能力不足时明确说明。详见[协作方式](collaboration_modes.md)。

## 开始前确认任务范围

当前标准流程支持静态、原子参照、固定胞 fresh/warm 弛豫，采用无外场、无 SOC 的范围。超出时先说明支持缺口与验证要求，不因 skill 已安装而宣称可以直接执行。可先阅读[完整对话示意](walkthrough.md)。
