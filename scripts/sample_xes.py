from pathlib import Path
from collections import Counter
import xml.etree.ElementTree as ET

# 原始数据与脚本放在同一个文件夹
source = Path(__file__).resolve().parent / "BPI_Challenge_2019.xes"

# 保存：每个采购单据编号对应多少个行项目
document_counts = Counter()

trace_count = 0
missing_document_count = 0

print("开始读取 XES，只做统计，不修改原始文件……", flush=True)

# 流式读取，避免一次性把整个文件加载进内存
with source.open("rb") as file:
    context = ET.iterparse(file, events=("start", "end"))
    _, root = next(context)

    for action, element in context:
        tag = element.tag.rsplit("}", 1)[-1]

        # 一个 trace 代表一个采购订单行项目
        if action == "end" and tag == "trace":
            trace_count += 1
            document_id = None

            # 找到该行项目所属的采购单据编号
            for attribute in element:
                if attribute.get("key") == "Purchasing Document":
                    document_id = attribute.get("value")
                    break

            if document_id:
                document_counts[document_id] += 1
            else:
                missing_document_count += 1

            # 已处理的行项目不再保留在内存中
            root.remove(element)
            element.clear()

            if trace_count % 50000 == 0:
                print(f"已处理 {trace_count:,} 个行项目", flush=True)

print("\n统计完成：")
print(f"采购单据数量：{len(document_counts):,}")
print(f"行项目数量：{trace_count:,}")
print(f"缺少采购单据编号的行项目：{missing_document_count:,}")

import random

# 抽样参数
sample_size = 3000
random_seed = 42

# 所有不同的采购单据编号，排序后保证输入顺序稳定
all_document_ids = sorted(document_counts.keys())

# 等概率、不放回地抽取 3000 个编号
rng = random.Random(random_seed)
selected_document_ids = rng.sample(all_document_ids, sample_size)

# 后续用这个集合快速判断某个行项目是否应被保留
selected_document_set = set(selected_document_ids)

# 计算这些单据一共包含多少个行项目
selected_trace_count = sum(
    document_counts[document_id]
    for document_id in selected_document_ids
)

print("\n抽样结果：")
print(f"随机种子：{random_seed}")
print(f"抽中的采购单据数量：{len(selected_document_set):,}")
print(f"这些单据包含的全部行项目数量：{selected_trace_count:,}")

print("\n抽中的前 10 个采购单据编号：")
for document_id in selected_document_ids[:10]:
    print(document_id)

from copy import deepcopy

output = source.with_name("BPI_2019_sample_3000_seed42.xes")
id_output = source.with_name("sampled_document_ids_seed42.txt")

# 避免重复运行时覆盖已经导出的文件
if output.exists() or id_output.exists():
    raise FileExistsError("输出文件已存在，为避免覆盖，停止导出。")

# 第一遍统计时已经移除了所有 trace，
# root 中仍保留着日志属性、扩展声明等元数据
sample_root = deepcopy(root)
exported_counts = Counter()
exported_event_count = 0

print("\n开始第二遍读取，提取选中单据的完整数据……", flush=True)

with source.open("rb") as file:
    context = ET.iterparse(file, events=("start", "end"))
    _, source_root = next(context)

    for action, element in context:
        tag = element.tag.rsplit("}", 1)[-1]

        if action == "end" and tag == "trace":
            document_id = None

            for attribute in element:
                if attribute.get("key") == "Purchasing Document":
                    document_id = attribute.get("value")
                    break

            if document_id in selected_document_set:
                # 整个行项目一起保留，包括全部属性和事件
                sample_root.append(element)
                exported_counts[document_id] += 1

                exported_event_count += sum(
                    child.tag.rsplit("}", 1)[-1] == "event"
                    for child in element
                )
            else:
                element.clear()

            source_root.remove(element)

# 核对每份选中单据的行项目数，确保没有漏掉部分行项目
expected_counts = Counter({
    document_id: document_counts[document_id]
    for document_id in selected_document_ids
})

if exported_counts != expected_counts:
    raise RuntimeError("行项目数量核对失败，停止导出。")

# 保留 XES 的 XML 命名空间
if sample_root.tag.startswith("{"):
    namespace = sample_root.tag.split("}", 1)[0][1:]
    ET.register_namespace("", namespace)

# 写出新的 XES 文件
with output.open("xb") as file:
    ET.ElementTree(sample_root).write(
        file,
        encoding="utf-8",
        xml_declaration=True
    )

# 保存抽中的采购单据编号，一行一个
with id_output.open("x", encoding="utf-8") as file:
    file.write("\n".join(sorted(selected_document_ids)) + "\n")

print("\n导出完成：")
print(f"采购单据数量：{len(exported_counts):,}")
print(f"行项目数量：{sum(exported_counts.values()):,}")
print(f"事件数量：{exported_event_count:,}")
print(f"抽样文件：{output.name}")
print(f"编号清单：{id_output.name}")