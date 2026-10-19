"""근거 모으기: 검색 결과를 순위대로 담되 토큰 예산을 넘기 직전에 멈춘다.

[쉽게 말하면]
  질문 하나마다 "검색 1위 카드, 2위 카드, 3위 카드 ..." 순서로 바구니에 담는다.
  바구니 크기는 토큰 예산(기본 4,000토큰)이다. 다음 카드를 넣으면 넘치는 순간 멈춘다.
  카드를 잘라서 넣거나, 넘치는 카드를 건너뛰고 뒤의 작은 카드를 넣지 않는다.
  모든 방식(B0-R, B0-S, B0-RC, B1)에 똑같이 적용해서 같은 분량의 글을 받게 한다.

[이 파일에서 먼저 볼 것]
  collect_evidence  : 위 규칙 그대로의 담기(가장 기본)
  gather            : 검색 + 담기를 한 번에(eval_runner가 부르는 입구)
  gather_priority   : "요구사항 카드를 먼저 담기" 설정이 켜져 있을 때의 담기
  (B1 검색+연결 팔은 이 파일 대신 link_expansion.gather_with_links를 쓴다)

채점표 §3: "검색 순위대로 청크를 담고, 다음 청크를 담으면 토큰 예산을 넘으면 거기서 멈춘다
(청크를 자르지 않는다)". 토큰은 tiktoken cl100k_base로 센 **청크 text의 합**이다
(구분자·출처 표기는 세지 않는다). B0와 B1에 똑같이 적용해서 같은 분량의 글을 받게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass

_encoder = None


def count_tokens(text: str) -> int:
    """글의 토큰 수(cl100k_base). 토크나이저는 처음 한 번만 불러 와 다시 쓴다."""
    global _encoder
    if _encoder is None:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
    return len(_encoder.encode(text))


@dataclass
class Evidence:
    """한 질문에 대해 담은 결과(바구니). 담은 카드 목록, 쓴 토큰, 멈춘 이유를 함께 들고 다닌다."""
    chunks: list           # 담은 후보(검색 결과 그대로, 순위 순서)
    used_tokens: int
    budget: int
    n_candidates: int
    stopped_reason: str     # "budget" | "first_exceeds" | "all_fit" | "no_candidates"
    pool_exhausted: bool    # 후보를 모두 담고도 예산이 남았다 = 후보가 모자랐을 수 있다는 신호(오류 아님)
    link_report: dict | None = None   # 연결 따라가기 팔에서만 채워진다(link_expansion.gather_with_links)
    topic_report: dict | None = None  # 주제별·결합 전략에서만 채워진다(topic_search.gather_by_topics)
    diversity_report: dict | None = None  # 묶음 73: 다양화를 켰을 때만 채워진다


def collect_evidence(candidates: list[dict], token_budget: int, count=count_tokens) -> Evidence:
    """검색 순위대로 카드를 담다가, 다음 카드가 예산을 넘기면 멈춘다(가장 기본 규칙).

    candidates  : 검색 결과(1위부터 순서대로). 각 항목은 {"id", "text", "metadata", "distance"}
    token_budget: 담을 수 있는 최대 토큰 수
    돌려주는 값 : Evidence. stopped_reason은 왜 멈췄는지 알려 준다
                  budget(예산이 참) / first_exceeds(1위 카드부터 예산보다 큼) /
                  all_fit(후보를 다 담음) / no_candidates(검색 결과 없음)
    """
    chosen, used = [], 0
    reason = "all_fit" if candidates else "no_candidates"
    for c in candidates:
        n = count(c["text"])
        if used + n > token_budget:
            reason = "budget" if chosen else "first_exceeds"
            break               # 여기서 멈춘다. 뒤의 더 작은 청크를 건너뛰어 담지 않는다
        chosen.append(c)
        used += n
    return Evidence(
        chunks=chosen, used_tokens=used, budget=token_budget, n_candidates=len(candidates),
        stopped_reason=reason, pool_exhausted=(reason == "all_fit" and used < token_budget),
    )


def priority_parts(query: str, collection_name: str, settings):
    """묶음 ㊷: (요구사항 후보 전체, 우선순위 순으로 정렬한 나머지 후보, 전체 검색 후보).

    요구사항 후보는 서랍 전체를 검색해 요구사항 종류만 남긴 것(거리 순, "요구사항 문서 안에서 한 번 검색")이다.
    나머지는 전체 검색의 후보 한도만큼의 후보를 (우선순위 순위, 거리) 순으로 정렬하고 요구사항 종류는 뺀 것이다.
    첫 검색의 순위에는 가중치를 주지 않는다. 우선순위는 담는 순서에만 쓴다.
    """
    import priority
    from pipeline import query_engine

    cfg = settings.doc_priority
    pool = query_engine.search(query, settings.candidate_pool, collection_name=collection_name)
    first_on = cfg.get("first_enabled", True)                              # 묶음 ㊹: 요구사항 먼저 담기를 끌 수 있다(기본 켬)
    req_all = []
    if first_on:
        everything = query_engine.search(query, 10 ** 9, collection_name=collection_name)   # 서랍 크기까지(검색 폭 상한은 check_index가 지킴)
        req_all = [c for c in everything if priority.doc_type_of(c["metadata"], cfg) == cfg["first_group"]]
        if cfg.get("first_scope") == "pool":                                # 묶음 ㊸: 전체 검색의 후보 한도 안에 든 요구사항만
            in_pool = {c["id"] for c in pool}
            req_all = [c for c in req_all if c["id"] in in_pool]
    if cfg.get("rest_order", "priority") == "distance":                     # 묶음 ㊹: 문서 종류 순서를 끄고 검색 순위 그대로
        rest = [c for c in pool if not (first_on and priority.doc_type_of(c["metadata"], cfg) == cfg["first_group"])]
    else:
        rest = priority.priority_order(pool, cfg, exclude_first=first_on)
    return req_all, rest, pool


def gather_priority(query: str, collection_name: str, settings, count=count_tokens, div=None) -> Evidence:
    """묶음 ㊷(연결 없는 팔): 요구사항을 상한(first_cap_tokens)까지 먼저 담고, 남은 예산을 우선순위 순으로 채운다.
    div(묶음 73, diversity.Context)가 있으면 나머지 검색 카드에만 다양화 상한을 쓴다(요구사항 먼저 담기는 그대로)."""
    import priority

    cfg, budget = settings.doc_priority, settings.token_budget
    req_all, rest, pool = priority_parts(query, collection_name, settings)
    req_chosen, used_req = priority.take_until(req_all, min(cfg["first_cap_tokens"], budget), count)
    capper = div.new() if div is not None else None
    if capper is None:
        rest_chosen, used_rest = priority.take_until(rest, budget - used_req, count)
        rest_done = len(rest_chosen) == len(rest)
    else:
        rest_chosen, used_rest, stop = capper.take(rest, budget - used_req, count)
        rest_done = stop == len(rest)
    chunks = [dict(c, via="req_search") for c in req_chosen] + [dict(c, via="search") for c in rest_chosen]
    used = used_req + used_rest
    if not pool and not req_all:
        reason = "no_candidates"
    elif not chunks:
        reason = "first_exceeds"
    elif rest_done:
        reason = "all_fit"
    else:
        reason = "budget"
    return Evidence(chunks=chunks, used_tokens=used, budget=budget, n_candidates=len(pool), stopped_reason=reason,
                    pool_exhausted=(reason == "all_fit" and used < budget),
                    diversity_report=capper.report() if capper is not None else None)


def collect_diverse(candidates: list[dict], token_budget: int, capper, count=count_tokens) -> tuple[Evidence, int]:
    """묶음 73: collect_evidence와 같되 다양화 상한에 걸린 카드는 건너뛴다. (Evidence, 멈춘 자리)."""
    chosen, used, stop = capper.take(candidates, token_budget, count)
    if not candidates:
        reason = "no_candidates"
    elif stop == len(candidates):
        reason = "all_fit"
    else:
        reason = "budget" if chosen else "first_exceeds"
    return Evidence(chunks=chosen, used_tokens=used, budget=token_budget, n_candidates=len(candidates), stopped_reason=reason,
                    pool_exhausted=(reason == "all_fit" and used < token_budget), diversity_report=capper.report()), stop


def gather(query: str, collection_name: str, settings, count=count_tokens, div=None) -> Evidence:
    """검색해서 설정의 후보 수만큼 가져오고, 설정의 토큰 예산까지 담는다. div는 묶음 73 다양화(없으면 기존 경로 그대로)."""
    if getattr(settings, "doc_priority", None):                          # 묶음 ㊷: 켰을 때만. 꺼져 있으면 아래 기존 경로 그대로
        return gather_priority(query, collection_name, settings, count, div=div)
    if getattr(settings, "search_mode", "dense") == "hybrid":          # 묶음 ㉘ X3: 밀집 + BM25(RRF)
        if div is not None:
            raise ValueError("다양화(evidence.diversity)는 search_mode dense에서만 쓸 수 있습니다")
        import hybrid_search

        candidates = hybrid_search.search(query, settings.candidate_pool, collection_name)
        return collect_evidence(candidates, settings.token_budget, count)
    from pipeline import query_engine

    candidates = query_engine.search(query, settings.candidate_pool, collection_name=collection_name)
    if div is not None:
        return collect_diverse(candidates, settings.token_budget, div.new(), count)[0]
    return collect_evidence(candidates, settings.token_budget, count)
