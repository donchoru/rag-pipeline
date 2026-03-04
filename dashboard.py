"""Streamlit 대시보드 — RAG 파이프라인 모니터링."""

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent))

import db
from config import INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR, ERROR_DIR, TRACE_DIR, SCHEDULE_CRON

st.set_page_config(page_title="RAG Pipeline", page_icon="📄", layout="wide")
st.title("📄 RAG Pipeline 대시보드")

tab1, tab2, tab3, tab4 = st.tabs(["대시보드", "트레이스", "에러 로그", "설정"])

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

    # 수동 실행
    if st.button("🚀 지금 즉시 실행", type="primary"):
        with st.spinner("파이프라인 실행 중…"):
            try:
                result = subprocess.run(
                    [sys.executable, "pipeline.py"],
                    capture_output=True, text=True, timeout=600,
                    cwd=str(Path(__file__).parent),
                )
                if result.returncode == 0:
                    st.success(f"완료! {result.stdout.strip()}")
                else:
                    st.error(f"에러: {result.stderr.strip()}")
            except subprocess.TimeoutExpired:
                st.error("타임아웃 (10분 초과)")
        st.rerun()

    # 실행 이력
    st.subheader("실행 이력")
    runs = db.get_runs(20)
    if runs:
        df = pd.DataFrame(runs)
        display_cols = ["run_id", "start_time", "end_time", "total_files", "success_count", "error_count", "status"]
        df = df[[c for c in display_cols if c in df.columns]]
        df.columns = ["Run ID", "시작", "종료", "전체", "성공", "에러", "상태"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("아직 실행 이력이 없습니다.")

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
        st.dataframe(df, use_container_width=True, hide_index=True)
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
