import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape


project_folder = Path(__file__).resolve().parents[1]
outputs_folder = project_folder / "outputs"
evals_folder = project_folder / "evals"
docs_folder = project_folder / "docs"
figures_folder = docs_folder / "figures"

full_predictions_path = (
    outputs_folder / "agent_predictions_all_3000_frozen_v2_2.jsonl"
)
full_summary_path = (
    outputs_folder / "agent_predictions_all_3000_frozen_v2_2_summary.json"
)
batch_state_path = outputs_folder / "all_3000_batch_state_frozen_v2_2.json"
raw_results_folder = outputs_folder / "all_3000_batch_results_frozen_v2_2"

holdout_predictions_path = (
    evals_folder / "blind_holdout_system_predictions_frozen_v2_2.jsonl"
)
holdout_labels_path = evals_folder / "blind_holdout_labels_v2_2.jsonl"
holdout_review_ids_path = (
    evals_folder / "blind_holdout_review_po_ids_v2_2.txt"
)

metrics_path = outputs_folder / "final_experiment_metrics_v2_2.json"
distribution_csv_path = outputs_folder / "full_3000_agent_distribution_v2_2.csv"
crosstab_csv_path = outputs_folder / "full_3000_rule_agent_crosstab_v2_2.csv"
holdout_csv_path = outputs_folder / "final_holdout_metrics_v2_2.csv"
report_path = docs_folder / "final_experiment_report_v2_2.md"

distribution_chart_path = (
    figures_folder / "full_3000_agent_distribution_v2_2.svg"
)
disagreement_chart_path = (
    figures_folder / "full_3000_disagreement_breakdown_v2_2.svg"
)
holdout_chart_path = figures_folder / "holdout_disagreement_accuracy_v2_2.svg"

EXPECTED_FULL_COUNT = 3000
EXPECTED_HOLDOUT_COUNT = 47
EXPECTED_FINGERPRINT = (
    "6D016275F48316DF74CDCB2A3336D94DEFEA7FC43B74B479000AFD2678B3DC6B"
)

# Official GPT-5.4 Mini Batch prices as of 2026-09-06, USD per 1M tokens.
BATCH_UNCACHED_INPUT_PRICE = 0.75
BATCH_CACHED_INPUT_PRICE = 0.075
BATCH_OUTPUT_PRICE = 4.50

DECISION_LABELS = {
    "further_investigation": "Further investigation",
    "insufficient_evidence": "Insufficient evidence",
    "no_further_investigation": "No further investigation",
}
DECISION_COLORS = {
    "further_investigation": "#D95F59",
    "insufficient_evidence": "#E0A526",
    "no_further_investigation": "#4E9F6D",
}


def load_jsonl(path):
    records = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise SystemExit(
                    f"{path.name} 第 {line_number} 行不是有效 JSON。"
                ) from error
    return records


def index_by_po(records, source_name):
    indexed = {}
    for record in records:
        po_id = record["purchasing_document"]
        if po_id in indexed:
            raise SystemExit(f"{source_name} 出现重复 PO：{po_id}")
        indexed[po_id] = record
    return indexed


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)


def aggregate_raw_usage():
    totals = Counter()
    for path in sorted(raw_results_folder.glob("part_*_output.jsonl")):
        for record in load_jsonl(path):
            response = record.get("response") or {}
            usage = (response.get("body") or {}).get("usage") or {}
            totals["input_tokens"] += usage.get("input_tokens", 0) or 0
            totals["output_tokens"] += usage.get("output_tokens", 0) or 0
            totals["total_tokens"] += usage.get("total_tokens", 0) or 0
            details = usage.get("input_tokens_details") or {}
            totals["cached_input_tokens"] += details.get("cached_tokens", 0) or 0
    totals["uncached_input_tokens"] = (
        totals["input_tokens"] - totals["cached_input_tokens"]
    )
    return dict(totals)


def verify_full_run(records, summary, state):
    indexed = index_by_po(records, full_predictions_path.name)
    if len(indexed) != EXPECTED_FULL_COUNT:
        raise SystemExit(
            f"全量预测应有 {EXPECTED_FULL_COUNT} 个 PO，实际为 {len(indexed)}。"
        )
    if summary["po_count"] != EXPECTED_FULL_COUNT:
        raise SystemExit("全量汇总的 PO 数量不正确。")
    if summary["frozen_agent_fingerprint"] != EXPECTED_FINGERPRINT:
        raise SystemExit("全量汇总不是冻结 V2.2 版本。")
    if any(
        record.get("frozen_agent_fingerprint") != EXPECTED_FINGERPRINT
        for record in records
    ):
        raise SystemExit("全量预测混入了其他 Agent 版本。")
    actual_hash = sha256(full_predictions_path)
    if summary["prediction_sha256"] != actual_hash:
        raise SystemExit("全量预测 SHA256 与汇总记录不一致。")
    chunks = state.get("chunks") or []
    if not chunks or any(
        chunk.get("status") != "completed"
        or not chunk.get("collected")
        or (chunk.get("request_counts") or {}).get("failed") != 0
        for chunk in chunks
    ):
        raise SystemExit("存在未完成、未收集或失败的 Batch 分批。")


def full_metrics(records):
    agent_counts = Counter(record["agent_decision"] for record in records)
    rule_counts = Counter(record["rule_decision"] for record in records)
    cross = Counter(
        (record["rule_decision"], record["agent_decision"])
        for record in records
    )
    agreement_count = sum(record["agree"] for record in records)
    finding_counts = Counter(
        record["agent_output"]["primary_finding"] for record in records
    )
    return agent_counts, rule_counts, cross, agreement_count, finding_counts


def holdout_metrics():
    predictions = index_by_po(
        load_jsonl(holdout_predictions_path),
        holdout_predictions_path.name,
    )
    labels = index_by_po(load_jsonl(holdout_labels_path), holdout_labels_path.name)
    review_ids = {
        line.strip()
        for line in holdout_review_ids_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    if len(predictions) != EXPECTED_HOLDOUT_COUNT:
        raise SystemExit("冻结留出集预测数量不是 47。")
    disagreements = {
        po_id for po_id, record in predictions.items() if not record["agree"]
    }
    if disagreements != review_ids or set(labels) != review_ids:
        raise SystemExit("冻结留出集的分歧清单与人工标签不一致。")

    rule_correct = sum(
        predictions[po_id]["rule_decision"] == labels[po_id]["review_decision"]
        for po_id in review_ids
    )
    agent_correct = sum(
        predictions[po_id]["agent_decision"] == labels[po_id]["review_decision"]
        for po_id in review_ids
    )
    primary_correct = sum(
        predictions[po_id]["agent_output"]["primary_finding"]
        == labels[po_id]["primary_finding"]
        for po_id in review_ids
    )
    focus_correct = sum(
        set(predictions[po_id]["agent_output"]["focus_items"])
        == set(labels[po_id]["focus_items"])
        for po_id in review_ids
    )
    advantage = agent_correct - rule_correct
    return {
        "total_cases": len(predictions),
        "agreement_cases_unreviewed": len(predictions) - len(review_ids),
        "disagreement_cases_reviewed": len(review_ids),
        "rule_correct_on_disagreements": rule_correct,
        "agent_correct_on_disagreements": agent_correct,
        "agent_primary_finding_correct_on_disagreements": primary_correct,
        "agent_focus_items_correct_on_disagreements": focus_correct,
        "rule_disagreement_accuracy": rule_correct / len(review_ids),
        "agent_disagreement_accuracy": agent_correct / len(review_ids),
        "agent_primary_finding_accuracy": primary_correct / len(review_ids),
        "agent_focus_items_accuracy": focus_correct / len(review_ids),
        "agent_net_correct_advantage_cases": advantage,
        "agent_accuracy_advantage_on_47": advantage / len(predictions),
        "absolute_accuracy_available": False,
    }


def svg_bar_chart(path, title, subtitle, labels, values, colors, percent=False):
    width = 920
    height = 500
    margin_left = 250
    margin_right = 70
    top = 115
    row_height = 92
    chart_width = width - margin_left - margin_right
    maximum = max(values) if values else 1
    if percent:
        maximum = 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#FAFAF7"/>',
        f'<text x="40" y="48" font-family="Arial, sans-serif" font-size="26" font-weight="700" fill="#202421">{escape(title)}</text>',
        f'<text x="40" y="78" font-family="Arial, sans-serif" font-size="15" fill="#5A625D">{escape(subtitle)}</text>',
    ]
    for index, (label, value, color) in enumerate(zip(labels, values, colors)):
        y = top + index * row_height
        bar_width = 0 if maximum == 0 else value / maximum * chart_width
        parts.extend(
            [
                f'<text x="40" y="{y + 30}" font-family="Arial, sans-serif" font-size="17" fill="#202421">{escape(label)}</text>',
                f'<rect x="{margin_left}" y="{y}" width="{chart_width}" height="42" rx="8" fill="#E8EAE6"/>',
                f'<rect x="{margin_left}" y="{y}" width="{bar_width:.1f}" height="42" rx="8" fill="{color}"/>',
            ]
        )
        value_text = f"{value:.1%}" if percent else f"{int(value):,}"
        parts.append(
            f'<text x="{margin_left + min(bar_width + 12, chart_width - 4):.1f}" y="{y + 29}" font-family="Arial, sans-serif" font-size="17" font-weight="700" fill="#202421">{value_text}</text>'
        )
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


def build_charts(agent_counts, cross, holdout):
    decision_order = [
        "no_further_investigation",
        "further_investigation",
        "insufficient_evidence",
    ]
    svg_bar_chart(
        distribution_chart_path,
        "Agent decisions across all 3,000 purchase orders",
        "Frozen V2.2; counts are deployment outputs, not ground-truth accuracy",
        [DECISION_LABELS[key] for key in decision_order],
        [agent_counts[key] for key in decision_order],
        [DECISION_COLORS[key] for key in decision_order],
    )
    disagreement_rows = [
        (
            "Rule no further -> Agent insufficient",
            cross[("no_further_investigation", "insufficient_evidence")],
            "#E0A526",
        ),
        (
            "Rule further -> Agent insufficient",
            cross[("further_investigation", "insufficient_evidence")],
            "#D6B45C",
        ),
        (
            "Rule further -> Agent no further",
            cross[("further_investigation", "no_further_investigation")],
            "#4E9F6D",
        ),
    ]
    svg_bar_chart(
        disagreement_chart_path,
        "Breakdown of 372 rule-Agent disagreements",
        "Most differences are explicit uncertainty decisions introduced by the Agent",
        [row[0] for row in disagreement_rows],
        [row[1] for row in disagreement_rows],
        [row[2] for row in disagreement_rows],
    )
    svg_bar_chart(
        holdout_chart_path,
        "Accuracy on 9 adjudicated holdout disagreements",
        "Independent frozen holdout; n=9 disagreements within 47 total cases",
        ["Rule baseline", "Frozen V2.2 Agent"],
        [holdout["rule_disagreement_accuracy"], holdout["agent_disagreement_accuracy"]],
        ["#75808A", "#3F7CAC"],
        percent=True,
    )


def build_report(metrics):
    full = metrics["full_3000"]
    holdout = metrics["final_holdout_47"]
    usage = metrics["usage_and_cost"]
    findings = full["top_primary_findings"]
    finding_lines = "\n".join(
        f"| `{name}` | {count:,} | {count / EXPECTED_FULL_COUNT:.1%} |"
        for name, count in findings[:10]
    )
    report = f"""# 冻结 V2.2 采购单流程调查实验报告

生成时间：{metrics['generated_at']}  
数据规模：3,000 份采购单，冻结版本指纹：`{metrics['frozen_agent_fingerprint']}`

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
| 规则基线 | {full['rule_decision_counts']['further_investigation']:,} | 0 | {full['rule_decision_counts']['no_further_investigation']:,} |
| 冻结 V2.2 Agent | {full['agent_decision_counts']['further_investigation']:,} | {full['agent_decision_counts']['insufficient_evidence']:,} | {full['agent_decision_counts']['no_further_investigation']:,} |

Agent 将二元规则分流扩展为三类决策，其中 361 份 PO（12.0%）被明确标记为证据不足。这一类别避免把日志状态缺失直接解释成业务异常或流程闭环。

## 3. 规则与 Agent 的差异

![Disagreement breakdown](figures/full_3000_disagreement_breakdown_v2_2.svg)

| 规则决策 | Agent决策 | PO数量 |
|---|---|---:|
| 需要进一步调查 | 需要进一步调查 | {full['cross_tab']['further_investigation -> further_investigation']:,} |
| 需要进一步调查 | 证据不足 | {full['cross_tab']['further_investigation -> insufficient_evidence']:,} |
| 需要进一步调查 | 无需进一步调查 | {full['cross_tab']['further_investigation -> no_further_investigation']:,} |
| 无需进一步调查 | 证据不足 | {full['cross_tab']['no_further_investigation -> insufficient_evidence']:,} |
| 无需进一步调查 | 无需进一步调查 | {full['cross_tab']['no_further_investigation -> no_further_investigation']:,} |

372 个分歧中有 361 个属于 Agent 使用 `insufficient_evidence`：252 个原本被规则视为无需调查，109 个原本被规则视为需要调查。仅 11 个案例被 Agent 从规则的需要调查降为无需调查。全量数据表明 Agent 的主要行为变化是增加不确定性表达，而不是普遍提高或降低调查量。

## 4. 冻结留出集比较

![Holdout disagreement accuracy](figures/holdout_disagreement_accuracy_v2_2.svg)

| 指标 | 规则 | Agent |
|---|---:|---:|
| 9个已裁决分歧案例的决策正确率 | {holdout['rule_disagreement_accuracy']:.1%} | {holdout['agent_disagreement_accuracy']:.1%} |
| 主要发现正确率 | 不适用 | {holdout['agent_primary_finding_accuracy']:.1%} |
| 重点行项目正确率 | 不适用 | {holdout['agent_focus_items_accuracy']:.1%} |

Agent 在分歧案例中净多判对 {holdout['agent_net_correct_advantage_cases']} 份 PO，对全部 47 个独立留出案例形成精确的相对优势：+{holdout['agent_accuracy_advantage_on_47'] * 100:.1f} 个百分点。该优势方向支持 Agent 相对于规则基线具有增量价值，但人工裁决的分歧样本只有 9 个，统计证据仍有限，不应把 77.8% 外推成全体 PO 的绝对准确率。

## 5. 主要发现分布

| Agent主要发现 | PO数量 | 占比 |
|---|---:|---:|
{finding_lines}

## 6. Token 与费用

| 项目 | Token |
|---|---:|
| 普通输入 | {usage['uncached_input_tokens']:,} |
| 缓存输入 | {usage['cached_input_tokens']:,} |
| 输出（含推理Token） | {usage['output_tokens']:,} |
| 总计 | {usage['total_tokens']:,} |

按生成报告时的 GPT-5.4 Mini Batch 单价估算，本次全量 API 费用约为 **${usage['estimated_batch_cost_usd']:.2f}**。这是依据返回的 Token 使用量计算的估值，最终金额以 OpenAI API 账单为准。

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

全量预测 SHA256：`{metrics['prediction_sha256']}`
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")


def main():
    full_records = load_jsonl(full_predictions_path)
    summary = json.loads(full_summary_path.read_text(encoding="utf-8"))
    state = json.loads(batch_state_path.read_text(encoding="utf-8"))
    verify_full_run(full_records, summary, state)

    agent_counts, rule_counts, cross, agreement_count, findings = full_metrics(
        full_records
    )
    holdout = holdout_metrics()
    usage = aggregate_raw_usage()
    if usage["total_tokens"] != summary["usage"]["total_tokens"]:
        raise SystemExit("原始 Batch Token 合计与全量汇总不一致。")
    estimated_cost = (
        usage["uncached_input_tokens"] / 1_000_000
        * BATCH_UNCACHED_INPUT_PRICE
        + usage["cached_input_tokens"] / 1_000_000
        * BATCH_CACHED_INPUT_PRICE
        + usage["output_tokens"] / 1_000_000
        * BATCH_OUTPUT_PRICE
    )
    usage["estimated_batch_cost_usd"] = round(estimated_cost, 4)
    usage["price_as_of"] = "2026-09-06"
    usage["price_source"] = (
        "https://developers.openai.com/api/docs/models/gpt-5.4-mini"
    )

    cross_serialized = {
        f"{rule} -> {agent}": count
        for (rule, agent), count in sorted(cross.items())
    }
    metrics = {
        "generated_at": summary["completed_at"],
        "frozen_agent_fingerprint": summary["frozen_agent_fingerprint"],
        "prediction_sha256": summary["prediction_sha256"],
        "full_3000": {
            "po_count": EXPECTED_FULL_COUNT,
            "agent_decision_counts": dict(sorted(agent_counts.items())),
            "rule_decision_counts": dict(sorted(rule_counts.items())),
            "agreement_count": agreement_count,
            "disagreement_count": EXPECTED_FULL_COUNT - agreement_count,
            "agreement_rate": agreement_count / EXPECTED_FULL_COUNT,
            "cross_tab": cross_serialized,
            "top_primary_findings": findings.most_common(),
        },
        "final_holdout_47": holdout,
        "usage_and_cost": usage,
        "interpretation_limits": [
            "全量3000例没有逐例人工标签，一致率不能解释为准确率。",
            "最终留出集中38个共同预测未人工复核，绝对准确率不可得。",
            "9个已裁决分歧案例可确定相对正确数量差，但样本量较小。",
            "开发集和初始盲测案例参与过V2.2调优，不作为最终无偏测试。",
        ],
    }
    write_json(metrics_path, metrics)

    write_csv(
        distribution_csv_path,
        ["system", "decision", "count", "share"],
        [
            ["agent", decision, agent_counts[decision], agent_counts[decision] / 3000]
            for decision in sorted(agent_counts)
        ]
        + [
            ["rule", decision, rule_counts[decision], rule_counts[decision] / 3000]
            for decision in sorted(rule_counts)
        ],
    )
    write_csv(
        crosstab_csv_path,
        ["rule_decision", "agent_decision", "count", "share_of_3000"],
        [
            [rule, agent, count, count / 3000]
            for (rule, agent), count in sorted(cross.items())
        ],
    )
    write_csv(
        holdout_csv_path,
        ["metric", "rule", "agent"],
        [
            [
                "decision_accuracy_on_9_adjudicated_disagreements",
                holdout["rule_disagreement_accuracy"],
                holdout["agent_disagreement_accuracy"],
            ],
            [
                "primary_finding_accuracy_on_9_adjudicated_disagreements",
                "",
                holdout["agent_primary_finding_accuracy"],
            ],
            [
                "focus_items_accuracy_on_9_adjudicated_disagreements",
                "",
                holdout["agent_focus_items_accuracy"],
            ],
            [
                "exact_relative_accuracy_advantage_on_all_47",
                0,
                holdout["agent_accuracy_advantage_on_47"],
            ],
        ],
    )
    build_charts(agent_counts, cross, holdout)
    build_report(metrics)

    print("最终实验包已生成：")
    print(f"报告：{report_path}")
    print(f"统计：{metrics_path}")
    print(f"图表目录：{figures_folder}")
    print(f"全量预测 SHA256：{summary['prediction_sha256']}")
    print(f"估算 Batch 费用：${estimated_cost:.2f}")


if __name__ == "__main__":
    main()
