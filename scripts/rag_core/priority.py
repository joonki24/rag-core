"""문서 우선순위와 요구사항 먼저 담기(묶음 ㊷, 기준은 CHANGES.md 묶음 ㊶ "확정").

UI·Chroma·모델을 부르지 않는 순수 함수만 둔다. 설정(`evidence.doc_priority`)이 없으면 이 모듈은 쓰이지 않는다.
"""
from __future__ import annotations

OTHER = "other"


class PriorityError(Exception):
    """묶음 51 D5: 문서 종류를 가를 수 없어 멈춘다(조용히 other로 두지 않는다)."""


def known_types(cfg: dict) -> set:
    """설정이 아는 문서 종류: 대응표 방식이면 프로필 id들, 조각 방식이면 file_patterns의 종류들."""
    return set(cfg["b0_file_types"].values()) if "b0_file_types" in cfg else set(cfg["file_patterns"])


def doc_type_of(metadata: dict, cfg: dict) -> str:
    """청크의 문서 종류. 청크 meta에 doc_type이 있으면(B1) 그것을, 없으면(B0) 파일 이름으로 가른다(file_doc_type).
    B1의 doc_type이 설정이 아는 종류 밖이면 멈춘다(묶음 51 D5 c)."""
    dt = (metadata or {}).get("doc_type")
    if dt:
        if dt not in known_types(cfg):
            raise PriorityError(f"청크의 doc_type {dt!r}이 설정이 아는 문서 종류 {sorted(known_types(cfg))} 밖입니다")
        return dt
    return file_doc_type((metadata or {}).get("source_file") or "", cfg)


def file_doc_type(file_name: str, cfg: dict) -> str:
    """파일 이름만으로 문서 종류를 가른다(B0 청크, 정답표의 근거 파일, 청크 위치 정보용).
    대응표(b0_file_types)가 있으면 이름이 정확히 같은 항목, 없으면 file_patterns의 조각. 못 가르면 멈춘다(묶음 51 D5 a·b)."""
    if "b0_file_types" in cfg:
        if file_name not in cfg["b0_file_types"]:
            raise PriorityError(f"문서 종류 대응표(b0_project_yaml의 documents)에 없는 파일입니다: {file_name!r}")
        return cfg["b0_file_types"][file_name]
    for kind, piece in cfg["file_patterns"].items():
        if piece in file_name:
            return kind
    raise PriorityError(f"어느 파일 이름 조각(file_patterns {cfg['file_patterns']})에도 맞지 않는 파일입니다: {file_name!r}")


def rank_of(doc_type: str, cfg: dict) -> int:
    """우선순위 순위(작을수록 먼저). 순서에 없는 문서 종류는 마지막."""
    for i, g in enumerate(cfg["order"]):
        if doc_type == g or (isinstance(g, list) and doc_type in g):
            return i
    return len(cfg["order"])


def take_until(candidates: list, cap: int, count) -> tuple[list, int]:
    """순서대로 담되 다음 청크가 상한을 넘으면 거기서 멈춘다(자르지 않고, 뒤의 작은 청크를 건너뛰어 담지 않는다)."""
    chosen, used = [], 0
    for c in candidates:
        n = count(c["text"])
        if used + n > cap:
            break
        chosen.append(c)
        used += n
    return chosen, used


def priority_order(candidates: list, cfg: dict, exclude_first: bool = True) -> list:
    """후보를 (우선순위 순위, 거리) 순으로 정렬한다. 입력은 이미 거리 순이어야 하고 같은 순위 안의 순서는 입력 순서(검색 순위)다.
    먼저 담을 문서 종류(요구사항)는 별도 몫이라 기본으로 뺀다."""
    first = cfg["first_group"]
    rest = [c for c in candidates if not (exclude_first and doc_type_of(c["metadata"], cfg) == first)]
    return sorted(rest, key=lambda c: rank_of(doc_type_of(c["metadata"], cfg), cfg))


def requirement_report(gold_evidence, chunks: list, cfg: dict) -> dict:
    """질문 하나의 요구사항 지표(G3, 판정에 쓰지 않음).

    gold_evidence: 정답표 근거(file, sheet, rows). chunks: 담긴 청크(file, sheet, rows=(시작, 끝)).
    관련 요구사항 재현율 = 요구사항 정답 근거 중 행의 50% 이상을 덮은 근거 수 / 요구사항 정답 근거 수.
    혼입 = 담긴 요구사항 청크 중 어느 요구사항 정답 근거 행과도 겹치지 않는 수.
    """
    first = cfg["first_group"]
    req_ev = [e for e in gold_evidence if file_doc_type(e.file, cfg) == first]
    req_chunks = [c for c in chunks if file_doc_type(c["file"], cfg) == first]
    hit = 0
    for e in req_ev:
        rows = set(e.rows)
        got = set()
        for c in chunks:
            if c["file"] == e.file and c["sheet"] == e.sheet:
                got |= rows & set(range(c["rows"][0], c["rows"][1] + 1))
        if rows and len(got) / len(rows) >= 0.5:
            hit += 1
    contaminated = 0
    for c in req_chunks:
        span = set(range(c["rows"][0], c["rows"][1] + 1))
        if not any(e.file == c["file"] and e.sheet == c["sheet"] and set(e.rows) & span for e in req_ev):
            contaminated += 1
    return {"n_req_evidence": len(req_ev), "n_req_hit": hit, "n_req_chunks": len(req_chunks), "n_contaminated": contaminated}


def card_labels(chunks: list, cfg: dict) -> dict:
    """묶음 ㊺ H3·H4: 담긴 청크마다 문서 종류와 우선순위 등급, F/O·B/O 확인 필요 표시. 검색·담기·점수에는 쓰지 않는다.

    cards: {청크 id: {"doc_type", "priority_rank"}}. confirm_needed: F/O와 B/O 청크가 같은 category_path_text로 함께 담긴 쌍
    (내용이 다른지는 판단하지 않고 사람·답 만드는 단계에 넘기는 표시). 경로가 없는 청크(B0)는 표시하지 않는다.
    """
    cards, by_path = {}, {}
    for c in chunks:
        md = c.get("metadata") or {}
        dt = doc_type_of(md, cfg)
        cards[c["id"]] = {"doc_type": dt, "priority_rank": rank_of(dt, cfg)}
        if c.get("via") == "link":                                   # 묶음 ㊾: 연결로 온 카드는 연결 등급(확정/추정)과 방법을 표시
            cards[c["id"]].update(link_grade=c.get("link_grade"), link_method=c.get("link_method"))
        p = md.get("category_path_text")
        if p and dt in ("fo", "bo"):
            by_path.setdefault(p, {}).setdefault(dt, []).append(c["id"])
    confirm = [{"fo": v["fo"], "bo": v["bo"]} for p, v in sorted(by_path.items()) if "fo" in v and "bo" in v]
    return {"cards": cards, "confirm_needed": confirm}
