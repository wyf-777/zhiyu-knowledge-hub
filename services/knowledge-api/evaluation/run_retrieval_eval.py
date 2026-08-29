from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from knowledge_api.evaluation import load_dataset, run_retrieval_evaluation, validate_corpus_provenance  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="运行知域本地检索基线评测")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("retrieval_realistic_v2.json"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--embedding-provider", choices=["hashing", "ollama"], default="hashing")
    parser.add_argument("--embedding-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default="bge-m3")
    parser.add_argument("--reranker-provider", choices=["feature", "off"], default="feature")
    parser.add_argument("--min-recall", type=float)
    parser.add_argument("--min-mrr", type=float)
    parser.add_argument("--min-chunk-mrr", type=float)
    parser.add_argument("--min-answerability", type=float)
    parser.add_argument(
        "--require-real-corpus",
        action="store_true",
        help="仅允许 provenance.type=sanitized-enterprise 的脱敏企业语料",
    )
    args = parser.parse_args()
    try:
        dataset = load_dataset(args.dataset)
        validate_corpus_provenance(dataset, require_real_corpus=args.require_real_corpus)
        with TemporaryDirectory(prefix="zhiyu-eval-") as temporary_dir:
            report = run_retrieval_evaluation(
                dataset,
                Path(temporary_dir),
                top_k=max(1, args.top_k),
                embedding_provider=args.embedding_provider,
                embedding_base_url=args.embedding_base_url,
                embedding_model=args.embedding_model,
                reranker_provider=args.reranker_provider,
            )
    except Exception as error:  # noqa: BLE001
        parser.exit(2, f"检索评测启动失败：{error}\n")
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    thresholds = {
        "recall_at_k": args.min_recall,
        "mrr": args.min_mrr,
        "chunk_mrr": args.min_chunk_mrr,
        "answerability_accuracy": args.min_answerability,
    }
    failures = [
        f"{name}={report['metrics'].get(name)} < {minimum}"
        for name, minimum in thresholds.items()
        if minimum is not None and float(report["metrics"].get(name) or 0.0) < minimum
    ]
    if failures:
        parser.exit(3, "检索质量门禁未通过：" + "; ".join(failures) + "\n")


if __name__ == "__main__":
    main()
