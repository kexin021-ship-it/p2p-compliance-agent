import argparse
import hashlib
import json
import os
import random
import time
from pathlib import Path

from openai import OpenAI

from agent_baseline_frozen_v1 import (
    AgentDecision,
    SYSTEM_PROMPT,
    build_agent_input,
    load_jsonl_record,
    po_data_path,
    rule_results_path,
)


MODEL = "gpt-5.4-mini-2026-03-17"
REASONING_EFFORT = "low"
RANDOM_SEED = 42

project_folder = Path(__file__).resolve().parents[1]
blind_ids_path = project_folder / "evals" / "blind_eval_po_ids.txt"
prediction_path = (
    project_folder
    / "evals"
    / "blind_system_predictions_frozen_v1.jsonl"
)
review_ids_path = (
    project_folder / "evals" / "blind_review_po_ids.txt"
)
frozen_agent_path = (
    project_folder / "src" / "agent_baseline_frozen_v1.py"
)


RULE_DECISION_MAP = {
    "further_analysis": "further_investigation",
    "no_further_closure_analysis": "no_further_investigation",
    "no_rule_signal": "no_further_investigation",
}


def read_ids(path):
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_existing_predictions(path, expected_sha256):
    records = {}

    if not path.exists():
        return records

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            record = json.loads(line)
            po_id = record["purchasing_document"]

            if record.get("frozen_agent_sha256") != expected_sha256:
                raise SystemExit(
                    "已有盲测结果使用了不同版本的冻结 Agent。"
                    f"请先保留并重命名 {path.name}，再重新运行。"
                )

            if po_id in records:
                raise SystemExit(
                    f"{path.name} 第 {line_number} 行出现重复 PO：{po_id}"
                )

            records[po_id] = record

    return records


def token_usage(response):
    usage = response.usage
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }

    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
    }


def call_agent(client, agent_input, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            response = client.responses.parse(
                model=MODEL,
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
                reasoning={"effort": REASONING_EFFORT},
                text_format=AgentDecision,
                store=False,
            )

            if response.output_parsed is None:
                raise RuntimeError("模型没有返回可解析的结果")

            return response
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)


def choose_agreement_audit(records, audit_size):
    if audit_size <= 0:
        return []

    rng = random.Random(RANDOM_SEED)
    agreements = [record for record in records if record["agree"]]
    chosen = []

    target_decisions = [
        "further_investigation",
        "no_further_investigation",
    ]
    per_group = audit_size // len(target_decisions)

    for decision in target_decisions:
        candidates = sorted(
            record["purchasing_document"]
            for record in agreements
            if record["agent_decision"] == decision
        )
        rng.shuffle(candidates)
        chosen.extend(candidates[:per_group])

    remaining = sorted(
        record["purchasing_document"]
        for record in agreements
        if record["purchasing_document"] not in chosen
    )
    rng.shuffle(remaining)
    chosen.extend(remaining[: max(0, audit_size - len(chosen))])

    return chosen


def create_review_list(records, audit_size):
    disagreements = [
        record["purchasing_document"]
        for record in records
        if not record["agree"]
    ]
    audit_ids = choose_agreement_audit(records, audit_size)

    review_ids = list(dict.fromkeys(disagreements + audit_ids))
    rng = random.Random(RANDOM_SEED + 1)
    rng.shuffle(review_ids)

    review_ids_path.write_text(
        "".join(f"{po_id}\n" for po_id in review_ids),
        encoding="utf-8",
    )

    return disagreements, audit_ids, review_ids


def main():
    parser = argparse.ArgumentParser(
        description="运行盲测集的规则基线与冻结 Agent，并生成盲审清单。"
    )
    parser.add_argument(
        "--audit-size",
        type=int,
        default=10,
        help="从双方一致案例中抽查的数量，默认 10。",
    )
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit(
            "当前 PowerShell 会话没有 OPENAI_API_KEY。"
            "请在已设置密钥的窗口运行本程序。"
        )

    blind_ids = read_ids(blind_ids_path)
    if len(blind_ids) != 60:
        raise SystemExit(
            f"盲测清单应有 60 个 PO，当前读到 {len(blind_ids)} 个。"
        )
    if len(set(blind_ids)) != len(blind_ids):
        raise SystemExit("盲测清单中存在重复 PO。")

    frozen_sha256 = file_sha256(frozen_agent_path)
    predictions = load_existing_predictions(
        prediction_path,
        frozen_sha256,
    )
    unexpected_ids = set(predictions) - set(blind_ids)
    if unexpected_ids:
        raise SystemExit(
            "已有结果包含不属于本次盲测清单的 PO："
            + ", ".join(sorted(unexpected_ids))
        )

    if predictions:
        print(
            f"检测到已完成 {len(predictions)}/60 个案例，将从断点继续。"
        )

    client = OpenAI()
    prediction_path.parent.mkdir(parents=True, exist_ok=True)

    with prediction_path.open("a", encoding="utf-8") as output_file:
        for case_number, po_id in enumerate(blind_ids, start=1):
            if po_id in predictions:
                continue

            print(f"正在运行：{case_number}/60 | PO {po_id}")
            po_record = load_jsonl_record(po_data_path, po_id)
            rule_record = load_jsonl_record(rule_results_path, po_id)
            rule_routing_status = rule_record["po_routing_status"]

            if rule_routing_status not in RULE_DECISION_MAP:
                raise RuntimeError(
                    f"未知规则分流状态：{rule_routing_status}"
                )

            agent_input = build_agent_input(po_record, rule_record)
            response = call_agent(client, agent_input)
            parsed = response.output_parsed
            agent_output = parsed.model_dump(mode="json")

            if agent_output["purchasing_document"] != po_id:
                raise RuntimeError(
                    f"Agent 返回了错误的 PO 编号："
                    f"{agent_output['purchasing_document']}，预期 {po_id}"
                )

            rule_decision = RULE_DECISION_MAP[rule_routing_status]
            agent_decision = agent_output["decision"]
            record = {
                "purchasing_document": po_id,
                "model": MODEL,
                "reasoning_effort": REASONING_EFFORT,
                "frozen_agent_sha256": frozen_sha256,
                "rule_routing_status": rule_routing_status,
                "rule_decision": rule_decision,
                "agent_decision": agent_decision,
                "agree": rule_decision == agent_decision,
                "agent_output": agent_output,
                "usage": token_usage(response),
            }

            output_file.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )
            output_file.flush()
            predictions[po_id] = record

    ordered_records = [predictions[po_id] for po_id in blind_ids]
    disagreements, audit_ids, review_ids = create_review_list(
        ordered_records,
        args.audit_size,
    )

    total_usage = sum(
        record["usage"].get("total_tokens", 0)
        for record in ordered_records
    )
    print("\n规则与冻结 Agent 的盲测运行完成：")
    print(f"案例数量：{len(ordered_records)}")
    print(f"双方一致：{len(ordered_records) - len(disagreements)}")
    print(f"双方不一致：{len(disagreements)}")
    print(f"一致案例抽查：{len(audit_ids)}")
    print(f"需要人工盲审：{len(review_ids)}")
    print(f"总 Token：{total_usage:,}")
    print(f"\n人工盲审清单：{review_ids_path}")
    print(f"系统预测密封文件：{prediction_path}")
    print("人工标注锁定前，请不要打开系统预测密封文件。")


if __name__ == "__main__":
    main()
