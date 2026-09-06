from pathlib import Path
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

folder = Path(__file__).resolve().parent


def inspect_time_range(path):
    earliest = None
    latest = None
    item_count = 0
    event_count = 0
    missing_time = 0
    invalid_time = 0
    earliest_detail = None
    latest_detail = None

    print(f"\n正在检查：{path.name}", flush=True)

    with path.open("rb") as file:
        context = ET.iterparse(file, events=("start", "end"))
        _, root = next(context)

        for action, element in context:
            tag = element.tag.rsplit("}", 1)[-1]

            if action != "end" or tag != "trace":
                continue

            item_count += 1

            for event in element:
                if event.tag.rsplit("}", 1)[-1] != "event":
                    continue

                event_count += 1

                timestamp = next(
                    (
                        child.get("value")
                        for child in event
                        if child.get("key") == "time:timestamp"
                    ),
                    None,
                )

                if not timestamp:
                    missing_time += 1
                    continue

                try:
                    event_time = datetime.fromisoformat(
                        timestamp.replace("Z", "+00:00")
                    )

                    if event_time.tzinfo is None:
                        raise ValueError("时间缺少时区")

                    event_time = event_time.astimezone(timezone.utc)

                except ValueError:
                    invalid_time += 1
                    continue

                is_earliest = earliest is None or event_time < earliest
                is_latest = latest is None or event_time > latest

                if is_earliest or is_latest:
                    case_id = next(
                        (
                            child.get("value")
                            for child in element
                            if child.get("key") == "concept:name"
                        ),
                        "【未记录】",
                    )

                    activity = next(
                        (
                            child.get("value")
                            for child in event
                            if child.get("key") == "concept:name"
                        ),
                        "【未记录】",
                    )

                    detail = f"Case ID：{case_id} | 活动：{activity}"

                    if is_earliest:
                        earliest = event_time
                        earliest_detail = detail

                    if is_latest:
                        latest = event_time
                        latest_detail = detail

            root.remove(element)
            element.clear()

            if item_count % 50_000 == 0:
                print(
                    f"已检查 {item_count:,} 个行项目",
                    flush=True,
                )

    print(f"\n文件：{path.name}")
    print(f"行项目数量：{item_count:,}")
    print(f"事件数量：{event_count:,}")
    print(f"最早事件时间（UTC）：{earliest}")
    print(f"最晚事件时间（UTC）：{latest}")
    print(f"最早事件详情：{earliest_detail}")
    print(f"最晚事件详情：{latest_detail}")
    print(f"缺失时间的事件：{missing_time:,}")
    print(f"无法解析或缺少时区的事件：{invalid_time:,}")


for filename in [
    "BPI_Challenge_2019.xes",
    "BPI_2019_sample_3000_seed42.xes",
]:
    inspect_time_range(folder / filename)