from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from knowledge_api.evaluation import load_dataset  # noqa: E402
from knowledge_api.experience_evaluation import (  # noqa: E402
    default_app_data_dir,
    run_experience_evaluation_sync,
    snapshot_knowledge_data,
    validate_experience_manifest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="在本地知识库只读快照上运行真实体验回归")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("experience_regression_v3.json"))
    parser.add_argument("--data-dir", type=Path, default=default_app_data_dir())
    parser.add_argument("--mode", choices=["retrieval", "answer", "all"], default="all")
    parser.add_argument("--embedding-provider", choices=["auto", "hashing", "ollama"], default="auto")
    parser.add_argument("--embedding-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default="bge-m3")
    parser.add_argument("--case", action="append", dest="case_ids", help="仅运行指定案例 id，可重复传入")
    parser.add_argument("--include-answers", action="store_true", help="在本地报告中包含回答正文，仅用于诊断")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", action="store_true", help="控制台仅输出汇总与失败案例")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()
    try:
        manifest = validate_experience_manifest(load_dataset(args.manifest))
        with TemporaryDirectory(prefix="zhiyu-experience-eval-") as temporary_dir:
            snapshot_dir = Path(temporary_dir)
            snapshot_knowledge_data(args.data_dir, snapshot_dir)
            report = run_experience_evaluation_sync(
                manifest,
                snapshot_dir,
                mode=args.mode,
                embedding_provider=args.embedding_provider,
                embedding_base_url=args.embedding_base_url,
                embedding_model=args.embedding_model,
                case_ids=set(args.case_ids or []),
                include_answers=args.include_answers,
            )
    except Exception as error:  # noqa: BLE001
        parser.exit(2, f"真实体验回归启动失败：{error}\n")

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.summary:
        print(json.dumps({
            key: report[key]
            for key in ("manifest_version", "mode", "case_count", "passed", "failed", "pass_rate", "embedding", "elapsed_ms")
        } | {
            "failures": [
                {"id": case["id"], "failures": case["failures"], "latency_ms": case["latency_ms"]}
                for case in report["cases"]
                if not case["passed"]
            ]
        }, ensure_ascii=False, indent=2))
    else:
        print(rendered)
    if args.fail_on_error and report["failed"]:
        parser.exit(3, f"真实体验回归未通过：{report['failed']} / {report['case_count']} 个案例失败\n")


if __name__ == "__main__":
    main()
