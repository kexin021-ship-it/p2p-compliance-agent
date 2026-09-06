from pathlib import Path
import xml.etree.ElementTree as ET

folder = Path(__file__).resolve().parent
source_path = folder / "BPI_Challenge_2019.xes"

target_cases = {
    "4507004931_00010",
    "4507005744_00010",
}
found_cases = set()


def read_attributes(element):
    return {
        child.get("key"): child.get("value")
        for child in element
        if child.get("key") is not None
    }


with source_path.open("rb") as file:
    context = ET.iterparse(file, events=("start", "end"))
    _, root = next(context)

    for action, element in context:
        tag = element.tag.rsplit("}", 1)[-1]

        if action != "end" or tag != "trace":
            continue

        attributes = read_attributes(element)
        case_id = attributes.get("concept:name")

        if case_id in target_cases:
            found_cases.add(case_id)

            print("\n" + "=" * 60)
            print(f"Case ID：{case_id}")
            print(f"流程类型：{attributes.get('Item Category')}")
            print("以下按原日志顺序展示，保留原始时间字符串：")

            number = 0
            for event in element:
                if event.tag.rsplit("}", 1)[-1] != "event":
                    continue

                number += 1
                event_attributes = read_attributes(event)

                timestamp = event_attributes.get("time:timestamp")
                activity = event_attributes.get("concept:name")

                print(f"{number:02d}. {timestamp} | {activity}")

        root.remove(element)
        element.clear()

        if found_cases == target_cases:
            break

print(f"\n已找到 {len(found_cases)} / {len(target_cases)} 个目标案例。")