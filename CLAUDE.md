# rag-pipeline — RAG 텍스트 전처리 파이프라인

`.txt` 문서를 Gemini로 마크다운 구조화 + 메타데이터 추출하여 JSONL로 변환.
APScheduler 자동 실행 + SQLite 이력 + Streamlit 대시보드.

## 구조
```
config.py       — 설정 (경로, LLM 모델, 스케줄)
db.py           — SQLite CRUD (execution_history, error_logs)
llm.py          — LLM 추상화 (Gemini → 교체 가능)
pipeline.py     — Core 파이프라인 (run_pipeline())
main.py         — APScheduler 스케줄러 진입점
dashboard.py    — Streamlit 대시보드 (port 8501)
```

## 실행
```bash
source .venv/bin/activate

# 파이프라인 단독 실행
python pipeline.py

# 스케줄러 (매일 자정 KST)
python main.py

# 대시보드 (로컬)
streamlit run dashboard.py --server.port 8501

# 대시보드 (LAN/외부 접속 — 고정 IP + 방화벽 8501 오픈 필요)
streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0
```

## 흐름
1. `input_docs/*.txt` → LLM 구조화 → `output_jsonl/processed.jsonl`
2. 원본 파일은 `input_docs/`에 **항상 유지** (이동하지 않음)
3. 파일 편집 시 → 이전 버전이 `archive_docs/`에 타임스탬프 백업
4. 실패 → `error_logs` DB 기록 (파일 이동 없음)
5. `pipeline_logs.db`에 실행 이력 저장

## 주요 기능
- **웹 업로드**: 브라우저에서 .txt 드래그 앤 드롭 → 즉시 파이프라인 실행
- **파일 편집기**: input/archive 파일 조회·수정·새 파일 생성
- **검색**: 처리된 문서 키워드 검색 + 토픽 필터
- **변경 파일만 실행**: 마지막 실행 이후 추가/수정된 파일만 처리 (`--since`)
- **동시 실행 방지**: `fcntl.flock` 파일 락 — 다른 사용자 실행 중이면 대기
- **LLM 재시도**: JSON 파싱 실패 시 temperature=0.0으로 1회 자동 재시도

## 제약사항
- 입력 파일은 **10KB 이하** 권장 (Gemini Flash가 긴 문서에서 JSON 깨뜨림)
- 10KB 초과 시 챕터 단위로 분할 후 투입

## LLM 교체
`llm.py`의 `LLMClient` 내부만 수정하면 됨.
현재: Gemini 2.0 Flash (`google-genai`)

## 비용 추적
`claw_tracker` 심볼릭 링크 → `claw-manager/claw_tracker/`
에이전트명: `rag-pipeline`

## 의존성
```
google-genai, streamlit, apscheduler
```
