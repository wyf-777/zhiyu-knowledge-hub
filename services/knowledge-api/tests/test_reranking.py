from __future__ import annotations

from knowledge_api.reranking import RerankerManager, extract_anchors, required_query_anchors


def candidate(chunk_id: str, content: str, score: float, *, heading: str = "", document_name: str = "手册.txt") -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "content": content,
        "heading": heading,
        "document_name": document_name,
        "score": score,
    }


def test_feature_reranker_promotes_exact_error_code() -> None:
    reranker = RerankerManager("feature")
    results = reranker.rerank(
        "XZ-1042 错误代码如何处理？",
        [
            candidate("wrong", "错误代码 XZ-1000 表示网络连接失败。", 0.72),
            candidate("right", "错误代码 XZ-1042 表示授权过期，请替换授权文件。", 0.64),
        ],
        2,
    )

    assert results[0]["chunk_id"] == "right"
    assert results[0]["reranker_model_id"] == "local-feature-reranker-v1"
    assert results[0]["rerank_score"] > results[1]["rerank_score"]


def test_disabled_reranker_preserves_retrieval_order() -> None:
    reranker = RerankerManager("off")
    results = reranker.rerank(
        "任意问题",
        [candidate("first", "第一项", 0.8), candidate("second", "第二项", 0.5)],
        2,
    )

    assert [item["chunk_id"] for item in results] == ["first", "second"]
    assert results[0]["score"] == results[0]["retrieval_score"]
    assert reranker.status().state == "disabled"


def test_feature_reranker_handles_common_time_paraphrase() -> None:
    results = RerankerManager("feature").rerank(
        "出差回来几天内要提交报销？",
        [
            candidate("dorm", "离职员工应在5个工作日内办理退宿。", 0.58, heading="退出流程"),
            candidate("travel", "员工应在出差结束后5个工作日内提交差旅报销申请。", 0.54, heading="报销时限"),
        ],
        2,
    )

    assert results[0]["chunk_id"] == "travel"


def test_error_code_anchor_keeps_word_boundaries_between_heading_and_content() -> None:
    candidate_anchors = extract_anchors("第 1 页\nPDF POLICY ZY-7788 RETENTION")
    assert "zy-7788" in candidate_anchors
    assert all("policy" not in anchor for anchor in candidate_anchors)
    assert extract_anchors("ZY-7788 RETENTION") == {"zy-7788"}


def test_range_lookup_does_not_require_input_value_as_literal_anchor() -> None:
    assert required_query_anchors("司龄6年的年假有几天？") == set()
    assert required_query_anchors("我入职半年，年假有几天？") == set()
    assert required_query_anchors("虚假报销6000元怎么处理？") == set()
    assert required_query_anchors("经理去深圳住酒店650元/晚能不能报？") == set()
    assert required_query_anchors("设备编号 ZY-7788 的保留期？") == {"zy-7788"}
