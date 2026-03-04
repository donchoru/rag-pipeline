"""LLM 추상화 — Gemini에서 다른 모델로 교체 가능."""

import json
import logging
import time

from config import (LLM_MODEL, AGENT_NAME, CLAW_DB_PATH,
                    CHUNK_CONFIG_PATH, DEFAULT_CHUNK_MIN, DEFAULT_CHUNK_MAX,
                    SPLIT_THRESHOLD, get_api_key)

logger = logging.getLogger(__name__)

_BASE_JSON_SCHEMA = """\
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
"""


def _load_chunk_config() -> tuple[int, int]:
    """청크 크기 설정 로드. Returns (min, max)."""
    if CHUNK_CONFIG_PATH.exists():
        try:
            cfg = json.loads(CHUNK_CONFIG_PATH.read_text(encoding="utf-8"))
            return cfg.get("min", DEFAULT_CHUNK_MIN), cfg.get("max", DEFAULT_CHUNK_MAX)
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_CHUNK_MIN, DEFAULT_CHUNK_MAX


def _build_prompt(mode: str) -> str:
    """모드 + 청크 크기에 맞는 시스템 프롬프트 생성."""
    chunk_min, chunk_max = _load_chunk_config()
    chunk_rule = f"각 청크는 {chunk_min}~{chunk_max}자 내외."

    if mode == "reorganize":
        return f"""\
당신은 문서 구조화 및 재구성 전문가입니다.
주어진 텍스트는 두서없이 작성되어 정보가 흩어져 있을 수 있습니다.
내용을 **주제별로 모아서 논리적 순서로 재배치**하고, 아래 JSON 형식으로 변환하세요.

{_BASE_JSON_SCHEMA}
재구성 규칙:
- reasoning.document_analysis에 "원문이 어떻게 흩어져 있었는지"와 "어떤 기준으로 재구성했는지"를 반드시 설명.
- markdown: 흩어진 동일 주제를 하나로 통합하고, 논리적 흐름(개요→세부→결론)으로 재배치. 중복 내용은 통합하되, 원문에 있는 정보를 빠뜨리지 말 것.
- chunks: 재구성된 구조 기준으로 의미 단위 분할. {chunk_rule}
- metadata.keywords: 5개 이내.
- 원문에 없는 내용을 새로 추가하지 말 것. 기존 내용의 순서와 그룹핑만 변경.
"""
    else:
        return f"""\
당신은 문서 구조화 전문가입니다. 주어진 텍스트를 분석하여 아래 JSON 형식으로 변환하세요.

{_BASE_JSON_SCHEMA}
규칙:
- reasoning: 모든 판단의 근거를 상세히 기술할 것. 특히 chunk_details에서 각 청크별 분할 사유를 반드시 포함.
- markdown: 원문 내용을 구조화된 마크다운으로 변환. 내용을 추가하거나 생략하지 말 것.
- chunks: 문서를 의미 단위(섹션, 단락)로 분할. {chunk_rule}
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

    def structure_document(self, text: str, filename: str, mode: str = "preserve") -> dict:
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
        system_prompt = _build_prompt(mode)

        start = time.time()
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        )
        latency_ms = int((time.time() - start) * 1000)

        raw = response.text.strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # 1회 재시도 — 긴 문서에서 JSON 깨질 수 있음
            logger.warning(f"JSON 파싱 실패, 재시도: {filename}")
            time.sleep(2)
            start = time.time()
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0.0,
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

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Gemini text-embedding-004로 텍스트 리스트 임베딩.

        Returns:
            768차원 벡터 리스트 (입력 texts와 동일 순서/길이).
        """
        result = self._client.models.embed_content(
            model="text-embedding-004",
            contents=texts,
        )
        return [e.values for e in result.embeddings]

    def split_document(self, text: str, filename: str,
                       max_chars: int = SPLIT_THRESHOLD) -> list[dict]:
        """대용량 문서를 논리적 섹션으로 분할.

        줄 번호가 포함된 텍스트를 LLM에 전달하고,
        분할 지점(start_line, end_line, title)만 받아옴.
        Python에서 실제 텍스트 슬라이싱.

        Returns: [{"title": str, "content": str}, ...]
        """
        lines = text.splitlines()
        numbered = "\n".join(f"{i+1}: {line}" for i, line in enumerate(lines))

        system_prompt = f"""\
당신은 문서 분할 전문가입니다.
주어진 문서를 논리적 섹션(장, 절, 주제 단위)으로 분할하세요.

규칙:
- 각 섹션은 {max_chars}자 이내여야 합니다.
- 섹션 경계는 주제 전환, 장/절 구분, 큰 문맥 변화 지점에 두세요.
- 문서 내용을 반환하지 마세요. 분할 지점만 반환하세요.
- 반드시 유효한 JSON 배열만 출력하세요. 다른 텍스트는 포함하지 마세요.

출력 형식:
[
  {{"title": "섹션 제목", "start_line": 1, "end_line": 50}},
  {{"title": "섹션 제목", "start_line": 51, "end_line": 120}}
]

- start_line, end_line은 1-based 줄 번호입니다.
- 모든 줄이 빠짐없이 포함되어야 합니다 (첫 섹션 start=1, 마지막 섹션 end={len(lines)}).
- 섹션은 연속적이어야 합니다 (이전 end_line + 1 = 다음 start_line).
"""

        prompt = f"파일명: {filename}\n총 {len(lines)}줄, {len(text)}자\n\n---\n\n{numbered}"

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.0,
                "response_mime_type": "application/json",
            },
        )

        raw = response.text.strip()
        sections_meta = json.loads(raw)

        # 줄 번호로 실제 content 슬라이싱
        sections = []
        for sec in sections_meta:
            start = sec["start_line"] - 1  # 0-based
            end = sec["end_line"]           # exclusive
            content = "\n".join(lines[start:end])
            char_count = len(content)

            if char_count > max_chars:
                logger.warning(
                    f"섹션 '{sec['title']}' ({char_count}자)이 "
                    f"max_chars({max_chars})를 초과합니다"
                )

            sections.append({"title": sec["title"], "content": content})

        logger.info(
            f"문서 분할 완료: {filename} → {len(sections)}개 섹션 "
            f"({', '.join(f'{len(s[\"content\"])}자' for s in sections)})"
        )

        return sections
