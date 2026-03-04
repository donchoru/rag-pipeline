"""Core 파이프라인 — run_pipeline()."""

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone

from config import INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR, ERROR_DIR, TRACE_DIR
import db
from llm import LLMClient

logger = logging.getLogger(__name__)


def run_pipeline(since: float | None = None) -> str:
    """전체 파이프라인 실행. Returns run_id.

    Args:
        since: Unix timestamp. 지정 시 이 시각 이후 변경된 파일만 처리.
    """
    run_id = str(uuid.uuid4())[:8]

    # 1. 입력 파일 수집
    txt_files = sorted(INPUT_DIR.glob("*.txt"))
    if since is not None:
        txt_files = [f for f in txt_files if f.stat().st_mtime > since]
    total = len(txt_files)

    logger.info(f"[{run_id}] 파이프라인 시작 — {total}개 파일")

    if total == 0:
        logger.info(f"[{run_id}] 처리할 파일 없음")
        db.create_run(run_id, 0)
        db.finish_run(run_id, 0, 0)
        return run_id

    db.create_run(run_id, total)

    # 2. LLM 클라이언트 초기화
    llm = LLMClient()

    # 3. 트레이스 디렉토리 (run_id별)
    run_trace_dir = TRACE_DIR / run_id
    run_trace_dir.mkdir(exist_ok=True)

    success_count = 0
    error_count = 0

    # 4. 각 파일 처리
    for filepath in txt_files:
        filename = filepath.name
        try:
            text = filepath.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("빈 파일")

            # LLM 호출 → 결과 + 트레이스
            output = llm.structure_document(text, filename)
            result = output["result"]
            trace = output["trace"]

            # JSONL 출력
            output_record = {
                "source_file": filename,
                "run_id": run_id,
                "markdown": result["markdown"],
                "metadata": result["metadata"],
                "chunks": result["chunks"],
            }

            output_path = OUTPUT_DIR / "processed.jsonl"
            with open(output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(output_record, ensure_ascii=False) + "\n")

            # 트레이스 파일 저장
            trace_record = {
                "run_id": run_id,
                "filename": filename,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": trace["model"],
                "input_chars": trace["input_chars"],
                "input_tokens": trace["input_tokens"],
                "output_tokens": trace["output_tokens"],
                "latency_ms": trace["latency_ms"],
                "reasoning": trace["reasoning"],
                "output_summary": {
                    "chunk_count": len(result["chunks"]),
                    "chunk_sizes": [len(c["content"]) for c in result["chunks"]],
                    "metadata": result["metadata"],
                },
            }

            stem = filepath.stem  # 확장자 제외 파일명
            trace_path = run_trace_dir / f"{stem}.trace.json"
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(trace_record, f, ensure_ascii=False, indent=2)

            # 원본 → archive
            shutil.move(str(filepath), str(ARCHIVE_DIR / filename))
            success_count += 1
            logger.info(f"[{run_id}] ✓ {filename} (trace → {trace_path.name})")

        except Exception as e:
            error_count += 1
            error_type = type(e).__name__
            error_msg = str(e)[:500]
            logger.error(f"[{run_id}] ✗ {filename}: {error_type} — {error_msg}")

            db.log_error(run_id, filename, error_type, error_msg)

            # 원본 → error
            try:
                shutil.move(str(filepath), str(ERROR_DIR / filename))
            except Exception:
                pass

    # 5. 완료 기록
    db.finish_run(run_id, success_count, error_count)
    logger.info(
        f"[{run_id}] 파이프라인 완료 — 성공: {success_count}, 에러: {error_count}"
    )
    return run_id


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=float, default=None,
                        help="Unix timestamp — 이 시각 이후 변경된 파일만 처리")
    args = parser.parse_args()

    run_id = run_pipeline(since=args.since)
    print(f"Run ID: {run_id}")
