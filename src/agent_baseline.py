import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel
from openai import OpenAI


class Decision(str, Enum):
    further_investigation = "further_investigation"
    no_further_investigation = "no_further_investigation"
    insufficient_evidence = "insufficient_evidence"


class EvidenceItem(BaseModel):
    item_number: str
    activity: str
    timestamp: str
    significance: str


class AgentDecision(BaseModel):
    purchasing_document: str
    decision: Decision
    primary_finding: str
    focus_items: list[str]
    observed_facts: list[str]
    evidence: list[EvidenceItem]
    possible_causes: list[str]
    missing_information: list[str]
    decision_reason: str
    recommended_action: str

SYSTEM_PROMPT = """
你是采购到付款（P2P）流程调查 Agent。

你的任务是以整份采购单为单位，结合全部行项目的原始事件时间线和规则检查结果，判断是否需要进一步调查。

规则检查结果只是调查线索，不是标准答案。你必须独立检查原始事件。

决策定义：

1. further_investigation
存在具体、可观察的未解决流程缺口、异常顺序、撤销后未处理状态或事件历史矛盾，需要人工继续调查。

2. no_further_investigation
事件时间线已经充分显示流程闭环，或者行项目在没有未解决后续活动的情况下被合理撤回或终止。

3. insufficient_evidence
现有日志不能确定最终状态，关键字段或事件关联缺失，但也没有足够依据认定存在具体未解决问题。

业务解释：

- Vendor creates invoice 是供应商侧开票，不等于 SAP 中已经完成发票入账。
- Record Invoice Receipt 才表示观察到发票入账。
- Clear Invoice 表示应付项目已清账，但不一定代表现金付款，也不能证明金额已经足额匹配。
- invoice before GR 流程允许发票先于收货。
- invoice after GR 流程要求发票入账不能严格早于收货。
- 撤销事件不能单独视为异常，必须检查撤销后是否重新执行、清账或终止。
- 收货后撤销收货，再删除行项目，并且没有发票相关活动，可以视为流程上已撤回并终止。
- 发票撤销后出现更晚的清账，并且没有新的后续活动，可以视为流程上已经处理。
- 清账后又发生发票撤销，且没有更晚的处理，是需要调查的信号。
- 两个事件时间完全相同表示顺序不确定，不能根据 JSON 或 XES 中的排列顺序判断先后。
- Change Delivery Indicator 没有提供变更后的值时，不能据此确认行项目已经关闭。
- required_gr_not_observed 和 required_invoice_receipt_not_observed 只表示日志中未观察到相应事件，不能单独证明流程仍未完成。
- 如果某行出现 Change Delivery Indicator，但没有记录变更后的标志值，并且没有其他明确事件证明它仍在执行或已经终止，则不能仅凭缺少收货或发票入账判为 further_investigation。此时应判为 insufficient_evidence，primary_finding 使用 process_state_uncertain。
- 只有缺少预期事件、同时也没有任何可能表示终止或关闭但状态值缺失的事件时，才可以将缺少收货或发票入账作为 further_investigation 的依据。
- observed_facts 只列出与最终 decision 直接相关的关键事实，最多 6 条，不要逐项复述所有正常行项目。
- evidence 只列出支持最终 decision 的关键事件，最多 8 条。
- 现有数据不支持金额匹配、重复发票确认、足额付款确认或审批合规判断。
- Vendor creates invoice 严格早于 Create Purchase Order Item 是需要调查的历史顺序疑点。即使后续已经收货、发票入账和清账，也不能用后续闭环消除该顺序疑点。此时判为 further_investigation，primary_finding 使用 vendor_invoice_before_po_creation_observed。可以将追溯建单、错误关联或时间字段含义差异列为可能原因，但不能因此忽略该疑点。
- 已经观察到 Record Invoice Receipt，但截至日志末尾没有观察到能够处理该入账的后续 Clear Invoice 或撤销处理，表示存在具体的未闭环状态，应判为 further_investigation，primary_finding 使用 invoice_receipt_without_clear_observed。
- 发票入账可能尚未到期、处于付款冻结状态或受到观察窗口截断影响，这些属于可能原因和缺失信息，不能把 decision 降为 insufficient_evidence，也不能直接认定逾期或违规。
- 关于 Change Delivery Indicator 的 insufficient_evidence 例外，只适用于“缺少收货或发票入账是唯一疑点”的情况；它不能覆盖未处理的发票入账或其他明确顺序疑点。
- Block Purchase Order Item 后出现 Reactivate Purchase Order Item，可以解释为冻结后重新激活，不能仅因没有 Delete Purchase Order Item 就认定生命周期历史缺失。
主要发现选择优先级：

当一份 PO 同时存在多个 finding 时，primary_finding 不得随意选择。按照以下优先级选择最先适用的标签，其他问题仍需写入 observed_facts、focus_items 和 decision_reason：

1. vendor_invoice_before_po_creation_observed
2. clear_without_prior_invoice_observed
3. clear_before_required_gr_observed
4. reversal_without_original_event_observed
5. invoice_receipt_without_clear_observed
6. latest_invoice_receipt_without_later_clear
7. 其他未解决的撤销或生命周期矛盾
8. required_gr_not_observed 或 required_invoice_receipt_not_observed
9. process_state_uncertain
10. process_appears_resolved

上述优先级只用于对已经成立的 finding 排序，不能改变各 finding 的成立条件，也不能覆盖前面的业务判断规则。

- 当 Change Delivery Indicator 缺少变更后的值，并且没有其他正向证据证明行项目仍在执行或已经终止时，required_gr_not_observed 和 required_invoice_receipt_not_observed 不属于已经成立的 finding。此时必须使用 process_state_uncertain。
- invoice_receipt_without_clear_observed 只用于某个行项目存在 Record Invoice Receipt、但该行项目完全没有观察到 Clear Invoice 的情况。
- latest_invoice_receipt_without_later_clear 只用于该行项目曾经存在 Clear Invoice，但之后又出现新的 Record Invoice Receipt，且没有更晚清账的情况。
- 如果不同的行项目分别符合以上两个发票标签，primary_finding 优先使用 invoice_receipt_without_clear_observed，同时在 observed_facts 中保留另一个问题。
- 如果 Clear Invoice 之后发生 Cancel Invoice Receipt，并且没有更晚的处理，primary_finding 使用 post_clear_invoice_reversal_observed，不要使用笼统的 other_unresolved_lifecycle_contradiction。
- Vendor creates debit memo 等非 Vendor creates invoice 的供应商侧单据早于 PO 行创建时，将该行列入 focus_items 并说明时间含义需要确认，但不要仅凭这一事件将 decision 改为 further_investigation。

focus_items 完整性要求：

- focus_items 不是代表性抽样，必须列出所有存在未解决问题、状态不确定、历史顺序疑点或需要特殊解释的行项目。
- 如果多个行项目存在相同问题，也必须逐一列出，不能只列几个代表。
- 不能因为某个行项目已经足以决定整份 PO 的 decision，就省略其他存在次要问题的行项目。
- 已经解决但包含非简单撤销、重新执行或特殊终止过程的行项目，也应列入 focus_items；普通的正常闭环行和未执行便删除的简单行项目不必列入。
- Vendor creates debit memo 等供应商侧单据事件早于 PO 行创建时，应将该行列入 focus_items 并说明时间含义需要确认；但如果不是 Vendor creates invoice，不要仅凭该事件自动认定违规。

输出要求：

- 以整份 PO 作出一个最终 decision。
- focus_items 只列出影响最终判断的行项目编号。
- observed_facts 只能写日志直接支持的事实。
- possible_causes 必须写成可能性，不得当作事实。
- missing_information 列出确认判断所需但数据中不存在的信息。
- evidence 必须引用输入中真实存在的事件名称、行项目编号和时间。
- 不要因为规则产生了标签就自动判定需要调查。
- primary_finding 必须是简短的英文 snake_case 标签，例如 process_appears_resolved、invoice_receipt_without_clear_observed 或 process_state_uncertain，不能填写完整句子。
- focus_items 只列出存在异常、状态不确定或需要特别解释的关键行项目。不要为了完整性列出所有正常闭环行；如果没有关键行项目，可以返回空列表。
- evidence 中每个对象只能引用一个原始事件。activity 必须是一个输入中真实存在的事件名称，timestamp 也只能填写该事件的一个时间；不得使用斜杠合并多个事件。
- 没有金额和数量匹配数据时，不得声称已经完成完整三方匹配，只能表述为观察到流程闭环迹象。
- 除 decision、primary_finding、原始事件名称和其他固定系统值以外，所有解释性文字必须使用中文。
- 如果规则结果中的某个 tag 能准确描述最主要问题，primary_finding 应优先原样使用该 tag，不要创造同义的新标签。最近一次发票入账晚于最近一次清账且没有更晚清账时，统一使用 latest_invoice_receipt_without_later_clear。
"""

project_folder = Path(__file__).resolve().parents[1]

po_data_path = (
    project_folder
    / "data"
    / "processed"
    / "purchase_orders.jsonl"
)

rule_results_path = (
    project_folder
    / "outputs"
    / "rule_findings_v2.jsonl"
)

test_po_id = "4507028502"


def load_jsonl_record(path, purchasing_document):
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            record = json.loads(line)

            if (
                record["purchasing_document"]
                == purchasing_document
            ):
                return record

    raise ValueError(
        f"找不到采购单：{purchasing_document}"
    )

def build_agent_input(po_record, rule_record):
    rule_items = {
        item["item_number"]: item
        for item in rule_record["items"]
    }

    simplified_items = []

    for item in po_record["items"]:
        item_number = item["item_number"]
        attributes = item["attributes"]
        rule_item = rule_items[item_number]

        simplified_events = []

        for event in item["events"]:
            event_attributes = event["attributes"]

            simplified_events.append({
                "event_index": event["event_index"],
                "timestamp": event_attributes[
                    "time:timestamp"
                ],
                "activity": event_attributes[
                    "concept:name"
                ],
                "resource": (
                    event_attributes.get("org:resource")
                    or event_attributes.get("User")
                    or "NONE"
                ),
            })

        simplified_findings = [
            {
                "rule_id": finding["rule_id"],
                "tag": finding["tag"],
                "explanation": finding["explanation"],
            }
            for finding in rule_item["findings"]
        ]

        simplified_items.append({
            "case_id": item["case_id"],
            "item_number": item_number,
            "item_category": attributes.get(
                "Item Category"
            ),
            "goods_receipt_required": attributes.get(
                "Goods Receipt"
            ),
            "gr_based_invoice_verification": attributes.get(
                "GR-Based Inv. Verif."
            ),
            "item_type": attributes.get("Item Type"),
            "events": simplified_events,
            "rule_routing_status": rule_item[
                "routing_status"
            ],
            "rule_findings": simplified_findings,
        })

    agent_input = {
        "purchasing_document": po_record[
            "purchasing_document"
        ],
        "po_rule_routing_status": rule_record[
            "po_routing_status"
        ],
        "items": simplified_items,
    }

    return json.dumps(
        agent_input,
        ensure_ascii=False,
        indent=2,
    )

if __name__ == "__main__":
    po_record = load_jsonl_record(
        po_data_path,
        test_po_id,
    )

    rule_record = load_jsonl_record(
        rule_results_path,
        test_po_id,
    )

    agent_input = build_agent_input(
        po_record,
        rule_record,
    )

    client = OpenAI()

    response = client.responses.parse(
        model="gpt-5.4-mini-2026-03-17",
        input=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": agent_input,
            },
        ],
        reasoning={"effort": "low"},
        text_format=AgentDecision,
        store=False,
    )

    agent_decision = response.output_parsed

    if agent_decision is None:
        raise RuntimeError("模型没有返回可解析的结果")

    print(
        json.dumps(
            agent_decision.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )

    if response.usage:
        print("\nToken 使用量：")
        print(f"输入：{response.usage.input_tokens}")
        print(f"输出：{response.usage.output_tokens}")
        print(f"合计：{response.usage.total_tokens}")