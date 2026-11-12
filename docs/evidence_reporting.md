# 结论证据索引与多格式报告

analysis.render 继续执行原facts、review、preparation、backend、方法和raw来源检查。通过后新增brief.json、brief.md、evidence_index.json，并在delivery.json绑定这些产物及完整README的字节身份。均不授予科学接受或新计算权限。

## 结论关联

索引将每条observations/interpretations文本关联到精确文件/JSON pointer、渲染时字节身份、原解析方法、比较条件和限制；附原raw来源清单、上下文合同、受控图表/方法/数据及已有科学决定引用。answer关联到上述已引用条目，仅作导航，不机械证明文字结论成立。

review可选evidence_sha256将**准确的evidence_refs字符串**映射到审阅时文件SHA-256，变更后拒绝交付；多条pointer可共享同一文件哈希。没有显式哈希时保留原已有检查并记录渲染时绑定，不假称独立证明外部文本未在审阅后改变。review里未被任何claim引用的哈希键拒绝，避免误以为已消费。

claim可选artifact_refs关联facts.artifacts或supporting_artifacts中已纳入交付的文件，未知附件拒绝。普通说明无需逐句元数据；关键观察和解释仍分别有evidence_refs。科学支持关系、比较条件有效性和最终接受由原负责人审阅。

## 同源输出

brief.json从同一次校验后的facts/review生成，包含问题、结果、观测时间、解析范围、依据、比较条件、限制/告警和下一步及负责人。brief.md呈现该对象；完整README保留原技术和证据内容。HTML使用brief.json的同一结果字段，显示观察范围和告警，并提供完整索引及报告。生成HTML前后复核原facts/review身份，改变时拒绝HTML发布。

任务卡保留历史和其他任务区块；新科学简报不移除已有CONTINUATION导航。替换导航仍由原checkpoint显式事件更新，不将导航误作实时运行状态。

未收敛或部分解析可以交付受限事实，但简报和HTML须保留相同范围及告警；backend失败仍禁止结果交付。新产物不是第二套收敛状态。旧review无需新增可选字段即可读取，旧生产记录保持原样。
