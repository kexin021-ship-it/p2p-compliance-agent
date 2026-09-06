import json
import math
from collections import Counter, defaultdict
from datetime import datetime


CATEGORY_REQUIREMENTS = {
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

INVOICE_REVERSALS = {
    "Cancel Invoice Receipt",
    "Cancel Subsequent Invoice",
}


def parse_timestamp(event):
    value = event["attributes"]["time:timestamp"]
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def activity(event):
    return event["attributes"]["concept:name"]


def simplified_event(event):
    attributes = event["attributes"]
    return {
        "event_index": event["event_index"],
        "timestamp": attributes["time:timestamp"],
        "activity": attributes["concept:name"],
        "resource": (
            attributes.get("org:resource")
            or attributes.get("User")
            or "NONE"
        ),
    }


def events_named(events, names):
    return [event for event in events if activity(event) in names]


def add_once(values, value):
    if value not in values:
        values.append(value)


def has_original_at_or_before(originals, reversal):
    reversal_time = parse_timestamp(reversal)
    return any(
        parse_timestamp(original) <= reversal_time
        for original in originals
    )


def has_original_strictly_before(originals, reversal):
    reversal_time = parse_timestamp(reversal)
    return any(
        parse_timestamp(original) < reversal_time
        for original in originals
    )


def latest_time(events):
    if not events:
        return None
    return max(parse_timestamp(event) for event in events)


def earliest_time(events):
    if not events:
        return None
    return min(parse_timestamp(event) for event in events)


def build_item_summary(item, rule_item=None):
    events = item["events"]
    attributes = item["attributes"]
    item_category = attributes.get("Item Category")
    requirements = CATEGORY_REQUIREMENTS.get(item_category)

    by_activity = defaultdict(list)
    for event in events:
        by_activity[activity(event)].append(event)

    create_events = by_activity["Create Purchase Order Item"]
    gr_events = by_activity["Record Goods Receipt"]
    cancel_gr_events = by_activity["Cancel Goods Receipt"]
    invoice_events = by_activity["Record Invoice Receipt"]
    invoice_cancel_events = events_named(events, INVOICE_REVERSALS)
    clear_events = by_activity["Clear Invoice"]
    delete_events = by_activity["Delete Purchase Order Item"]
    reactivate_events = by_activity["Reactivate Purchase Order Item"]
    delivery_indicator_events = by_activity["Change Delivery Indicator"]
    vendor_invoice_events = by_activity["Vendor creates invoice"]
    vendor_debit_memo_events = by_activity["Vendor creates debit memo"]

    attention_signals = []
    uncertainty_signals = []
    resolution_signals = []

    if requirements is None:
        add_once(attention_signals, "unsupported_item_category")

    latest_delete = latest_time(delete_events)
    latest_reactivate = latest_time(reactivate_events)
    currently_deleted = bool(delete_events) and (
        latest_reactivate is None or latest_delete > latest_reactivate
    )

    if delete_events and reactivate_events:
        if latest_delete == latest_reactivate:
            add_once(
                uncertainty_signals,
                "delete_reactivate_order_uncertain",
            )
        elif latest_reactivate > latest_delete:
            add_once(
                attention_signals,
                "reactivated_after_delete_observed",
            )
    elif reactivate_events:
        prior_inactive = any(
            activity(event) == "Block Purchase Order Item"
            and parse_timestamp(event) < latest_reactivate
            for event in events
        )
        if not prior_inactive:
            add_once(
                attention_signals,
                "reactivate_without_prior_inactive_event_observed",
            )

    if currently_deleted:
        followon_events = events_named(events, FOLLOWON_ACTIVITIES)
        followon_after_delete = [
            event
            for event in followon_events
            if parse_timestamp(event) >= latest_delete
        ]
        if not followon_events:
            add_once(
                resolution_signals,
                "deleted_without_followon_observed",
            )
        elif followon_after_delete:
            add_once(
                attention_signals,
                "followon_activity_at_or_after_delete_observed",
            )

    orphan_gr_reversals = [
        event
        for event in cancel_gr_events
        if not has_original_at_or_before(gr_events, event)
    ]
    uncertain_gr_reversals = [
        event
        for event in cancel_gr_events
        if has_original_at_or_before(gr_events, event)
        and not has_original_strictly_before(gr_events, event)
    ]
    orphan_invoice_reversals = [
        event
        for event in invoice_cancel_events
        if not has_original_at_or_before(invoice_events, event)
    ]
    uncertain_invoice_reversals = [
        event
        for event in invoice_cancel_events
        if has_original_at_or_before(invoice_events, event)
        and not has_original_strictly_before(invoice_events, event)
    ]

    if orphan_gr_reversals:
        add_once(
            attention_signals,
            "gr_reversal_without_original_gr_observed",
        )
    if uncertain_gr_reversals:
        add_once(
            uncertainty_signals,
            "gr_reversal_original_order_uncertain",
        )
    if orphan_invoice_reversals:
        add_once(
            attention_signals,
            "invoice_reversal_without_original_invoice_observed",
        )
    if uncertain_invoice_reversals:
        add_once(
            uncertainty_signals,
            "invoice_reversal_original_order_uncertain",
        )

    for cancel_event in invoice_cancel_events:
        cancel_time = parse_timestamp(cancel_event)
        prior_clear = any(
            parse_timestamp(event) < cancel_time for event in clear_events
        )
        later_processing = any(
            parse_timestamp(event) > cancel_time
            for event in invoice_events + clear_events + delete_events
        )
        if prior_clear and not later_processing:
            add_once(
                attention_signals,
                "post_clear_invoice_reversal_observed",
            )
        elif later_processing:
            add_once(
                resolution_signals,
                "invoice_reversal_followed_by_later_processing",
            )

    first_create = earliest_time(create_events)
    first_vendor_invoice = earliest_time(vendor_invoice_events)
    first_vendor_debit_memo = earliest_time(vendor_debit_memo_events)
    if first_create is not None and first_vendor_invoice is not None:
        if first_vendor_invoice < first_create:
            add_once(
                attention_signals,
                "vendor_invoice_before_po_creation_observed",
            )
        elif first_vendor_invoice == first_create:
            add_once(
                uncertainty_signals,
                "vendor_invoice_po_creation_order_uncertain",
            )
    if first_create is not None and first_vendor_debit_memo is not None:
        if first_vendor_debit_memo < first_create:
            add_once(
                uncertainty_signals,
                "vendor_debit_memo_before_po_creation_time_needs_context",
            )

    if (
        item_category == "3-way match, invoice after GR"
        and gr_events
        and invoice_events
    ):
        first_gr = earliest_time(gr_events)
        first_invoice = earliest_time(invoice_events)
        if first_invoice < first_gr:
            add_once(
                attention_signals,
                "invoice_receipt_before_gr_for_after_gr_process",
            )
        elif first_invoice == first_gr:
            add_once(
                uncertainty_signals,
                "invoice_receipt_gr_order_uncertain",
            )

    if clear_events:
        first_clear = earliest_time(clear_events)
        first_invoice = earliest_time(invoice_events)
        if first_invoice is None or first_clear < first_invoice:
            add_once(
                attention_signals,
                "clear_without_prior_invoice_observed",
            )
        elif first_clear == first_invoice:
            add_once(
                uncertainty_signals,
                "clear_invoice_receipt_order_uncertain",
            )

        gr_expected = bool(
            requirements and requirements["goods_receipt_expected"]
        )
        first_gr = earliest_time(gr_events)
        if gr_expected and (first_gr is None or first_clear < first_gr):
            add_once(
                attention_signals,
                "clear_before_required_gr_observed",
            )
        elif gr_expected and first_clear == first_gr:
            add_once(
                uncertainty_signals,
                "clear_required_gr_order_uncertain",
            )

    if requirements and not currently_deleted:
        gr_expected = requirements["goods_receipt_expected"]
        invoice_expected = requirements["invoice_receipt_expected"]

        if gr_expected and not gr_events:
            add_once(
                attention_signals,
                "required_gr_not_observed",
            )
        if invoice_expected and not invoice_events:
            add_once(
                attention_signals,
                "required_invoice_receipt_not_observed",
            )
        if not invoice_expected and invoice_events:
            add_once(
                attention_signals,
                "invoice_receipt_observed_for_consignment",
            )

        missing_expected = (
            (gr_expected and not gr_events)
            or (invoice_expected and not invoice_events)
        )
        if delivery_indicator_events and missing_expected:
            add_once(
                uncertainty_signals,
                "process_state_uncertain_delivery_indicator_value_missing",
            )

            # 这些标签仍会保留在 rule_tags 中作为“未观察到事件”的
            # 原始事实，但在缺少交货标志新值时不能被当成已证实的
            # 未完成状态，因此不再放入明确 attention_signals。
            for missing_signal in (
                "required_gr_not_observed",
                "required_invoice_receipt_not_observed",
            ):
                if missing_signal in attention_signals:
                    attention_signals.remove(missing_signal)

        if (
            item_category == "Consignment"
            and gr_events
            and not delete_events
        ):
            add_once(
                uncertainty_signals,
                "consignment_received_final_state_not_observable",
            )

        # 发票入账已撤销、之后又清账时，清账可能是在处理正负应付
        # 项目，而不是在证明该采购行已经完成收货。若同时只有一个
        # 未给出新值的交货标志变更，最终交货状态仍应判为不确定。
        latest_invoice = latest_time(invoice_events)
        latest_invoice_cancel = latest_time(invoice_cancel_events)
        latest_clear = latest_time(clear_events)
        reversal_then_clear_without_gr = (
            gr_expected
            and not gr_events
            and delivery_indicator_events
            and latest_invoice is not None
            and latest_invoice_cancel is not None
            and latest_clear is not None
            and latest_invoice < latest_invoice_cancel < latest_clear
        )
        if reversal_then_clear_without_gr:
            if "clear_before_required_gr_observed" in attention_signals:
                attention_signals.remove(
                    "clear_before_required_gr_observed"
                )
            add_once(
                uncertainty_signals,
                "delivery_state_uncertain_after_invoice_reversal_and_clear",
            )
            add_once(
                resolution_signals,
                "financial_reversal_appears_processed_but_delivery_state_unknown",
            )

    if invoice_events and requirements and requirements["invoice_receipt_expected"]:
        latest_invoice = latest_time(invoice_events)
        latest_clear = latest_time(clear_events)
        later_invoice_reversal = any(
            parse_timestamp(event) > latest_invoice
            for event in invoice_cancel_events
        )

        if latest_clear is None and not later_invoice_reversal:
            add_once(
                attention_signals,
                "invoice_receipt_without_clear_observed",
            )
        elif (
            latest_clear is not None
            and latest_invoice > latest_clear
            and not later_invoice_reversal
        ):
            add_once(
                attention_signals,
                "latest_invoice_receipt_without_later_clear",
            )
        elif latest_clear is not None and latest_clear >= latest_invoice:
            add_once(resolution_signals, "invoice_cycle_appears_cleared")
        elif later_invoice_reversal:
            add_once(
                resolution_signals,
                "latest_invoice_receipt_followed_by_reversal",
            )

    if requirements and requirements["goods_receipt_expected"] and gr_events:
        latest_gr = latest_time(gr_events)
        latest_clear = latest_time(clear_events)
        later_gr_reversal = any(
            parse_timestamp(event) > latest_gr for event in cancel_gr_events
        )
        if (
            latest_clear is not None
            and latest_gr > latest_clear
            and not later_gr_reversal
        ):
            add_once(
                attention_signals,
                "latest_gr_without_later_invoice_clear_cycle",
            )
        elif later_gr_reversal:
            add_once(
                resolution_signals,
                "latest_goods_receipt_followed_by_reversal",
            )

    # 如果交货标志值缺失是唯一让“必需事件缺失”变得不确定的原因，
    # 将必需事件标签保留为观察事实，但明确给模型提供覆盖信号。
    if (
        "process_state_uncertain_delivery_indicator_value_missing"
        in uncertainty_signals
    ):
        add_once(
            resolution_signals,
            "do_not_treat_missing_required_events_as_proven_open_state",
        )

    rule_tags = []
    if rule_item is not None:
        rule_tags = [
            finding["tag"] for finding in rule_item.get("findings", [])
        ]

    activity_sequence = [activity(event) for event in events]
    activity_counts = dict(Counter(activity_sequence))

    simple_unexecuted_delete = (
        "deleted_without_followon_observed" in resolution_signals
        and not attention_signals
        and not uncertainty_signals
    )
    is_special = bool(
        attention_signals
        or uncertainty_signals
        or cancel_gr_events
        or invoice_cancel_events
        or reactivate_events
        or (delete_events and not simple_unexecuted_delete)
    )

    return {
        "item_number": item["item_number"],
        "case_id": item["case_id"],
        "item_category": item_category,
        "goods_receipt_required": attributes.get("Goods Receipt"),
        "gr_based_invoice_verification": attributes.get(
            "GR-Based Inv. Verif."
        ),
        "item_type": attributes.get("Item Type"),
        "event_count": len(events),
        "activity_sequence": activity_sequence,
        "activity_counts": activity_counts,
        "attention_signals": attention_signals,
        "uncertainty_signals": uncertainty_signals,
        "resolution_signals": resolution_signals,
        "rule_tags": rule_tags,
        "is_special": is_special,
        "events": [simplified_event(event) for event in events],
    }


def group_signature(item_summary):
    return (
        item_summary["item_category"],
        item_summary["goods_receipt_required"],
        item_summary["gr_based_invoice_verification"],
        item_summary["item_type"],
        tuple(item_summary["activity_sequence"]),
        tuple(item_summary["attention_signals"]),
        tuple(item_summary["uncertainty_signals"]),
        tuple(item_summary["resolution_signals"]),
        tuple(item_summary["rule_tags"]),
    )


def build_po_summary(po_record, rule_record):
    rule_items = {
        item["item_number"]: item for item in rule_record["items"]
    }
    item_summaries = [
        build_item_summary(
            item,
            rule_items.get(item["item_number"]),
        )
        for item in po_record["items"]
    ]

    grouped = defaultdict(list)
    for item_summary in item_summaries:
        grouped[group_signature(item_summary)].append(item_summary)

    groups = []
    for group_number, summaries in enumerate(grouped.values(), start=1):
        representative = summaries[0]
        groups.append({
            "group_id": f"G{group_number:03d}",
            "item_count": len(summaries),
            "item_numbers": [
                summary["item_number"] for summary in summaries
            ],
            "shared_attributes": {
                "item_category": representative["item_category"],
                "goods_receipt_required": representative[
                    "goods_receipt_required"
                ],
                "gr_based_invoice_verification": representative[
                    "gr_based_invoice_verification"
                ],
                "item_type": representative["item_type"],
            },
            "activity_sequence": representative["activity_sequence"],
            "activity_counts": representative["activity_counts"],
            "attention_signals": representative["attention_signals"],
            "uncertainty_signals": representative["uncertainty_signals"],
            "resolution_signals": representative["resolution_signals"],
            "rule_tags": representative["rule_tags"],
            "representative_item": {
                "item_number": representative["item_number"],
                "events": representative["events"],
            },
        })

    attention_counts = Counter()
    uncertainty_counts = Counter()
    resolution_counts = Counter()
    candidate_focus_items = []

    for summary in item_summaries:
        attention_counts.update(summary["attention_signals"])
        uncertainty_counts.update(summary["uncertainty_signals"])
        resolution_counts.update(summary["resolution_signals"])
        if summary["is_special"]:
            candidate_focus_items.append(summary["item_number"])

    total_items = len(item_summaries)
    systemic_threshold = max(2, math.ceil(total_items * 0.5))
    systemic_signals = [
        {
            "signal": signal,
            "affected_item_count": count,
            "affected_ratio": round(count / total_items, 4),
        }
        for signal, count in sorted(
            (attention_counts + uncertainty_counts).items()
        )
        if count >= systemic_threshold
    ]

    category_counts = Counter(
        summary["item_category"] for summary in item_summaries
    )

    return {
        "purchasing_document": po_record["purchasing_document"],
        "total_items": total_items,
        "po_rule_routing_status": rule_record["po_routing_status"],
        "aggregate_facts": {
            "item_category_counts": dict(category_counts),
            "attention_signal_counts": dict(attention_counts),
            "uncertainty_signal_counts": dict(uncertainty_counts),
            "resolution_signal_counts": dict(resolution_counts),
            "systemic_signals": systemic_signals,
            "candidate_focus_items": candidate_focus_items,
        },
        "item_pattern_groups": groups,
    }


def build_agent_input_v2(po_record, rule_record):
    return json.dumps(
        build_po_summary(po_record, rule_record),
        ensure_ascii=False,
        indent=2,
    )
