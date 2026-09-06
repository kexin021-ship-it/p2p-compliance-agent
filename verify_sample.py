from pathlib import Path
from collections import Counter
import xml.etree.ElementTree as ET

folder = Path(__file__).resolve().parent
sample_path = folder / "BPI_2019_sample_3000_seed42.xes"
ids_path = folder / "sampled_document_ids_seed42.txt"

# 从保存的清单读取预期的采购单据编号
expected_ids = [
    line.strip()
    for line in ids_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

document_counts = Counter()
case_ids = set()
trace_count = 0
event_count = 0

print("开始独立检查抽样文件……")

with sample_path.open("rb") as file:
    context = ET.iterparse(file, events=("start", "end"))
    _, root = next(context)

    for action, element in context:
        tag = element.tag.rsplit("}", 1)[-1]

        if action == "end" and tag == "trace":
            # 提取行项目属性，不读取事件内部的属性
            attributes = {
                child.get("key"): child.get("value")
                for child in element
                if child.get("key") is not None
            }

            document_id = attributes.get("Purchasing Document")
            case_id = attributes.get("concept:name")

            if not document_id or not case_id:
                raise ValueError("发现缺少采购单据编号或行项目编号的记录。")

            if case_id in case_ids:
                raise ValueError(f"发现重复行项目编号：{case_id}")

            case_ids.add(case_id)
            document_counts[document_id] += 1
            trace_count += 1

            event_count += sum(
                child.tag.rsplit("}", 1)[-1] == "event"
                for child in element
            )

            root.remove(element)
            element.clear()

checks = {
    "编号清单恰好包含 3000 个不重复编号":
        len(expected_ids) == len(set(expected_ids)) == 3000,
    "文件中的单据编号与清单完全一致":
        set(document_counts) == set(expected_ids),
    "行项目数量为 10059":
        trace_count == 10059,
    "事件数量为 65136":
        event_count == 65136,
}

print("\n检查结果：")
for description, passed in checks.items():
    print(f"{'通过' if passed else '未通过'}：{description}")

if not all(checks.values()):
    raise RuntimeError("检查未全部通过，请先不要继续分析。")

print("\n全部通过，抽样文件可正常解析，数量及单据范围一致。")