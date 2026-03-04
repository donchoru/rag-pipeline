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

# 대시보드
streamlit run dashboard.py --server.port 8501
```

## 흐름
1. `input_docs/*.txt` → LLM 구조화 → `output_jsonl/processed.jsonl`
2. 성공 → `archive_docs/`로 이동
3. 실패 → `error_docs/`로 이동 + `error_logs` 기록
4. `pipeline_logs.db`에 실행 이력 저장

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
