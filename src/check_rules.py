from pathlib import Path
from datetime import datetime
from collections import Counter
import json

project_folder = Path(__file__).resolve().parents[1]

input_path = (
    project_folder
    / "data"
    / "processed"
    / "purchase_orders.jsonl"
)

output_path = (
    project_folder
    / "outputs"
    / "rule_findings_closure_v1.jsonl"
)

REVERSAL_ACTIVITIES = {
    "Cancel Goods Receipt",
    "Cancel Invoice Receipt",
    "Cancel Subsequent Invoice",
}

# 这些活动说明删除前后已经出现了需要关注的业务活动。
FOLLOWON_ACTIVITIES = {
    "Record Service Entry Sheet",
    "Record Goods Receipt",
    "Cancel Goods Receipt",
    "Vendor creates invoice",
    "Vendor creates debit memo",
    "Record Invoice Receipt",
    "Cancel Invoice Receipt",
    "Cancel Subsequent Invoice",
    "Clear Invoice",
    "Set Payment Block",
    "Remove Payment Block",
}


def parse_timestamp(event):
    value = event["attributes"]["time:timestamp"]

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def event_evidence(events, activity_names):
    evidence = []

    for event in events:
        activity = event["attributes"]["concept:name"]

        if activity in activity_names:
            evidence.append({
                "event_index": event["event_index"],
                "activity": activity,
                "timestamp": event["attributes"]["time:timestamp"],
            })

    return evidence


def add_finding(findings, rule_id, tag, explanation, evidence):
    findings.append({
        "rule_id": rule_id,
        "tag": tag,
        "explanation": explanation,
        "evidence": evidence,
    })


def evaluate_lifecycle(item):
    events = item["events"]

    delete_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        == "Delete Purchase Order Item"
    ]

    reactivate_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        == "Reactivate Purchase Order Item"
    ]

    reversal_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        in REVERSAL_ACTIVITIES
    ]

    followon_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        in FOLLOWON_ACTIVITIES
    ]

    findings = []
    needs_further_analysis = False
    simple_checks_applicable = True

    currently_deleted = False

    # 先判断删除后是否又重新激活。
    if delete_events and reactivate_events:
        latest_delete_time = max(
            parse_timestamp(event)
            for event in delete_events
        )

        latest_reactivate_time = max(
            parse_timestamp(event)
            for event in reactivate_events
        )

        if latest_delete_time > latest_reactivate_time:
            currently_deleted = True

        elif latest_reactivate_time > latest_delete_time:
            needs_further_analysis = True

            add_finding(
                findings=findings,
                rule_id="L003",
                tag="reactivated_after_delete_observed",
                explanation=(
                    "观察到删除后重新激活，"
                    "需要进一步判断当前生命周期。"
                ),
                evidence=event_evidence(
                    events,
                    {
                        "Delete Purchase Order Item",
                        "Reactivate Purchase Order Item",
                    },
                ),
            )

        else:
            needs_further_analysis = True
            simple_checks_applicable = False

            add_finding(
                findings=findings,
                rule_id="L004",
                tag="delete_reactivate_order_uncertain",
                explanation=(
                    "删除与重新激活的时间完全相同，"
                    "无法判断当前生命周期状态。"
                ),
                evidence=event_evidence(
                    events,
                    {
                        "Delete Purchase Order Item",
                        "Reactivate Purchase Order Item",
                    },
                ),
            )

    elif delete_events:
        currently_deleted = True

    elif reactivate_events:
        needs_further_analysis = True

        add_finding(
            findings=findings,
            rule_id="L006",
            tag="reactivate_without_delete_observed",
            explanation=(
                "观察到重新激活，但日志中没有此前的删除事件，"
                "生命周期历史可能不完整。"
            ),
            evidence=event_evidence(
                events,
                {"Reactivate Purchase Order Item"},
            ),
        )

    # 当前最终状态仍为删除。
    if currently_deleted:
        simple_checks_applicable = False

        if followon_events:
            needs_further_analysis = True

            add_finding(
                findings=findings,
                rule_id="L002",
                tag="deleted_with_followon_activity",
                explanation=(
                    "行项目已删除，但日志中存在收货、"
                    "入账、开票或其他后续业务活动。"
                ),
                evidence=(
                    event_evidence(
                        events,
                        {"Delete Purchase Order Item"},
                    )
                    + event_evidence(
                        events,
                        FOLLOWON_ACTIVITIES,
                    )
                ),
            )

        else:
            add_finding(
                findings=findings,
                rule_id="L001",
                tag="deleted_without_followon_observed",
                explanation=(
                    "行项目已删除，且未观察到需要继续"
                    "完成收货、入账或清账的业务活动。"
                ),
                evidence=event_evidence(
                    events,
                    {"Delete Purchase Order Item"},
                ),
            )

    # 撤销标签可以与删除标签同时出现。
    if reversal_events:
        needs_further_analysis = True
        simple_checks_applicable = False

        add_finding(
            findings=findings,
            rule_id="L005",
            tag="reversal_present",
            explanation=(
                "观察到撤销事件，不能只根据某个事件"
                "是否曾经出现来判断当前状态。"
            ),
            evidence=event_evidence(
                events,
                REVERSAL_ACTIVITIES,
            ),
        )

    if needs_further_analysis:
        routing_status = "further_analysis"

    elif (
        currently_deleted
        and not followon_events
        and not reversal_events
    ):
        routing_status = "no_further_closure_analysis"

    else:
        routing_status = "continue_rule_checks"

    return {
        "case_id": item["case_id"],
        "item_number": item["item_number"],
        "item_category": item["attributes"].get("Item Category"),
        "routing_status": routing_status,
        "simple_checks_applicable": simple_checks_applicable,
        "findings": findings,
    }

def evaluate_required_events(item, result):
    # 生命周期复杂或已删除的行，不运行简单必需事件规则。
    if not result["simple_checks_applicable"]:
        result["required_event_rules_evaluated"] = False
        return result

    events = item["events"]
    activities = {
        event["attributes"]["concept:name"]
        for event in events
    }

    item_category = item["attributes"].get("Item Category")

    category_requirements = {
        "3-way match, invoice after GR": {
            "goods_receipt_expected": True,
            "invoice_receipt_expected": True,
        },
        "3-way match, invoice before GR": {
            "goods_receipt_expected": True,
            "invoice_receipt_expected": True,
        },
        "2-way match": {
            "goods_receipt_expected": False,
            "invoice_receipt_expected": True,
        },
        "Consignment": {
            "goods_receipt_expected": True,
            "invoice_receipt_expected": False,
        },
    }

    requirements = category_requirements.get(item_category)
    needs_further_analysis = False

    # 如果出现未知流程类型，不自行猜测适用规则。
    if requirements is None:
        needs_further_analysis = True

        add_finding(
            findings=result["findings"],
            rule_id="D000",
            tag="unsupported_item_category",
            explanation=(
                "没有找到该流程类型对应的必需事件规则。"
            ),
            evidence=event_evidence(
                events,
                {"Create Purchase Order Item"},
            ),
        )

        result["required_event_rules_evaluated"] = False
        result["routing_status"] = "further_analysis"
        return result

    has_goods_receipt = (
        "Record Goods Receipt" in activities
    )

    has_invoice_receipt = (
        "Record Invoice Receipt" in activities
    )

    # 需要收货，但日志中没有观察到收货。
    if (
        requirements["goods_receipt_expected"]
        and not has_goods_receipt
    ):
        needs_further_analysis = True

        add_finding(
            findings=result["findings"],
            rule_id="D001",
            tag="required_gr_not_observed",
            explanation=(
                "该流程类型预期收货，但截至日志末尾"
                "没有观察到收货记录。"
            ),
            evidence=event_evidence(
                events,
                {"Create Purchase Order Item"},
            ),
        )

    # 需要 PO 发票，但日志中没有观察到发票入账。
    if (
        requirements["invoice_receipt_expected"]
        and not has_invoice_receipt
    ):
        needs_further_analysis = True

        add_finding(
            findings=result["findings"],
            rule_id="D002",
            tag="required_invoice_receipt_not_observed",
            explanation=(
                "该流程类型预期发票入账，但截至日志末尾"
                "没有观察到发票入账记录。"
            ),
            evidence=event_evidence(
                events,
                {"Create Purchase Order Item"},
            ),
        )

    # Consignment 在 PO 层面不预期发票。
    if (
        not requirements["invoice_receipt_expected"]
        and has_invoice_receipt
    ):
        needs_further_analysis = True

        add_finding(
            findings=result["findings"],
            rule_id="D003",
            tag="invoice_receipt_observed_for_consignment",
            explanation=(
                "该行属于 Consignment，但在 PO 行层面"
                "观察到了发票入账，需要进一步核对。"
            ),
            evidence=event_evidence(
                events,
                {"Record Invoice Receipt"},
            ),
        )

    result["required_event_rules_evaluated"] = True

    if needs_further_analysis:
        result["routing_status"] = "further_analysis"

    return result

def evaluate_sequence_rules(item, result):
    if not result["simple_checks_applicable"]:
        result["sequence_rules_evaluated"] = False
        return result

    item_category = item["attributes"].get("Item Category")

    supported_categories = {
        "3-way match, invoice after GR",
        "3-way match, invoice before GR",
        "2-way match",
        "Consignment",
    }

    if item_category not in supported_categories:
        result["sequence_rules_evaluated"] = False
        return result

    events = item["events"]

    goods_receipt_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        == "Record Goods Receipt"
    ]

    invoice_receipt_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        == "Record Invoice Receipt"
    ]

    clear_invoice_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        == "Clear Invoice"
    ]

    needs_further_analysis = False

    # S001：
    # invoice after GR 流程中，发票入账严格早于首次收货。
    if (
        item_category == "3-way match, invoice after GR"
        and goods_receipt_events
        and invoice_receipt_events
    ):
        first_gr_time = min(
            parse_timestamp(event)
            for event in goods_receipt_events
        )

        first_invoice_time = min(
            parse_timestamp(event)
            for event in invoice_receipt_events
        )

        if first_invoice_time < first_gr_time:
            needs_further_analysis = True

            add_finding(
                findings=result["findings"],
                rule_id="S001",
                tag="invoice_receipt_before_gr_for_after_gr_process",
                explanation=(
                    "该行声明为 invoice after GR，"
                    "但首次发票入账严格早于首次收货。"
                ),
                evidence=event_evidence(
                    events,
                    {
                        "Record Goods Receipt",
                        "Record Invoice Receipt",
                    },
                ),
            )

    if clear_invoice_events:
        first_clear_time = min(
            parse_timestamp(event)
            for event in clear_invoice_events
        )

        # S002：
        # 清账前没有观察到发票入账。
        if not invoice_receipt_events:
            needs_further_analysis = True

            add_finding(
                findings=result["findings"],
                rule_id="S002",
                tag="clear_without_prior_invoice_observed",
                explanation=(
                    "观察到发票清账，但日志中没有观察到"
                    "此前的发票入账记录。"
                ),
                evidence=event_evidence(
                    events,
                    {"Clear Invoice"},
                ),
            )

        else:
            first_invoice_time = min(
                parse_timestamp(event)
                for event in invoice_receipt_events
            )

            if first_clear_time < first_invoice_time:
                needs_further_analysis = True

                add_finding(
                    findings=result["findings"],
                    rule_id="S002",
                    tag="clear_without_prior_invoice_observed",
                    explanation=(
                        "首次清账严格早于首次观察到的"
                        "发票入账，事件历史可能不完整。"
                    ),
                    evidence=event_evidence(
                        events,
                        {
                            "Record Invoice Receipt",
                            "Clear Invoice",
                        },
                    ),
                )

        # S003：
        # 对需要收货的流程，清账不能严格早于首次收货。
        gr_required_categories = {
            "3-way match, invoice after GR",
            "3-way match, invoice before GR",
        }

        if item_category in gr_required_categories:
            if not goods_receipt_events:
                needs_further_analysis = True

                add_finding(
                    findings=result["findings"],
                    rule_id="S003",
                    tag="clear_before_required_gr_observed",
                    explanation=(
                        "该流程要求收货，但在观察到清账时，"
                        "日志中没有收货记录。"
                    ),
                    evidence=event_evidence(
                        events,
                        {"Clear Invoice"},
                    ),
                )

            else:
                first_gr_time = min(
                    parse_timestamp(event)
                    for event in goods_receipt_events
                )

                if first_clear_time < first_gr_time:
                    needs_further_analysis = True

                    add_finding(
                        findings=result["findings"],
                        rule_id="S003",
                        tag="clear_before_required_gr_observed",
                        explanation=(
                            "首次清账严格早于首次收货。"
                        ),
                        evidence=event_evidence(
                            events,
                            {
                                "Record Goods Receipt",
                                "Clear Invoice",
                            },
                        ),
                    )

    result["sequence_rules_evaluated"] = True

    if needs_further_analysis:
        result["routing_status"] = "further_analysis"

    return result

def evaluate_closure_rules(item, result):
    if not result["simple_checks_applicable"]:
        result["closure_rules_evaluated"] = False
        return result

    item_category = item["attributes"].get("Item Category")

    invoice_expected_categories = {
        "3-way match, invoice after GR",
        "3-way match, invoice before GR",
        "2-way match",
    }

    gr_required_categories = {
        "3-way match, invoice after GR",
        "3-way match, invoice before GR",
    }

    supported_categories = (
        invoice_expected_categories
        | {"Consignment"}
    )

    if item_category not in supported_categories:
        result["closure_rules_evaluated"] = False
        return result

    events = item["events"]

    goods_receipt_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        == "Record Goods Receipt"
    ]

    invoice_receipt_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        == "Record Invoice Receipt"
    ]

    clear_invoice_events = [
        event
        for event in events
        if event["attributes"]["concept:name"]
        == "Clear Invoice"
    ]

    needs_further_analysis = False
    closure_pattern_observed = False

    invoice_expected = (
        item_category in invoice_expected_categories
    )

    gr_required = (
        item_category in gr_required_categories
    )

    if invoice_expected and invoice_receipt_events:
        # C001：已经入账，但完全没有观察到清账。
        if not clear_invoice_events:
            needs_further_analysis = True

            add_finding(
                findings=result["findings"],
                rule_id="C001",
                tag="invoice_receipt_without_clear_observed",
                explanation=(
                    "观察到发票入账，但截至日志末尾"
                    "没有观察到发票清账。"
                ),
                evidence=event_evidence(
                    events,
                    {"Record Invoice Receipt"},
                ),
            )

        else:
            latest_invoice_time = max(
                parse_timestamp(event)
                for event in invoice_receipt_events
            )

            latest_clear_time = max(
                parse_timestamp(event)
                for event in clear_invoice_events
            )

            # C002：最近一次入账发生在最近一次清账之后。
            if latest_invoice_time > latest_clear_time:
                needs_further_analysis = True

                add_finding(
                    findings=result["findings"],
                    rule_id="C002",
                    tag=(
                        "latest_invoice_receipt_without_"
                        "later_clear"
                    ),
                    explanation=(
                        "最近一次发票入账发生在最近一次"
                        "清账之后，未观察到后续清账。"
                    ),
                    evidence=event_evidence(
                        events,
                        {
                            "Record Invoice Receipt",
                            "Clear Invoice",
                        },
                    ),
                )

            latest_gr_time = None

            if gr_required and goods_receipt_events:
                latest_gr_time = max(
                    parse_timestamp(event)
                    for event in goods_receipt_events
                )

                # C003：最近一次收货发生在最近一次清账之后。
                if latest_gr_time > latest_clear_time:
                    needs_further_analysis = True

                    add_finding(
                        findings=result["findings"],
                        rule_id="C003",
                        tag=(
                            "latest_gr_without_later_"
                            "invoice_clear_cycle"
                        ),
                        explanation=(
                            "最近一次收货发生在最近一次清账"
                            "之后，可能存在尚未完成的新一轮"
                            "入账与清账。"
                        ),
                        evidence=event_evidence(
                            events,
                            {
                                "Record Goods Receipt",
                                "Record Invoice Receipt",
                                "Clear Invoice",
                            },
                        ),
                    )

            # 只有没有其他规则疑点时，才记录闭环迹象。
            if (
                latest_clear_time >= latest_invoice_time
                and (
                    not gr_required
                    or (
                        latest_gr_time is not None
                        and latest_clear_time >= latest_gr_time
                    )
                )
            ):
                closure_pattern_observed = True

    result["closure_rules_evaluated"] = True

    if needs_further_analysis:
        result["routing_status"] = "further_analysis"

    elif (
        closure_pattern_observed
        and result["routing_status"] == "continue_rule_checks"
    ):
        add_finding(
            findings=result["findings"],
            rule_id="C004",
            tag="line_appears_closed",
            explanation=(
                "截至日志末尾，最近一次清账不早于"
                "最近一次所需收货和发票入账。"
                "这表示观察到闭环迹象，"
                "不代表已经证明足额付款。"
            ),
            evidence=event_evidence(
                events,
                {
                    "Record Goods Receipt",
                    "Record Invoice Receipt",
                    "Clear Invoice",
                },
            ),
        )

        result["routing_status"] = "no_rule_signal"

    elif result["routing_status"] == "continue_rule_checks":
        result["routing_status"] = "no_rule_signal"

    return result

if output_path.exists():
    raise SystemExit(
        f"输出文件已存在，为避免覆盖，本次停止：{output_path}"
    )

output_path.parent.mkdir(parents=True, exist_ok=True)

po_count = 0
item_count = 0
tag_counts = Counter()
po_route_counts = Counter()

with input_path.open("r", encoding="utf-8") as source:
    with output_path.open("x", encoding="utf-8") as target:
        for line in source:
            purchase_order = json.loads(line)

            item_results = []

            for item in purchase_order["items"]:

                lifecycle_result = evaluate_lifecycle(item)

                required_event_result = evaluate_required_events(
                    item,
                    lifecycle_result,
                )

                sequence_result = evaluate_sequence_rules(
                    item,
                    required_event_result,
                )

                result = evaluate_closure_rules(
                    item,
                    sequence_result,
                )

                item_results.append(result)
                item_count += 1

                for finding in result["findings"]:
                    tag_counts[finding["tag"]] += 1

            if any(
                item["routing_status"] == "further_analysis"
                for item in item_results
            ):
                po_routing_status = "further_analysis"

            elif all(
                item["routing_status"]
                == "no_further_closure_analysis"
                for item in item_results
            ):
                po_routing_status = "no_further_closure_analysis"

            else:
                po_routing_status = "no_rule_signal"

            po_route_counts[po_routing_status] += 1
            po_count += 1

            output_record = {
                "purchasing_document":
                    purchase_order["purchasing_document"],
                "po_routing_status": po_routing_status,
                "items": item_results,
            }

            target.write(
                json.dumps(
                    output_record,
                    ensure_ascii=False,
                )
                + "\n"
            )

if po_count != 3000 or item_count != 10059:
    raise ValueError(
        "规则输出数量与输入不一致："
        f"PO={po_count}，行项目={item_count}"
    )

print("\n规则检查完成：")
print(f"PO 数量：{po_count:,}")
print(f"行项目数量：{item_count:,}")

print("\n规则标签数量：")
for tag, count in sorted(tag_counts.items()):
    print(f"{tag}: {count:,}")

print("\nPO 分流数量：")
for route, count in sorted(po_route_counts.items()):
    print(f"{route}: {count:,}")

print(f"\n输出文件：{output_path}")