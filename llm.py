"""LLM 추상화 — Gemini에서 다른 모델로 교체 가능."""

import json
import logging
import time

from config import LLM_MODEL, AGENT_NAME, CLAW_DB_PATH, get_api_key

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
당신은 문서 구조화 전문가입니다. 주어진 텍스트를 분석하여 아래 JSON 형식으로 변환하세요.

반드시 유효한 JSON만 출력하세요. 다른 텍스트는 포함하지 마세요.

{
  "reasoning": {
    "document_analysis": "문서의 전체 구조와 주제를 어떻게 파악했는지 설명",
    "chunk_strategy": "청크를 어떤 기준으로 나누었는지 (의미 단위, 섹션 경계, 주제 전환 등)",
    "chunk_details": [
      {
        "chunk_id": "chunk_001",
        "why": "이 청크를 이 범위로 잘라낸 근거 (주제 응집성, 길이, 문맥 완결성 등)"
      }
    ],
    "metadata_rationale": "title, topic, summary, keywords를 어떻게 결정했는지 근거"
  },
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
- reasoning: 모든 판단의 근거를 상세히 기술할 것. 특히 chunk_details에서 각 청크별 분할 사유를 반드시 포함.
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
        """텍스트 → 마크다운 구조화 + 메타데이터 추출 + 추론 근거.

        Returns:
            {
                "result": {"markdown": str, "metadata": {...}, "chunks": [...]},
                "trace": {
                    "model": str,
                    "input_chars": int,
                    "input_tokens": int,
                    "output_tokens": int,
                    "latency_ms": int,
                    "reasoning": {...},
                }
            }
        """
        prompt = f"파일명: {filename}\n\n---\n\n{text}"

        start = time.time()
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config={
                "system_instruction": _SYSTEM_PROMPT,
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        )
        latency_ms = int((time.time() - start) * 1000)

        raw = response.text.strip()
        parsed = json.loads(raw)

        # 필수 필드 검증
        for key in ("markdown", "metadata", "chunks"):
            if key not in parsed:
                raise ValueError(f"LLM 응답에 '{key}' 필드 누락")

        # 토큰 사용량 추출
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0 if usage else 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0 if usage else 0

        # reasoning 분리
        reasoning = parsed.pop("reasoning", None)

        trace = {
            "model": self._model,
            "input_chars": len(text),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "reasoning": reasoning,
        }

        return {"result": parsed, "trace": trace}
