"""문서를 임베딩해서 ChromaDB에 저장하는 범용 유틸 (도메인 무관).

EMBEDDING_BACKEND=local  (기본값, 무료): sentence-transformers 로컬 모델
EMBEDDING_BACKEND=openai (유료): OpenAI Embeddings API

TODO: 가전제품 설명서/FAQ를 {"id", "text", "metadata"} 형태의 문서 리스트로
변환하는 도메인 전용 빌더가 아직 없다 (예전 build_documents.py는 무신사 상품
JSON 전용이라 삭제함). 새 빌더가 만들어지면 ingest_documents()에 넘기면 된다.
"""

import chromadb

import config

_local_model = None  # sentence-transformers 모델은 무겁기 때문에 한 번만 로드해서 재사용


def _embed_local(texts: list[str]) -> list[list[float]]:
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer

        print(f"[embed] 로컬 임베딩 모델 로딩 중: {config.LOCAL_EMBEDDING_MODEL} (최초 1회는 다운로드 필요)")
        _local_model = SentenceTransformer(config.LOCAL_EMBEDDING_MODEL)
    return _local_model.encode(texts, normalize_embeddings=True).tolist()


OPENAI_EMBED_BATCH_SIZE = 100  # 한 번에 너무 많이 보내면 300,000 토큰 제한에 걸림


def _embed_openai(texts: list[str]) -> list[list[float]]:
    config.assert_external_allowed("OpenAI Embeddings API")  # [rag_core F1]
    from openai import OpenAI

    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    embeddings = []
    for i in range(0, len(texts), OPENAI_EMBED_BATCH_SIZE):
        batch = texts[i:i + OPENAI_EMBED_BATCH_SIZE]
        resp = client.embeddings.create(model=config.EMBEDDING_MODEL, input=batch)
        embeddings.extend(d.embedding for d in resp.data)
    return embeddings


def embed_texts(texts: list[str]) -> list[list[float]]:
    if config.EMBEDDING_BACKEND == "openai":
        return _embed_openai(texts)
    return _embed_local(texts)


class CollectionNotFoundError(RuntimeError):
    """[보정 B1] 검색하려는 컬렉션이 없다. 이름이 틀렸을 수 있어 조용히 새로 만들지 않는다."""


class CollectionConfigError(RuntimeError):
    """[보정 B2] 컬렉션의 거리 설정이 cosine이 아니다. 채점표는 코사인을 정했다."""


def _model_name() -> str:
    """색인에 쓰는 임베딩 모델 이름(서랍에 적어 두는 값, 묶음 ㉜ Z2)."""
    return config.EMBEDDING_MODEL if config.EMBEDDING_BACKEND == "openai" else config.LOCAL_EMBEDDING_MODEL


def get_collection(name: str | None = None, create: bool = True):
    """[rag_core F8] 컬렉션 이름을 지정할 수 있다. B0-R, B0-S, B1이 한 컬렉션에 섞이지 않게 한다.

    create=True(색인할 때)는 없으면 cosine으로 새로 만든다. create=False(검색할 때)는 없으면 오류다.
    [보정 B2] 이미 있는 컬렉션이 cosine이 아니면 오류다(Chroma는 기존 컬렉션에 대한 cosine 지정을 조용히 무시한다).
    """
    chroma_client = chromadb.PersistentClient(path=config.CHROMA_DIR)
    cname = name or config.COLLECTION_NAME
    if create:
        collection = chroma_client.get_or_create_collection(name=cname, metadata={"hnsw:space": "cosine", "embedding_model": _model_name()})
        recorded = (collection.metadata or {}).get("embedding_model")
        if recorded and recorded != _model_name():                                   # 묶음 ㉜ Z2: 다른 모델로 만든 서랍에 섞어 넣지 않는다
            raise CollectionConfigError(
                f"컬렉션 '{cname}'은 임베딩 모델 {recorded!r}로 만들어졌는데 지금 설정은 {_model_name()!r}입니다. "
                "컬렉션을 지우고 다시 색인하세요(--reindex)."
            )
    else:
        try:
            collection = chroma_client.get_collection(name=cname)
        except chromadb.errors.NotFoundError as e:
            raise CollectionNotFoundError(
                f"컬렉션 '{cname}'이 없습니다. 이름을 확인하거나 먼저 색인하세요."
            ) from e
    space = dict(collection.configuration).get("hnsw", {}).get("space")
    if space != "cosine":
        raise CollectionConfigError(
            f"컬렉션 '{cname}'의 거리 설정이 {space!r}입니다. cosine이어야 합니다(채점표 §1). "
            "컬렉션을 지우고 다시 색인하세요."
        )
    _ensure_search_width(collection)
    return collection


def _ensure_search_width(collection) -> None:
    """[이슈 24] 컬렉션의 HNSW 검색 폭(ef_search)이 설정값보다 작으면 올린다. 색인을 다시 하지 않아도 되고 저장소에 남는다.
    기본값(100)에서는 후보 경계 부근 목록이 다시 색인한 저장소마다 달라질 수 있다. 바꿨을 때는 알린다(조용히 바꾸지 않음)."""
    current = (dict(collection.configuration).get("hnsw") or {}).get("ef_search")
    if isinstance(current, int) and current >= config.HNSW_EF_SEARCH:
        return
    from chromadb.api.collection_configuration import UpdateCollectionConfiguration, UpdateHNSWConfiguration

    collection.modify(configuration=UpdateCollectionConfiguration(hnsw=UpdateHNSWConfiguration(ef_search=config.HNSW_EF_SEARCH)))
    print(f"[search-width] 컬렉션 '{collection.name}'의 ef_search를 {current} -> {config.HNSW_EF_SEARCH}로 올렸습니다(이슈 24).")


def reset_collection(name: str) -> int:
    """[묶음 ⑦] 컬렉션을 지운다(없으면 아무것도 안 함). 지운 문서 수를 돌려준다. 다시 만들 때는 get_collection이 cosine으로 새로 만든다."""
    chroma_client = chromadb.PersistentClient(path=config.CHROMA_DIR)
    try:
        removed = chroma_client.get_collection(name=name).count()
    except chromadb.errors.NotFoundError:
        return 0
    chroma_client.delete_collection(name=name)
    return removed


# [rag_core F3] get_known_models(가전 모델명 목록)는 우리 문제와 무관해서 지웠다.


def ingest_documents(docs: list[dict], collection_name: str | None = None):
    """docs: [{"id": str, "text": str, "metadata": dict}, ...] 형태를 받아 저장한다."""
    if not docs:
        print("[ingest] 문서가 없습니다.")
        return

    texts = [d["text"] for d in docs]
    embeddings = embed_texts(texts)

    collection = get_collection(collection_name)
    collection.upsert(
        ids=[d["id"] for d in docs],
        embeddings=embeddings,
        documents=texts,
        metadatas=[d.get("metadata", {}) for d in docs],
    )
    print(
        f"[ingest] {len(docs)}개 문서를 '{collection.name}' 컬렉션에 저장했습니다. "
        f"(embedding backend: {config.EMBEDDING_BACKEND})"
    )


# [2026-10-11 정리] 옛 문서 JSON 입구(ingest, main)는 호출하는 곳이 없어 뺐다. 색인은 ingest_documents를 쓴다. 원본은 _origin/에 있다.
