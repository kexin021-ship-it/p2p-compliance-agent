import json
from collections import Counter, defaultdict
from pathlib import Path


project_folder = Path(__file__).resolve().parents[1]
prediction_path = (
    project_folder
    / "evals"
    / "blind_system_predictions_frozen_v1.jsonl"
)
labels_path = project_folder / "evals" / "blind_labels_v1.jsonl"
review_ids_path = project_folder / "evals" / "blind_review_po_ids.txt"


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
review_ids = [
    line.strip()
    for line in review_ids_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

if len(predictions) != 60:
    raise SystemExit(f"系统预测应有60条，当前为{len(predictions)}条")
if len(review_ids) != len(set(review_ids)):
    raise SystemExit("人工盲审清单存在重复 PO")
if set(labels) != set(review_ids):
    missing = sorted(set(review_ids) - set(labels))
    extra = sorted(set(labels) - set(review_ids))
    raise SystemExit(
        f"人工标签与盲审清单不一致。缺少：{missing}；多出：{extra}"
    )

disagreement_ids = sorted(
    po_id for po_id, record in predictions.items() if not record["agree"]
)
if not set(disagreement_ids).issubset(labels):
    raise SystemExit("仍有规则与 Agent 不一致的案例未完成人工标注")

audit_ids = sorted(
    po_id for po_id in labels if predictions[po_id]["agree"]
)


def is_correct(po_id, field):
    return (
        predictions[po_id][field]
        == labels[po_id]["review_decision"]
    )


systems = {
    "规则": "rule_decision",
    "Agent": "agent_decision",
}

print("盲测比较完成：")
print(f"系统预测案例：{len(predictions)}")
print(f"规则与 Agent 不一致：{len(disagreement_ids)}（已全部人工复核）")
print(f"双方一致案例抽查：{len(audit_ids)}")

print("\n不一致案例的人工裁决：")
for po_id in disagreement_ids:
    record = predictions[po_id]
    gold = labels[po_id]["review_decision"]
    rule_mark = "正确" if is_correct(po_id, "rule_decision") else "错误"
    agent_mark = "正确" if is_correct(po_id, "agent_decision") else "错误"
    print(
        f"{po_id} | 人工={gold} | "
        f"规则={record['rule_decision']}（{rule_mark}）| "
        f"Agent={record['agent_decision']}（{agent_mark}）"
    )

print("\n人工已审13例的决策正确率：")
for name, field in systems.items():
    correct = sum(is_correct(po_id, field) for po_id in labels)
    print(f"{name}：{correct}/{len(labels)} ({correct / len(labels):.1%})")

audit_correct = sum(
    is_correct(po_id, "rule_decision") for po_id in audit_ids
)
print(
    f"\n双方一致案例抽查：{audit_correct}/{len(audit_ids)} "
    f"({audit_correct / len(audit_ids):.1%})"
)
audit_errors = [
    po_id
    for po_id in audit_ids
    if not is_correct(po_id, "rule_decision")
]
print(f"双方共同判断错误的抽查 PO：{audit_errors}")

print("\n60例上的相对表现（精确）：")
disagreement_scores = {}
for name, field in systems.items():
    correct = sum(is_correct(po_id, field) for po_id in disagreement_ids)
    disagreement_scores[name] = correct
    print(f"{name}在3个分歧案例中正确：{correct}/{len(disagreement_ids)}")

advantage = disagreement_scores["Agent"] - disagreement_scores["规则"]
if advantage > 0:
    print(f"Agent 比规则多判对 {advantage} 个 PO。")
elif advantage < 0:
    print(f"规则比 Agent 多判对 {-advantage} 个 PO。")
else:
    print("规则与 Agent 在60例上的正确数量差为0。")

# 一致案例按共同决策分层抽样，分别外推到对应的一致案例总体。
agreement_population = Counter(
    record["rule_decision"]
    for record in predictions.values()
    if record["agree"]
)
audit_by_decision = defaultdict(list)
for po_id in audit_ids:
    decision = predictions[po_id]["rule_decision"]
    audit_by_decision[decision].append(po_id)

estimated_agreement_correct = 0.0
estimate_available = True
for decision, population_size in agreement_population.items():
    sample_ids = audit_by_decision[decision]
    if not sample_ids:
        estimate_available = False
        break
    sample_accuracy = sum(
        is_correct(po_id, "rule_decision") for po_id in sample_ids
    ) / len(sample_ids)
    estimated_agreement_correct += population_size * sample_accuracy

if estimate_available:
    print("\n基于10个一致案例分层抽查的60例正确率估计：")
    for name in systems:
        estimated_correct = (
            estimated_agreement_correct + disagreement_scores[name]
        )
        print(
            f"{name}：约 {estimated_correct / len(predictions):.1%} "
            f"（小样本估计，不是精确绝对正确率）"
        )

unknown_agreements = len(predictions) - len(labels)
print("\n绝对正确率的保守范围：")
for name, field in systems.items():
    known_correct = sum(is_correct(po_id, field) for po_id in labels)
    minimum = known_correct / len(predictions)
    maximum = (known_correct + unknown_agreements) / len(predictions)
    print(f"{name}：{minimum:.1%} 至 {maximum:.1%}")
print(
    "说明：其余未人工复核的47例中双方预测相同，"
    "所以不会改变谁更好，只会影响双方各自的绝对正确率。"
)
