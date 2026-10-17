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
