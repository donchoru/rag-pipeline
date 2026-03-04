"""JSONL → Gemini 임베딩 → Milvus upsert (별도 실행).

Usage:
    python ingest.py                    # processed.jsonl 전체
    python ingest.py --source "파일.txt"  # 특정 소스 파일만
    python ingest.py --run-id abc12345   # 특정 run_id만
    python ingest.py --dry-run           # 실제 upsert 없이 확인만
"""

import argparse
import json
import logging
from pathlib import Path

from config import OUTPUT_DIR
from llm import LLMClient
from vectorstore import VectorStore

logger = logging.getLogger(__name__)

JSONL_PATH = OUTPUT_DIR / "processed.jsonl"


def load_records(source: str | None = None,
                 run_id: str | None = None) -> list[dict]:
    """processed.jsonl에서 레코드 로드. 필터 옵션 적용."""
    if not JSONL_PATH.exists():
        logger.error(f"JSONL 파일 없음: {JSONL_PATH}")
        return []

    records = []
    with open(JSONL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if source and rec.get("source_file") != source:
                continue
            if run_id and rec.get("run_id") != run_id:
                continue
            records.append(rec)

    return records


def ingest(records: list[dict], dry_run: bool = False) -> dict:
    """레코드의 청크를 임베딩하여 Milvus에 upsert.

    Returns:
        {"total_records": int, "total_chunks": int, "errors": int}
    """
    if not records:
        logger.info("upsert할 레코드 없음")
        return {"total_records": 0, "total_chunks": 0, "errors": 0}

    llm = LLMClient()
    vs = None if dry_run else VectorStore()

    total_chunks = 0
    errors = 0

    for i, rec in enumerate(records, 1):
        source_file = rec["source_file"]
        section = rec.get("section")
        chunks = rec.get("chunks", [])
        topic = rec.get("metadata", {}).get("topic", "")

        if not chunks:
            continue

        label = f"{source_file}" + (f" [{section}]" if section else "")

        try:
            contents = [c["content"] for c in chunks]
            embeddings = llm.embed_texts(contents)

            if not dry_run:
                vs.upsert_chunks(source_file, section, topic, chunks, embeddings)

            total_chunks += len(chunks)
            logger.info(f"[{i}/{len(records)}] {label} — {len(chunks)}개 청크 {'(dry-run)' if dry_run else 'upsert 완료'}")

        except Exception as e:
            errors += 1
            logger.error(f"[{i}/{len(records)}] {label} — 실패: {e}")

    stats = {"total_records": len(records), "total_chunks": total_chunks, "errors": errors}
    logger.info(f"완료: {stats}")
    return stats


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="JSONL → Milvus 벡터 인제스트")
    parser.add_argument("--source", type=str, default=None,
                        help="특정 source_file만 처리")
    parser.add_argument("--run-id", type=str, default=None,
                        help="특정 run_id만 처리")
    parser.add_argument("--dry-run", action="store_true",
                        help="임베딩만 하고 Milvus upsert는 스킵")
    args = parser.parse_args()

    records = load_records(source=args.source, run_id=args.run_id)
    print(f"대상 레코드: {len(records)}개")

    stats = ingest(records, dry_run=args.dry_run)
    print(f"결과: 레코드 {stats['total_records']}, 청크 {stats['total_chunks']}, 에러 {stats['errors']}")
