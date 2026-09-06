import json
import os
from pathlib import Path

from openai import OpenAI

from agent_baseline_v2 import (
    MODEL,
    REASONING_EFFORT,
    run_agent_v2,
)


project_folder = Path(__file__).resolve().parents[1]
labels_path = project_folder / "evals" / "blind_labels_v1.jsonl"
output_path = (
    project_folder / "evals" / "agent_predictions_error_cases_v2_2.jsonl"
)

ERROR_CASE_IDS = [
    "4508048533",
    "4507004049",
    "4507013117",
    "4507021416",
]


def load_jsonl(path):
    records = {}
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            records[record["purchasing_document"]] = record
    return records


def usage_dict(response):
    usage = response.usage
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
    }


if not os.getenv("OPENAI_API_KEY"):
    raise SystemExit(
        "当前 PowerShell 会话没有 OPENAI_API_KEY。"
        "请在已设置密钥的窗口运行本程序。"
    )

labels = load_jsonl(labels_path)
missing_labels = [po_id for po_id in ERROR_CASE_IDS if po_id not in labels]
if missing_labels:
    raise SystemExit(f"缺少人工标签：{missing_labels}")

predictions = load_jsonl(output_path)
unexpected = set(predictions) - set(ERROR_CASE_IDS)
if unexpected:
    raise SystemExit(f"已有输出包含意外 PO：{sorted(unexpected)}")

if predictions:
    print(f"检测到已完成 {len(predictions)}/4 个案例，将从断点继续。")

client = OpenAI()
with output_path.open("a", encoding="utf-8") as output_file:
    for case_number, po_id in enumerate(ERROR_CASE_IDS, start=1):
        if po_id in predictions:
            continue

        print(f"正在运行 V2.2：{case_number}/4 | PO {po_id}")
        response = run_agent_v2(po_id, client=client)
        decision = response.output_parsed.model_dump(mode="json")
        record = {
            "purchasing_document": po_id,
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "agent_output": decision,
            "usage": usage_dict(response),
        }
        output_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        output_file.flush()
        predictions[po_id] = record

print("\nAgent V2.2 错误案例回归测试：")
decision_correct = 0
primary_correct = 0
focus_correct = 0
total_tokens = 0

for po_id in ERROR_CASE_IDS:
    predicted = predictions[po_id]["agent_output"]
    expected = labels[po_id]
    decision_ok = predicted["decision"] == expected["review_decision"]
    primary_ok = predicted["primary_finding"] == expected["primary_finding"]
    focus_ok = set(predicted["focus_items"]) == set(expected["focus_items"])
    decision_correct += decision_ok
    primary_correct += primary_ok
    focus_correct += focus_ok
    total_tokens += predictions[po_id]["usage"]["total_tokens"]

    print(
        f"{po_id} | 决策={'正确' if decision_ok else '错误'} | "
        f"主要发现={'正确' if primary_ok else '错误'} | "
        f"重点行={'正确' if focus_ok else '错误'}"
    )

print(f"\n决策正确：{decision_correct}/4")
print(f"主要发现正确：{primary_correct}/4")
print(f"重点行项目正确：{focus_correct}/4")
print(f"总 Token：{total_tokens:,}")
print(f"详细结果：{output_path}")
