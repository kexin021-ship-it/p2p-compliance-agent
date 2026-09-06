from pathlib import Path
from collections import Counter
import json
import random

project_folder = Path(__file__).resolve().parents[1]

rule_results_path = (
    project_folder
    / "outputs"
    / "rule_findings_closure_v1.jsonl"
)

development_ids_path = (
    project_folder
    / "evals"
    / "development_edge_po_ids.txt"
)

blind_eval_ids_path = (
    project_folder
    / "evals"
    / "blind_eval_po_ids.txt"
)

output_paths = [
    development_ids_path,
    blind_eval_ids_path,
]

for output_path in output_paths:
    if output_path.exists():
        raise SystemExit(
            f"输出文件已存在，为避免覆盖，本次停止：{output_path}"
        )

records = []
seen_po_ids = set()

with rule_results_path.open("r", encoding="utf-8") as file:
    for line in file:
        purchase_order = json.loads(line)
        po_id = purchase_order["purchasing_document"]

        if po_id in seen_po_ids:
            raise ValueError(f"发现重复 PO：{po_id}")

        seen_po_ids.add(po_id)

        tags = {
            finding["tag"]
            for item in purchase_order["items"]
            for finding in item["findings"]
        }

        categories = {
            item["item_category"]
            for item in purchase_order["items"]
            if item["item_category"] is not None
        }

        records.append({
            "po_id": po_id,
            "route": purchase_order["po_routing_status"],
            "tags": tags,
            "categories": categories,
        })

if len(records) != 3000:
    raise ValueError(
        f"预期读取 3,000 份 PO，实际读取 {len(records):,} 份。"
    )

random_generator = random.Random(42)

# --------------------------------------
# 第一组：20 份开发练习案例
# --------------------------------------

edge_tags = [
    "deleted_with_followon_activity",
    "reactivate_without_delete_observed",
    "reactivated_after_delete_observed",
    "reversal_present",
    "clear_without_prior_invoice_observed",
    "clear_before_required_gr_observed",
    "latest_invoice_receipt_without_later_clear",
    "latest_gr_without_later_invoice_clear_cycle",
    "invoice_receipt_without_clear_observed",
]

development_records = []
development_ids = set()

# 每个重要标签先尽量选一份。
for tag in edge_tags:
    candidates = [
        record
        for record in records
        if tag in record["tags"]
        and record["po_id"] not in development_ids
    ]

    random_generator.shuffle(candidates)

    if candidates:
        selected = candidates[0]
        development_records.append(selected)
        development_ids.add(selected["po_id"])

# 不足 20 份时，从需要深入分析的 PO 中补齐。
remaining_development_candidates = [
    record
    for record in records
    if record["route"] == "further_analysis"
    and record["po_id"] not in development_ids
]

random_generator.shuffle(remaining_development_candidates)

# 优先保留触发标签较多的案例；相同数量时保持随机顺序。
remaining_development_candidates.sort(
    key=lambda record: len(record["tags"]),
    reverse=True,
)

for record in remaining_development_candidates:
    if len(development_records) >= 20:
        break

    development_records.append(record)
    development_ids.add(record["po_id"])

if len(development_records) != 20:
    raise ValueError(
        f"开发案例没有选够 20 份：{len(development_records)}"
    )

random_generator.shuffle(development_records)

# --------------------------------------
# 第二组：60 份盲测案例
# --------------------------------------

category_order = [
    "3-way match, invoice after GR",
    "3-way match, invoice before GR",
    "2-way match",
    "Consignment",
]

blind_eval_records = []
blind_eval_ids = set()

route_targets = {
    "further_analysis": 25,
    "no_rule_signal": 30,
    "no_further_closure_analysis": 5,
}


def select_from_route(route, target_count):
    available = [
        record
        for record in records
        if record["route"] == route
        and record["po_id"] not in development_ids
        and record["po_id"] not in blind_eval_ids
    ]

    random_generator.shuffle(available)
    selected = []

    # 先尽量保证四种流程类型都有代表。
    for category in category_order:
        candidate = next(
            (
                record
                for record in available
                if category in record["categories"]
                and record["po_id"] not in {
                    item["po_id"]
                    for item in selected
                }
            ),
            None,
        )

        if candidate is not None:
            selected.append(candidate)

        if len(selected) >= target_count:
            break

    selected_ids = {
        record["po_id"]
        for record in selected
    }

    # 然后随机补足当前分组。
    for record in available:
        if len(selected) >= target_count:
            break

        if record["po_id"] in selected_ids:
            continue

        selected.append(record)
        selected_ids.add(record["po_id"])

    if len(selected) != target_count:
        raise ValueError(
            f"{route} 没有选够 {target_count} 份，"
            f"实际为 {len(selected)} 份。"
        )

    return selected


for route, target_count in route_targets.items():
    selected_records = select_from_route(
        route,
        target_count,
    )

    blind_eval_records.extend(selected_records)

    for record in selected_records:
        blind_eval_ids.add(record["po_id"])

if len(blind_eval_records) != 60:
    raise ValueError(
        f"盲测案例没有选够 60 份：{len(blind_eval_records)}"
    )

if development_ids & blind_eval_ids:
    raise ValueError("开发案例与盲测案例出现重复 PO。")

random_generator.shuffle(blind_eval_records)

# --------------------------------------
# 输出两个 PO 编号清单
# --------------------------------------

development_ids_path.parent.mkdir(
    parents=True,
    exist_ok=True,
)

development_ids_path.write_text(
    "\n".join(
        record["po_id"]
        for record in development_records
    )
    + "\n",
    encoding="utf-8",
)

blind_eval_ids_path.write_text(
    "\n".join(
        record["po_id"]
        for record in blind_eval_records
    )
    + "\n",
    encoding="utf-8",
)

development_tag_counts = Counter(
    tag
    for record in development_records
    for tag in record["tags"]
)

blind_route_counts = Counter(
    record["route"]
    for record in blind_eval_records
)

blind_category_counts = Counter(
    category
    for record in blind_eval_records
    for category in record["categories"]
)

print("\n审核集创建完成：")
print(f"开发练习 PO：{len(development_records)}")
print(f"盲测 PO：{len(blind_eval_records)}")
print("两组重复 PO：0")
print("随机种子：42")

print("\n开发练习集覆盖的标签：")
for tag, count in sorted(development_tag_counts.items()):
    print(f"{tag}: {count}")

print("\n盲测集的分流构成：")
for route, count in sorted(blind_route_counts.items()):
    print(f"{route}: {count}")

print("\n盲测集覆盖的流程类型：")
for category, count in sorted(blind_category_counts.items()):
    print(f"{category}: {count}")

print(f"\n开发清单：{development_ids_path}")
print(f"盲测清单：{blind_eval_ids_path}")