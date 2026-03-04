"""LLM 추상화 — Gemini에서 다른 모델로 교체 가능."""

import json
import logging

from config import LLM_MODEL, AGENT_NAME, CLAW_DB_PATH, get_api_key

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
당신은 문서 구조화 전문가입니다. 주어진 텍스트를 분석하여 아래 JSON 형식으로 변환하세요.

반드시 유효한 JSON만 출력하세요. 다른 텍스트는 포함하지 마세요.

{
  "markdown": "마크다운으로 구조화된 문서 (제목, 소제목, 목록 등 활용)",
  "metadata": {
    "title": "문서 제목",
    "topic": "주제 분류",
    "summary": "3줄 이내 요약",
    "keywords": ["핵심 키워드 목록"]
  },
  "chunks": [
    {
      "id": "chunk_001",
      "heading": "섹션 제목",
      "content": "해당 섹션의 텍스트 내용"
    }
  ]
}

규칙:
- markdown: 원문 내용을 구조화된 마크다운으로 변환. 내용을 추가하거나 생략하지 말 것.
- chunks: 문서를 의미 단위(섹션, 단락)로 분할. 각 청크는 300~800자 내외.
- metadata.keywords: 5개 이내.
"""


class LLMClient:
    """LLM 호출 추상화 — 내부 구현만 바꾸면 모델 교체 가능."""

    def __init__(self):
        api_key = get_api_key()

        # claw_tracker가 있으면 비용 추적, 없으면 일반 클라이언트
        try:
            from claw_tracker import tracked_client
            self._client = tracked_client(
                api_key=api_key,
                agent=AGENT_NAME,
                db_path=str(CLAW_DB_PATH),
            )
            logger.info("LLM client initialized with cost tracking")
        except ImportError:
            from google import genai
            self._client = genai.Client(api_key=api_key)
            logger.warning("claw_tracker not available, using raw genai client")

        self._model = LLM_MODEL

    def structure_document(self, text: str, filename: str) -> dict:
        """텍스트 → 마크다운 구조화 + 메타데이터 추출.

        Returns:
            {"markdown": str, "metadata": {...}, "chunks": [...]}
        """
        response = self._client.models.generate_content(
            model=self._model,
            contents=f"파일명: {filename}\n\n---\n\n{text}",
            config={
                "system_instruction": _SYSTEM_PROMPT,
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        )

        raw = response.text.strip()
        result = json.loads(raw)

        # 필수 필드 검증
        for key in ("markdown", "metadata", "chunks"):
            if key not in result:
                raise ValueError(f"LLM 응답에 '{key}' 필드 누락")

        return result
