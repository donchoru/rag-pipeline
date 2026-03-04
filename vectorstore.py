"""Milvus 벡터 DB 연동 — 청크 임베딩 저장 & 검색."""

import logging

from pymilvus import MilvusClient, DataType

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768  # Gemini text-embedding-004


class VectorStore:
    """Milvus 벡터 스토어 — 청크 upsert & 유사도 검색."""

    def __init__(self, uri: str = "http://localhost:19530",
                 collection: str = "rag_chunks"):
        self._uri = uri
        self._collection = collection
        self._client = MilvusClient(uri=uri)
        self.ensure_collection()

    def ensure_collection(self):
        """컬렉션 없으면 생성."""
        if self._client.has_collection(self._collection):
            return

        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, max_length=256, is_primary=True)
        schema.add_field("source_file", DataType.VARCHAR, max_length=512)
        schema.add_field("section", DataType.VARCHAR, max_length=256)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=64)
        schema.add_field("heading", DataType.VARCHAR, max_length=512)
        schema.add_field("content", DataType.VARCHAR, max_length=8192)
        schema.add_field("topic", DataType.VARCHAR, max_length=256)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128},
        )

        self._client.create_collection(
            collection_name=self._collection,
            schema=schema,
            index_params=index_params,
        )
        logger.info(f"Milvus 컬렉션 생성: {self._collection}")

    def upsert_chunks(self, source_file: str, section: str | None,
                      topic: str, chunks: list[dict],
                      embeddings: list[list[float]]):
        """청크 + 임베딩을 Milvus에 upsert.

        Args:
            source_file: 원본 파일 경로.
            section: 섹션 라벨 (없으면 "").
            topic: 문서 토픽.
            chunks: [{"id": "chunk_001", "heading": "...", "content": "..."}, ...]
            embeddings: 각 청크에 대응하는 768차원 벡터 리스트.
        """
        data = []
        for chunk, emb in zip(chunks, embeddings):
            data.append({
                "id": f"{source_file}::{chunk['id']}",
                "source_file": source_file,
                "section": section or "",
                "chunk_id": chunk["id"],
                "heading": chunk.get("heading", "")[:512],
                "content": chunk.get("content", "")[:8192],
                "topic": (topic or "")[:256],
                "embedding": emb,
            })

        self._client.upsert(collection_name=self._collection, data=data)
        logger.info(
            f"Milvus upsert: {source_file} — {len(data)}개 청크"
        )

    def search(self, query_embedding: list[float], top_k: int = 5,
               filter_expr: str | None = None) -> list[dict]:
        """벡터 유사도 검색.

        Returns:
            [{"id", "source_file", "section", "chunk_id", "heading",
              "content", "topic", "distance"}, ...]
        """
        results = self._client.search(
            collection_name=self._collection,
            data=[query_embedding],
            limit=top_k,
            filter=filter_expr or "",
            output_fields=["source_file", "section", "chunk_id",
                           "heading", "content", "topic"],
        )
        hits = []
        for hit in results[0]:
            entry = hit["entity"]
            entry["id"] = hit["id"]
            entry["distance"] = hit["distance"]
            hits.append(entry)
        return hits
