# 分析已有计算

用户已有 VASP 输出、但没有本流程批准和准备记录时，使用 tools/legacy_import.py，而不是伪造批准或绕过标准 analysis.py 的绑定检查。先明确来源目录和用户希望了解的问题；将旧材料放在独立私有工作区，原样保留。

读取 --help，指定 --source、--workspace、--output（新目录，位于工作区且与来源独立）、--tool-root 和实际科学 --python。导入只复制 POSCAR、CONTCAR、INCAR、KPOINTS、OUTCAR、OSZICAR、vasprun.xml，按 --max-bytes 限额。POTCAR 不复制、不读取。原始目录保持不变，副本建立文件身份记录，解析读取副本。

交付 README.md、import.json 及 parser 的真实结果。backend_status=FAILED 表示解析器没有成功完成，不是部分科学结果；SUCCEEDED 只证明后端返回且与落盘记录一致。PARTIAL 保留 XML 缺失/截断等解析限制，OSZICAR 能量不能单独证明收敛。无历史批准、实际软件和赝势来源等信息时明确缺失，不把这次导入称为原计算已通过标准工作流。

展示任何稳定性比较或科学解释前核实方法、参照、赝势、输入、收敛与用途。报告副本若后来改变，须重新导入解析，不以旧摘要作为当前证据；新计算必须另走研究澄清、方案确认和执行授权。
