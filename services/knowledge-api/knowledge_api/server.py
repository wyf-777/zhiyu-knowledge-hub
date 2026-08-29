from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from knowledge_api.app import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="知域本地知识库服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path.cwd() / ".zhiyu-data")
    parser.add_argument("--embedding-provider", choices=["auto", "hashing", "ollama"], default="auto")
    parser.add_argument("--embedding-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default="bge-m3")
    parser.add_argument("--reranker-provider", choices=["feature", "off"], default="feature")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.host not in {"127.0.0.1", "::1"}:
        raise SystemExit("knowledge-api only accepts a loopback host")
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")

    server_holder: dict[str, uvicorn.Server] = {}

    def request_shutdown() -> None:
        server = server_holder.get("server")
        if server is not None:
            server.should_exit = True

    config = uvicorn.Config(
        create_app(
            args.token,
            request_shutdown,
            args.data_dir,
            default_embedding_provider=args.embedding_provider,
            embedding_base_url=args.embedding_base_url,
            embedding_model=args.embedding_model,
            default_reranker_provider=args.reranker_provider,
        ),
        host=args.host,
        port=args.port,
        log_level="warning",
        log_config=None,
        access_log=False,
        server_header=False,
    )
    server = uvicorn.Server(config)
    server_holder["server"] = server
    server.run()


if __name__ == "__main__":
    main()
