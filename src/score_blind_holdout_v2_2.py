import json
from pathlib import Path


project_folder = Path(__file__).resolve().parents[1]
prediction_path = (
    project_folder
    / "evals"
    / "blind_holdout_system_predictions_frozen_v2_2.jsonl"
)
labels_path = project_folder / "evals" / "blind_holdout_labels_v2_2.jsonl"
review_ids_path = (
    project_folder / "evals" / "blind_holdout_review_po_ids_v2_2.txt"
)


def load_jsonl(path):
    records = {}
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            po_id = record["purchasing_document"]
            if po_id in records:
                raise SystemExit(
                    f"{path.name} 第 {line_number} 行出现重复 PO：{po_id}"
                )
            records[po_id] = record
    return records


predictions = load_jsonl(prediction_path)
labels = load_jsonl(labels_path)
review_ids = {
    line.strip()
    for line in review_ids_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
}

if len(predictions) != 47:
    raise SystemExit(f"系统预测应有47条，当前为{len(predictions)}条。")
if set(labels) != review_ids:
    raise SystemExit("人工标签与最终盲审清单不一致。")

disagreement_ids = {
    po_id for po_id, record in predictions.items() if not record["agree"]
}
if disagreement_ids != review_ids:
    raise SystemExit("最终盲审标签必须恰好覆盖全部规则与Agent分歧案例。")


def decision_correct(po_id, field):
    return predictions[po_id][field] == labels[po_id]["review_decision"]


rule_correct = sum(
    decision_correct(po_id, "rule_decision") for po_id in review_ids
)
agent_correct = sum(
    decision_correct(po_id, "agent_decision") for po_id in review_ids
)
primary_correct = sum(
    predictions[po_id]["agent_output"]["primary_finding"]
    == labels[po_id]["primary_finding"]
    for po_id in review_ids
)
focus_correct = sum(
    set(predictions[po_id]["agent_output"]["focus_items"])
    == set(labels[po_id]["focus_items"])
    for po_id in review_ids
)

advantage_cases = agent_correct - rule_correct
advantage_points = advantage_cases / len(predictions)

print("冻结 V2.2 独立盲测比较完成：")
print(f"独立案例：{len(predictions)}")
print(f"双方一致：{len(predictions) - len(review_ids)}")
print(f"双方分歧且已全部人工裁决：{len(review_ids)}")
print("\n分歧案例决策正确率：")
print(f"规则：{rule_correct}/{len(review_ids)} ({rule_correct / len(review_ids):.1%})")
print(f"Agent：{agent_correct}/{len(review_ids)} ({agent_correct / len(review_ids):.1%})")
print(f"Agent主要发现：{primary_correct}/{len(review_ids)} ({primary_correct / len(review_ids):.1%})")
print(f"Agent重点行项目：{focus_correct}/{len(review_ids)} ({focus_correct / len(review_ids):.1%})")
print("\n47例上的精确相对表现：")
print(f"Agent比规则净多判对：{advantage_cases}个PO")
print(
    "Agent相对规则的准确率差："
    f"+{advantage_points * 100:.1f}个百分点"
)
print("说明：38个一致案例未人工复核，所以不能计算双方各自的精确绝对准确率。")

print("\n逐个分歧案例：")
for po_id in sorted(review_ids):
    prediction = predictions[po_id]
    gold = labels[po_id]["review_decision"]
    rule_mark = "正确" if decision_correct(po_id, "rule_decision") else "错误"
    agent_mark = "正确" if decision_correct(po_id, "agent_decision") else "错误"
    print(
        f"{po_id} | 人工={gold} | "
        f"规则={prediction['rule_decision']}（{rule_mark}）| "
        f"Agent={prediction['agent_decision']}（{agent_mark}）"
    )
