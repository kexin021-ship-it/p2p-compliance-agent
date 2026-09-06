import argparse
import hashlib
import json
import os
import random
import time
from pathlib import Path

from openai import OpenAI

from agent_baseline_frozen_v1 import (
    load_jsonl_record,
    po_data_path,
    rule_results_path,
)
from agent_baseline_v2 import MODEL, REASONING_EFFORT, run_agent_v2


RANDOM_SEED = 42
VERSION = "V2.2"

project_folder = Path(__file__).resolve().parents[1]
holdout_ids_path = (
    project_folder / "evals" / "blind_holdout_v2_2_po_ids.txt"
)
prediction_path = (
    project_folder
    / "evals"
    / "blind_holdout_system_predictions_frozen_v2_2.jsonl"
)
review_ids_path = (
    project_folder / "evals" / "blind_holdout_review_po_ids_v2_2.txt"
)
manifest_path = (
    project_folder / "evals" / "frozen_agent_v2_2_manifest.json"
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
    return digest.hexdigest().upper()


def verify_frozen_version():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["version"] != VERSION:
        raise SystemExit("冻结清单版本与运行程序不一致。")
    if manifest["model"] != MODEL:
        raise SystemExit("模型与冻结清单不一致。")
    if manifest["reasoning_effort"] != REASONING_EFFORT:
        raise SystemExit("reasoning effort 与冻结清单不一致。")

    for relative_path, expected_hash in manifest["source_sha256"].items():
        actual_hash = file_sha256(project_folder / relative_path)
        if actual_hash != expected_hash:
            raise SystemExit(
                f"冻结文件已变化：{relative_path}\n"
                f"预期：{expected_hash}\n实际：{actual_hash}\n"
                "请不要继续盲测，先确认版本。"
            )

    regression_path = project_folder / manifest["regression_result"]
    regression_hash = file_sha256(regression_path)
    if regression_hash != manifest["regression_result_sha256"]:
        raise SystemExit("V2.2 回归结果文件与冻结清单不一致。")

    canonical = json.dumps(manifest, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest().upper()


def load_existing_predictions(path, frozen_fingerprint):
    records = {}
    if not path.exists():
        return records

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            po_id = record["purchasing_document"]
            if record.get("frozen_agent_fingerprint") != frozen_fingerprint:
                raise SystemExit(
                    "已有预测使用了不同版本。请保留并重命名旧文件，"
                    "不要把不同版本写入同一密封文件。"
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
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
    }


def run_with_retry(po_id, client, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            return run_agent_v2(po_id, client=client)
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)


def choose_agreement_audit(records, audit_size):
    if audit_size <= 0:
        return []

    rng = random.Random(RANDOM_SEED)
    groups = {}
    for record in records:
        if record["agree"]:
            groups.setdefault(record["agent_decision"], []).append(
                record["purchasing_document"]
            )

    chosen = []
    if audit_size >= len(groups):
        for decision in sorted(groups):
            candidates = sorted(groups[decision])
            rng.shuffle(candidates)
            if candidates:
                chosen.append(candidates[0])

    remaining = sorted(
        record["purchasing_document"]
        for record in records
        if record["agree"]
        and record["purchasing_document"] not in chosen
    )
    rng.shuffle(remaining)
    chosen.extend(remaining[: max(0, audit_size - len(chosen))])
    return chosen


def write_review_list(records, audit_size):
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
        description="运行冻结 V2.2 在47个未审案例上的盲测。"
    )
    parser.add_argument(
        "--audit-size",
        type=int,
        default=10,
        help="从双方一致案例中分层抽查的数量，默认10。",
    )
    args = parser.parse_args()

    if args.audit_size < 0:
        raise SystemExit("audit-size 不能小于0。")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit(
            "当前 PowerShell 会话没有 OPENAI_API_KEY。"
            "请在已设置密钥的窗口运行本程序。"
        )

    frozen_fingerprint = verify_frozen_version()
    holdout_ids = read_ids(holdout_ids_path)
    if len(holdout_ids) != 47 or len(set(holdout_ids)) != 47:
        raise SystemExit("V2.2 独立盲测清单必须包含47个不重复的 PO。")

    predictions = load_existing_predictions(
        prediction_path,
        frozen_fingerprint,
    )
    unexpected = set(predictions) - set(holdout_ids)
    if unexpected:
        raise SystemExit(f"预测文件包含清单外 PO：{sorted(unexpected)}")
    if predictions:
        print(f"检测到已完成 {len(predictions)}/47 个案例，将从断点继续。")

    client = OpenAI()
    with prediction_path.open("a", encoding="utf-8") as output_file:
        for case_number, po_id in enumerate(holdout_ids, start=1):
            if po_id in predictions:
                continue

            print(f"正在运行冻结 V2.2：{case_number}/47 | PO {po_id}")
            rule_record = load_jsonl_record(rule_results_path, po_id)
            rule_status = rule_record["po_routing_status"]
            if rule_status not in RULE_DECISION_MAP:
                raise RuntimeError(f"未知规则分流状态：{rule_status}")

            response = run_with_retry(po_id, client)
            agent_output = response.output_parsed.model_dump(mode="json")
            if agent_output["purchasing_document"] != po_id:
                raise RuntimeError(
                    "Agent 返回了错误的 PO 编号："
                    f"{agent_output['purchasing_document']}，预期 {po_id}"
                )

            rule_decision = RULE_DECISION_MAP[rule_status]
            agent_decision = agent_output["decision"]
            record = {
                "purchasing_document": po_id,
                "version": VERSION,
                "model": MODEL,
                "reasoning_effort": REASONING_EFFORT,
                "frozen_agent_fingerprint": frozen_fingerprint,
                "rule_routing_status": rule_status,
                "rule_decision": rule_decision,
                "agent_decision": agent_decision,
                "agree": rule_decision == agent_decision,
                "agent_output": agent_output,
                "usage": token_usage(response),
            }
            output_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            output_file.flush()
            predictions[po_id] = record

    ordered = [predictions[po_id] for po_id in holdout_ids]
    disagreements, audit_ids, review_ids = write_review_list(
        ordered,
        args.audit_size,
    )
    total_tokens = sum(
        record["usage"].get("total_tokens", 0) for record in ordered
    )

    print("\n冻结 V2.2 独立盲测运行完成：")
    print(f"案例数量：{len(ordered)}")
    print(f"双方一致：{len(ordered) - len(disagreements)}")
    print(f"双方不一致：{len(disagreements)}")
    print(f"一致案例抽查：{len(audit_ids)}")
    print(f"需要人工盲审：{len(review_ids)}")
    print(f"总 Token：{total_tokens:,}")
    print(f"冻结版本指纹：{frozen_fingerprint}")
    print(f"\n人工盲审清单：{review_ids_path}")
    print(f"系统预测密封文件：{prediction_path}")
    print("人工标签锁定前，请不要打开系统预测密封文件。")


if __name__ == "__main__":
    main()
