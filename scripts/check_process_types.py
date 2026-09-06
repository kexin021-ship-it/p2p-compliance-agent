from pathlib import Path
from collections import Counter
import xml.etree.ElementTree as ET

folder = Path(__file__).resolve().parent


def count_process_types(file_path):
    """统计一个 XES 文件中，各流程类型的行项目数量。"""
    counts = Counter()
    total = 0

    print(f"正在统计：{file_path.name}", flush=True)

    with file_path.open("rb") as file:
        context = ET.iterparse(file, events=("start", "end"))
        _, root = next(context)

        for action, element in context:
            tag = element.tag.rsplit("}", 1)[-1]

            if action == "end" and tag == "trace":
                category = "【缺失或空值】"

                for attribute in element:
                    if attribute.get("key") == "Item Category":
                        category = attribute.get("value") or "【缺失或空值】"
                        break

                counts[category] += 1
                total += 1

                root.remove(element)
                element.clear()

    return counts, total


# 使用相同的方法，分别统计全量文件和抽样文件
full_counts, full_total = count_process_types(
    folder / "BPI_Challenge_2019.xes"
)

sample_counts, sample_total = count_process_types(
    folder / "BPI_2019_sample_3000_seed42.xes"
)

print(f"\n全量行项目：{full_total:,}")
print(f"样本行项目：{sample_total:,}")

# 合并两份文件中实际出现的类型，避免遗漏任何类别
all_categories = sorted(set(full_counts) | set(sample_counts))

for category in all_categories:
    full_n = full_counts[category]
    sample_n = sample_counts[category]

    full_pct = full_n / full_total * 100
    sample_pct = sample_n / sample_total * 100

    print(f"\n流程类型：{category}")
    print(f"  全量：{full_n:,} 个，占 {full_pct:.2f}%")
    print(f"  样本：{sample_n:,} 个，占 {sample_pct:.2f}%")
    print(f"  占比差：{sample_pct - full_pct:+.2f} 个百分点")

    if full_n > 0 and sample_n == 0:
        print("  提示：这类流程在全量中存在，但样本未覆盖。")