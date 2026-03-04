"""설정 — 경로, LLM 모델, 스케줄."""

import os
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "input_docs"
OUTPUT_DIR = BASE_DIR / "output_jsonl"
ARCHIVE_DIR = BASE_DIR / "archive_docs"
ERROR_DIR = BASE_DIR / "error_docs"
TRACE_DIR = BASE_DIR / "traces"
DB_PATH = BASE_DIR / "pipeline_logs.db"

LLM_MODEL = "gemini-2.0-flash"
SCHEDULE_CRON = {"hour": 0, "minute": 0}  # 매일 자정 KST

# 청크 설정 파일
CHUNK_CONFIG_PATH = BASE_DIR / ".chunk_config.json"
DEFAULT_CHUNK_MIN = 300
DEFAULT_CHUNK_MAX = 800

SPLIT_THRESHOLD = 10_000  # 이 글자수 이상이면 LLM 자동 분할

CLAW_DB_PATH = BASE_DIR.parent / "claw-manager" / "manager.db"
AGENT_NAME = "rag-pipeline"


def get_api_key() -> str:
    """GEMINI_API_KEY: 환경변수 → Keychain 폴백."""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "GEMINI_API_KEY", "-w"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        raise RuntimeError("GEMINI_API_KEY not found in env or Keychain")


# 디렉토리 자동 생성
for d in (INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR, ERROR_DIR, TRACE_DIR):
    d.mkdir(exist_ok=True)
