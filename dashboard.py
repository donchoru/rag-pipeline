"""Streamlit 대시보드 — RAG 파이프라인 모니터링."""

import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent))

import db
from config import INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR, ERROR_DIR, SCHEDULE_CRON

st.set_page_config(page_title="RAG Pipeline", page_icon="📄", layout="wide")
st.title("📄 RAG Pipeline 대시보드")

tab1, tab2, tab3 = st.tabs(["대시보드", "에러 로그", "설정"])

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

# ── 탭 2: 에러 로그 ──
with tab2:
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

# ── 탭 3: 설정 ──
with tab3:
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

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📥 input_docs", f"{count_files(INPUT_DIR, '*.txt')}개")
    with col2:
        st.metric("📤 output_jsonl", f"{count_files(OUTPUT_DIR, '*.jsonl')}개")
    with col3:
        st.metric("📦 archive_docs", f"{count_files(ARCHIVE_DIR, '*.txt')}개")
    with col4:
        st.metric("⚠️ error_docs", f"{count_files(ERROR_DIR, '*.txt')}개")
