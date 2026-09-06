from pathlib import Path
from datetime import datetime
import xml.etree.ElementTree as ET

folder = Path(__file__).resolve().parent
sample_path = folder / "BPI_2019_sample_3000_seed42.xes"
ids_path = folder / "sampled_document_ids_seed42.txt"


def read_attributes(element):
    """读取当前元素的直接属性，不进入下一层事件。"""
    return {
        child.get("key"): child.get("value")
        for child in element
        if child.get("key") is not None
    }

# 寻找一个适合对照学习的行项目
required_activities = {
    "Record Goods Receipt",
    "Record Invoice Receipt",
}

target_document = None
target_case = None

with sample_path.open("rb") as file:
    context = ET.iterparse(file, events=("start", "end"))
    _, root = next(context)

    for action, element in context:
        tag = element.tag.rsplit("}", 1)[-1]

        if action == "end" and tag == "trace":
            attributes = read_attributes(element)

            activities = {
                read_attributes(child).get("concept:name")
                for child in element
                if child.tag.rsplit("}", 1)[-1] == "event"
            }

            if (
                attributes.get("Item Category")
                == "3-way match, invoice before GR"
                and required_activities.issubset(activities)
		and "Clear Invoice" not in activities
                and "Delete Purchase Order Item" not in activities
                and attributes.get("Purchasing Document")
            ):
                target_document = attributes["Purchasing Document"]
                target_case = attributes.get("concept:name")
                break

            root.remove(element)
            element.clear()

if target_document is None:
    raise SystemExit("没有找到符合展示条件的行项目。")

print(f"本次重点观察的 Case ID：{target_case}")

print(f"查看采购单据：{target_document}")
matched_items = 0

with sample_path.open("rb") as file:
    context = ET.iterparse(file, events=("start", "end"))
    _, root = next(context)

    for action, element in context:
        tag = element.tag.rsplit("}", 1)[-1]

        if action == "end" and tag == "trace":
            attributes = read_attributes(element)

            if attributes.get("Purchasing Document") == target_document:
                matched_items += 1

                print("\n" + "=" * 60)
                print(f"行项目编号：{attributes.get('Item')}")
                print(f"Case ID：{attributes.get('concept:name')}")

                for field in [
                    "Item Category",
                    "Goods Receipt",
                    "GR-Based Inv. Verif.",
                    "Item Type",
                    "Company",
                    "Vendor",
                ]:
                    print(f"{field}：{attributes.get(field, '【缺失】')}")

                # 收集该行项目的全部事件
                events = [
                    read_attributes(child)
                    for child in element
                    if child.tag.rsplit("}", 1)[-1] == "event"
                ]

                # 按事件时间排序；时间相同则保留原日志中的相对顺序
                events.sort(
                    key=lambda event: datetime.fromisoformat(
                        event["time:timestamp"].replace("Z", "+00:00")
                    )
                )

                print(f"\n事件时间线，共 {len(events)} 条：")

                for number, event in enumerate(events, start=1):
                    timestamp = event.get("time:timestamp")
                    activity = event.get("concept:name")
                    user = event.get("User", "【未记录】")

                    print(f"{number:02d}. {timestamp} | {activity} | {user}")

            root.remove(element)
            element.clear()

print(f"\n这份采购单据共展示 {matched_items} 个行项目。")