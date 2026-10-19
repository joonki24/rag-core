"""si-prep(전처리)의 chunks.jsonl을 읽어 검증하고 Chroma 입력 문서로 바꾼다.

[쉽게 말하면]
  카드(청크) 파일을 평가기로 들여오는 "입구"다. 하는 일은 세 가지다.
  1) 읽기와 검사  : 줄마다 팀 스키마(si_prep.schema.Chunk)에 맞는지 본다. 하나라도 틀리면 멈춘다.
  2) 방식 구분    : 카드 번호 앞부분으로 방식을 안다. B1-… = si-prep, B0-R-… = 행 단위, B0-S-… = 500자 조각
  3) 색인(서랍)   : 방식마다 따로 된 Chroma 서랍에 넣는다. 서랍 이름은 "{데이터셋}_{방식}"(예: svc_c_b1)

[이 파일에서 먼저 볼 것]
  read_chunks     : 카드 파일을 읽어 검사된 목록으로 돌려줌
  ingest_chunks   : 카드 파일을 서랍에 넣음(rebuild=True면 서랍을 비우고 새로)
  collection_for  : 방식("B1", "B0-R", "B0-S")에 맞는 서랍 이름

경계는 INTERFACE.md. si-prep의 파일은 읽기만 하고, 청크 검증은 si-prep의 `si_prep.schema.Chunk`로만 한다.
`si_prep`은 팀 저장소를 `pip install -e`로 설치해서 쓴다(2026-10-06 변경, 묶음 ⑬). 폴더 경로만 알려 주는
SI_PREP_SRC 방식은 `si_prep/__init__.py`가 설치 기록을 읽어서 더 이상 동작하지 않는다.
"""
from __future__ import annotations

import json
from collections import Counter

import config  # noqa: F401  (.env를 읽어 환경 변수로 올리고, Chroma 통계 전송을 끈다)

# Chroma 메타데이터에 우리가 덧붙이는 키. 스키마 meta 키와 겹치면 안 된다(INTERFACE.md 4번).
ADDED_KEYS = ("source_file", "source_sheet", "row_start", "row_end", "variant")
# 방식 -> Settings.collections의 키(서랍 이름은 dataset에서 만든 `{dataset}_{방식}`, 묶음 ⑰ R2)
_VARIANT_TO_COLLECTION_KEY = {"B1": "b1", "B0-R": "b0_r", "B0-S": "b0_s"}


class ContractError(RuntimeError):
    """si-prep의 스키마와 맞지 않는 입력이거나, 스키마를 불러올 수 없을 때."""


def _chunk_model():
    try:
        from si_prep.schema import Chunk
    except ImportError as e:
        raise ContractError(
            "si_prep.schema를 불러올 수 없습니다. 팀 저장소(si-prep)를 받아 `pip install -e <si-prep 폴더>`로 "
            "설치하세요. 스키마를 복사해서 대신 쓰지 않습니다(INTERFACE.md 2번)."
        ) from e
    return Chunk


def variant_of(chunk_id: str) -> str:
    """청크 번호 접두어로 어느 방식의 청크인지 구분한다."""
    if chunk_id.startswith("B1-"):
        return "B1"
    if chunk_id.startswith("B0-R-"):
        return "B0-R"
    if chunk_id.startswith("B0-S-"):
        return "B0-S"
    if chunk_id.startswith("B0-"):
        return "B0"
    raise ContractError(f"알 수 없는 chunk_id 접두어: {chunk_id}")


def safe_error(e: Exception) -> str:
    """묶음 ㉝ Y6: 스키마 위반 메시지에서 입력값(본문 조각)을 뺀다. 위치와 종류만 남긴다."""
    errs = getattr(e, "errors", None)
    if callable(errs):
        try:
            return "; ".join(f"{'.'.join(str(x) for x in d['loc']) or '(최상위)'}: {d['type']}: {d['msg']}" for d in errs(include_input=False, include_url=False, include_context=False))
        except Exception:
            return type(e).__name__
    return type(e).__name__ if not isinstance(e, ValueError) or "line" not in str(e) else str(e)[:120]


def _read(path: str):
    """줄마다 스키마로 검증해 (줄 번호, 청크) 목록을 돌려준다. 위반이 하나라도 있으면 줄 번호와 함께 멈춘다."""
    Chunk = _chunk_model()
    pairs, errors, seen = [], [], {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                chunk = Chunk.model_validate(json.loads(line))
            except Exception as e:  # JSON 오류, 스키마 위반 모두
                errors.append(f"{n}줄: {safe_error(e)[:200]}")
                continue
            # 행 범위(1 이상, 시작 <= 끝)는 이제 스키마의 RowRange가 검사한다. 어댑터의 임시 검사는 지웠다.
            if chunk.chunk_id in seen:
                errors.append(f"{n}줄: chunk_id 중복 ({chunk.chunk_id}, 앞서 {seen[chunk.chunk_id]}줄)")
                continue
            seen[chunk.chunk_id] = n
            pairs.append((n, chunk))
    if errors:
        more = f"\n... 외 {len(errors) - 5}개" if len(errors) > 5 else ""
        raise ContractError(f"{path}: 스키마와 맞지 않는 줄 {len(errors)}개\n" + "\n".join(errors[:5]) + more)
    return pairs


def read_chunks(path: str):
    """검증된 청크 목록. 위반이 하나라도 있으면 줄 번호와 함께 멈추고 아무것도 돌려주지 않는다."""
    return [c for _, c in _read(path)]


def to_documents(chunks):
    """검증된 청크를 Chroma 입력({id, text, metadata})으로 바꾼다. 메타데이터는 평평한 값만 쓴다.

    `meta`의 None은 Chroma가 오류 없이 버리므로, 조용히 사라지지 않게 개수를 보고에 남긴다.
    """
    docs, none_dropped = [], 0
    for c in chunks:
        clash = set(c.meta) & set(ADDED_KEYS)
        if clash:
            raise ContractError(f"{c.chunk_id}: meta 키가 우리가 붙이는 키와 겹침 {sorted(clash)}")
        md = {}
        for k, v in c.meta.items():
            if v is None:
                none_dropped += 1
                continue
            md[k] = v
        md.update({
            "source_file": c.source.file,
            "source_sheet": c.source.sheet,
            "row_start": c.source.rows.start,
            "row_end": c.source.rows.end,
            "variant": variant_of(c.chunk_id),
        })
        docs.append({"id": c.chunk_id, "text": c.text, "metadata": md})
    report = {
        "n_chunks": len(docs),
        "meta_none_dropped": none_dropped,
        "variants": dict(Counter(d["metadata"]["variant"] for d in docs)),
    }
    return docs, report


def collection_for(variant: str, settings) -> str:
    """[보정 C2-2] 방식에 맞는 서랍 이름을 설정에서 고른다. R/S 구분이 없는 B0는 정할 수 없어 멈춘다."""
    key = _VARIANT_TO_COLLECTION_KEY.get(variant)
    if key is None:
        raise ContractError(
            f"방식 '{variant}'는 설정으로 서랍을 정할 수 없습니다(B0 청크는 B0-R- 또는 B0-S-로 시작해야 함)"
        )
    return settings.collections[key]


def ingest_chunks(path: str, collection_name: str | None = None, settings=None,
                  rebuild: bool = False, append: bool = False) -> dict:
    """chunks.jsonl을 검증하고 서랍에 색인한다. 검증이 실패하면 아무것도 저장하지 않는다.

    [보정 C2] 한 파일에는 한 방식만 있어야 하고, 서랍에는 한 방식만 들어간다.
    settings를 주면 방식에 맞는 서랍을 고르고, 서랍 이름을 직접 주면(시험용) 그 이름을 쓴다.
    [묶음 ⑦] 서랍은 한 입력 파일의 전체 집합이다. 서랍에 이번 입력에 없는 옛 청크가 있으면 저장 전에 멈춘다.
    rebuild=True는 서랍을 비우고 입력만 넣는다. append=True는 기존 청크를 두고 더한다(합집합).
    """
    from pipeline import embed_store

    if rebuild and append:
        raise ContractError("rebuild와 append는 같이 쓸 수 없습니다")
    pairs = _read(path)
    if not pairs:
        raise ContractError(f"{path}: 청크가 하나도 없습니다")

    lines_by_variant: dict[str, list[int]] = {}
    for n, c in pairs:
        lines_by_variant.setdefault(variant_of(c.chunk_id), []).append(n)
    if len(lines_by_variant) > 1:
        detail = ", ".join(f"{v} {lines[0]}줄부터 {len(lines)}개" for v, lines in lines_by_variant.items())
        raise ContractError(f"{path}: 한 파일에 방식이 섞여 있습니다 ({detail}). B0와 B1은 파일을 나누세요")
    (variant,) = lines_by_variant

    if settings is not None:
        mapped = collection_for(variant, settings)
        if collection_name is not None and collection_name != mapped:
            raise ContractError(f"서랍 이름 '{collection_name}'이 설정의 '{mapped}'와 다릅니다({variant})")
        collection_name = mapped
    elif collection_name is None:
        raise ContractError("collection_name 또는 settings 중 하나가 필요합니다")

    docs, report = to_documents([c for _, c in pairs])

    # C2-3: 대상 서랍에 이미 다른 방식의 문서가 있으면 조용히 섞지 않고 멈춘다.
    others = embed_store.get_collection(collection_name).get(where={"variant": {"$ne": variant}}, limit=1)
    if others["ids"]:
        raise ContractError(f"서랍 '{collection_name}'에 이미 다른 방식의 문서가 있습니다(예: {others['ids'][0]}). 섞지 않습니다")

    # 묶음 ⑦: 이번 입력에 없는 옛 청크가 서랍에 남아 있으면 조용히 섞이지 않게 멈춘다.
    new_ids = {d["id"] for d in docs}
    existing = set(embed_store.get_collection(collection_name).get(include=[])["ids"])
    stale = sorted(existing - new_ids)
    report["stale_found"] = len(stale)
    if stale and not (rebuild or append):
        raise ContractError(
            f"서랍 '{collection_name}'에 이번 입력에 없는 옛 청크가 {len(stale)}개 있습니다(예: {stale[:3]}). "
            "저장하지 않았습니다. 입력을 다시 만든 것이라면 rebuild=True로 서랍을 비우고 다시 넣고, "
            "일부러 더하는 것이라면 append=True를 주세요."
        )
    report["stale_removed"] = embed_store.reset_collection(collection_name) if rebuild else 0

    embed_store.ingest_documents(docs, collection_name=collection_name)
    stored = embed_store.get_collection(collection_name, create=False).count()
    if not append and stored != len(new_ids):
        raise ContractError(f"서랍 '{collection_name}'에 저장된 건수 {stored}가 입력 {len(new_ids)}건과 다릅니다")
    report["collection"] = collection_name
    return report


def hit_view(candidate: dict) -> dict:
    """검색 결과 한 건을 chunk_id, 거리, source(파일·시트·행)가 보이는 모양으로 바꾼다."""
    m = candidate["metadata"]
    return {
        "chunk_id": candidate["id"],
        "distance": candidate["distance"],
        "variant": m["variant"],
        "source": {"file": m["source_file"], "sheet": m["source_sheet"], "rows": [m["row_start"], m["row_end"]]},
        "text": candidate["text"],
    }


def search_with_source(query: str, top_k: int, collection_name: str) -> list[dict]:
    from pipeline import query_engine

    return [hit_view(c) for c in query_engine.search(query, top_k, collection_name=collection_name)]
