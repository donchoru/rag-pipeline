"""Core 파이프라인 — run_pipeline()."""

import fcntl
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import INPUT_DIR, OUTPUT_DIR, TRACE_DIR, BASE_DIR, SPLIT_THRESHOLD
import db
from llm import LLMClient

logger = logging.getLogger(__name__)

LOCK_FILE = BASE_DIR / ".pipeline.lock"


class PipelineBusy(RuntimeError):
    """다른 파이프라인이 실행 중."""


MODE_FILE = INPUT_DIR / ".mode.json"


def _load_modes() -> dict[str, str]:
    """파일별 처리 모드 로드. 없으면 빈 dict."""
    if MODE_FILE.exists():
        try:
            return json.loads(MODE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


SELECTED_FILE = INPUT_DIR / ".selected.json"


def run_pipeline(since: float | None = None, default_mode: str = "preserve",
                 use_selected: bool = False) -> str:
    """전체 파이프라인 실행. Returns run_id.

    Args:
        since: Unix timestamp. 지정 시 이 시각 이후 변경된 파일만 처리.
        default_mode: 기본 처리 모드 ("preserve" 또는 "reorganize").
        use_selected: True면 .selected.json에 명시된 파일만 처리.
    Raises:
        PipelineBusy: 다른 파이프라인이 이미 실행 중일 때.
    """
    # 0. 동시 실행 방지 (file lock)
    lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fp.close()
        raise PipelineBusy("다른 사용자가 파이프라인을 실행 중입니다. 잠시 후 다시 시도하세요.")

    try:
        return _run_pipeline_locked(since, default_mode, use_selected, lock_fp)
    finally:
        fcntl.flock(lock_fp, fcntl.LOCK_UN)
        lock_fp.close()
        LOCK_FILE.unlink(missing_ok=True)


def _run_pipeline_locked(since: float | None, default_mode: str,
                         use_selected: bool, lock_fp) -> str:
    """락 획득 후 실제 파이프라인 실행."""
    run_id = str(uuid.uuid4())[:8]
    file_modes = _load_modes()

    # 1. 입력 파일 수집
    txt_files = sorted(INPUT_DIR.rglob("*.txt"))
    if use_selected and SELECTED_FILE.exists():
        try:
            selected = set(json.loads(SELECTED_FILE.read_text(encoding="utf-8")))
            txt_files = [f for f in txt_files
                         if str(f.relative_to(INPUT_DIR)) in selected]
        except (json.JSONDecodeError, OSError):
            pass
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
        rel_path = filepath.relative_to(INPUT_DIR)
        filename = str(rel_path)
        try:
            text = filepath.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("빈 파일")

            mode = file_modes.get(filename, default_mode)

            # 대용량 문서 자동 분할
            if len(text) > SPLIT_THRESHOLD:
                logger.info(f"[{run_id}] 대용량 문서 분할: {filename} ({len(text)}자)")
                sections = llm.split_document(text, filepath.name)
                total_sections = len(sections)

                for sec_idx, section in enumerate(sections, 1):
                    section_label = f"{sec_idx}/{total_sections}: {section['title']}"
                    logger.info(f"[{run_id}]   섹션 {section_label} 처리 중…")

                    output = llm.structure_document(
                        section["content"], filepath.name, mode=mode,
                    )
                    result = output["result"]
                    trace = output["trace"]

                    output_record = {
                        "source_file": filename,
                        "section": section_label,
                        "run_id": run_id,
                        "mode": mode,
                        "markdown": result["markdown"],
                        "metadata": result["metadata"],
                        "chunks": result["chunks"],
                    }

                    output_path = OUTPUT_DIR / "processed.jsonl"
                    with open(output_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(output_record, ensure_ascii=False) + "\n")

                    trace_record = {
                        "run_id": run_id,
                        "filename": filename,
                        "section": section_label,
                        "mode": mode,
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

                    stem = filepath.stem
                    trace_path = run_trace_dir / f"{stem}_sec{sec_idx}.trace.json"
                    with open(trace_path, "w", encoding="utf-8") as f:
                        json.dump(trace_record, f, ensure_ascii=False, indent=2)

                    logger.info(f"[{run_id}]   ✓ 섹션 {section_label} (trace → {trace_path.name})")

                success_count += 1
                logger.info(f"[{run_id}] ✓ {filename} ({total_sections}개 섹션 완료)")

            else:
                # 기존 로직 — 10KB 이하 문서
                output = llm.structure_document(text, filepath.name, mode=mode)
                result = output["result"]
                trace = output["trace"]

                output_record = {
                    "source_file": filename,
                    "section": None,
                    "run_id": run_id,
                    "mode": mode,
                    "markdown": result["markdown"],
                    "metadata": result["metadata"],
                    "chunks": result["chunks"],
                }

                output_path = OUTPUT_DIR / "processed.jsonl"
                with open(output_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(output_record, ensure_ascii=False) + "\n")

                trace_record = {
                    "run_id": run_id,
                    "filename": filename,
                    "mode": mode,
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

                stem = filepath.stem
                trace_path = run_trace_dir / f"{stem}.trace.json"
                with open(trace_path, "w", encoding="utf-8") as f:
                    json.dump(trace_record, f, ensure_ascii=False, indent=2)

                success_count += 1
                logger.info(f"[{run_id}] ✓ {filename} (trace → {trace_path.name})")

        except Exception as e:
            error_count += 1
            error_type = type(e).__name__
            error_msg = str(e)[:500]
            logger.error(f"[{run_id}] ✗ {filename}: {error_type} — {error_msg}")

            db.log_error(run_id, filename, error_type, error_msg)

    # 5. 처리 완료된 파일의 모드 설정 정리
    if file_modes:
        remaining = {k: v for k, v in file_modes.items()
                     if (INPUT_DIR / k).exists()}
        if remaining != file_modes:
            MODE_FILE.write_text(
                json.dumps(remaining, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    # 6. 완료 기록
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
    parser.add_argument("--mode", choices=["preserve", "reorganize"],
                        default="preserve",
                        help="기본 처리 모드 (파일별 .mode.json 우선)")
    parser.add_argument("--selected", action="store_true",
                        help=".selected.json에 명시된 파일만 처리")
    args = parser.parse_args()

    run_id = run_pipeline(since=args.since, default_mode=args.mode,
                          use_selected=args.selected)
    print(f"Run ID: {run_id}")
