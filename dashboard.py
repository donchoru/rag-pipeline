"""Streamlit 대시보드 — RAG 파이프라인 모니터링."""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent))

import db
from config import INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR, ERROR_DIR, TRACE_DIR, SCHEDULE_CRON

PROCESSED_JSONL = OUTPUT_DIR / "processed.jsonl"

st.set_page_config(page_title="RAG Pipeline", page_icon="📄", layout="wide")
st.title("📄 RAG Pipeline 대시보드")

tab1, tab_upload, tab_search, tab2, tab3, tab4 = st.tabs(
    ["대시보드", "📤 업로드", "🔍 검색", "트레이스", "에러 로그", "설정"],
)

# ── 탭 1: 대시보드 ──
with tab1:
    # 메트릭
    last_run = db.get_last_run()
    stats = db.get_total_stats()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if last_run:
            status = last_run["status"]
            color = "🟢" if status == "Success" else ("🟡" if status == "Running" else "🔴")
            st.metric("최근 실행", f"{color} {status}")
        else:
            st.metric("최근 실행", "없음")

    with col2:
        st.metric("총 처리 문서", stats["total_success"])

    with col3:
        st.metric("에러 문서", stats["total_error"])

    with col4:
        cron_str = f"매일 {SCHEDULE_CRON.get('hour', 0):02d}:{SCHEDULE_CRON.get('minute', 0):02d} KST"
        st.metric("실행 스케줄", cron_str)

    st.divider()

    # 수동 실행 — 변경/전체 파일 카운트
    all_pending = sorted(INPUT_DIR.glob("*.txt"))
    all_archived = sorted(ARCHIVE_DIR.glob("*.txt"))
    _last = db.get_last_run()
    _since = 0.0
    if _last and _last.get("start_time"):
        _since = datetime.fromisoformat(_last["start_time"]).timestamp()
    changed_count = sum(1 for f in all_pending if f.stat().st_mtime > _since)
    total_count = len(all_pending) + len(all_archived)

    run_mode = st.radio(
        "실행 모드",
        [f"변경된 파일만 ({changed_count}개)", f"전체 재실행 ({total_count}개)"],
        horizontal=True,
        help="변경된 파일만: 마지막 실행 이후 새로 추가된 파일만 처리\n"
             "전체 재실행: 이미 처리된 archive 파일까지 전부 다시 처리",
    )

    _lock_file = Path(__file__).parent / ".pipeline.lock"
    _is_locked = _lock_file.exists()

    if _is_locked:
        st.warning("🔒 다른 사용자가 파이프라인을 실행 중입니다. 완료 후 다시 시도하세요.")

    if st.button("🚀 지금 즉시 실행", type="primary", disabled=_is_locked):
        cmd = [sys.executable, "pipeline.py"]
        if run_mode.startswith("변경된 파일만") and _since > 0:
            cmd += ["--since", str(_since)]
        elif run_mode.startswith("전체 재실행"):
            import shutil
            for af in all_archived:
                dest = INPUT_DIR / af.name
                if not dest.exists():
                    shutil.copy2(str(af), str(dest))
        with st.spinner("파이프라인 실행 중… (다른 사용자는 대기)"):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True, text=True, timeout=600,
                    cwd=str(Path(__file__).parent),
                )
                if result.returncode == 0:
                    st.success(f"완료! {result.stdout.strip()}")
                elif "PipelineBusy" in (result.stderr or ""):
                    st.warning("다른 사용자가 실행 중입니다. 잠시 후 다시 시도하세요.")
                else:
                    st.error(f"에러: {result.stderr.strip()}")
            except subprocess.TimeoutExpired:
                st.error("타임아웃 (10분 초과)")
        st.rerun()

    # 실행 이력
    st.subheader("실행 이력")
    runs: list = db.get_runs(20)
    if runs:
        df = pd.DataFrame(runs)
        display_cols = ["run_id", "start_time", "end_time", "total_files", "success_count", "error_count", "status"]
        df = df[[c for c in display_cols if c in df.columns]]
        df.columns = ["Run ID", "시작", "종료", "전체", "성공", "에러", "상태"]
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("아직 실행 이력이 없습니다.")

# ── 탭: 업로드 ──
with tab_upload:
    st.subheader("문서 업로드")

    uploaded_files = st.file_uploader(
        "`.txt` 파일을 드래그하거나 선택하세요",
        type=["txt"],
        accept_multiple_files=True,
    )

    run_after = st.checkbox("업로드 후 즉시 파이프라인 실행", value=True)

    if uploaded_files:
        upload_ts = datetime.now().timestamp()
        for uf in uploaded_files:
            dest = INPUT_DIR / uf.name
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                dest = INPUT_DIR / f"{stem}_{ts}{suffix}"
            dest.write_bytes(uf.getvalue())
            st.success(f"저장: `{dest.name}`")

        if run_after:
            with st.spinner("파이프라인 실행 중…"):
                try:
                    result = subprocess.run(
                        [sys.executable, "pipeline.py",
                         "--since", str(upload_ts - 5)],
                        capture_output=True, text=True, timeout=600,
                        cwd=str(Path(__file__).parent),
                    )
                    if result.returncode == 0:
                        st.success(f"파이프라인 완료! {result.stdout.strip()}")
                    elif "PipelineBusy" in (result.stderr or ""):
                        st.warning("다른 사용자가 파이프라인 실행 중입니다. 파일은 저장되었으니, 완료 후 대시보드에서 실행하세요.")
                    else:
                        st.error(f"파이프라인 에러: {result.stderr.strip()}")
                except subprocess.TimeoutExpired:
                    st.error("타임아웃 (10분 초과)")
            st.rerun()

    st.divider()
    st.subheader("대기 파일 목록")
    pending = sorted(INPUT_DIR.glob("*.txt"))
    if pending:
        last = db.get_last_run()
        since_ts = 0.0
        if last and last.get("start_time"):
            since_ts = datetime.fromisoformat(last["start_time"]).timestamp()

        changed = [f for f in pending if f.stat().st_mtime > since_ts]
        unchanged = [f for f in pending if f.stat().st_mtime <= since_ts]

        def _file_card(f: Path, icon: str):
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m/%d %H:%M")
            text = f.read_text(encoding="utf-8", errors="replace")
            first_line = text.split("\n", 1)[0].strip()
            preview = text[:300].replace("\n", " ").strip()
            if len(text) > 300:
                preview += "…"
            with st.expander(f"{icon} **{f.name}**  —  {first_line}"):
                st.caption(f"{f.stat().st_size:,} bytes  |  수정: {mtime}")
                st.text(preview)

        if changed:
            st.caption(f"🟢 새로 추가/변경된 파일 ({len(changed)}개)")
            for f in changed:
                _file_card(f, "🆕")
        if unchanged:
            st.caption(f"⚪ 이전 실행 전 파일 ({len(unchanged)}개)")
            for f in unchanged:
                _file_card(f, "📄")
        if not changed and not unchanged:
            st.info("대기 중인 파일이 없습니다.")
    else:
        st.info("대기 중인 파일이 없습니다.")

    # 파일 편집기
    st.divider()
    st.subheader("파일 편집기")
    all_editable = sorted(INPUT_DIR.glob("*.txt")) + sorted(ARCHIVE_DIR.glob("*.txt"))
    if all_editable:
        labels = []
        for f in all_editable:
            folder = "input" if f.parent == INPUT_DIR else "archive"
            labels.append(f"[{folder}] {f.name}")
        selected_idx = st.selectbox(
            "파일 선택", range(len(all_editable)),
            format_func=lambda i: labels[i], key="editor_file",
        )
        sel_file = all_editable[selected_idx]
        original = sel_file.read_text(encoding="utf-8", errors="replace")

        edited = st.text_area(
            "내용", value=original, height=400, key=f"editor_{sel_file.name}",
        )

        col_save, col_new = st.columns(2)
        with col_save:
            if st.button("💾 저장", disabled=(edited == original)):
                sel_file.write_text(edited, encoding="utf-8")
                st.success(f"`{sel_file.name}` 저장 완료!")
                st.rerun()
        with col_new:
            new_name = st.text_input("새 파일명 (.txt)", placeholder="new_document.txt", key="new_filename")
            if st.button("📝 새 파일로 저장") and new_name:
                if not new_name.endswith(".txt"):
                    new_name += ".txt"
                new_path = INPUT_DIR / new_name
                new_path.write_text(edited, encoding="utf-8")
                st.success(f"`{new_name}` 생성 완료!")
                st.rerun()
    else:
        st.info("편집할 파일이 없습니다.")


# ── 탭: 검색 ──
def _jsonl_mtime() -> float:
    return PROCESSED_JSONL.stat().st_mtime if PROCESSED_JSONL.exists() else 0

@st.cache_data(ttl=10)
def _load_documents(_mtime: float = 0) -> list[dict]:
    """processed.jsonl → 중복 제거된 문서 리스트 (같은 source_file은 최신만)."""
    if not PROCESSED_JSONL.exists():
        return []
    docs_by_source: dict[str, dict] = {}
    for line in PROCESSED_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        doc = json.loads(line)
        docs_by_source[doc["source_file"]] = doc
    return list(docs_by_source.values())


with tab_search:
    st.subheader("처리된 문서 검색")

    docs = _load_documents(_jsonl_mtime())

    if not docs:
        st.info("아직 처리된 문서가 없습니다.")
    else:
        col_q, col_t = st.columns([2, 1])
        with col_q:
            query = st.text_input("🔎 키워드 검색", placeholder="예: 딥러닝, Docker")
        with col_t:
            all_topics = sorted({d["metadata"].get("topic", "") for d in docs} - {""})
            topic_filter = st.multiselect("토픽 필터", all_topics)

        # 필터링
        filtered = docs
        if topic_filter:
            filtered = [d for d in filtered if d["metadata"].get("topic") in topic_filter]
        if query:
            q = query.lower()
            def _match(d: dict) -> bool:
                meta = d["metadata"]
                if q in meta.get("title", "").lower():
                    return True
                if q in meta.get("summary", "").lower():
                    return True
                for kw in meta.get("keywords", []):
                    if q in kw.lower():
                        return True
                for chunk in d.get("chunks", []):
                    if q in chunk.get("content", "").lower():
                        return True
                return False
            filtered = [d for d in filtered if _match(d)]

        st.caption(f"{len(filtered)}건 / 전체 {len(docs)}건")

        for doc in filtered:
            meta = doc["metadata"]
            title = meta.get("title", doc["source_file"])
            topic = meta.get("topic", "")
            summary = meta.get("summary", "")
            keywords = meta.get("keywords", [])

            st.markdown(f"### {title}")
            if topic:
                st.caption(f"토픽: **{topic}**")
            if summary:
                st.write(summary)
            if keywords:
                st.markdown(" ".join(f"`{kw}`" for kw in keywords))

            with st.expander("📦 청크 목록"):
                for chunk in doc.get("chunks", []):
                    st.markdown(f"**{chunk.get('heading', chunk['id'])}**")
                    st.write(chunk["content"])
                    st.divider()

            with st.expander("📝 전체 마크다운"):
                st.markdown(doc.get("markdown", ""))

            st.divider()


# ── 탭 2: 트레이스 ──
with tab2:
    st.subheader("LLM 추론 트레이스")

    # run_id 목록
    trace_runs = sorted(
        [d.name for d in TRACE_DIR.iterdir() if d.is_dir()],
        reverse=True,
    ) if TRACE_DIR.exists() else []

    if not trace_runs:
        st.info("아직 트레이스 기록이 없습니다.")
    else:
        selected_run = st.selectbox("Run ID", trace_runs, key="trace_run")
        run_dir = TRACE_DIR / selected_run
        trace_files = sorted(run_dir.glob("*.trace.json"))

        if not trace_files:
            st.warning("이 실행의 트레이스 파일이 없습니다.")
        else:
            selected_file = st.selectbox(
                "파일 선택",
                trace_files,
                format_func=lambda p: p.stem.replace(".trace", ""),
                key="trace_file",
            )

            trace = json.loads(selected_file.read_text(encoding="utf-8"))

            # 요약 메트릭
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("모델", trace.get("model", "-"))
            with col2:
                st.metric("입력 토큰", f"{trace.get('input_tokens', 0):,}")
            with col3:
                st.metric("출력 토큰", f"{trace.get('output_tokens', 0):,}")
            with col4:
                st.metric("응답 시간", f"{trace.get('latency_ms', 0):,}ms")

            st.divider()

            # 청크 요약
            summary = trace.get("output_summary", {})
            chunk_sizes = summary.get("chunk_sizes", [])
            if chunk_sizes:
                st.write(f"**청크 수:** {summary.get('chunk_count', 0)}개 | "
                         f"**청크 크기:** {min(chunk_sizes)}~{max(chunk_sizes)}자 (평균 {sum(chunk_sizes)//len(chunk_sizes)}자)")

            # 추론 근거
            reasoning = trace.get("reasoning")
            if reasoning:
                st.subheader("추론 근거")

                st.write("**문서 분석**")
                st.info(reasoning.get("document_analysis", "-"))

                st.write("**청크 분할 전략**")
                st.info(reasoning.get("chunk_strategy", "-"))

                st.write("**메타데이터 추출 근거**")
                st.info(reasoning.get("metadata_rationale", "-"))

                chunk_details = reasoning.get("chunk_details", [])
                if chunk_details:
                    st.write("**청크별 분할 사유**")
                    for cd in chunk_details:
                        with st.expander(f"📦 {cd.get('chunk_id', '?')}"):
                            st.write(cd.get("why", "-"))
            else:
                st.warning("이 파일의 추론 근거가 없습니다.")

            # 원본 JSON
            with st.expander("전체 트레이스 JSON"):
                st.json(trace)

# ── 탭 3: 에러 로그 ──
with tab3:
    st.subheader("에러 로그")

    # run_id 필터
    runs = db.get_runs(50)
    run_ids = ["전체"] + [r["run_id"] for r in runs if r["error_count"] > 0]
    selected = st.selectbox("Run ID 필터", run_ids)

    filter_id = None if selected == "전체" else selected
    errors = db.get_errors(run_id=filter_id)

    if errors:
        df = pd.DataFrame(errors)
        display_cols = ["run_id", "filename", "error_type", "error_message", "created_at"]
        df = df[[c for c in display_cols if c in df.columns]]
        df.columns = ["Run ID", "파일명", "에러 타입", "에러 메시지", "시간"]
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("에러 기록이 없습니다.")

# ── 탭 4: 설정 ──
with tab4:
    st.subheader("현재 설정")

    col1, col2 = st.columns(2)

    with col1:
        st.write("**스케줄**")
        st.code(f"Cron: hour={SCHEDULE_CRON.get('hour', 0)}, minute={SCHEDULE_CRON.get('minute', 0)}")

    with col2:
        st.write("**LLM 모델**")
        from config import LLM_MODEL
        st.code(LLM_MODEL)

    st.divider()
    st.subheader("폴더 상태")

    def count_files(d: Path, pattern: str = "*") -> int:
        return len(list(d.glob(pattern))) if d.exists() else 0

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("📥 input_docs", f"{count_files(INPUT_DIR, '*.txt')}개")
    with col2:
        st.metric("📤 output_jsonl", f"{count_files(OUTPUT_DIR, '*.jsonl')}개")
    with col3:
        st.metric("📦 archive_docs", f"{count_files(ARCHIVE_DIR, '*.txt')}개")
    with col4:
        st.metric("⚠️ error_docs", f"{count_files(ERROR_DIR, '*.txt')}개")
    with col5:
        trace_count = sum(1 for _ in TRACE_DIR.rglob("*.trace.json")) if TRACE_DIR.exists() else 0
        st.metric("🔍 traces", f"{trace_count}개")
