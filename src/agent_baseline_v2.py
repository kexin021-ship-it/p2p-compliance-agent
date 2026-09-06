import json
import sys

from openai import OpenAI

from agent_baseline_frozen_v1 import (
    AgentDecision,
    SYSTEM_PROMPT as BASE_SYSTEM_PROMPT,
    load_jsonl_record,
    po_data_path,
    rule_results_path,
)
from po_summary_v2 import build_po_summary


MODEL = "gpt-5.4-mini-2026-03-17"
REASONING_EFFORT = "low"

SYSTEM_PROMPT_V2 = BASE_SYSTEM_PROMPT + """

V2 结构化事实摘要使用方法：

- 输入不再逐行重复展示整份 PO，而是把相同事件模式的行项目合并为 item_pattern_groups。item_numbers 是该组包含的完整行号；representative_item 是其中一个真实行项目及其原始事件。
- aggregate_facts 中的计数由程序直接从全部原始事件计算，不是模型推测。必须先检查 systemic_signals 和 attention_signal_counts，再阅读各模式组。
- attention_signals 表示明确需要解释的事实信号；uncertainty_signals 表示现有日志不足以确定状态；resolution_signals 表示观察到的后续处理或终止迹象。它们仍是证据摘要，不是最终标准答案。
- 如果 gr_reversal_without_original_gr_observed 或 invoice_reversal_without_original_invoice_observed 大量、系统性地影响行项目，应判为 further_investigation，primary_finding 使用 reversal_without_original_event_observed。不能因为行项目数量很多而忽略这一模式。
- process_state_uncertain_delivery_indicator_value_missing 表示缺少预期事件，但 Change Delivery Indicator 没有变更后的值。没有更强的明确问题时，应判为 insufficient_evidence，primary_finding 使用 process_state_uncertain。
- 即使同一行的发票撤销后出现更晚清账，也只能说明财务撤销存在后续处理，不能证明缺少新值的 Change Delivery Indicator 已经把交货状态关闭。不得仅凭后续清账把这类行判为 no_further_investigation。
- delivery_state_uncertain_after_invoice_reversal_and_clear 表示发票撤销及后续清账看起来已经处理，但所需收货未观察到，且交货标志新值缺失。没有其他更强问题时，必须判为 insufficient_evidence，primary_finding 使用 process_state_uncertain。
- 当摘要层已经依据撤销后清账的上下文，把 required_gr_not_observed 或 clear_before_required_gr_observed 从 attention_signals 移除时，这些标签即使仍出现在 rule_tags 中也只是规则线索，不能覆盖 uncertainty_signals。
- consignment_received_final_state_not_observable 表示寄售行已收货，但现有 PO 日志不能观察寄售领用、结算或最终关闭状态。没有更强的明确问题时，应判为 insufficient_evidence，primary_finding 使用 process_state_uncertain；不得仅凭没有普通发票入账判为异常，也不得直接认定已结束。
- do_not_treat_missing_required_events_as_proven_open_state 是对 required_gr_not_observed 或 required_invoice_receipt_not_observed 的状态不确定覆盖提示，只在没有更强问题时使用。
- candidate_focus_items 是需要重点解释的完整候选集合。最终 focus_items 仍须按业务判断筛选；普通闭环行和未执行便删除的简单行不应列入。
- 如果一个模式组的所有行均存在同一未解决问题或状态不确定，focus_items 必须包含该组 item_numbers 中的全部行号，不能只返回 representative_item。
- evidence 必须引用 representative_item.events 中真实存在的一条事件。不要把聚合计数伪装成单条原始事件。
"""


ORPHAN_REVERSAL_SIGNALS = {
    "gr_reversal_without_original_gr_observed",
    "invoice_reversal_without_original_invoice_observed",
}


def reconcile_focus_items(decision, po_summary):
    """Expand grouped focus items deterministically and keep PO order."""
    groups = po_summary["item_pattern_groups"]
    grouped_items = [
        item_number
        for group in groups
        for item_number in group["item_numbers"]
    ]
    candidate_items = po_summary["aggregate_facts"]["candidate_focus_items"]
    candidate_set = set(candidate_items)
    ordered_items = candidate_items + [
        item_number
        for item_number in grouped_items
        if item_number not in candidate_set
    ]
    valid_items = set(ordered_items)
    reconciled = {
        item_number
        for item_number in decision.focus_items
        if item_number in valid_items
    }

    # Identical pattern groups are compressed only for model input. If the
    # model selects any member, every member has the same relevant evidence.
    for group in groups:
        members = set(group["item_numbers"])
        if reconciled & members:
            reconciled.update(members)

    # Large lists are easy for a model to truncate while enumerating. For the
    # systemic orphan-reversal finding, the program already knows exactly
    # which complete groups exhibit the finding, so expand them here.
    if decision.primary_finding == "reversal_without_original_event_observed":
        for group in groups:
            if ORPHAN_REVERSAL_SIGNALS & set(group["attention_signals"]):
                reconciled.update(group["item_numbers"])

    return [item for item in ordered_items if item in reconciled]


def run_agent_v2(purchasing_document, client=None):
    po_record = load_jsonl_record(po_data_path, purchasing_document)
    rule_record = load_jsonl_record(
        rule_results_path,
        purchasing_document,
    )
    po_summary = build_po_summary(po_record, rule_record)
    agent_input = json.dumps(po_summary, ensure_ascii=False, indent=2)
    api_client = client or OpenAI()

    response = api_client.responses.parse(
        model=MODEL,
        input=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT_V2,
            },
            {
                "role": "user",
                "content": agent_input,
            },
        ],
        reasoning={"effort": REASONING_EFFORT},
        text_format=AgentDecision,
        store=False,
    )

    if response.output_parsed is None:
        raise RuntimeError("模型没有返回可解析的结果")

    response.output_parsed.focus_items = reconcile_focus_items(
        response.output_parsed,
        po_summary,
    )

    return response


if __name__ == "__main__":
    po_id = sys.argv[1] if len(sys.argv) > 1 else "4507021416"
    result = run_agent_v2(po_id)
    print(
        json.dumps(
            result.output_parsed.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
