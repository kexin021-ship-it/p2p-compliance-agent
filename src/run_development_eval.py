import json
from pathlib import Path

from openai import OpenAI

from agent_baseline import (
    AgentDecision,
    SYSTEM_PROMPT,
    build_agent_input,
    load_jsonl_record,
    po_data_path,
    rule_results_path,
)


project_folder = Path(__file__).resolve().parents[1]

po_ids_path = (
    project_folder
    / "evals"
    / "development_edge_po_ids.txt"
)

labels_path = (
    project_folder
    / "evals"
    / "development_labels_v1.jsonl"
)

output_path = (
    project_folder
    / "evals"
    / "agent_predictions_development_v4.jsonl"
)


def load_labels(path):
    labels = {}

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                record = json.loads(line)
                labels[record["purchasing_document"]] = record

    return labels


po_ids = [
    line.strip()
    for line in po_ids_path.read_text(
        encoding="utf-8"
    ).splitlines()
    if line.strip()
]

labels = load_labels(labels_path)
client = OpenAI()
results = []

with output_path.open("w", encoding="utf-8") as output_file:
    for index, po_id in enumerate(po_ids, start=1):
        print(f"[{index}/{len(po_ids)}] 正在分析 PO：{po_id}")

        po_record = load_jsonl_record(
            po_data_path,
            po_id,
        )

        rule_record = load_jsonl_record(
            rule_results_path,
            po_id,
        )

        agent_input = build_agent_input(
            po_record,
            rule_record,
        )

        response = client.responses.parse(
            model="gpt-5.4-mini-2026-03-17",
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": agent_input,
                },
            ],
            reasoning={"effort": "low"},
            text_format=AgentDecision,
            store=False,
        )

        decision = response.output_parsed

        if decision is None:
            raise RuntimeError(
                f"PO {po_id} 没有返回可解析结果"
            )

        prediction = decision.model_dump(mode="json")
        gold = labels[po_id]

        result = {
            "purchasing_document": po_id,
            "gold_decision": gold["review_decision"],
            "predicted_decision": prediction["decision"],
            "decision_match": (
                gold["review_decision"]
                == prediction["decision"]
            ),
            "gold_primary_finding": gold["primary_finding"],
            "predicted_primary_finding": prediction[
                "primary_finding"
            ],
            "primary_finding_match": (
                gold["primary_finding"]
                == prediction["primary_finding"]
            ),
            "gold_focus_items": gold["focus_items"],
            "predicted_focus_items": prediction["focus_items"],
            "focus_items_match": (
                set(gold["focus_items"])
                == set(prediction["focus_items"])
            ),
            "agent_output": prediction,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        }

        results.append(result)

        output_file.write(
            json.dumps(
                result,
                ensure_ascii=False,
            )
            + "\n"
        )
        output_file.flush()


total = len(results)

decision_correct = sum(
    result["decision_match"]
    for result in results
)

finding_correct = sum(
    result["primary_finding_match"]
    for result in results
)

focus_correct = sum(
    result["focus_items_match"]
    for result in results
)

total_tokens = sum(
    result["usage"]["total_tokens"]
    for result in results
)

decision_mismatches = [
    result["purchasing_document"]
    for result in results
    if not result["decision_match"]
]

print("\n开发集评估完成：")
print(f"案例数量：{total}")
print(
    f"决策正确：{decision_correct}/{total} "
    f"({decision_correct / total:.1%})"
)
print(
    f"主要发现正确：{finding_correct}/{total} "
    f"({finding_correct / total:.1%})"
)
print(
    f"重点行项目正确：{focus_correct}/{total} "
    f"({focus_correct / total:.1%})"
)
print(f"总 Token：{total_tokens:,}")
print(f"决策不一致的 PO：{decision_mismatches}")
print(f"详细结果：{output_path}")