import json
import sys
from pathlib import Path


PROJECT_FOLDER = Path(__file__).resolve().parent.parent

PO_DATA_PATH = (
    PROJECT_FOLDER
    / "data"
    / "processed"
    / "purchase_orders.jsonl"
)

RULE_DATA_PATH = (
    PROJECT_FOLDER
    / "outputs"
    / "rule_findings_closure_v1.jsonl"
)


def find_po(file_path, po_id):
    with file_path.open("r", encoding="utf-8") as file:
        for line in file:
            record = json.loads(line)

            if record["purchasing_document"] == po_id:
                return record

    return None


if len(sys.argv) != 2:
    raise SystemExit(
        "用法：python src\\review_one_po.py 采购单编号"
    )

po_id = sys.argv[1]

po_record = find_po(PO_DATA_PATH, po_id)
rule_record = find_po(RULE_DATA_PATH, po_id)

if po_record is None:
    raise SystemExit(f"没有找到采购单：{po_id}")

rule_items = {}

if rule_record is not None:
    rule_items = {
        item["case_id"]: item
        for item in rule_record["items"]
    }

print(f"\n采购单编号：{po_id}")
print(f"行项目数量：{len(po_record['items'])}")

if rule_record is not None:
    print(f"PO 规则分流：{rule_record['po_routing_status']}")

for item in po_record["items"]:
    case_id = item["case_id"]
    attributes = item["attributes"]

    print("\n" + "=" * 70)
    print(f"行项目：{item['item_number']}")
    print(f"Case ID：{case_id}")
    print(f"流程类型：{attributes.get('Item Category')}")
    print(f"需要收货：{attributes.get('Goods Receipt')}")
    print(
        "GR-Based Inv. Verif.："
        f"{attributes.get('GR-Based Inv. Verif.')}"
    )

    print("\n原始事件时间线：")

    for event in item["events"]:
        event_attributes = event["attributes"]

        print(
            f"{event['event_index']:02d}. "
            f"{event_attributes.get('time:timestamp')} | "
            f"{event_attributes.get('concept:name')} | "
            f"{event_attributes.get('User')}"
        )

    finding_item = rule_items.get(case_id)

    print("\n规则检查结果：")

    if finding_item is None:
        print("没有找到规则检查结果。")
        continue

    print(f"行项目分流：{finding_item['routing_status']}")

    for finding in finding_item["findings"]:
        print(
            f"- {finding['rule_id']} | "
            f"{finding['tag']}"
        )
        print(f"  解释：{finding['explanation']}")