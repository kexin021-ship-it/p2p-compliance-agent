import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


project_folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_folder / "src"))

from agent_baseline_frozen_v1 import (  # noqa: E402
    load_jsonl_record,
    po_data_path,
    rule_results_path,
)
from agent_baseline_v2 import reconcile_focus_items  # noqa: E402
from po_summary_v2 import (  # noqa: E402
    build_item_summary,
    build_po_summary,
)


def load_records(po_id):
    return (
        load_jsonl_record(po_data_path, po_id),
        load_jsonl_record(rule_results_path, po_id),
    )


def item_summary(po_id, item_number):
    po_record, rule_record = load_records(po_id)
    po_item = next(
        item
        for item in po_record["items"]
        if item["item_number"] == item_number
    )
    rule_item = next(
        item
        for item in rule_record["items"]
        if item["item_number"] == item_number
    )
    return build_item_summary(po_item, rule_item)


class PoSummaryV2Tests(unittest.TestCase):
    def test_consignment_received_state_is_uncertain(self):
        summary = item_summary("4508048533", "00010")
        self.assertIn(
            "consignment_received_final_state_not_observable",
            summary["uncertainty_signals"],
        )
        self.assertNotIn(
            "required_invoice_receipt_not_observed",
            summary["attention_signals"],
        )

    def test_delivery_indicator_missing_value_overrides_missing_events(self):
        summary = item_summary("4507004049", "00030")
        self.assertIn(
            "process_state_uncertain_delivery_indicator_value_missing",
            summary["uncertainty_signals"],
        )
        self.assertIn(
            "do_not_treat_missing_required_events_as_proven_open_state",
            summary["resolution_signals"],
        )
        self.assertIn(
            "delivery_state_uncertain_after_invoice_reversal_and_clear",
            summary["uncertainty_signals"],
        )
        self.assertNotIn(
            "required_gr_not_observed",
            summary["attention_signals"],
        )
        self.assertNotIn(
            "clear_before_required_gr_observed",
            summary["attention_signals"],
        )

    def test_two_delivery_indicator_items_are_preserved(self):
        po_record, rule_record = load_records("4507013117")
        summary = build_po_summary(po_record, rule_record)
        focus = summary["aggregate_facts"]["candidate_focus_items"]
        self.assertIn("00040", focus)
        self.assertIn("00150", focus)

    def test_systemic_orphan_reversals_are_counted_and_compressed(self):
        po_record, rule_record = load_records("4507021416")
        summary = build_po_summary(po_record, rule_record)
        aggregate = summary["aggregate_facts"]
        self.assertEqual(
            aggregate["attention_signal_counts"][
                "gr_reversal_without_original_gr_observed"
            ],
            184,
        )
        self.assertEqual(
            aggregate["attention_signal_counts"][
                "invoice_reversal_without_original_invoice_observed"
            ],
            184,
        )
        self.assertEqual(len(aggregate["candidate_focus_items"]), 184)
        self.assertLess(len(summary["item_pattern_groups"]), 20)

    def test_systemic_orphan_reversal_focus_is_expanded_completely(self):
        po_record, rule_record = load_records("4507021416")
        summary = build_po_summary(po_record, rule_record)
        decision = SimpleNamespace(
            primary_finding="reversal_without_original_event_observed",
            focus_items=["00010"],
        )
        reconciled = reconcile_focus_items(decision, summary)
        self.assertEqual(
            reconciled,
            summary["aggregate_facts"]["candidate_focus_items"],
        )
        self.assertEqual(len(reconciled), 184)

    def test_selected_pattern_group_is_expanded_in_po_order(self):
        po_record, rule_record = load_records("4507013117")
        summary = build_po_summary(po_record, rule_record)
        decision = SimpleNamespace(
            primary_finding="process_state_uncertain",
            focus_items=["00040"],
        )
        reconciled = reconcile_focus_items(decision, summary)
        self.assertEqual(reconciled, ["00040", "00150"])

    def test_simple_unexecuted_delete_is_not_a_focus_candidate(self):
        po_record, rule_record = load_records("4507034681")
        summary = build_po_summary(po_record, rule_record)
        self.assertEqual(
            summary["aggregate_facts"]["candidate_focus_items"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
