import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI
from openai.lib._parsing._responses import type_to_text_format_param

from agent_baseline_frozen_v1 import (
    AgentDecision,
    po_data_path,
    rule_results_path,
)
from agent_baseline_v2 import (
    MODEL,
    REASONING_EFFORT,
    SYSTEM_PROMPT_V2,
    reconcile_focus_items,
)
from po_summary_v2 import build_po_summary
from run_blind_holdout_v2_2 import (
    RULE_DECISION_MAP,
    VERSION,
    file_sha256,
    verify_frozen_version,
)


EXPECTED_PO_COUNT = 3000
MAX_BATCH_FILE_BYTES = 200 * 1024 * 1024
# The Tier 1 queue limit for this model is 5M input tokens. JSON byte size is
# deliberately capped at 12 MiB per part so even text with a relatively dense
# token-to-byte ratio stays comfortably below that queue limit.
MAX_CHUNK_BYTES = 12 * 1024 * 1024

project_folder = Path(__file__).resolve().parents[1]
output_folder = project_folder / "outputs"
request_folder = output_folder / "all_3000_batch_requests_frozen_v2_2"
state_path = output_folder / "all_3000_batch_state_frozen_v2_2.json"
raw_result_folder = output_folder / "all_3000_batch_results_frozen_v2_2"
prediction_path = (
    output_folder / "agent_predictions_all_3000_frozen_v2_2.jsonl"
)
summary_path = (
    output_folder / "agent_predictions_all_3000_frozen_v2_2_summary.json"
)
failed_ids_path = output_folder / "all_3000_batch_failed_po_ids_v2_2.txt"
disagreement_ids_path = (
    output_folder / "all_3000_agent_rule_disagreements_v2_2.txt"
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def write_json(path, value):
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl_index(path):
    records = {}
    order = []
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
            order.append(po_id)
    return records, order


def load_corpus():
    po_records, po_order = load_jsonl_index(po_data_path)
    rule_records, rule_order = load_jsonl_index(rule_results_path)

    if len(po_order) != EXPECTED_PO_COUNT:
        raise SystemExit(
            f"采购单数据应有 {EXPECTED_PO_COUNT} 个 PO，实际为 {len(po_order)}。"
        )
    if len(rule_order) != EXPECTED_PO_COUNT:
        raise SystemExit(
            f"规则结果应有 {EXPECTED_PO_COUNT} 个 PO，实际为 {len(rule_order)}。"
        )

    missing_rules = set(po_order) - set(rule_records)
    unexpected_rules = set(rule_order) - set(po_records)
    if missing_rules or unexpected_rules:
        raise SystemExit(
            "采购单数据与规则结果的 PO 集合不一致。\n"
            f"缺少规则结果：{sorted(missing_rules)[:10]}\n"
            f"规则结果多出：{sorted(unexpected_rules)[:10]}"
        )
    return po_records, rule_records, po_order


def request_part_path(part_number):
    return request_folder / f"part_{part_number:03d}.jsonl"


def raw_output_part_path(part_number):
    return raw_result_folder / f"part_{part_number:03d}_output.jsonl"


def raw_error_part_path(part_number):
    return raw_result_folder / f"part_{part_number:03d}_errors.jsonl"


def request_set_sha256(chunks):
    canonical = json.dumps(
        [
            {
                "index": chunk["index"],
                "po_ids": chunk["po_ids"],
                "request_sha256": chunk["request_sha256"],
            }
            for chunk in chunks
        ],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    import hashlib

    return hashlib.sha256(canonical).hexdigest().upper()


def build_request_files(po_records, rule_records, po_order):
    text_format = type_to_text_format_param(AgentDecision)
    request_folder.mkdir(parents=True, exist_ok=True)
    chunks = []
    part_number = 0
    part_file = None
    temporary_path = None
    part_po_ids = []
    part_size = 0

    def close_part():
        nonlocal part_file, temporary_path, part_po_ids, part_size
        if part_file is None:
            return
        part_file.close()
        final_path = request_part_path(part_number)
        temporary_path.replace(final_path)
        if final_path.stat().st_size > MAX_BATCH_FILE_BYTES:
            raise SystemExit(f"{final_path.name} 超过 200 MB Batch 文件上限。")
        chunks.append(
            {
                "index": part_number,
                "request_file": str(final_path.relative_to(project_folder)),
                "request_bytes": final_path.stat().st_size,
                "request_sha256": file_sha256(final_path),
                "po_count": len(part_po_ids),
                "po_ids": list(part_po_ids),
                "status": "prepared",
            }
        )
        part_file = None
        temporary_path = None
        part_po_ids = []
        part_size = 0

    for case_number, po_id in enumerate(po_order, start=1):
        po_summary = build_po_summary(po_records[po_id], rule_records[po_id])
        request = {
            "custom_id": po_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": MODEL,
                "input": [
                    {"role": "system", "content": SYSTEM_PROMPT_V2},
                    {
                        "role": "user",
                        "content": json.dumps(
                            po_summary,
                            ensure_ascii=False,
                            indent=2,
                        ),
                    },
                ],
                "reasoning": {"effort": REASONING_EFFORT},
                "text": {"format": text_format},
                "store": False,
            },
        }
        encoded_line = (
            json.dumps(request, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        if len(encoded_line) > MAX_CHUNK_BYTES:
            raise SystemExit(f"单个 PO 请求过大，无法安全分批：{po_id}")
        if part_file is not None and part_size + len(encoded_line) > MAX_CHUNK_BYTES:
            close_part()
        if part_file is None:
            part_number += 1
            final_path = request_part_path(part_number)
            temporary_path = final_path.with_suffix(final_path.suffix + ".tmp")
            part_file = temporary_path.open("wb")
        part_file.write(encoded_line)
        part_po_ids.append(po_id)
        part_size += len(encoded_line)
        if case_number % 250 == 0:
            print(f"正在准备全量请求：{case_number}/{len(po_order)}")
    close_part()

    total_size = sum(chunk["request_bytes"] for chunk in chunks)
    return chunks, total_size, request_set_sha256(chunks)


def verify_state(state, fingerprint):
    expected = {
        "version": VERSION,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "frozen_agent_fingerprint": fingerprint,
        "po_count": EXPECTED_PO_COUNT,
        "po_data_sha256": file_sha256(po_data_path),
        "rule_results_sha256": file_sha256(rule_results_path),
    }
    for key, expected_value in expected.items():
        if state.get(key) != expected_value:
            raise SystemExit(
                f"批处理状态与当前冻结实验不一致：{key}\n"
                f"状态文件：{state.get(key)}\n当前值：{expected_value}\n"
                "请保留现有文件，不要混合不同版本或不同数据。"
            )

    chunks = state.get("chunks") or []
    all_po_ids = [po_id for chunk in chunks for po_id in chunk.get("po_ids", [])]
    if len(all_po_ids) != EXPECTED_PO_COUNT or len(set(all_po_ids)) != EXPECTED_PO_COUNT:
        raise SystemExit("分批状态没有完整且唯一地覆盖 3000 个 PO。")
    for chunk in chunks:
        path = project_folder / chunk["request_file"]
        if not path.exists():
            raise SystemExit(f"找不到分批请求文件：{path}")
        if file_sha256(path) != chunk["request_sha256"]:
            raise SystemExit(
                f"分批请求文件发生变化：{path.name}，已停止以保护实验完整性。"
            )
    if state.get("request_set_sha256") != request_set_sha256(chunks):
        raise SystemExit("分批请求集合指纹不一致，已停止以保护实验完整性。")


def request_counts_dict(batch):
    counts = getattr(batch, "request_counts", None)
    return {
        "total": getattr(counts, "total", 0) or 0,
        "completed": getattr(counts, "completed", 0) or 0,
        "failed": getattr(counts, "failed", 0) or 0,
    }


def save_batch_status(state, chunk, batch):
    chunk["status"] = batch.status
    chunk["request_counts"] = request_counts_dict(batch)
    chunk["output_file_id"] = getattr(batch, "output_file_id", None)
    chunk["error_file_id"] = getattr(batch, "error_file_id", None)
    state["last_checked_at"] = utc_now()
    write_json(state_path, state)


def submit_chunk(client, state, chunk):
    path = project_folder / chunk["request_file"]
    print(
        f"正在上传第 {chunk['index']}/{len(state['chunks'])} 批："
        f"{chunk['po_count']} 个 PO……"
    )
    with path.open("rb") as file:
        uploaded = client.files.create(file=file, purpose="batch")
    chunk["input_file_id"] = uploaded.id
    write_json(state_path, state)
    batch = client.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/responses",
        completion_window="24h",
        metadata={
            "description": (
                f"frozen V2.2 all 3000 PO part {chunk['index']}"
            ),
            "version": VERSION,
        },
    )
    chunk["batch_id"] = batch.id
    chunk["submitted_at"] = utc_now()
    save_batch_status(state, chunk, batch)
    return batch


def download_file(client, file_id, path):
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    client.files.content(file_id).write_to_file(temporary_path)
    temporary_path.replace(path)


def response_output_text(body):
    pieces = []
    refusal_messages = []
    for output_item in body.get("output", []):
        if output_item.get("type") != "message":
            continue
        for content_item in output_item.get("content", []):
            if content_item.get("type") == "output_text":
                pieces.append(content_item.get("text", ""))
            elif content_item.get("type") == "refusal":
                refusal_messages.append(content_item.get("refusal", ""))
    if refusal_messages:
        raise ValueError("模型拒绝输出：" + " ".join(refusal_messages))
    if not pieces:
        raise ValueError("Response 中没有 output_text。")
    return "".join(pieces)


def read_batch_results(path):
    results = {}
    failures = {}
    if not path.exists():
        return results, failures

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            po_id = record.get("custom_id")
            if not po_id:
                raise SystemExit(f"批处理输出第 {line_number} 行没有 custom_id。")
            if po_id in results or po_id in failures:
                raise SystemExit(f"批处理输出出现重复 PO：{po_id}")
            response = record.get("response")
            error = record.get("error")
            if error or not response or response.get("status_code") != 200:
                failures[po_id] = error or response
            else:
                results[po_id] = response["body"]
    return results, failures


def read_error_results(path):
    failures = {}
    if not path.exists():
        return failures
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            failures[record.get("custom_id", "UNKNOWN")] = record.get("error")
    return failures


def usage_dict(body):
    usage = body.get("usage") or {}
    return {
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "total_tokens": usage.get("total_tokens", 0) or 0,
    }


def validate_existing_final(fingerprint):
    if not prediction_path.exists():
        return False
    records, order = load_jsonl_index(prediction_path)
    if len(order) != EXPECTED_PO_COUNT:
        raise SystemExit(
            f"已有全量结果只有 {len(order)} 条；为避免覆盖，程序已停止。"
        )
    if any(
        record.get("frozen_agent_fingerprint") != fingerprint
        for record in records.values()
    ):
        raise SystemExit("已有全量结果与当前冻结版本不一致。")
    print("全量 3000 个 PO 已经完成，无需再次提交。")
    print(f"结果文件：{prediction_path}")
    print(f"汇总文件：{summary_path}")
    return True


def finalize_predictions(state, fingerprint):
    po_records, rule_records, po_order = load_corpus()
    raw_results = {}
    failures = {}
    batch_by_po = {}
    for chunk in state["chunks"]:
        part_results, part_failures = read_batch_results(
            raw_output_part_path(chunk["index"])
        )
        part_failures.update(
            read_error_results(raw_error_part_path(chunk["index"]))
        )
        duplicate_ids = (set(raw_results) | set(failures)) & (
            set(part_results) | set(part_failures)
        )
        if duplicate_ids:
            raise SystemExit(
                f"不同批次中出现重复 PO：{sorted(duplicate_ids)[:10]}"
            )
        raw_results.update(part_results)
        failures.update(part_failures)
        for po_id in chunk["po_ids"]:
            batch_by_po[po_id] = chunk["batch_id"]
    missing = set(po_order) - set(raw_results) - set(failures)
    unexpected = (set(raw_results) | set(failures)) - set(po_order)
    if unexpected:
        raise SystemExit(f"批处理结果包含数据集外 PO：{sorted(unexpected)[:10]}")

    if failures or missing:
        failed_ids = [po_id for po_id in po_order if po_id in failures or po_id in missing]
        atomic_write_text(
            failed_ids_path,
            "".join(f"{po_id}\n" for po_id in failed_ids),
        )
        print("批处理存在失败或缺失请求，暂不生成不完整的全量结果。")
        print(f"成功：{len(raw_results)}/{EXPECTED_PO_COUNT}")
        print(f"失败或缺失：{len(failed_ids)}")
        print(f"失败清单：{failed_ids_path}")
        return False

    records = []
    decision_counts = Counter()
    agreement_count = 0
    total_usage = Counter()
    for case_number, po_id in enumerate(po_order, start=1):
        body = raw_results[po_id]
        parsed = AgentDecision.model_validate_json(response_output_text(body))
        if parsed.purchasing_document != po_id:
            raise SystemExit(
                f"Agent 返回了错误 PO：{parsed.purchasing_document}，预期 {po_id}"
            )

        po_summary = build_po_summary(po_records[po_id], rule_records[po_id])
        parsed.focus_items = reconcile_focus_items(parsed, po_summary)
        agent_output = parsed.model_dump(mode="json")
        rule_status = rule_records[po_id]["po_routing_status"]
        if rule_status not in RULE_DECISION_MAP:
            raise SystemExit(f"未知规则分流状态：{rule_status}")
        rule_decision = RULE_DECISION_MAP[rule_status]
        agent_decision = agent_output["decision"]
        agree = rule_decision == agent_decision
        usage = usage_dict(body)
        record = {
            "purchasing_document": po_id,
            "version": VERSION,
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "frozen_agent_fingerprint": fingerprint,
            "batch_id": batch_by_po[po_id],
            "rule_routing_status": rule_status,
            "rule_decision": rule_decision,
            "agent_decision": agent_decision,
            "agree": agree,
            "agent_output": agent_output,
            "usage": usage,
        }
        records.append(record)
        decision_counts[agent_decision] += 1
        agreement_count += int(agree)
        total_usage.update(usage)
        if case_number % 250 == 0:
            print(f"正在整理全量结果：{case_number}/{EXPECTED_PO_COUNT}")

    prediction_content = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    atomic_write_text(prediction_path, prediction_content)
    disagreement_ids = [
        record["purchasing_document"] for record in records if not record["agree"]
    ]
    atomic_write_text(
        disagreement_ids_path,
        "".join(f"{po_id}\n" for po_id in disagreement_ids),
    )
    summary = {
        "experiment": "frozen_v2_2_all_3000",
        "completed_at": utc_now(),
        "po_count": EXPECTED_PO_COUNT,
        "version": VERSION,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "frozen_agent_fingerprint": fingerprint,
        "batch_ids": [chunk["batch_id"] for chunk in state["chunks"]],
        "po_data_sha256": state["po_data_sha256"],
        "rule_results_sha256": state["rule_results_sha256"],
        "request_set_sha256": state["request_set_sha256"],
        "prediction_sha256": file_sha256(prediction_path),
        "agent_decision_counts": dict(sorted(decision_counts.items())),
        "agent_rule_agreement_count": agreement_count,
        "agent_rule_disagreement_count": EXPECTED_PO_COUNT - agreement_count,
        "agent_rule_agreement_rate": agreement_count / EXPECTED_PO_COUNT,
        "usage": dict(total_usage),
        "interpretation_limit": (
            "全量结果可用于描述分布和规则/Agent一致性；"
            "没有人工标签的 PO 不能用于计算绝对准确率。"
        ),
    }
    write_json(summary_path, summary)
    print("\n冻结 V2.2 全量运行完成：")
    print(f"案例数量：{EXPECTED_PO_COUNT:,}")
    print(f"双方一致：{agreement_count:,}")
    print(f"双方不一致：{EXPECTED_PO_COUNT - agreement_count:,}")
    print(f"总 Token：{total_usage['total_tokens']:,}")
    print(f"结果文件：{prediction_path}")
    print(f"汇总文件：{summary_path}")
    print(f"分歧清单：{disagreement_ids_path}")
    return True


def print_chunk_status(state, chunk, batch):
    counts = request_counts_dict(batch)
    print("\n冻结 V2.2 全量批处理状态：")
    print(f"批次：{chunk['index']}/{len(state['chunks'])}")
    print(f"Batch ID：{batch.id}")
    print(f"状态：{batch.status}")
    print(
        "请求进度："
        f"完成 {counts['completed']:,} / {counts['total']:,}，"
        f"失败 {counts['failed']:,}"
    )


def collect_completed_chunk(client, state, chunk, batch):
    if not batch.output_file_id:
        raise SystemExit("Batch 已完成，但没有 output_file_id。")
    raw_result_folder.mkdir(parents=True, exist_ok=True)
    output_path = raw_output_part_path(chunk["index"])
    error_path = raw_error_part_path(chunk["index"])
    print(f"正在下载并校验第 {chunk['index']} 批结果……")
    download_file(client, batch.output_file_id, output_path)
    if batch.error_file_id:
        download_file(client, batch.error_file_id, error_path)
    elif error_path.exists():
        error_path.unlink()

    results, failures = read_batch_results(output_path)
    failures.update(read_error_results(error_path))
    expected = set(chunk["po_ids"])
    unexpected = (set(results) | set(failures)) - expected
    missing = expected - set(results) - set(failures)
    if unexpected:
        raise SystemExit(
            f"第 {chunk['index']} 批包含清单外 PO：{sorted(unexpected)[:10]}"
        )
    if failures or missing:
        failed_ids = [
            po_id
            for po_id in chunk["po_ids"]
            if po_id in failures or po_id in missing
        ]
        atomic_write_text(
            failed_ids_path,
            "".join(f"{po_id}\n" for po_id in failed_ids),
        )
        print(f"第 {chunk['index']} 批存在失败或缺失请求，已停止后续提交。")
        print(f"成功：{len(results)}/{chunk['po_count']}")
        print(f"失败或缺失：{len(failed_ids)}")
        print(f"失败清单：{failed_ids_path}")
        return False

    chunk["collected"] = True
    chunk["collected_at"] = utc_now()
    write_json(state_path, state)
    print(f"第 {chunk['index']} 批已完整保存：{len(results)} 个 PO。")
    return True


def main():
    parser = argparse.ArgumentParser(
        description=(
            "冻结 V2.2 在全部 3000 个 PO 上的 Batch API 运行器。"
            "首次运行提交任务；之后运行同一命令查询并下载结果。"
        )
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只生成并检查 Batch JSONL，不上传或提交。",
    )
    args = parser.parse_args()

    fingerprint = verify_frozen_version()
    if validate_existing_final(fingerprint):
        return

    if not state_path.exists():
        po_records, rule_records, po_order = load_corpus()
        chunks, total_size, request_hash = build_request_files(
            po_records,
            rule_records,
            po_order,
        )
        state = {
            "experiment": "frozen_v2_2_all_3000_chunked",
            "created_at": utc_now(),
            "version": VERSION,
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "frozen_agent_fingerprint": fingerprint,
            "po_count": EXPECTED_PO_COUNT,
            "po_data_sha256": file_sha256(po_data_path),
            "rule_results_sha256": file_sha256(rule_results_path),
            "request_set_sha256": request_hash,
            "chunks": chunks,
        }
        write_json(state_path, state)
        print(
            f"分批请求已生成：{EXPECTED_PO_COUNT:,} 个 PO，"
            f"共 {len(chunks)} 批，{total_size / 1024 / 1024:.1f} MB"
        )
        print(f"分批请求集合 SHA256：{request_hash}")
    else:
        state = read_json(state_path)
        verify_state(state, fingerprint)

    if args.prepare_only:
        submitted = sum(bool(chunk.get("batch_id")) for chunk in state["chunks"])
        collected = sum(bool(chunk.get("collected")) for chunk in state["chunks"])
        print(
            f"prepare-only 检查完成：共 {len(state['chunks'])} 批，"
            f"已提交 {submitted} 批，已收集 {collected} 批。"
        )
        print("本次没有上传、提交或查询 API。")
        return

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit(
            "当前 PowerShell 会话没有 OPENAI_API_KEY。"
            "请在已设置密钥的窗口运行本程序；本次没有提交 API。"
        )

    client = OpenAI()
    for chunk in state["chunks"]:
        if chunk.get("collected"):
            continue

        if not chunk.get("batch_id"):
            batch = submit_chunk(client, state, chunk)
            print_chunk_status(state, chunk, batch)
            print(
                "这一批已在 OpenAI 后台运行。稍后再次执行同一个命令；"
                "程序会先收回结果，再自动提交下一批。"
            )
            return

        batch = client.batches.retrieve(chunk["batch_id"])
        save_batch_status(state, chunk, batch)
        print_chunk_status(state, chunk, batch)

        if batch.status == "completed":
            if not collect_completed_chunk(client, state, chunk, batch):
                return
            continue
        if batch.status in {"failed", "expired", "cancelled"}:
            error_path = raw_error_part_path(chunk["index"])
            if batch.error_file_id:
                raw_result_folder.mkdir(parents=True, exist_ok=True)
                download_file(client, batch.error_file_id, error_path)
            print(f"这一批未成功结束，错误文件：{error_path}")
            print("程序没有提交下一批，以免掩盖问题。")
            return

        print("任务仍在 OpenAI 后台运行。稍后再次执行同一个命令即可查询。")
        return

    finalize_predictions(state, fingerprint)


if __name__ == "__main__":
    main()
