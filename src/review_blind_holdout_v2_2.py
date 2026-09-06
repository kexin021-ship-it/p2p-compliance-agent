import json
import sys
from pathlib import Path


project_folder = Path(__file__).resolve().parents[1]
po_data_path = (
    project_folder / "data" / "processed" / "purchase_orders.jsonl"
)
review_ids_path = (
    project_folder / "evals" / "blind_holdout_review_po_ids_v2_2.txt"
)


def find_po(path, purchasing_document):
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["purchasing_document"] == purchasing_document:
                return record
    return None


if not review_ids_path.exists():
    raise SystemExit(
        "尚未生成 V2.2 独立盲审清单。"
        "请先运行 run_blind_holdout_v2_2.py。"
    )

review_ids = [
    line.strip()
    for line in review_ids_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

if len(sys.argv) != 2:
    raise SystemExit(
        "用法：python review_blind_holdout_v2_2.py 序号或采购单编号"
    )

argument = sys.argv[1]
if argument in review_ids:
    po_id = argument
    case_number = review_ids.index(po_id) + 1
else:
    try:
        case_number = int(argument)
    except ValueError:
        raise SystemExit("请输入盲审序号或采购单编号")
    if not 1 <= case_number <= len(review_ids):
        raise SystemExit(f"序号必须在 1 至 {len(review_ids)} 之间")
    po_id = review_ids[case_number - 1]

po_record = find_po(po_data_path, po_id)
if po_record is None:
    raise SystemExit(f"没有找到采购单：{po_id}")

print(f"\nV2.2 独立人工盲审案例：{case_number}/{len(review_ids)}")
print(f"采购单编号：{po_id}")
print(f"行项目数量：{len(po_record['items'])}")

for item in po_record["items"]:
    attributes = item["attributes"]
    print("\n" + "=" * 70)
    print(f"行项目：{item['item_number']}")
    print(f"Case ID：{item['case_id']}")
    print(f"流程类型：{attributes.get('Item Category')}")
    print(f"需要收货：{attributes.get('Goods Receipt')}")
    print(
        "GR-Based Inv. Verif.："
        f"{attributes.get('GR-Based Inv. Verif.')}"
    )
    print(f"Item Type：{attributes.get('Item Type')}")
    print("\n原始事件时间线：")

    for event in item["events"]:
        event_attributes = event["attributes"]
        print(
            f"{event['event_index']:02d}. "
            f"{event_attributes.get('time:timestamp')} | "
            f"{event_attributes.get('concept:name')} | "
            f"{event_attributes.get('User')}"
        )

label_template = {
    "purchasing_document": po_id,
    "review_decision": "",
    "primary_finding": "",
    "focus_items": [],
    "reviewer_note": "",
    "scope_limitation": "",
}

print("\n人工标注模板：")
print(json.dumps(label_template, ensure_ascii=False, indent=2))
