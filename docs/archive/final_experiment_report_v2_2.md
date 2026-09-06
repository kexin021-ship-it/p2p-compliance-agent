# 冻结 V2.2 采购单流程调查实验报告

生成时间：2026-09-06T08:38:57.015510+00:00  
数据规模：3,000 份采购单，冻结版本指纹：`6D016275F48316DF74CDCB2A3336D94DEFEA7FC43B74B479000AFD2678B3DC6B`

## 执行摘要

冻结 V2.2 Agent 已在全部 3,000 份采购单上完成运行。五个 Batch 分批均成功完成，未发生请求失败。Agent 与纯规则基线在 2,628 份 PO 上给出相同决策，一致率为 87.6%；在 372 份 PO 上给出不同决策。

全量 3,000 例没有逐例人工标签，因此 87.6% 是一致率，不是准确率。准确性比较应以独立冻结留出集为依据：47 个留出案例中双方有 9 个分歧，均已完成人工裁决；Agent 判对 7 个，规则判对 2 个。由于其余 38 个案例双方预测相同，无论共同判断正确与否，Agent 在 47 例上的正确数量都比规则净多 5 个，即相对准确率优势为 10.6 个百分点。但是，双方各自的绝对准确率仍不能由现有标签精确计算。

## 1. 实验设计

| 阶段 | 样本 | 用途 | 是否用于最终无偏比较 |
|---|---:|---|---|
| 规则基线全量运行 | 3,000 PO | 建立无 AI 的规则对照组 | 用于全量分布与一致性 |
| 开发集 | 20 PO | 提示词、摘要逻辑与输出规则调优 | 否 |
| 初始盲测与错误回归 | 60 PO 中人工复核 13 PO | 发现错误模式并形成 V2.2 | 否，已参与调优 |
| 冻结独立留出集 | 47 PO | 冻结版本后的相对准确性比较 | 是 |
| 冻结 V2.2 全量运行 | 3,000 PO | 评估部署分流、覆盖和工作量变化 | 不具备全量人工真值 |

模型固定为 `gpt-5.4-mini-2026-03-17`，`reasoning_effort=low`。全量运行前冻结模型、提示词、事实摘要程序和回归结果，并在每次批处理前校验 SHA256。

## 2. 全量 3,000 PO 结果

![Agent decision distribution](figures/full_3000_agent_distribution_v2_2.svg)

| 系统 | 需要进一步调查 | 证据不足 | 无需进一步调查 |
|---|---:|---:|---:|
| 规则基线 | 785 | 0 | 2,215 |
| 冻结 V2.2 Agent | 665 | 361 | 1,974 |

Agent 将二元规则分流扩展为三类决策，其中 361 份 PO（12.0%）被明确标记为证据不足。这一类别避免把日志状态缺失直接解释成业务异常或流程闭环。

## 3. 规则与 Agent 的差异

![Disagreement breakdown](figures/full_3000_disagreement_breakdown_v2_2.svg)

| 规则决策 | Agent决策 | PO数量 |
|---|---|---:|
| 需要进一步调查 | 需要进一步调查 | 665 |
| 需要进一步调查 | 证据不足 | 109 |
| 需要进一步调查 | 无需进一步调查 | 11 |
| 无需进一步调查 | 证据不足 | 252 |
| 无需进一步调查 | 无需进一步调查 | 1,963 |

372 个分歧中有 361 个属于 Agent 使用 `insufficient_evidence`：252 个原本被规则视为无需调查，109 个原本被规则视为需要调查。仅 11 个案例被 Agent 从规则的需要调查降为无需调查。全量数据表明 Agent 的主要行为变化是增加不确定性表达，而不是普遍提高或降低调查量。

## 4. 冻结留出集比较

![Holdout disagreement accuracy](figures/holdout_disagreement_accuracy_v2_2.svg)

| 指标 | 规则 | Agent |
|---|---:|---:|
| 9个已裁决分歧案例的决策正确率 | 22.2% | 77.8% |
| 主要发现正确率 | 不适用 | 77.8% |
| 重点行项目正确率 | 不适用 | 100.0% |

Agent 在分歧案例中净多判对 5 份 PO，对全部 47 个独立留出案例形成精确的相对优势：+10.6 个百分点。该优势方向支持 Agent 相对于规则基线具有增量价值，但人工裁决的分歧样本只有 9 个，统计证据仍有限，不应把 77.8% 外推成全体 PO 的绝对准确率。

## 5. 主要发现分布

| Agent主要发现 | PO数量 | 占比 |
|---|---:|---:|
| `process_appears_resolved` | 1,974 | 65.8% |
| `process_state_uncertain` | 360 | 12.0% |
| `invoice_receipt_without_clear_observed` | 335 | 11.2% |
| `required_invoice_receipt_not_observed` | 175 | 5.8% |
| `required_gr_not_observed` | 55 | 1.8% |
| `vendor_invoice_before_po_creation_observed` | 51 | 1.7% |
| `latest_invoice_receipt_without_later_clear` | 19 | 0.6% |
| `post_clear_invoice_reversal_observed` | 19 | 0.6% |
| `reversal_without_original_event_observed` | 7 | 0.2% |
| `clear_without_prior_invoice_observed` | 5 | 0.2% |

## 6. Token 与费用

| 项目 | Token |
|---|---:|
| 普通输入 | 4,156,666 |
| 缓存输入 | 8,219,904 |
| 输出（含推理Token） | 1,981,847 |
| 总计 | 14,358,417 |

按生成报告时的 GPT-5.4 Mini Batch 单价估算，本次全量 API 费用约为 **$12.65**。这是依据返回的 Token 使用量计算的估值，最终金额以 OpenAI API 账单为准。

## 7. 可以支持的结论

1. 冻结 V2.2 已具备在 3,000 PO 规模上稳定批处理的能力，3,000 个请求全部成功。
2. Agent 与规则基线的总体一致率为 87.6%，说明规则覆盖了多数明显场景。
3. Agent 的核心增量是识别状态不确定性；361 份 PO 被分配到规则基线无法表达的 `insufficient_evidence` 类别。
4. 在冻结独立留出集上，Agent 相对规则净多判对 5 份 PO，对 47 例形成 +10.6 个百分点的精确相对优势。
5. 现有证据不能支持“Agent在3,000例上的绝对准确率为某个百分比”，因为全量数据没有人工真值，且留出集中的38个共同预测尚未人工复核。

## 8. 建议表述

> 在3,000份采购单上的全量实验中，冻结V2.2 Agent与纯规则基线达到87.6%的一致率，并将12.0%的采购单识别为证据不足。在47份冻结独立留出案例中，双方9个分歧均经人工裁决，Agent判对7个、规则判对2个，因此Agent相对规则净多判对5份采购单，对完整留出集形成10.6个百分点的相对准确率优势。由于其余共同预测案例未全部获得人工标签，本研究不将全量一致率解释为绝对准确率。

## 9. 可复现文件

- 全量预测：`outputs/agent_predictions_all_3000_frozen_v2_2.jsonl`
- 全量原始汇总：`outputs/agent_predictions_all_3000_frozen_v2_2_summary.json`
- 本报告统计：`outputs/final_experiment_metrics_v2_2.json`
- 决策分布表：`outputs/full_3000_agent_distribution_v2_2.csv`
- 规则-Agent交叉表：`outputs/full_3000_rule_agent_crosstab_v2_2.csv`
- 留出集指标表：`outputs/final_holdout_metrics_v2_2.csv`

全量预测 SHA256：`2D2D85D72D0EA45F1CD495406E87B1556FD7F61A7C028774AAFEDF6079B38584`
