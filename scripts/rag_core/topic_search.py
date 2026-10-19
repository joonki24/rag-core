"""주제별 검색과 결합(묶음 ⑳, 기준은 CHANGES.md의 Q1~Q14).

같은 질문을 세 가지 방식으로 검색해 같은 총 예산 안에서 비교하게 한다.
  original  질문 하나로 검색(지금 방식, evidence.gather가 그대로 한다. 이 모듈은 건드리지 않는다)
  topics    점검표의 주제마다 따로 검색해서 합친다
  combined  원래 질문 몫 + 주제 몫

검색 입력은 질문 글과 점검표 문구뿐이다(Q2). 정답표의 근거·영역·조건은 이 모듈에 들어오지 않는다.
UI, Chroma, 모델을 부르지 않는 함수로 짜고, 검색 함수는 밖에서 받는다(`gather`만 실제 검색을 묶는다).
"""
from __future__ import annotations

import datetime
import hashlib
import re

import yaml

import evidence as ev

STRATEGIES = ("original", "topics", "combined")
_KEYS = {"kind", "author", "date", "query_template", "topics"}
_PLACEHOLDER = re.compile(r"\{question\}|\{topic\}")


class TopicError(ValueError):
    """점검표나 전략 인자가 Q3, Q6, Q7, Q10 기준에 맞지 않을 때."""


def _text(v, where):
    if not isinstance(v, str) or not v.strip():
        raise TopicError(f"{where}: 비어 있지 않은 글이어야 합니다: {v!r}")
    return v.strip()


def load_topics(path: str, purpose: str) -> dict:
    """점검표 파일을 읽는다(Q3). 빠진 칸·모르는 칸·목적이 다른 kind·주제 이름 중복·주제 2개 미만이면 멈춘다."""
    with open(path, "rb") as f:
        data = f.read()
    raw = yaml.safe_load(data.decode("utf-8"))
    if not isinstance(raw, dict):
        raise TopicError(f"{path}: 점검표는 키와 값의 목록이어야 합니다")
    extra, missing = set(raw) - _KEYS, _KEYS - set(raw)
    if extra or missing:
        raise TopicError(f"{path}: 칸이 맞지 않습니다(모르는 칸 {sorted(extra)}, 빠진 칸 {sorted(missing)})")
    if raw["kind"] not in ("dev", "eval"):
        raise TopicError(f"{path}: kind는 dev 또는 eval이어야 합니다: {raw['kind']!r}")
    if raw["kind"] != purpose:
        raise TopicError(f"{path}: 점검표 kind({raw['kind']})와 실행 목적({purpose})이 다릅니다(개발용과 평가용은 섞지 않음)")
    date = raw["date"].isoformat() if isinstance(raw["date"], (datetime.date, datetime.datetime)) else raw["date"]
    template = _text(raw["query_template"], f"{path} query_template")
    if template.count("{question}") != 1 or template.count("{topic}") != 1:
        raise TopicError(f"{path}: query_template에는 {{question}}과 {{topic}}이 한 번씩 있어야 합니다")
    topics = raw["topics"]
    if not isinstance(topics, list) or len(topics) < 2:
        raise TopicError(f"{path}: topics는 2개 이상의 목록이어야 합니다")
    out, seen = [], set()
    for i, t in enumerate(topics, 1):
        if not isinstance(t, dict) or set(t) != {"name", "text"}:
            raise TopicError(f"{path}: 주제 #{i}는 name과 text만 가진 항목이어야 합니다")
        name = _text(t["name"], f"{path} 주제 #{i} name")
        if name in seen:
            raise TopicError(f"{path}: 주제 이름이 겹칩니다: {name!r}")
        seen.add(name)
        out.append({"name": name, "text": _text(t["text"], f"{path} 주제 {name} text")})
    return {"kind": raw["kind"], "author": _text(raw["author"], f"{path} author"), "date": _text(date, f"{path} date"),
            "query_template": template, "topics": out, "sha256": hashlib.sha256(data).hexdigest()}


def build_query(template: str, question: str, topic_text: str) -> str:
    """Q4: 템플릿의 {question}과 {topic} 자리를 한 번에 바꾼다(질문 글에 {topic}이 있어도 다시 바뀌지 않는다)."""
    return _PLACEHOLDER.sub(lambda m: question if m.group(0) == "{question}" else topic_text, template)


def check_plan(strategy: str, topics_path, original_share) -> None:
    """Q10: 전략과 인자의 조합이 맞는지 본다. 쓰지 않는 인자가 와도 조용히 무시하지 않고 멈춘다."""
    if strategy not in STRATEGIES:
        raise TopicError(f"strategy는 {STRATEGIES} 중 하나여야 합니다: {strategy!r}")
    if strategy == "original":
        if topics_path is not None or original_share is not None:
            raise TopicError("strategy original에는 --topics, --original-share를 줄 수 없습니다(쓰이지 않는 인자)")
        return
    if topics_path is None:
        raise TopicError(f"strategy {strategy}에는 --topics(점검표)가 필요합니다")
    if strategy == "topics" and original_share is not None:
        raise TopicError("strategy topics에는 --original-share를 줄 수 없습니다")
    if strategy == "combined":
        if original_share is None:
            raise TopicError("strategy combined에는 --original-share가 필요합니다(기본값 없음, 값은 사람이 정함)")
        if isinstance(original_share, bool) or not isinstance(original_share, (int, float)) or not 0 < original_share < 1:
            raise TopicError(f"original_share는 0 초과 1 미만이어야 합니다: {original_share!r}")


def split_budget(token_budget: int, strategy: str, n_topics: int, original_share) -> tuple:
    """(원래 질문 몫, 주제 하나의 몫). Q6: 주제 몫 = 몫 ÷ N(내림, 나머지는 쓰지 않음). Q7: 원래 질문 몫 = 총 예산 × 비율(내림)."""
    orig = int(token_budget * original_share) if strategy == "combined" else 0
    per = (token_budget - orig) // n_topics
    if per < 1:
        raise TopicError(f"예산 {token_budget}을 주제 {n_topics}개로 나누면 주제 하나의 몫이 0입니다")
    return orig, per


def gather_by_topics(question: str, cfg: dict, search, token_budget: int, strategy: str, original_share=None,
                     count=ev.count_tokens) -> ev.Evidence:
    """topics 또는 combined 전략으로 근거를 모은다. search(검색문) -> 후보 목록(순위 순서, 검색 결과 그대로)."""
    if strategy not in ("topics", "combined"):
        raise TopicError(f"이 함수는 topics, combined만 처리합니다(original은 evidence.gather): {strategy!r}")
    topics = cfg["topics"]
    orig_cap, per = split_budget(token_budget, strategy, len(topics), original_share)
    parts = []                                                     # (이름, 검색문, 몫)
    if strategy == "combined":
        parts.append(("original", question, orig_cap))
    parts += [(f"topic:{t['name']}", build_query(cfg["query_template"], question, t["text"]), per) for t in topics]

    chunks, taken, used, report, n_candidates, reasons = [], set(), 0, [], 0, []
    for via, query, cap in parts:                                  # 순서대로 한 몫씩. 이미 담은 청크는 후보에서 빼고 자기 몫까지 담는다(순서가 결과의 일부, Q6)
        cands = search(query)
        fresh = [c for c in cands if c["id"] not in taken]
        got = ev.collect_evidence(fresh, cap, count)
        n_candidates += len(cands)
        reasons.append(got.stopped_reason)
        tokens = 0
        for c in got.chunks:
            taken.add(c["id"])
            chunks.append(dict(c, via=via))
            tokens += count(c["text"])
        used += tokens
        report.append({"part": via, "cap": cap, "n_candidates": len(cands), "n_taken": len(got.chunks),
                       "skipped_taken": len(cands) - len(fresh), "tokens": tokens, "stopped_reason": got.stopped_reason})
    if not chunks:
        reason = "no_candidates" if n_candidates == 0 else "first_exceeds"
    elif any(r in ("budget", "first_exceeds") for r in reasons):
        reason = "budget"
    else:
        reason = "all_fit"
    return ev.Evidence(
        chunks=chunks, used_tokens=used, budget=token_budget, n_candidates=n_candidates, stopped_reason=reason,
        pool_exhausted=(reason == "all_fit" and used < token_budget),
        topic_report={"strategy": strategy, "parts": report, "empty_parts": [p["part"] for p in report if p["n_taken"] == 0]})   # Q9


def gather(question: str, collection_name: str, settings, cfg: dict, strategy: str, original_share=None) -> ev.Evidence:
    """실제 검색을 묶는다. 후보 수와 예산은 설정 그대로(Q6)."""
    from pipeline import query_engine

    def search(q):
        return query_engine.search(q, settings.candidate_pool, collection_name=collection_name)

    return gather_by_topics(question, cfg, search, settings.token_budget, strategy, original_share)
