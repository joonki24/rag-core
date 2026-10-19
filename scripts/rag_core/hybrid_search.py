"""하이브리드 검색(묶음 ㉘ X3, 채점표 변경 12의 C2): 밀집(코사인)과 BM25(글자 2-gram)를 RRF로 합친다.

모든 팔에 같은 공통 검색 설정으로 쓴다. 이 모듈은 검색 후보 목록만 돌려주고 채점·예산·연결을 모른다.
- 밀집: 기존 `query_engine.search`의 상위 DEPTH개(거리 오름차순)
- BM25: 청크 본문의 글자 2-gram(NFC, 소문자, 공백 제거), k1=1.5, b=0.75, 상위 DEPTH개(동점은 chunk id 순)
- 합치기: RRF, 점수 = Σ 1/(RRF_K + 순위). 정확한 분수로 계산한다(부동소수 동점 흔들림 없음)
- 동점: 밀집 순위(없으면 맨 뒤), 그다음 chunk id
값(DEPTH, RRF_K, BM25 파라미터)은 이 묶음에서 고정이며 점수를 보고 바꾸지 않는다.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from fractions import Fraction

DEPTH = 100
RRF_K = 60
BM25_K1 = 1.5
BM25_B = 0.75
_cache: dict = {}


def grams(text: str) -> list[str]:
    s = re.sub(r"\s+", "", unicodedata.normalize("NFC", text or "").lower())
    if len(s) < 2:
        return [s] if s else []
    return [s[i:i + 2] for i in range(len(s) - 1)]


class BM25:
    def __init__(self, ids: list, docs: list):
        self.ids = ids
        self.tf = [Counter(grams(d)) for d in docs]
        self.dl = [sum(c.values()) for c in self.tf]
        self.n = len(ids)
        self.avgdl = (sum(self.dl) / self.n) if self.n else 0.0
        df = Counter()
        for c in self.tf:
            df.update(c.keys())
        self.idf = {t: math.log(1 + (self.n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.post = {}
        for i, c in enumerate(self.tf):
            for t in c:
                self.post.setdefault(t, []).append(i)

    def scores(self, query_grams: list[str]) -> dict:
        out: dict = {}
        for t in set(query_grams):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.post[t]:
                f = self.tf[i][t]
                norm = BM25_K1 * (1 - BM25_B + BM25_B * self.dl[i] / self.avgdl) if self.avgdl else BM25_K1
                out[i] = out.get(i, 0.0) + idf * f * (BM25_K1 + 1) / (f + norm)
        return out


def _index(coll, name: str):
    import config

    key = (str(config.CHROMA_DIR), name, coll.count())
    if key not in _cache:
        got = coll.get(include=["documents", "metadatas"])
        order = sorted(range(len(got["ids"])), key=lambda i: got["ids"][i])
        ids = [got["ids"][i] for i in order]
        docs = [got["documents"][i] for i in order]
        metas = [got["metadatas"][i] for i in order]
        _cache[key] = (BM25(ids, docs), docs, metas)
    return _cache[key]


def fuse(dense_ids: list, bm25_ids: list, top_k: int) -> list:
    """두 순위 목록을 RRF로 합쳐 상위 top_k개 id를 돌려준다(순수 함수, 시험이 직접 부른다)."""
    score: dict = {}
    for r, cid in enumerate(dense_ids, 1):
        score[cid] = score.get(cid, Fraction(0)) + Fraction(1, RRF_K + r)
    for r, cid in enumerate(bm25_ids, 1):
        score[cid] = score.get(cid, Fraction(0)) + Fraction(1, RRF_K + r)
    drank = {cid: r for r, cid in enumerate(dense_ids, 1)}
    return sorted(score, key=lambda c: (-score[c], drank.get(c, 10 ** 9), c))[:top_k]


def search(query: str, top_k: int, collection_name: str) -> list[dict]:
    from pipeline import query_engine
    from pipeline.embed_store import get_collection

    coll = get_collection(collection_name, create=False)
    n = coll.count()
    if n <= 0:
        return []
    dense = query_engine.search(query, min(DEPTH, n), collection_name=collection_name)
    index, docs, metas = _index(coll, collection_name)
    sc = index.scores(grams(query))
    bm = sorted(sc, key=lambda i: (-sc[i], index.ids[i]))[:DEPTH]
    fused = fuse([c["id"] for c in dense], [index.ids[i] for i in bm], top_k)
    by_dense = {c["id"]: c for c in dense}
    pos = {cid: i for i, cid in enumerate(index.ids)}
    out = []
    for cid in fused:
        if cid in by_dense:
            out.append(by_dense[cid])
        else:                                                   # BM25에서만 올라온 후보: 밀집 거리는 없다(None)
            i = pos[cid]
            out.append({"id": cid, "text": docs[i], "metadata": metas[i], "distance": None})
    return out
