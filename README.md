# RAG 텍스트 전처리 파이프라인

`.txt` 문서를 LLM(Gemini)으로 **마크다운 구조화 + 메타데이터 추출**하여 JSONL로 변환하는 자동화 파이프라인.
APScheduler로 스케줄 실행하고, SQLite로 이력을 기록하며, Streamlit 대시보드로 모니터링한다.

---

## 왜 이게 필요한가?

RAG(Retrieval-Augmented Generation) 시스템을 구축할 때, 원본 문서를 그대로 임베딩하면 검색 품질이 떨어진다.
이 파이프라인은 **비정형 텍스트를 구조화된 청크로 전처리**하여 RAG의 검색 정확도를 높이는 것이 목표다.

```
[비정형 .txt] → [LLM 구조화] → [마크다운 + 메타데이터 + 청크 JSONL]
```

- 마크다운: 제목/소제목/목록 등으로 구조화된 문서
- 메타데이터: title, topic, summary, keywords 자동 추출
- 청크: 의미 단위(300~800자)로 분할 → 벡터DB에 바로 적재 가능

---

## 아키텍처

```
                        ┌─────────────────────┐
                        │   input_docs/*.txt   │  ← 처리 대기 문서
                        └──────────┬──────────┘
                                   │
                           ┌───────▼───────┐
                           │  pipeline.py   │  ← Core 파이프라인
                           │               │
                           │  1. 파일 읽기  │
                           │  2. LLM 호출   │──→ llm.py (Gemini API)
                           │  3. JSONL 저장  │
                           │  4. 파일 이동   │
                           └───┬───────┬───┘
                               │       │
                    ┌──────────▼┐  ┌──▼──────────┐
                    │ archive_  │  │  error_docs/ │
                    │ docs/     │  │  (실패 파일)  │
                    │ (성공)    │  └──────────────┘
                    └───────────┘
                               │
                    ┌──────────▼──────────┐
                    │ output_jsonl/       │
                    │ processed.jsonl     │  ← 구조화된 결과
                    └─────────────────────┘

  ┌──────────┐         ┌──────────────┐
  │ main.py  │────────→│ APScheduler  │  매일 자정(KST) 자동 실행
  └──────────┘         └──────────────┘

  ┌──────────────┐     ┌──────────────────┐
  │ dashboard.py │────→│ Streamlit :8501  │  모니터링 + 수동 실행
  └──────────────┘     └──────────────────┘

  ┌──────────┐
  │  db.py   │────→ pipeline_logs.db (SQLite)  실행 이력 + 에러 로그
  └──────────┘
```

---

## 프로젝트 구조

```
rag-pipeline/
├── config.py           # 설정 (경로, LLM 모델, 스케줄, API 키)
├── db.py               # SQLite CRUD (실행 이력 + 에러 로그)
├── llm.py              # LLM 추상화 (Gemini → 교체 가능)
├── pipeline.py         # Core 파이프라인 — run_pipeline()
├── main.py             # APScheduler 스케줄러 진입점
├── dashboard.py        # Streamlit 대시보드 (port 8501)
├── requirements.txt    # 의존성
├── input_docs/         # 입력 .txt 파일 (처리 대기)
├── output_jsonl/       # 출력 .jsonl 파일
├── archive_docs/       # 처리 완료된 원본
└── error_docs/         # 에러 난 파일
```

---

## 모듈별 상세 설명

### 1. `config.py` — 설정

모든 설정을 한 곳에서 관리한다.

```python
# 경로
INPUT_DIR  = BASE_DIR / "input_docs"     # .txt 파일을 여기에 넣으면 처리됨
OUTPUT_DIR = BASE_DIR / "output_jsonl"   # 결과 JSONL 출력
ARCHIVE_DIR = BASE_DIR / "archive_docs"  # 성공한 원본 이동
ERROR_DIR  = BASE_DIR / "error_docs"     # 실패한 원본 이동

# LLM
LLM_MODEL = "gemini-2.0-flash"          # 모델 변경은 여기서

# 스케줄
SCHEDULE_CRON = {"hour": 0, "minute": 0}  # 매일 자정 KST
```

**API 키 로딩 우선순위:**
1. 환경변수 `GEMINI_API_KEY`
2. macOS Keychain 폴백 (`security find-generic-password`)

> 다른 OS를 쓴다면 `get_api_key()` 함수에서 Keychain 부분을 환경변수로 대체하면 된다.

---

### 2. `llm.py` — LLM 추상화 레이어

**핵심 설계: 나중에 사내 모델로 교체할 수 있도록 추상화**

```python
class LLMClient:
    def structure_document(self, text: str, filename: str) -> dict:
        """텍스트 → 구조화된 결과 반환"""
        # Returns: {"markdown": str, "metadata": dict, "chunks": list}
```

`LLMClient` 클래스 내부만 수정하면 Gemini에서 다른 LLM(OpenAI, 로컬 모델, 사내 API 등)으로 교체 가능하다.
`pipeline.py`는 `LLMClient.structure_document()`만 호출하므로 **변경 영향 범위가 이 파일 하나로 제한**된다.

**LLM에게 보내는 프롬프트 구조:**

```
시스템 프롬프트: "문서 구조화 전문가" 역할 + JSON 출력 스키마 정의
사용자 입력:    "파일명: {filename}\n\n---\n\n{본문 텍스트}"
출력 형식:      application/json (Gemini structured output)
temperature:   0.1 (일관된 결과를 위해 낮게 설정)
```

**출력 JSON 스키마:**

```json
{
  "markdown": "# 제목\n\n## 소제목\n\n본문 내용...",
  "metadata": {
    "title": "문서 제목",
    "topic": "주제 분류",
    "summary": "3줄 이내 요약",
    "keywords": ["키워드1", "키워드2"]
  },
  "chunks": [
    {
      "id": "chunk_001",
      "heading": "섹션 제목",
      "content": "해당 섹션의 텍스트 (300~800자)"
    }
  ]
}
```

---

### 3. `db.py` — SQLite 이력 관리

파이프라인의 모든 실행 기록을 SQLite에 저장한다. 대시보드에서 조회하고, 장애 추적에 활용한다.

**테이블 스키마:**

```sql
-- 실행 이력: 파이프라인 1회 실행 = 1 row
execution_history (
    run_id TEXT PRIMARY KEY,        -- UUID 앞 8자리 (예: "a1b2c3d4")
    start_time TEXT NOT NULL,       -- ISO 8601 UTC
    end_time TEXT,
    total_files INTEGER DEFAULT 0,  -- 처리 대상 파일 수
    success_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'Running'   -- Running → Success | Fail
)

-- 에러 로그: 파일별 에러 상세
error_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,           -- execution_history FK
    filename TEXT NOT NULL,         -- 에러 난 파일명
    error_type TEXT,                -- 예외 클래스명 (ValueError, APIError 등)
    error_message TEXT,             -- 에러 메시지 (500자 제한)
    created_at TEXT NOT NULL
)
```

**제공 함수:**

| 함수 | 용도 |
|------|------|
| `create_run(run_id, total_files)` | 파이프라인 시작 시 Running 상태로 기록 |
| `finish_run(run_id, success, errors)` | 완료 시 Success/Fail 업데이트 |
| `get_runs(limit=20)` | 최근 실행 이력 조회 |
| `get_last_run()` | 마지막 실행 결과 |
| `get_total_stats()` | 누적 성공/에러 수 |
| `log_error(run_id, filename, ...)` | 파일별 에러 기록 |
| `get_errors(run_id=None)` | 에러 로그 조회 (run_id 필터 가능) |

---

### 4. `pipeline.py` — Core 파이프라인

전체 흐름을 담당하는 핵심 모듈. `run_pipeline()` 하나만 호출하면 된다.

**실행 흐름:**

```
run_pipeline()
│
├── 1. run_id 생성 (UUID 앞 8자리)
├── 2. input_docs/*.txt 파일 목록 수집
├── 3. DB에 Running 상태 기록
├── 4. LLMClient 초기화
├── 5. 각 파일 순회:
│   ├── 텍스트 읽기 (UTF-8)
│   ├── LLM 호출 → 구조화 결과 수신
│   ├── 성공 시:
│   │   ├── output_jsonl/processed.jsonl에 1줄 append
│   │   └── 원본 → archive_docs/로 이동
│   └── 실패 시:
│       ├── error_logs 테이블에 기록
│       └── 원본 → error_docs/로 이동
├── 6. DB에 최종 결과 업데이트 (Success/Fail)
└── 7. run_id 반환
```

**JSONL 출력 형식** (한 줄 = 한 문서):

```json
{
  "source_file": "document.txt",
  "run_id": "a1b2c3d4",
  "markdown": "# 구조화된 마크다운...",
  "metadata": {"title": "...", "topic": "...", "summary": "...", "keywords": [...]},
  "chunks": [{"id": "chunk_001", "heading": "...", "content": "..."}, ...]
}
```

**단독 실행 가능:**

```bash
python pipeline.py
# 출력: Run ID: a1b2c3d4
```

---

### 5. `main.py` — 스케줄러

APScheduler의 `BlockingScheduler`로 파이프라인을 정해진 시간에 자동 실행한다.

```python
scheduler = BlockingScheduler(timezone="Asia/Seoul")
scheduler.add_job(run_pipeline, "cron", hour=0, minute=0, id="rag_pipeline")
```

- **매일 자정(KST)** 에 `run_pipeline()` 호출
- `SIGINT`/`SIGTERM` 시그널로 graceful shutdown
- 스케줄 변경은 `config.py`의 `SCHEDULE_CRON`만 수정

---

### 6. `dashboard.py` — Streamlit 대시보드

6개 탭으로 구성된 모니터링 + 문서 관리 UI.

| 탭 | 기능 |
|----|------|
| **대시보드** | 실행 상태 메트릭, 즉시 실행 버튼, 실행 이력 테이블 |
| **📤 업로드** | 브라우저에서 .txt 파일 드래그 앤 드롭 업로드, 즉시 파이프라인 실행 |
| **🔍 검색** | 처리된 문서 키워드 검색 + 토픽 필터, 청크/마크다운 열람 |
| **트레이스** | LLM 추론 과정 상세 보기 (토큰, 응답시간, 추론 근거) |
| **에러 로그** | run_id별 에러 파일, 에러 타입/메시지 조회 |
| **설정** | 스케줄, LLM 모델, 폴더별 파일 수 현황 |

---

## 빠른 시작

### 1. 설치

```bash
git clone https://github.com/donchoru/rag-pipeline.git
cd rag-pipeline

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. API 키 설정

```bash
# 방법 A: 환경변수
export GEMINI_API_KEY="your-api-key"

# 방법 B: macOS Keychain (Mac 사용자)
security add-generic-password -s GEMINI_API_KEY -a "" -w "your-api-key"
```

### 3. 문서 넣고 실행

```bash
# .txt 파일을 input_docs/에 넣기
cp my_document.txt input_docs/

# 파이프라인 실행
python pipeline.py
# → output_jsonl/processed.jsonl 에 결과 생성
# → 원본은 archive_docs/로 이동
```

### 4. 대시보드 실행

```bash
# 내 PC에서만 볼 때
streamlit run dashboard.py --server.port 8501
# → http://localhost:8501

# 팀원도 접속할 수 있게 열 때 (아래 "사내 배포 가이드" 참고)
streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0
```

### 5. 스케줄러 실행 (선택)

```bash
python main.py
# → 매일 자정(KST) 자동 실행
```

---

## LLM 모델 교체 가이드

현재 Gemini 2.0 Flash를 사용하지만, `llm.py`만 수정하면 다른 모델로 교체할 수 있다.

### 예: OpenAI로 교체

```python
# llm.py
from openai import OpenAI

class LLMClient:
    def __init__(self):
        self._client = OpenAI(api_key=get_api_key())

    def structure_document(self, text: str, filename: str) -> dict:
        response = self._client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"파일명: {filename}\n\n---\n\n{text}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        return json.loads(response.choices[0].message.content)
```

### 예: 로컬 모델 (Ollama)

```python
# llm.py
import httpx

class LLMClient:
    def __init__(self):
        self._base_url = "http://localhost:11434"

    def structure_document(self, text: str, filename: str) -> dict:
        response = httpx.post(f"{self._base_url}/api/generate", json={
            "model": "llama3",
            "prompt": f"{_SYSTEM_PROMPT}\n\n파일명: {filename}\n\n{text}",
            "format": "json",
            "stream": False,
        })
        return json.loads(response.json()["response"])
```

**핵심**: `structure_document()`의 입출력 인터페이스만 유지하면 `pipeline.py`는 변경 없이 동작한다.

---

## 출력 JSONL 활용

생성된 `processed.jsonl`을 벡터DB에 적재하는 예시:

```python
import json

with open("output_jsonl/processed.jsonl") as f:
    for line in f:
        doc = json.loads(line)

        # 청크 단위로 임베딩 + 벡터DB 적재
        for chunk in doc["chunks"]:
            embedding = embed(chunk["content"])  # 임베딩 함수
            vector_db.upsert(
                id=f"{doc['source_file']}_{chunk['id']}",
                vector=embedding,
                metadata={
                    "source": doc["source_file"],
                    "heading": chunk["heading"],
                    "title": doc["metadata"]["title"],
                    "topic": doc["metadata"]["topic"],
                    "keywords": doc["metadata"]["keywords"],
                },
                text=chunk["content"],
            )
```

---

## DB 스키마 ERD

```
┌────────────────────────┐       ┌──────────────────────────┐
│   execution_history    │       │       error_logs          │
├────────────────────────┤       ├──────────────────────────┤
│ run_id (PK)      TEXT  │◄──┐   │ id (PK)    AUTOINCREMENT │
│ start_time       TEXT  │   │   │ run_id (FK)        TEXT  │──┐
│ end_time         TEXT  │   └───│ filename           TEXT  │  │
│ total_files      INT   │       │ error_type         TEXT  │  │
│ success_count    INT   │       │ error_message      TEXT  │  │
│ error_count      INT   │       │ created_at         TEXT  │  │
│ status           TEXT  │       └──────────────────────────┘  │
│ (Running/Success/Fail) │                                     │
└────────────────────────┘                                     │
         ▲                                                     │
         └─────────────────────────────────────────────────────┘
                              FK: run_id
```

---

## 의존성

| 패키지 | 버전 | 용도 |
|--------|------|------|
| `google-genai` | latest | Gemini API 클라이언트 |
| `streamlit` | latest | 대시보드 UI |
| `apscheduler` | latest | 스케줄 실행 |

Python 3.12 이상 권장.

---

## 사내 배포 가이드 (팀원용)

> 회사 네트워크에서 여러 사람이 브라우저로 접속해서
> 문서를 업로드하고, 처리 결과를 검색/열람할 수 있도록 세팅하는 방법.

### 전체 흐름

```
┌─────────────────────────────────────────────────────┐
│  서버 PC (고정 IP, 예: 10.0.1.50)                    │
│                                                     │
│  streamlit run dashboard.py                         │
│    --server.port 8501                               │
│    --server.address 0.0.0.0                         │
│                                                     │
│  → 포트 8501에서 대기                                │
└──────────────────────┬──────────────────────────────┘
                       │ 방화벽 8501 오픈
                       │
        ┌──────────────┼──────────────┐
        │              │              │
   팀원 A 브라우저  팀원 B 브라우저  팀원 C 브라우저
   http://10.0.1.50:8501
```

### Step 1. 서버 PC에 설치

아무 PC나 가능. Windows/Mac/Linux 다 됨. Python만 있으면 됨.

```bash
# 1) Python 확인 (3.12 이상 권장)
python3 --version

# 2) 코드 받기
git clone https://github.com/donchoru/rag-pipeline.git
cd rag-pipeline

# 3) 가상환경 만들기
python3 -m venv .venv

# 4) 가상환경 활성화
#    Mac/Linux:
source .venv/bin/activate
#    Windows:
#    .venv\Scripts\activate

# 5) 패키지 설치
pip install -r requirements.txt
```

### Step 2. API 키 설정

Gemini API 키가 필요함. [Google AI Studio](https://aistudio.google.com/apikey)에서 무료로 발급.

```bash
# 방법 A: 환경변수 (가장 간단)
export GEMINI_API_KEY="여기에-발급받은-키-붙여넣기"

# 방법 B: .env 파일 (서버 재시작해도 유지하고 싶을 때)
echo 'GEMINI_API_KEY=여기에-발급받은-키-붙여넣기' > .env
# → config.py에서 python-dotenv로 읽도록 수정 필요 (기본은 환경변수)

# 방법 C: macOS Keychain (Mac 서버인 경우)
security add-generic-password -s GEMINI_API_KEY -a "" -w "여기에-키"
```

> **확인**: `python -c "from config import get_api_key; print(get_api_key()[:8])"` 실행해서 키 앞 8자리 나오면 OK.

### Step 3. 서버 IP 확인

```bash
# Mac/Linux
ifconfig | grep "inet " | grep -v 127.0.0.1

# Windows
ipconfig | findstr "IPv4"
```

예시 출력: `inet 10.0.1.50 netmask ...` → 서버 IP는 `10.0.1.50`

### Step 4. 방화벽 오픈 요청

인프라/보안팀에 아래 내용으로 요청:

```
- 용도: RAG 문서 전처리 대시보드
- 서버 IP: 10.0.1.50 (← 실제 IP로 변경)
- 포트: 8501 / TCP / 인바운드
- 접속 범위: 사내 네트워크 (10.0.0.0/16 등)
```

**OS 자체 방화벽도 확인:**

```bash
# macOS — 시스템 설정 > 네트워크 > 방화벽
# 켜져 있으면 "옵션"에서 Python 허용 추가

# Windows
netsh advfirewall firewall add rule name="Streamlit" dir=in action=allow protocol=TCP localport=8501

# Linux (Ubuntu)
sudo ufw allow 8501/tcp
```

### Step 5. 대시보드 실행

```bash
cd rag-pipeline
source .venv/bin/activate    # Windows: .venv\Scripts\activate

# 환경변수 설정 (Step 2에서 .env 안 쓴 경우)
export GEMINI_API_KEY="your-key"

# 대시보드 시작
streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0
```

터미널에 이렇게 나오면 성공:

```
  You can now view your Streamlit app in your browser.

  Network URL: http://10.0.1.50:8501
```

### Step 6. 팀원 접속

팀원에게 공유할 내용:

```
브라우저에서 http://10.0.1.50:8501 접속하세요.
                ↑ 실제 서버 IP로 변경

📤 업로드 탭: .txt 파일 드래그하면 자동으로 AI가 구조화 처리합니다.
🔍 검색 탭:  처리된 문서를 키워드/토픽으로 검색할 수 있습니다.
```

### 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 접속이 안 됨 | 방화벽 미오픈 | Step 4 다시 확인 |
| `ERR_CONNECTION_REFUSED` | 서버가 안 켜짐 | Step 5 터미널 확인 |
| `localhost`만 됨, IP 접속 안 됨 | `--server.address 0.0.0.0` 빠짐 | 실행 명령어 확인 |
| API 키 에러 | `GEMINI_API_KEY` 미설정 | Step 2 확인 |
| 업로드 후 처리 안 됨 | 파이프라인 에러 | 에러 로그 탭 확인 |
| 여러 명이 동시에 쓸 때 충돌? | 충돌 없음 | Streamlit이 세션 격리 내장 |

### 백그라운드 실행 (서버 꺼도 유지)

터미널 닫아도 계속 돌게 하려면:

```bash
# Mac/Linux — nohup 사용
nohup streamlit run dashboard.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  > dashboard.log 2>&1 &

# 끄려면
ps aux | grep streamlit
kill <PID>

# 또는 screen/tmux 사용
tmux new -s rag
streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0
# Ctrl+B, D 로 빠져나오면 백그라운드 유지
# 다시 들어가려면: tmux attach -t rag
```

---

## License

MIT
