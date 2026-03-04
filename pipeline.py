"""Core 파이프라인 — run_pipeline()."""

import json
import logging
import shutil
import uuid
from pathlib import Path

from config import INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR, ERROR_DIR
import db
from llm import LLMClient

logger = logging.getLogger(__name__)


def run_pipeline() -> str:
    """전체 파이프라인 실행. Returns run_id."""
    run_id = str(uuid.uuid4())[:8]

    # 1. 입력 파일 수집
    txt_files = sorted(INPUT_DIR.glob("*.txt"))
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

    success_count = 0
    error_count = 0

    # 3. 각 파일 처리
    for filepath in txt_files:
        filename = filepath.name
        try:
            text = filepath.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("빈 파일")

            # LLM 호출
            result = llm.structure_document(text, filename)

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

            # 원본 → archive
            shutil.move(str(filepath), str(ARCHIVE_DIR / filename))
            success_count += 1
            logger.info(f"[{run_id}] ✓ {filename}")

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

    # 4. 완료 기록
    db.finish_run(run_id, success_count, error_count)
    logger.info(
        f"[{run_id}] 파이프라인 완료 — 성공: {success_count}, 에러: {error_count}"
    )
    return run_id


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    run_id = run_pipeline()
    print(f"Run ID: {run_id}")
