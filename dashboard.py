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
from config import (INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR, ERROR_DIR, TRACE_DIR,
                    SCHEDULE_CRON, CHUNK_CONFIG_PATH, DEFAULT_CHUNK_MIN, DEFAULT_CHUNK_MAX)

PROCESSED_JSONL = OUTPUT_DIR / "processed.jsonl"
MODE_FILE = INPUT_DIR / ".mode.json"
SELECTED_FILE = INPUT_DIR / ".selected.json"


def _load_modes() -> dict[str, str]:
    if MODE_FILE.exists():
        try:
            return json.loads(MODE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_modes(modes: dict[str, str]):
    MODE_FILE.write_text(
        json.dumps(modes, ensure_ascii=False, indent=2), encoding="utf-8",
    )

st.set_page_config(page_title="FLOPI", page_icon="📄", layout="wide")
st.title("FLOPI — RAG Pipeline 대시보드")

tab1, tab_upload, tab_search, tab_compare, tab2, tab3, tab4 = st.tabs(
    ["대시보드", "📂 원본 관리", "🔍 검색", "🔄 비교", "트레이스", "에러 로그", "설정"],
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
    all_pending = sorted(INPUT_DIR.rglob("*.txt"))
    _last = db.get_last_run()
    _since = 0.0
    if _last and _last.get("start_time"):
        _since = datetime.fromisoformat(_last["start_time"]).timestamp()
    changed_count = sum(1 for f in all_pending if f.stat().st_mtime > _since)
    total_count = len(all_pending)

    run_mode = st.radio(
        "실행 범위",
        [f"변경된 파일만 ({changed_count}개)", f"전체 ({total_count}개)"],
        horizontal=True,
        help="변경된 파일만: 마지막 실행 이후 새로 추가/수정된 파일만 처리\n"
             "전체: 현재 입력 폴더의 모든 파일 처리",
    )

    # 파일 목록 구성
    if run_mode.startswith("전체"):
        _target_files = all_pending
    else:
        _target_files = [f for f in all_pending if f.stat().st_mtime > _since]

    # 파일별 선택 + 모드 설정
    if _target_files:
        st.subheader("처리할 파일")
        _modes = _load_modes()

        # 파일 엔트리 미리 구성
        _file_entries = []
        for f in _target_files:
            rel = str(f.relative_to(INPUT_DIR))
            _file_entries.append((f, rel, "input"))

        # 콜백: 전체 선택/해제
        def _on_select_all():
            val = st.session_state["select_all"]
            for _, rel, src in _file_entries:
                st.session_state[f"chk_{src}_{rel}"] = val

        # 콜백: 일괄 모드 변경
        def _on_bulk_mode():
            choice = st.session_state["bulk_mode"]
            if choice == "—":
                return
            modes = _load_modes()
            for _, rel, src in _file_entries:
                st.session_state[f"fmode_{src}_{rel}"] = choice
                if "재구성" in choice:
                    modes[rel] = "reorganize"
                elif rel in modes:
                    del modes[rel]
            _save_modes(modes)
            st.session_state["bulk_mode"] = "—"

        # 콜백: 개별 파일 모드 변경
        def _on_file_mode_change(rel, mode_key):
            modes = _load_modes()
            val = st.session_state[mode_key]
            if "재구성" in val:
                modes[rel] = "reorganize"
            elif rel in modes:
                del modes[rel]
            _save_modes(modes)

        col_all, col_mode_all = st.columns([3, 1])
        with col_all:
            st.checkbox("전체 선택", value=True, key="select_all",
                        on_change=_on_select_all)
        with col_mode_all:
            st.selectbox("일괄 모드 변경", ["—", "📄 원문 유지", "🔀 재구성"],
                         key="bulk_mode", on_change=_on_bulk_mode)

        selected_files: list[str] = []

        # 폴더별 그룹핑
        from collections import defaultdict
        _grouped: dict[str, list[tuple]] = defaultdict(list)
        for f, rel, src in _file_entries:
            folder = str(Path(rel).parent) if Path(rel).parent != Path(".") else ""
            _grouped[folder].append((f, rel, src))

        def _render_file_row(f, rel, src):
            fmode = _modes.get(rel, "preserve")
            chk_key = f"chk_{src}_{rel}"
            mode_key = f"fmode_{src}_{rel}"

            if chk_key not in st.session_state:
                st.session_state[chk_key] = True
            if mode_key not in st.session_state:
                st.session_state[mode_key] = "🔀 재구성" if fmode == "reorganize" else "📄 원문 유지"

            col_chk, col_name, col_md = st.columns([0.1, 4, 1.2])
            with col_chk:
                checked = st.checkbox("sel", key=chk_key,
                                      label_visibility="collapsed")
            with col_name:
                size = f.stat().st_size
                fname = Path(rel).name
                tag = "  *(archive)*" if src == "archive" else ""
                st.markdown(f"`{fname}`{tag}  <small style='color:gray'>{size:,}B</small>",
                            unsafe_allow_html=True)
            with col_md:
                st.selectbox(
                    "mode", ["📄 원문 유지", "🔀 재구성"],
                    key=mode_key, label_visibility="collapsed",
                    on_change=_on_file_mode_change, args=(rel, mode_key),
                )
            if checked:
                selected_files.append(rel)

        for folder in sorted(_grouped.keys()):
            entries = _grouped[folder]
            if folder:
                with st.expander(f"📁 **{folder}/** — {len(entries)}개", expanded=True):
                    for f, rel, src in entries:
                        _render_file_row(f, rel, src)
            else:
                for f, rel, src in entries:
                    _render_file_row(f, rel, src)

        st.caption(f"선택: {len(selected_files)}개 / 전체: {len(_target_files)}개")
    else:
        selected_files = []
        st.info("처리할 파일이 없습니다.")

    # 예상 소요 시간 계산 (과거 트레이스 기반)
    def _estimate_time(file_count: int) -> str:
        if not TRACE_DIR.exists():
            return ""
        latencies = []
        for tf in TRACE_DIR.rglob("*.trace.json"):
            try:
                data = json.loads(tf.read_text(encoding="utf-8"))
                if "latency_ms" in data:
                    latencies.append(data["latency_ms"])
            except (json.JSONDecodeError, OSError):
                pass
        if not latencies:
            return ""
        avg_ms = sum(latencies) / len(latencies)
        total_sec = int(avg_ms * file_count / 1000)
        if total_sec < 60:
            return f"약 {total_sec}초"
        minutes = total_sec // 60
        seconds = total_sec % 60
        return f"약 {minutes}분 {seconds}초"

    _n_selected = len(selected_files) if _target_files else 0
    _est = _estimate_time(_n_selected) if _n_selected > 0 else ""

    _lock_file = Path(__file__).parent / ".pipeline.lock"
    _is_locked = _lock_file.exists()

    if _is_locked:
        st.warning("🔒 다른 사용자가 파이프라인을 실행 중입니다. 완료 후 다시 시도하세요.")

    col_btn, col_est = st.columns([1, 2])
    with col_btn:
        _run_clicked = st.button("🚀 지금 즉시 실행", type="primary",
                                 disabled=_is_locked or _n_selected == 0)
    with col_est:
        if _est and _n_selected > 0:
            st.caption(f"⏱️ 예상 소요: **{_est}** ({_n_selected}개 파일, 과거 평균 기준)")

    if _run_clicked:
        # 선택 파일 저장
        SELECTED_FILE.write_text(
            json.dumps(selected_files, ensure_ascii=False), encoding="utf-8",
        )
        _default_mode_key = "preserve"  # 파일별 .mode.json이 우선
        cmd = [sys.executable, "pipeline.py", "--mode", _default_mode_key, "--selected"]
        with st.spinner(f"파이프라인 실행 중… ({_est}, 다른 사용자는 대기)"):
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
        # 선택 파일 정리
        SELECTED_FILE.unlink(missing_ok=True)
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

# ── 탭: 원본 관리 ──
with tab_upload:
    st.subheader("파일 업로드")

    # 폴더 선택
    existing_folders = sorted({
        str(f.relative_to(INPUT_DIR).parent)
        for f in INPUT_DIR.rglob("*.txt")
        if f.relative_to(INPUT_DIR).parent != Path(".")
    })
    folder_options = ["/ (루트)"] + [f"📁 {d}" for d in existing_folders] + ["➕ 새 폴더 만들기"]
    folder_choice = st.selectbox("업로드 폴더", folder_options)

    new_folder_name = ""
    if folder_choice == "➕ 새 폴더 만들기":
        new_folder_name = st.text_input("폴더명", placeholder="예: 반도체/공정")

    upload_mode = st.radio(
        "처리 모드",
        ["📄 원문 유지", "🔀 재구성"],
        horizontal=True,
        help="원문 유지: 원래 순서 그대로 구조화\n재구성: 흩어진 내용을 주제별로 모아 논리적으로 재배치",
        key="upload_mode",
    )
    _upload_mode_key = "reorganize" if "재구성" in upload_mode else "preserve"

    uploaded_files = st.file_uploader(
        "`.txt` 파일을 드래그하거나 선택하세요",
        type=["txt"],
        accept_multiple_files=True,
    )

    run_after = st.checkbox("업로드 후 즉시 파이프라인 실행", value=True)

    # 이미 처리한 업로드는 건너뛰기
    _upload_key = None
    if uploaded_files:
        _upload_key = "-".join(sorted(uf.name for uf in uploaded_files))
    _already_processed = (
        _upload_key is not None
        and st.session_state.get("_last_upload_key") == _upload_key
    )

    if uploaded_files and not _already_processed:
        st.session_state["_last_upload_key"] = _upload_key
        # 저장 폴더 결정
        if folder_choice == "➕ 새 폴더 만들기" and new_folder_name:
            upload_dir = INPUT_DIR / new_folder_name
        elif folder_choice == "/ (루트)":
            upload_dir = INPUT_DIR
        else:
            upload_dir = INPUT_DIR / folder_choice.replace("📁 ", "")
        upload_dir.mkdir(parents=True, exist_ok=True)

        upload_ts = datetime.now().timestamp()
        modes = _load_modes()
        for uf in uploaded_files:
            dest = upload_dir / uf.name
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                dest = upload_dir / f"{stem}_{ts}{suffix}"
            dest.write_bytes(uf.getvalue())
            rel = dest.relative_to(INPUT_DIR)
            mode_label = "🔀 재구성" if _upload_mode_key == "reorganize" else "📄 원문 유지"
            st.success(f"저장: `{rel}` ({mode_label})")
            if _upload_mode_key == "reorganize":
                modes[str(rel)] = "reorganize"
            elif str(rel) in modes:
                del modes[str(rel)]
        _save_modes(modes)

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

    # 새 파일 만들기
    with st.expander("📝 새 파일 만들기"):
        col_nf_name, col_nf_folder = st.columns(2)
        with col_nf_name:
            new_name = st.text_input("파일명", placeholder="new_document.txt", key="new_filename")
        with col_nf_folder:
            nf_folder = st.selectbox("저장 폴더", ["/ (루트)"] + [f"📁 {d}" for d in existing_folders],
                                     key="new_file_folder")
        new_content = st.text_area("내용", height=200, key="new_file_content",
                                   placeholder="여기에 내용을 입력하세요...")
        if st.button("📝 파일 생성") and new_name and new_content:
            if not new_name.endswith(".txt"):
                new_name += ".txt"
            if nf_folder == "/ (루트)":
                new_path = INPUT_DIR / new_name
            else:
                new_path = INPUT_DIR / nf_folder.replace("📁 ", "") / new_name
            new_path.parent.mkdir(parents=True, exist_ok=True)
            new_path.write_text(new_content, encoding="utf-8")
            st.success(f"`{new_path.relative_to(INPUT_DIR)}` 생성 완료!")
            st.rerun()

    st.divider()
    st.subheader("파일 목록")

    _input_files = sorted(INPUT_DIR.rglob("*.txt"))

    def _get_archive_versions(rel: str) -> list[Path]:
        """해당 파일의 아카이브 버전들 (최신순)."""
        stem = Path(rel).stem
        suffix = Path(rel).suffix
        archive_folder = ARCHIVE_DIR / Path(rel).parent
        if not archive_folder.exists():
            return []
        versions = []
        for af in sorted(archive_folder.iterdir(), reverse=True):
            if af.suffix != suffix:
                continue
            if af.name == f"{stem}{suffix}" or af.stem.startswith(f"{stem}_"):
                versions.append(af)
        return versions

    if _input_files:
        last = db.get_last_run()
        since_ts = 0.0
        if last and last.get("start_time"):
            since_ts = datetime.fromisoformat(last["start_time"]).timestamp()

        pending_modes = _load_modes()

        from collections import defaultdict
        folders: dict[str, list[tuple[str, Path]]] = defaultdict(list)
        for f in _input_files:
            rel = str(f.relative_to(INPUT_DIR))
            folder = str(Path(rel).parent) if Path(rel).parent != Path(".") else ""
            folders[folder].append((rel, f))

        def _file_line(rel: str, f: Path):
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m/%d %H:%M")
            fname = Path(rel).name
            fmode = pending_modes.get(rel, "preserve")
            text = f.read_text(encoding="utf-8", errors="replace")
            first_line = text.split("\n", 1)[0].strip()

            is_new = f.stat().st_mtime > since_ts
            icon = "🆕" if is_new else "📄"
            mode_tag = " 🔀" if fmode == "reorganize" else ""
            archive_versions = _get_archive_versions(rel)

            with st.expander(f"{icon} **{fname}**{mode_tag}  —  {first_line}"):
                mode_label = "🔀 재구성" if fmode == "reorganize" else "📄 원문 유지"
                ver_info = f"  |  📜 이전 버전 {len(archive_versions)}개" if archive_versions else ""
                st.caption(f"{f.stat().st_size:,} bytes  |  수정: {mtime}  |  {mode_label}{ver_info}")

                if archive_versions:
                    tab_edit, tab_history = st.tabs(["편집", f"📜 이전 버전 ({len(archive_versions)})"])
                else:
                    tab_edit = st.container()
                    tab_history = None

                with tab_edit:
                    edited = st.text_area(
                        "내용 편집", value=text, height=300,
                        key=f"edit_{rel}",
                    )
                    col_s, col_d = st.columns([1, 1])
                    with col_s:
                        if st.button("💾 저장", key=f"save_{rel}",
                                     disabled=(edited == text)):
                            # 이전 버전을 아카이브로 백업
                            archive_folder = ARCHIVE_DIR / Path(rel).parent
                            archive_folder.mkdir(parents=True, exist_ok=True)
                            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                            arc_name = f"{Path(rel).stem}_{ts}{Path(rel).suffix}"
                            (archive_folder / arc_name).write_text(text, encoding="utf-8")
                            # 새 내용 저장
                            f.write_text(edited, encoding="utf-8")
                            st.success("저장 완료! (이전 버전은 아카이브에 보관)")
                            st.rerun()
                    with col_d:
                        if st.button("🗑️ 삭제", key=f"del_{rel}"):
                            f.unlink()
                            if rel in pending_modes:
                                del pending_modes[rel]
                                _save_modes(pending_modes)
                            st.success(f"`{fname}` 삭제됨")
                            st.rerun()

                if tab_history and archive_versions:
                    with tab_history:
                        for av in archive_versions:
                            av_mtime = datetime.fromtimestamp(
                                av.stat().st_mtime
                            ).strftime("%Y-%m-%d %H:%M:%S")
                            av_size = av.stat().st_size
                            av_text = av.read_text(encoding="utf-8", errors="replace")
                            with st.expander(f"📜 {av.name}  —  {av_mtime}  ({av_size:,}B)"):
                                st.text_area(
                                    "이전 버전 (읽기 전용)", value=av_text,
                                    height=200, disabled=True,
                                    key=f"arc_{av.name}_{rel}",
                                )
                                if st.button("♻️ 이 버전으로 되돌리기",
                                             key=f"restore_{av.name}_{rel}"):
                                    # 현재 내용을 아카이브로 백업
                                    archive_folder = ARCHIVE_DIR / Path(rel).parent
                                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                                    arc_name = f"{Path(rel).stem}_{ts}{Path(rel).suffix}"
                                    (archive_folder / arc_name).write_text(
                                        text, encoding="utf-8")
                                    # 선택한 버전으로 복원
                                    f.write_text(av_text, encoding="utf-8")
                                    st.success("이전 버전으로 복원 완료!")
                                    st.rerun()

        st.caption(f"전체 {len(_input_files)}개 파일")

        for folder_name in sorted(folders.keys()):
            entries = folders[folder_name]
            if folder_name:
                with st.expander(f"📁 **{folder_name}/**  —  {len(entries)}개 파일", expanded=True):
                    for rel, fpath in entries:
                        _file_line(rel, fpath)
            else:
                for rel, fpath in entries:
                    _file_line(rel, fpath)
    else:
        st.info("파일이 없습니다. 위에서 업로드하거나 새 파일을 만드세요.")


# ── 탭: 검색 ──
def _jsonl_mtime() -> float:
    return PROCESSED_JSONL.stat().st_mtime if PROCESSED_JSONL.exists() else 0

@st.cache_data(ttl=10)
def _load_documents(_mtime: float = 0) -> list[dict]:
    """processed.jsonl → 중복 제거된 문서 리스트.

    섹션이 있는 문서는 source_file+section 조합으로 구분,
    섹션이 없는 문서는 source_file로 최신만 유지.
    """
    if not PROCESSED_JSONL.exists():
        return []
    docs_by_key: dict[str, dict] = {}
    for line in PROCESSED_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        doc = json.loads(line)
        section = doc.get("section")
        if section:
            key = f"{doc['source_file']}::{section}"
        else:
            key = doc["source_file"]
        docs_by_key[key] = doc
    return list(docs_by_key.values())


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
            section = doc.get("section")

            if section:
                st.markdown(f"### {title}  `[{section}]`")
            else:
                st.markdown(f"### {title}")
            doc_mode = doc.get("mode", "preserve")
            mode_badge = "🔀 재구성" if doc_mode == "reorganize" else "📄 원문 유지"
            if topic:
                st.caption(f"토픽: **{topic}** · {mode_badge}")
            else:
                st.caption(mode_badge)
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


# ── 탭: 비교 ──
with tab_compare:
    st.subheader("원본 vs 변환 결과 비교")

    compare_docs = _load_documents(_jsonl_mtime())

    if not compare_docs:
        st.info("아직 처리된 문서가 없습니다.")
    else:
        # 문서 선택 — 같은 source_file의 섹션들을 그룹으로 표시
        def _compare_label(i: int) -> str:
            d = compare_docs[i]
            title = d["metadata"].get("title", d["source_file"])
            section = d.get("section")
            if section:
                return f"{d['source_file']}  [{section}]  — {title}"
            return f"{d['source_file']}  — {title}"

        selected_idx = st.selectbox(
            "문서 선택", range(len(compare_docs)),
            format_func=_compare_label,
            key="compare_doc",
        )
        doc = compare_docs[selected_idx]
        source = doc["source_file"]

        # 원본 파일 찾기 (input → archive 순)
        original_path = INPUT_DIR / source
        if not original_path.exists():
            original_path = ARCHIVE_DIR / source
        original_text = ""
        if original_path.exists():
            original_text = original_path.read_text(encoding="utf-8", errors="replace")
        else:
            original_text = "(원본 파일을 찾을 수 없습니다)"

        # 모드 배지
        doc_mode = doc.get("mode", "preserve")
        mode_badge = "🔀 재구성" if doc_mode == "reorganize" else "📄 원문 유지"
        st.caption(f"처리 모드: {mode_badge}")

        # 좌우 비교
        col_orig, col_result = st.columns(2)

        with col_orig:
            st.markdown("#### 원본")
            st.text_area("원본 텍스트", value=original_text,
                         height=500, disabled=True, key=f"compare_original_{source}")

        with col_result:
            st.markdown("#### 변환 결과")
            view_mode = st.radio(
                "보기 방식", ["마크다운", "청크별", "텍스트"],
                horizontal=True, key="compare_view",
            )
            if view_mode == "마크다운":
                with st.container(height=500):
                    st.markdown(doc.get("markdown", ""))
            elif view_mode == "청크별":
                with st.container(height=500):
                    for i, chunk in enumerate(doc.get("chunks", [])):
                        heading = chunk.get("heading", chunk.get("id", f"청크 {i+1}"))
                        st.markdown(f"**{heading}**")
                        st.markdown(chunk["content"])
                        st.divider()
            else:
                st.text_area("변환 텍스트", value=doc.get("markdown", ""),
                             height=500, disabled=True, key=f"compare_result_{source}")

        # 하단: 메타데이터 + 청크 요약
        st.divider()
        meta = doc["metadata"]
        col_m1, col_m2, col_m3 = st.columns(3)
        with col_m1:
            st.markdown(f"**제목:** {meta.get('title', '-')}")
            st.markdown(f"**토픽:** {meta.get('topic', '-')}")
        with col_m2:
            st.markdown(f"**요약:** {meta.get('summary', '-')}")
        with col_m3:
            keywords = meta.get("keywords", [])
            st.markdown("**키워드:** " + " ".join(f"`{kw}`" for kw in keywords))
            chunks = doc.get("chunks", [])
            st.markdown(f"**청크:** {len(chunks)}개")


# ── 탭 2: 트레이스 ──
with tab2:
    st.subheader("LLM 추론 트레이스")

    # run_id 목록 + 실행 시간
    trace_runs = sorted(
        [d.name for d in TRACE_DIR.iterdir() if d.is_dir()],
        reverse=True,
    ) if TRACE_DIR.exists() else []

    # DB에서 run 정보 매핑
    _all_runs = {r["run_id"]: r for r in db.get_runs(100)}

    if not trace_runs:
        st.info("아직 트레이스 기록이 없습니다.")
    else:
        def _run_label(run_id: str) -> str:
            r = _all_runs.get(run_id)
            if r and r.get("start_time"):
                t = r["start_time"][:16].replace("T", " ")
                return f"{run_id}  ({t})"
            return run_id

        selected_run = st.selectbox("Run ID", trace_runs,
                                    format_func=_run_label, key="trace_run")
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
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("모델", trace.get("model", "-"))
            with col2:
                t_mode = trace.get("mode", "preserve")
                st.metric("처리 모드", "🔀 재구성" if t_mode == "reorganize" else "📄 원문 유지")
            with col3:
                st.metric("입력 토큰", f"{trace.get('input_tokens', 0):,}")
            with col4:
                st.metric("출력 토큰", f"{trace.get('output_tokens', 0):,}")
            with col5:
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
    st.subheader("청크 크기 설정")
    st.caption("임베딩 모델에 맞게 청크 크기를 조절하세요. LLM이 이 범위에 맞춰 의미 단위로 분할합니다.")

    # 현재 설정 로드
    _cur_min, _cur_max = DEFAULT_CHUNK_MIN, DEFAULT_CHUNK_MAX
    if CHUNK_CONFIG_PATH.exists():
        try:
            _cfg = json.loads(CHUNK_CONFIG_PATH.read_text(encoding="utf-8"))
            _cur_min = _cfg.get("min", DEFAULT_CHUNK_MIN)
            _cur_max = _cfg.get("max", DEFAULT_CHUNK_MAX)
        except (json.JSONDecodeError, OSError):
            pass

    col_min, col_max = st.columns(2)
    with col_min:
        chunk_min = st.number_input(
            "최소 (자)", min_value=100, max_value=2000,
            value=_cur_min, step=50, key="chunk_min",
        )
    with col_max:
        chunk_max = st.number_input(
            "최대 (자)", min_value=200, max_value=5000,
            value=_cur_max, step=50, key="chunk_max",
        )

    if chunk_min >= chunk_max:
        st.error("최소값은 최대값보다 작아야 합니다.")
    else:
        _changed = (chunk_min != _cur_min or chunk_max != _cur_max)
        if st.button("💾 청크 설정 저장", disabled=not _changed):
            CHUNK_CONFIG_PATH.write_text(
                json.dumps({"min": chunk_min, "max": chunk_max}),
                encoding="utf-8",
            )
            st.success(f"저장 완료! 다음 실행부터 {chunk_min}~{chunk_max}자로 청킹됩니다.")
            st.rerun()

        # 프리셋
        st.caption("프리셋:")
        p1, p2, p3 = st.columns(3)
        with p1:
            if st.button("짧게 (200~500)", key="preset_short"):
                CHUNK_CONFIG_PATH.write_text(
                    json.dumps({"min": 200, "max": 500}), encoding="utf-8")
                st.rerun()
        with p2:
            if st.button("기본 (300~800)", key="preset_default"):
                CHUNK_CONFIG_PATH.write_text(
                    json.dumps({"min": 300, "max": 800}), encoding="utf-8")
                st.rerun()
        with p3:
            if st.button("길게 (500~1500)", key="preset_long"):
                CHUNK_CONFIG_PATH.write_text(
                    json.dumps({"min": 500, "max": 1500}), encoding="utf-8")
                st.rerun()

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
