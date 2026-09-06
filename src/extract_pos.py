from pathlib import Path
import json
import xml.etree.ElementTree as ET

# 脚本位于项目的 src 文件夹，所以向上两级找到项目目录
project_folder = Path(__file__).resolve().parents[1]

source_path = project_folder / "BPI_2019_sample_3000_seed42.xes"
output_path = (
    project_folder / "data" / "processed" / "purchase_orders.jsonl"
)


def read_attributes(element):
    """保留直接属性的原始字符串值，不改日期或金额。"""
    attributes = {}

    for child in element:
        key = child.get("key")

        if key is None:
            continue

        # 遇到未支持的复杂属性时停止，避免默默丢失信息
        if "value" not in child.attrib or len(child) > 0:
            raise ValueError(f"发现需要单独处理的 XES 属性：{key}")

        attributes[key] = child.get("value")

    return attributes


if output_path.exists():
    raise SystemExit(
        f"输出文件已经存在，为避免覆盖，本次停止：{output_path}"
    )

purchase_orders = {}
seen_cases = set()
item_count = 0
event_count = 0

print("开始转换抽样文件，不修改原始数据……", flush=True)

with source_path.open("rb") as file:
    context = ET.iterparse(file, events=("start", "end"))
    _, root = next(context)

    for action, element in context:
        tag = element.tag.rsplit("}", 1)[-1]

        if action != "end" or tag != "trace":
            continue

        attributes = read_attributes(element)

        po_id = attributes.get("Purchasing Document")
        case_id = attributes.get("concept:name")
        item_number = attributes.get("Item")

        if not po_id or not case_id or not item_number:
            raise ValueError("发现缺少 PO 编号、Case ID 或行项目编号的记录。")

        if case_id in seen_cases:
            raise ValueError(f"发现重复 Case ID：{case_id}")

        seen_cases.add(case_id)

        events = []

        for child in element:
            if child.tag.rsplit("}", 1)[-1] != "event":
                continue

            events.append({
                "event_index": len(events) + 1,
                "attributes": read_attributes(child),
            })

        item = {
            "case_id": case_id,
            "item_number": item_number,
            "attributes": attributes,
            "events": events,
        }

        if po_id not in purchase_orders:
            purchase_orders[po_id] = {
                "purchasing_document": po_id,
                "items": [],
            }

        purchase_orders[po_id]["items"].append(item)

        item_count += 1
        event_count += len(events)

        root.remove(element)
        element.clear()

# 与之前已经验证过的抽样数量核对
actual_counts = (len(purchase_orders), item_count, event_count)
expected_counts = (3000, 10059, 65136)

if actual_counts != expected_counts:
    raise ValueError(
        f"数量不一致，停止导出。"
        f"实际：{actual_counts}；预期：{expected_counts}"
    )

output_path.parent.mkdir(parents=True, exist_ok=True)

# x 模式：只创建新文件，不覆盖已有文件
with output_path.open("x", encoding="utf-8") as file:
    for po_id in sorted(purchase_orders):
        record = purchase_orders[po_id]

        # JSONL：每一行是一份完整 PO
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

print("\n转换完成：")
print(f"PO 数量：{len(purchase_orders):,}")
print(f"行项目数量：{item_count:,}")
print(f"事件数量：{event_count:,}")
print(f"输出文件：{output_path}")