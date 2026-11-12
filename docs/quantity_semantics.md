# 科学量语义审计与使用边界（2026-11-10）

本维护保持科学输入、PAW、重启和科学接受职责。下面为工程检查，不能替代真实材料的数值收敛和路线审阅。

## 字段审计（4.1）

| 对象/实际消费者 | 原有字段与检查 | 实际缺口及本轮处理 |
|---|---|---|
| vasp_result_analysis → analysis facts | F/E0列名含eV、同POSCAR顺序、完整/部分XML、原收敛检查 | 增加总量/基准/backend元数据；functional、PAW环境、参考态未由该解析器证明，保留null；facts携带元数据 |
| scientific_helpers.reference_energy | E0、固定几何解释、PAW族/环境/组分、返回目录身份 | 拒绝非有限/布尔E0、电子未收敛、未完成状态和显式case/attempt不符；补量语义比较。缺元数据或旧合同缺期望身份时formal_comparability=INSUFFICIENT_EVIDENCE |
| practical_geometry / geometry_comparison | 物种/伪氢标签、掩码、晶胞、索引、距离 | force helper限制混合晶格掩码直接作用Cartesian力；非对角晶胞返回None和明确状态。XML力还须与比较的最终晶胞/坐标一致 |
| vasp_result_analysis自由力 | 原parse_poscar已禁止部分约束进入自由力统计 | 保留原保护，新增显式限制并传入facts告警；不扩大斜晶胞力投影支持 |
| planar potential → macro_average | 同LOCPOT网格/轴独立核对、显式窗口/平滑、候选状态 | 传播axis、eV、angstrom、potential_component和原始零点说明。各自未对齐的LOCPOT零点不能直接比较 |
| stress | 主结果解析未提供通用应力语义/转换接口 | 新纯显式转换，要求原单位、符号、顺序及Cartesian基。未新增XML应力自动解析或应力科学结论 |
| surface | 本地slab候选不计算表面能 | 新元数据检查要求面积单位、表面数、覆盖度及终止；未增加表面能求解器 |

## 元数据检查（4.2）

quantity_semantics.compare 显式接收 quantity=energy/force/potential/stress/surface 及 left/right 元数据。状态为 MATCH、INCOMPATIBLE、INSUFFICIENT_EVIDENCE、NOT_APPLICABLE。MATCH仅为声明字段相容，不是数据真实性或科学接受。缺失字段不补默认；已知不符与证据不足分别记录。NOT_APPLICABLE须双方明确不适用且有原因。

直接调用已有离线工具：

```text
python quantity_semantics.py --request request.json --output new-result.json
```

请求schema=dft-quantity-request/v1，operation=compare，quantity及left/right必需。energy需unit/backend/functional/paw_environment/energy_basis/reference_state/normalization；force需unit/coordinate_basis/constraint_basis/atom_mapping/constraints/cell_basis；potential需unit/axis/potential_component/reference_zero/sampling_window；surface需unit/energy_basis/reference_state/area_unit/surface_count/coverage/termination；stress需unit/sign_convention/component_order/coordinate_basis。原始各自LOCPOT零点保持cross_run_reference_alignment缺口。

CLI返回1表示不兼容或证据不足，0仅表示元数据相容/不适用或转换完成；不能用于自动提交、排序或接受结果。输出拒绝覆盖。参考能现有入口已直接消费比较函数，元数据与实际E0/PAW环境冲突时拒绝；functional/reference_state仍须原科学证据。

## 应力转换与旧记录（4.3）

operation=convert_stress 时 stress包含values（6个有限数）、unit（kbar/GPa/eV/angstrom^3）、sign_convention（compression_positive/tension_positive）、component_order（xx/yy/zz/yz/xz/xy的完整排列）、coordinate_basis=Cartesian。没有默认符号或顺序。

转换结果统一到 eV/angstrom^3、tension_positive、xx/yy/zz/yz/xz/xy，保留完整original、倍率和ASE版本。单位因子取实际ase.units.GPa。本机ASE Vasp.read_stress实际代码对OUTCAR kbar执行负号、重排及0.1*units.GPa；工程测试与其对应。仅当调用者明确输入含义时使用，禁止重复转换或从文件名推断原单位。

旧summary、record和合同保留不动；需要比较时生成新检查输出。旧字段缺失保持未知，不根据后来的批准反填历史数据。已存在原始力、能量和结构可以按其既有用途查看；受限自由力或未证明可比性不能生成正式科学排序。

验证以工程数据覆盖单位/参考/环境/归一化差异、索引/约束、轴/窗口/零点、有限值/电子收敛、混合约束/最终几何、应力符号/顺序/单位及旧记录缺项。真实科研参数、阈值和生产任务均未修改。
