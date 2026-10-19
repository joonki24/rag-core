"""정답표 로더와 채점(채점표 §2~§4). 기준은 CHANGES.md의 '묶음 ④'(H1~H10).

[쉽게 말하면]
  정답지에는 질문마다 "이 행들이 있어야 답할 수 있다"(필수 근거)가 적혀 있다.
  필수 근거는 "단위"로 묶여 있다(예: 단위 U1 = 정책 4행 + 기능 60행).
  채점은 이렇게 한다.
    - 담은 카드가 덮는 행 범위를 보고, 한 단위의 근거 행 중 50% 이상을 덮었으면 그 단위를 "찾음"으로 센다.
    - 질문 점수 = 찾은 단위 수 ÷ 전체 필수 단위 수 (예: 3/4)
    - 전체 점수 = 질문 점수의 평균
  두 방식 비교(compare)는 같은 질문끼리 점수 차이를 내고, 질문을 다시 뽑는 부트스트랩(10,000번)으로
  95% 구간을 만든다. 구간 아래 끝이 0보다 커야 "낫다"고 말한다.

[이 파일에서 먼저 볼 것]
  score_run : 한 실행(방식 하나)의 채점
  compare   : 두 실행의 차이와 95% 구간

UI, Chroma, 모델을 부르지 않는 순수 함수만 둔다. 점수는 분수(Fraction)로 계산하고 보고할 때만 소수로 바꾼다.
"""
from __future__ import annotations

import datetime
import json
import os
import random
import re
from dataclasses import dataclass
from fractions import Fraction

TYPES = ("사실 확인", "용어 다름", "변경 영향", "모순 찾기", "답변 불가", "부분 답변")      # 팀 정답지의 6개 이름(채점표 변경 5, 묶음 ⑭). gold_format.yaml이 없을 때의 기본값
FORMAT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gold_format.yaml")       # 묶음 ⑲ Q18
HIT_RATIO = Fraction(1, 2)                 # 채점표 §3: 필수 근거 행의 50% 이상
MIN_REQUIRED, MAX_REQUIRED = 1, 8          # 채점표 §2
_QUESTION_KEYS = {"id", "case_id", "type", "text", "request", "answerable", "gold_evidence", "gold_links", "gold_value", "must_not_say", "decision_timeline",
                  "stated_conditions", "slot_rule"}
UNIT_BUNDLE = "unit_bundle"                 # 묶음 ㉒ V10·V11: 채점 단위 안에서 같은 묶음은 모두(AND), 묶음끼리는 하나(OR)
_DECISION_KEYS = {"date", "source", "decision"}
UNCLASSIFIED = "미분류"                      # type이 없는 질문을 유형별 평균에서 모으는 이름(묶음 ⑧ M2)
_EVIDENCE_KEYS = {"file", "sheet", "required", "rows", "cells", "condition", "area", "bundle", "requirement", "unit"}
_CELL = re.compile(r"^([A-Za-z]{1,3})([1-9][0-9]*)(?::([A-Za-z]{1,3})([1-9][0-9]*))?$")


class ScoringError(ValueError):
    """정답표나 채점 입력이 기준(H1~H8)에 맞지 않을 때."""


@dataclass(frozen=True)
class GoldEvidence:
    file: str
    sheet: str
    rows: frozenset
    required: bool
    condition: str | None = None           # 묶음 ⑲: 적용 조건(None이면 조건 기능을 쓰지 않는 정답표)
    area: str | None = None                # 검토 영역(없으면 None)
    bundle: str | None = None              # 대체 근거 묶음 이름(같은 질문 안에서 같은 이름이면 한 칸, 없으면 None)
    requirement: bool | None = None        # 요구사항 여부(열이 없으면 None)
    unit: str | None = None                # 묶음 ㉒: 채점 단위 ID(있으면 이 정답표는 단위·묶음 규칙, 한 칸 = 한 단위)


@dataclass(frozen=True)
class Decision:
    date: str
    source: str
    decision: str


@dataclass(frozen=True)
class Question:
    id: str
    type: str | None
    answerable: bool
    evidence: tuple
    text: str = ""
    must_not_say: tuple = ()
    decision_timeline: tuple = ()          # 묶음 ⑩: 보관만 하고 채점에는 쓰지 않는다
    stated_conditions: tuple | None = None  # 묶음 ⑲: 질문에 명시된 조건(None이면 조건 기능을 쓰지 않는 정답표)


# ---------- 묶음 ⑲ Q18 정답표 형식 설정 ----------
_FORMAT_KEYS = {"types", "unanswerable_type", "group_separator", "condition_separator", "always_word", "columns"}
_COLUMN_KEYS = ("question_conditions", "evidence_condition", "evidence_area", "evidence_bundle", "evidence_requirement")
_DEFAULT_FORMAT = {"types": list(TYPES), "unanswerable_type": "답변 불가", "group_separator": " > ", "condition_separator": ",", "always_word": "항상",
                   "columns": {"question_conditions": "질문에 명시된 조건", "evidence_condition": "적용 조건", "evidence_area": "검토 영역",
                               "evidence_bundle": "대체 근거 묶음", "evidence_requirement": "요구사항 여부"}}


def load_format(path: str | None = None) -> dict:
    """정답표 형식 설정을 읽는다. 파일이 없으면 기본값(기존 6유형), 있는데 어긋나면 멈춘다(조용한 실패 금지)."""
    path = FORMAT_PATH if path is None else path
    if not os.path.exists(path):
        return {**_DEFAULT_FORMAT, "columns": dict(_DEFAULT_FORMAT["columns"])}
    import yaml

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ScoringError(f"{path}: 설정은 키와 값의 목록이어야 합니다")
    extra, missing = set(raw) - _FORMAT_KEYS, _FORMAT_KEYS - set(raw)
    if extra or missing:
        raise ScoringError(f"{path}: 칸이 맞지 않습니다(모르는 칸 {sorted(extra)}, 빠진 칸 {sorted(missing)})")
    types = raw["types"]
    if not isinstance(types, list) or not types or not all(isinstance(t, str) and t.strip() for t in types) or len(set(types)) != len(types):
        raise ScoringError(f"{path}: types는 겹치지 않는 비어 있지 않은 문자열의 목록이어야 합니다")
    for k in ("unanswerable_type", "group_separator", "condition_separator", "always_word"):
        if not isinstance(raw[k], str) or not raw[k].strip():
            raise ScoringError(f"{path}: {k}는 비어 있지 않은 문자열이어야 합니다")
    if raw["unanswerable_type"] not in types:
        raise ScoringError(f"{path}: unanswerable_type({raw['unanswerable_type']!r})이 types에 없습니다")
    cols = raw["columns"]
    if not isinstance(cols, dict) or set(cols) != set(_COLUMN_KEYS) or not all(isinstance(v, str) and v.strip() for v in cols.values()):
        raise ScoringError(f"{path}: columns는 {list(_COLUMN_KEYS)}를 모두 가진 비어 있지 않은 문자열이어야 합니다")
    if len(set(cols.values())) != len(cols):
        raise ScoringError(f"{path}: columns의 열 이름이 서로 겹칩니다")
    return {"types": list(types), "unanswerable_type": raw["unanswerable_type"], "group_separator": raw["group_separator"],
            "condition_separator": raw["condition_separator"], "always_word": raw["always_word"], "columns": dict(cols)}


def type_group(qtype: str | None, fmt: dict | None = None) -> str | None:
    """"신규·복합 > 신규 개념"의 상위 이름 "신규·복합". 하위 표기가 없으면 None(기존 6유형)."""
    sep = (fmt or load_format())["group_separator"]
    return qtype.split(sep, 1)[0] if isinstance(qtype, str) and sep in qtype else None


def _slots(evidence, required) -> list:
    """근거를 칸으로 묶어 근거 번호 목록의 목록으로 돌려준다(묶음 ⑲ Q6). 같은 대체 묶음 이름은 한 칸, 묶음이 없으면 근거마다 한 칸.
    단위 ID가 있으면(묶음 ㉒) 같은 단위가 한 칸이다. required가 True면 필수 근거만, False면 보조만, None이면 모두."""
    slots: dict = {}
    for i, e in enumerate(evidence):
        if required is None or e.required == required:
            key = ("u", e.unit) if e.unit is not None else ("b", e.bundle) if e.bundle else ("e", i)
            slots.setdefault(key, []).append(i)
    return list(slots.values())


def _slot_hit(evidence, slot, ok) -> bool:
    """칸이 적중인가. ok(i)는 근거 i가 적중(또는 도달 가능)인지.
    옛 규칙(묶음 ⑲ Q6): 칸 안의 하나만 맞아도 적중. 단위·묶음 규칙(묶음 ㉒ V10): 단위 안에서 같은 묶음의 근거가 모두 맞아야 그 묶음이 적중하고,
    묶음 중 하나라도 적중하면 단위가 적중한다."""
    if evidence[slot[0]].unit is None:
        return any(ok(i) for i in slot)
    groups: dict = {}
    for i in slot:
        groups.setdefault(evidence[i].bundle, []).append(i)
    return any(all(ok(i) for i in g) for g in groups.values())


def required_slots(evidence) -> list:
    """필수 근거 칸 목록. 칸 = 근거 목록."""
    return [[evidence[i] for i in s] for s in _slots(evidence, True)]


def _uses_phase19(q) -> bool:
    """묶음 ⑲의 새 칸을 하나라도 쓰는 질문인가. 아니면 출력 모양이 이전과 한 글자도 다르지 않아야 한다(Q12·Q13)."""
    return q.stated_conditions is not None or any(e.bundle or e.area or e.requirement is not None or e.unit is not None for e in q.evidence)


def _applies(condition, stated, always) -> bool:
    """Q3: 조건을 쓰지 않는 근거(None)나 '항상', 또는 질문에 명시된 조건이면 적용."""
    return condition is None or condition == always or condition in (stated or ())


# ---------- H1~H3 정답 로더 ----------
def cells_to_rows(cells, where: str) -> frozenset:
    """A1 표기 목록을 행 집합으로(H2). 열은 쓰지 않는다."""
    if not isinstance(cells, list) or not cells:
        raise ScoringError(f"{where}: cells는 비어 있지 않은 목록이어야 합니다")
    rows: set = set()
    for c in cells:
        m = _CELL.match(c.strip()) if isinstance(c, str) else None
        if not m:
            raise ScoringError(f"{where}: 셀 표기가 A1 형식이 아닙니다: {c!r}")
        r1 = int(m.group(2))
        r2 = int(m.group(4)) if m.group(4) else r1
        if r2 < r1:
            r1, r2 = r2, r1
        rows.update(range(r1, r2 + 1))
    return frozenset(rows)


def _rows_from_pair(rows, where: str) -> frozenset:
    ok = (isinstance(rows, list) and len(rows) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in rows))
    if not ok or rows[0] < 1 or rows[1] < rows[0]:
        raise ScoringError(f"{where}: rows는 [시작, 끝] 정수이고 1 이상, 시작 <= 끝이어야 합니다: {rows!r}")
    return frozenset(range(rows[0], rows[1] + 1))


def parse_gold(data, fmt: dict | None = None) -> list:
    fmt = load_format() if fmt is None else fmt                      # 묶음 ⑲ Q18: 허용 유형은 gold_format.yaml에서 읽는다
    if isinstance(data, dict) and "questions" in data:
        data = data["questions"]
    if not isinstance(data, list) or not data:
        raise ScoringError("정답표는 질문 목록이어야 합니다")
    questions, seen = [], set()
    for i, q in enumerate(data):
        if not isinstance(q, dict):
            raise ScoringError(f"질문 #{i + 1}: 객체여야 합니다")
        # M1: 팀 양식(case_id, request)과 기존 양식(id, text) 중 한쪽 이름을 정확히 하나만
        if ("case_id" in q) == ("id" in q):
            raise ScoringError(f"질문 #{i + 1}: case_id 또는 id 중 정확히 하나가 있어야 합니다")
        qid = q.get("case_id", q.get("id"))
        where = f"질문 {qid!r}(#{i + 1})"
        if not isinstance(qid, str) or not qid:
            raise ScoringError(f"{where}: id는 비어 있지 않은 문자열이어야 합니다")
        if qid in seen:
            raise ScoringError(f"{where}: id 중복")
        seen.add(qid)
        extra = set(q) - _QUESTION_KEYS
        if extra:
            raise ScoringError(f"{where}: 모르는 칸 {sorted(extra)}")
        if ("request" in q) == ("text" in q):
            raise ScoringError(f"{where}: request 또는 text 중 정확히 하나가 있어야 합니다")
        text = q.get("request", q.get("text"))
        if not isinstance(text, str) or not text.strip():
            raise ScoringError(f"{where}: 질문 글은 비어 있지 않은 문자열이어야 합니다")
        qtype = q.get("type")                                       # M2: 선택, 있으면 여섯 유형 중 하나
        if qtype is not None and qtype not in fmt["types"]:
            raise ScoringError(f"{where}: type은 {tuple(fmt['types'])} 중 하나여야 합니다: {qtype!r}")
        stated = q.get("stated_conditions")                         # 묶음 ⑲ Q1: 있으면 근거마다 condition도 있어야 한다(아래에서 짝 검사)
        if stated is not None and (not isinstance(stated, list) or len(set(stated)) != len(stated)
                                   or not all(isinstance(x, str) and x.strip() and x == x.strip() for x in stated)):
            raise ScoringError(f"{where}: stated_conditions는 겹치지 않는 문자열의 목록이어야 합니다(앞뒤 공백 없음)")
        if stated and fmt["always_word"] in stated:
            raise ScoringError(f"{where}: 질문에 명시된 조건에 '{fmt['always_word']}'를 쓸 수 없습니다(항상 적용되는 근거는 적용 조건에만 적습니다)")
        rule = q.get("slot_rule")                                   # 묶음 ㉒ V11: 있으면 "unit_bundle"뿐
        if rule is not None and rule != UNIT_BUNDLE:
            raise ScoringError(f"{where}: slot_rule은 {UNIT_BUNDLE!r}만 쓸 수 있습니다: {rule!r}")
        answerable = q.get("answerable", True)                      # M3: 생략하면 true, 불가는 명시한 것만
        if not isinstance(answerable, bool):
            raise ScoringError(f"{where}: answerable은 true/false여야 합니다")
        mns = q.get("must_not_say", [])                             # M4: 보관만 한다
        if not isinstance(mns, list) or not all(isinstance(x, str) and x.strip() for x in mns):
            raise ScoringError(f"{where}: must_not_say는 비어 있지 않은 문자열의 목록이어야 합니다")
        dtl = q.get("decision_timeline", [])                        # P1: 선택 칸, 항목은 date·source·decision 세 칸
        if not isinstance(dtl, list):
            raise ScoringError(f"{where}: decision_timeline은 목록이어야 합니다")
        decisions = []
        for k, d in enumerate(dtl):
            dw = f"{where} decision_timeline #{k + 1}"
            if not isinstance(d, dict):
                raise ScoringError(f"{dw}: 객체여야 합니다")
            extra_d, missing_d = set(d) - _DECISION_KEYS, _DECISION_KEYS - set(d)
            if extra_d or missing_d:
                raise ScoringError(f"{dw}: 칸이 맞지 않습니다(모르는 칸 {sorted(extra_d)}, 빠진 칸 {sorted(missing_d)})")
            if not all(isinstance(d[key], str) and d[key].strip() for key in _DECISION_KEYS):
                raise ScoringError(f"{dw}: date, source, decision은 비어 있지 않은 문자열이어야 합니다")
            try:
                datetime.date.fromisoformat(d["date"])
            except ValueError:
                raise ScoringError(f"{dw}: date는 YYYY-MM-DD 모양이어야 합니다: {d['date']!r}") from None
            decisions.append(Decision(d["date"], d["source"], d["decision"]))
        if not answerable and q.get("gold_evidence"):
            raise ScoringError(f"{where}: answerable이 false인데 gold_evidence가 있습니다")
        evs = []
        for j, e in enumerate(q.get("gold_evidence") or []):
            ew = f"{where} 근거 #{j + 1}"
            if not isinstance(e, dict):
                raise ScoringError(f"{ew}: 객체여야 합니다")
            extra = set(e) - _EVIDENCE_KEYS
            if extra:
                raise ScoringError(f"{ew}: 모르는 칸 {sorted(extra)}")
            for k in ("file", "sheet"):
                if not isinstance(e.get(k), str) or not e[k]:
                    raise ScoringError(f"{ew}: {k}가 필요합니다")
            if not isinstance(e.get("required"), bool):
                raise ScoringError(f"{ew}: required는 true/false여야 합니다")
            if ("rows" in e) == ("cells" in e):
                raise ScoringError(f"{ew}: rows와 cells 중 정확히 하나만 있어야 합니다")
            rows = _rows_from_pair(e["rows"], ew) if "rows" in e else cells_to_rows(e["cells"], ew)
            cond, area, bundle, reqmt, unit = e.get("condition"), e.get("area"), e.get("bundle"), e.get("requirement"), e.get("unit")
            if "condition" in e and (not isinstance(cond, str) or not cond.strip() or cond != cond.strip()):
                raise ScoringError(f"{ew}: condition은 비어 있지 않은 문자열이어야 합니다(조건과 무관하면 '{fmt['always_word']}')")
            for key, v in (("area", area), ("bundle", bundle), ("unit", unit)):
                if v is not None and (not isinstance(v, str) or not v.strip() or v != v.strip()):
                    raise ScoringError(f"{ew}: {key}는 비어 있지 않은 문자열이거나 null이어야 합니다")
            if "requirement" in e and not isinstance(reqmt, bool):
                raise ScoringError(f"{ew}: requirement는 true/false여야 합니다")
            evs.append(GoldEvidence(e["file"], e["sheet"], rows, e["required"], cond, area, bundle, reqmt, unit))
        if rule == UNIT_BUNDLE and evs:                              # 묶음 ㉒ V11: 표시와 열이 어긋나면 옛 규칙으로 조용히 넘어가지 않고 멈춘다
            if any(e.unit is None or e.bundle is None for e in evs):
                raise ScoringError(f"{where}: slot_rule이 {UNIT_BUNDLE}인데 unit이나 bundle이 빈 근거가 있습니다")
        elif any(e.unit is not None for e in evs):
            raise ScoringError(f"{where}: unit이 있는 근거가 있는데 slot_rule이 {UNIT_BUNDLE}로 표시되지 않았습니다")
        _check_phase19(where, stated, evs, fmt)
        n_req = len(required_slots(evs))                            # 묶음 ⑲ Q11: 대체 묶음은 한 칸으로 센다(묶음이 없으면 이전과 같은 근거 수)
        if answerable and not (MIN_REQUIRED <= n_req <= MAX_REQUIRED):
            raise ScoringError(f"{where}: 답변 가능 질문의 필수 근거는 {MIN_REQUIRED}~{MAX_REQUIRED}개여야 합니다(현재 {n_req}개)")
        questions.append(Question(qid, qtype, answerable, tuple(evs), text.strip(), tuple(mns), tuple(decisions),
                                  None if stated is None else tuple(stated)))
    return questions


def _check_phase19(where: str, stated, evs: list, fmt: dict) -> None:
    """묶음 ⑲의 짝·값 검사(Q1, Q2, Q6, Q10). 조건 기능을 쓰지 않는 옛 정답표는 아무것도 하지 않는다."""
    has_cond = [e.condition is not None for e in evs]
    if stated is None and any(has_cond):
        raise ScoringError(f"{where}: 근거에 condition이 있는데 질문에 stated_conditions가 없습니다(짝으로 써야 합니다)")
    if stated is not None and evs and not all(has_cond):
        raise ScoringError(f"{where}: stated_conditions가 있는데 condition이 빈 근거가 있습니다(조건과 무관하면 '{fmt['always_word']}')")
    if stated:
        known = {e.condition for e in evs}
        lost = [c for c in stated if c not in known]
        if lost:
            raise ScoringError(f"{where}: 질문에 명시된 조건 {lost}이 어느 근거의 적용 조건에도 없습니다(오타인지 확인)")
    members: dict = {}
    units: dict = {}
    for e in evs:
        if e.unit is not None:                                       # 묶음 ㉒ V12: 묶음 이름은 단위마다 다시 쓰이므로 단위 안에서 검사한다
            units.setdefault(e.unit, []).append(e)
        elif e.bundle:
            members.setdefault(e.bundle, []).append(e)
    for name, es in members.items():
        if len({e.required for e in es}) > 1 or len({e.condition for e in es}) > 1 or len({e.requirement for e in es}) > 1:
            raise ScoringError(f"{where}: 대체 근거 묶음 '{name}' 안의 근거는 필수/보조, 적용 조건, 요구사항 여부가 모두 같아야 합니다")
    for name, es in units.items():
        if len({e.required for e in es}) > 1 or len({e.condition for e in es}) > 1 or len({e.requirement for e in es}) > 1:
            raise ScoringError(f"{where}: 채점 단위 '{name}' 안의 근거는 필수/보조, 적용 조건, 요구사항 여부가 모두 같아야 합니다")


def load_gold(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return parse_gold(json.load(f))


# ---------- H9 어댑터 연결 ----------
def provided_from_evidence(evidence) -> dict:
    """`Evidence`(evidence.py)를 채점 입력으로. 메타데이터 칸이 없으면 멈춘다."""
    chunks = []
    for c in evidence.chunks:
        m = c.get("metadata") or {}
        missing = [k for k in ("source_file", "source_sheet", "row_start", "row_end") if k not in m]
        if missing:
            raise ScoringError(f"{c.get('id')}: 메타데이터에 {missing}이 없습니다")
        chunks.append({"chunk_id": c["id"], "file": m["source_file"], "sheet": m["source_sheet"],
                       "rows": (m["row_start"], m["row_end"])})
    # M7: 검색 1위 = 거리가 있는 첫 청크(거리가 없는 청크는 검색으로 온 것이 아니므로 1위로 보지 않는다)
    top1 = next(({"chunk_id": c["id"], "distance": c["distance"]} for c in evidence.chunks if c.get("distance") is not None), None)
    return {"chunks": chunks, "used_tokens": evidence.used_tokens, "stopped_reason": evidence.stopped_reason, "top1": top1}


# ---------- H4~H6 채점 ----------
def _provided_rows(chunks) -> dict:
    out: dict = {}
    for c in chunks:
        start, end = c["rows"]
        out.setdefault((c["file"], c["sheet"]), set()).update(range(start, end + 1))
    return out


def _max_score(q: Question, cover: dict, applied: list):
    """R1: 청크가 덮는 행(cover)만으로 낼 수 있는 점수 상한. 정답 행의 덮이는 비율이 50% 미만인 칸은 도달 불가.
    묶음 ⑲ Q19: 점수와 같은 규칙으로 적용되는 필수 칸만 센다(대체 묶음은 안의 하나라도 닿으면 도달 가능)."""
    reach_ok = lambda i: Fraction(len(q.evidence[i].rows & cover.get((q.evidence[i].file, q.evidence[i].sheet), set())), len(q.evidence[i].rows)) >= HIT_RATIO
    reach = [_slot_hit(q.evidence, slot, reach_ok) for slot in applied]          # 묶음 ㉒ V13: 점수와 같은 규칙으로 센다
    return Fraction(sum(1 for r in reach if r), len(reach)) if reach else None


def _pos(q: Question, slot: list) -> list:
    return [{"file": q.evidence[i].file, "sheet": q.evidence[i].sheet, "rows": [min(q.evidence[i].rows), max(q.evidence[i].rows)]} for i in slot]


def score_question(q: Question, run_item: dict, cover: dict | None = None, always: str | None = None) -> dict:
    chunks = run_item.get("chunks", [])
    provided = _provided_rows(chunks)
    req, opt, hit = [], [], []
    for e in q.evidence:
        covered = e.rows & provided.get((e.file, e.sheet), set())
        ratio = Fraction(len(covered), len(e.rows))
        rec = {"file": e.file, "sheet": e.sheet, "rows": sorted(e.rows), "hit": ratio >= HIT_RATIO, "ratio": float(ratio)}
        (req if e.required else opt).append(rec)
        hit.append(ratio >= HIT_RATIO)
    feat = _uses_phase19(q)
    always = (always or load_format()["always_word"]) if feat else None
    slots = _slots(q.evidence, True)
    applied = [s for s in slots if _applies(q.evidence[s[0]].condition, q.stated_conditions, always)]       # Q3
    ok = hit.__getitem__
    score = Fraction(sum(1 for s in applied if _slot_hit(q.evidence, s, ok)), len(applied)) if applied else None  # Q4, Q6, Q7, ㉒ V10
    out = {"id": q.id, "type": q.type, "score": score, "required": req, "optional": opt,
           "chunk_ids": [c["chunk_id"] for c in chunks],
           "used_tokens": run_item.get("used_tokens"), "stopped_reason": run_item.get("stopped_reason"),
           "max_score": _max_score(q, cover, applied) if cover is not None else None}
    if feat:                                                        # Q13: 새 칸을 쓰는 질문에만 필드를 더한다
        name = lambda s: q.evidence[s[0]].unit or q.evidence[s[0]].bundle or f"근거{s[0] + 1}"      # ㉒ V13: 단위 정답표의 칸 이름은 단위 ID
        req_slots = [s for s in _slots(q.evidence, None) if q.evidence[s[0]].requirement is True]
        out["phase19"] = {
            "applied_slots": len(applied), "applied_hits": sum(1 for s in applied if _slot_hit(q.evidence, s, ok)),
            "conditional": [{"slot": name(s), "condition": q.evidence[s[0]].condition, "hit": _slot_hit(q.evidence, s, ok)}
                            for s in slots if s not in applied],                                      # Q5
            "missed": [{"slot": name(s), "area": q.evidence[s[0]].area, "positions": _pos(q, s)}
                       for s in applied if not _slot_hit(q.evidence, s, ok)],                         # Q9: 위치만
            "requirement": {"n": len(req_slots), "found": sum(1 for s in req_slots if _slot_hit(q.evidence, s, ok))}}   # Q17
    return out


def score_run(questions: list, run: dict, chunk_spans: list | None = None) -> dict:
    """run: {질문 id: {"chunks": [...], "used_tokens": .., "stopped_reason": ..}}. 채점 대상은 답변 가능 질문.

    chunk_spans: 이 방식의 청크가 덮는 위치 [{"file", "sheet", "rows"}]. 주면 정답 근거가 범위 밖인 질문을 알린다(R2~R4).
    """
    scored = [q for q in questions if q.answerable]
    missing = [q.id for q in scored if q.id not in run]
    if missing:
        raise ScoringError(f"실행 결과에 없는 질문: {missing[:5]}")
    cover = _provided_rows(chunk_spans) if chunk_spans is not None else None
    details = [score_question(q, run[q.id], cover) for q in scored]
    return _aggregate(details, len(questions) - len(scored))


def _phase19_summary(details: list, scored: list) -> dict:
    """묶음 ⑲의 추가 보고(Q5, Q7, Q8, Q9, Q17). 계산은 코드가 하고 값은 만들어 채우지 않는다."""
    feat = [d for d in details if "phase19" in d]
    n_full = sum(1 for d in scored if d["score"] == 1)
    cond_n = sum(len(d["phase19"]["conditional"]) for d in feat)
    cond_found = sum(1 for d in feat for c in d["phase19"]["conditional"] if c["hit"])
    req_n = sum(d["phase19"]["requirement"]["n"] for d in feat)
    req_found = sum(d["phase19"]["requirement"]["found"] for d in feat)
    by_area: dict = {}
    for d in sorted(feat, key=lambda x: x["id"]):
        for m in d["phase19"]["missed"]:
            by_area.setdefault(m["area"] or "(영역 없음)", []).append({"id": d["id"], "slot": m["slot"], "positions": m["positions"]})
    return {"no_applicable_required": {"n": len(details) - len(scored), "ids": [d["id"] for d in details if d["score"] is None]},   # Q7
            "all_required_found": {"n": len(scored), "n_full": n_full, "share": (n_full / len(scored)) if scored else None},          # Q8
            "conditional_recall": {"n_slots": cond_n, "n_found": cond_found, "recall": (cond_found / cond_n) if cond_n else None,
                                   "by_question": {d["id"]: {"n": len(d["phase19"]["conditional"]),
                                                             "found": sum(1 for c in d["phase19"]["conditional"] if c["hit"])}
                                                   for d in feat if d["phase19"]["conditional"]}},                                    # Q5
            "requirement_recall": {"n_slots": req_n, "n_found": req_found, "recall": (req_found / req_n) if req_n else None,
                                   "n_questions_without_marks": sum(1 for d in feat if d["phase19"]["requirement"]["n"] == 0)},        # Q17
            "missed_by_area": dict(sorted(by_area.items()))}                                                                         # Q9


def _aggregate(details: list, n_unanswerable_excluded: int) -> dict:
    scored = [d for d in details if d["score"] is not None]         # Q7: 적용되는 필수 근거가 없는 질문은 평균에서 뺀다
    by_type: dict = {}
    for d in scored:
        by_type.setdefault(d["type"] or UNCLASSIFIED, []).append(d["score"])
    mean = lambda xs: float(sum(xs) / len(xs) * 100) if xs else None
    coverage, warnings = None, []
    if scored and all(d.get("max_score") is not None for d in scored):          # R3
        none_ids = [d["id"] for d in scored if d["max_score"] == 0]
        part_ids = [d["id"] for d in scored if 0 < d["max_score"] < 1]
        coverage = {"n": len(scored), "n_full": sum(1 for d in scored if d["max_score"] == 1),
                    "n_partial": len(part_ids), "n_unreachable": len(none_ids),
                    "unreachable_ids": none_ids, "partial_ids": part_ids,
                    "score_ceiling": mean([d["max_score"] for d in scored])}
        if part_ids or none_ids:                                                  # R4
            warnings.append(f"정답 근거가 청크 범위 밖인 질문 {len(part_ids) + len(none_ids)}개"
                            f"(상한 0 {len(none_ids)}개, 일부 {len(part_ids)}개), 점수 상한 {coverage['score_ceiling']:.1f}%")
    elif scored:
        warnings.append("근거 범위 점검을 하지 않았습니다(chunk_spans 없음): 정답 근거가 청크에 없어도 점수는 조용히 0이 됩니다")
    out = {"n_scored": len(details), "n_unanswerable_excluded": n_unanswerable_excluded,
           "overall": mean([d["score"] for d in scored]),
           "by_type": {t: {"n": len(v), "score": mean(v)} for t, v in by_type.items()},
           "coverage": coverage, "warnings": warnings,
           "questions": details}
    groups: dict = {}
    fmt = None
    for d in scored:                                                # 신규·복합처럼 "상위 > 하위" 유형은 상위 이름으로도 묶어 통합 점수를 낸다(Q18)
        if isinstance(d["type"], str):
            fmt = fmt or load_format()
            g = type_group(d["type"], fmt)
            if g:
                groups.setdefault(g, []).append(d["score"])
    if groups:
        out["by_group"] = {g: {"n": len(v), "score": mean(v)} for g, v in sorted(groups.items())}
    if any("phase19" in d for d in details):
        out["phase19"] = _phase19_summary(details, scored)
    return out


def spans_from_jsonl(path: str) -> list:
    """청크 파일(chunks.jsonl)에서 위치 목록을 만든다(R2). 스키마 검증은 하지 않고 위치 칸만 읽는다."""
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                src = d["source"]
                a, b = src["rows"]["start"], src["rows"]["end"]      # 스키마의 RowRange 모양({"start", "end"}). 옛 목록 모양은 거부
                out.append({"file": src["file"], "sheet": src["sheet"], "rows": (int(a), int(b))})
            except (ValueError, KeyError, TypeError) as e:
                raise ScoringError(f"{path}: {n}줄에서 source(file, sheet, rows)를 읽지 못했습니다: {e!r}") from None
    return out


def split_by_reachability(result: dict) -> dict:
    """점수 상한이 1인 질문과 그 외 질문으로 나눈 두 부분 결과(R5). 판정은 전체로 하고 이것은 보조다."""
    if result.get("coverage") is None:
        raise ScoringError("근거 범위 점검을 하지 않은 결과라 나눌 수 없습니다(chunk_spans를 주고 다시 채점하세요)")
    full = [d["id"] for d in result["questions"] if d["max_score"] == 1]
    limited = [d["id"] for d in result["questions"] if d["max_score"] != 1]
    return {"full": restrict_result(result, full), "limited": restrict_result(result, limited)}


# ---------- 묶음 ⑨ 여러 문서에 걸친 질문 점검과 나눠 보고(보조, 판정에 쓰지 않음) ----------
def document_span(q: Question) -> int:
    """질문의 문서 수 = 필수 근거의 서로 다른 file 수(N1). 같은 파일의 다른 시트는 한 문서."""
    return len({e.file for e in q.evidence if e.required})


def span_report(questions: list) -> dict:
    scored = [q for q in questions if q.answerable]
    by_span: dict = {}
    by_type: dict = {}
    multi = 0
    for q in scored:
        k = document_span(q)
        by_span[k] = by_span.get(k, 0) + 1
        t = by_type.setdefault(q.type or UNCLASSIFIED, {"n": 0, "multi_n": 0})
        t["n"] += 1
        if k >= 2:
            multi += 1
            t["multi_n"] += 1
    n = len(scored)
    return {"n_scored": n, "by_span": dict(sorted(by_span.items())), "multi_n": multi,
            "multi_share": (multi / n) if n else None, "by_type": by_type}


def restrict_result(result: dict, ids) -> dict:
    """채점 결과에서 주어진 질문 id만 남겨 전체와 유형별 점수를 다시 계산한다(N3)."""
    ids = set(ids)
    have = {d["id"] for d in result["questions"]}
    unknown = sorted(ids - have)
    if unknown:
        raise ScoringError(f"채점 결과에 없는 질문 id: {unknown[:5]}")
    kept = [d for d in result["questions"] if d["id"] in ids]
    return _aggregate(kept, result["n_unanswerable_excluded"])


def split_by_span(result: dict, questions: list) -> dict:
    """한 문서 질문과 여러 문서 질문으로 나눈 두 부분 결과. 각각에 compare를 그대로 쓸 수 있다."""
    span = {q.id: document_span(q) for q in questions if q.answerable}
    single = [i for i, k in span.items() if k < 2]
    multi = [i for i, k in span.items() if k >= 2]
    return {"single": restrict_result(result, single), "multi": restrict_result(result, multi)}


# ---------- 묶음 ⑧ M6 답변 불가 보조 지표(판정에 쓰지 않음) ----------
def _top1_summary(distances: list) -> dict:
    d = sorted(distances)
    n = len(d)
    if not n:
        return {"n": 0, "min": None, "median": None, "max": None}
    median = d[n // 2] if n % 2 else (d[n // 2 - 1] + d[n // 2]) / 2
    return {"n": n, "min": d[0], "median": median, "max": d[-1]}


def unanswerable_report(questions: list, run: dict) -> dict:
    """답변 불가 질문의 검색 1위 청크와 코사인 거리 분포(채점표 §5). 임계값도 합격/불합격도 없다.

    참고로 답변 가능 질문의 1위 거리 요약도 같이 낸다.
    """
    missing = [q.id for q in questions if not q.answerable and q.id not in run]
    if missing:
        raise ScoringError(f"실행 결과에 없는 답변 불가 질문: {missing[:5]}")
    rows, ref, no_results = [], [], 0
    for q in questions:
        if q.id not in run:
            continue
        top1 = run[q.id].get("top1")
        if q.answerable:
            if top1 is not None:
                ref.append(top1["distance"])
            continue
        if top1 is None:
            no_results += 1
            rows.append({"id": q.id, "top1_chunk_id": None, "top1_distance": None, "top1_similarity": None})
        else:
            rows.append({"id": q.id, "top1_chunk_id": top1["chunk_id"], "top1_distance": top1["distance"],
                         "top1_similarity": 1 - top1["distance"]})
    return {"unanswerable": rows, "no_results": no_results,
            "unanswerable_distance": _top1_summary([r["top1_distance"] for r in rows if r["top1_distance"] is not None]),
            "answerable_distance_reference": _top1_summary(ref)}


# ---------- H8 조용한 실패 금지 ----------
def check_gold_against_chunks(questions: list, chunk_spans: list, unread: frozenset = frozenset()) -> None:
    """정답표의 (file, sheet)가 색인한 청크에 하나도 없으면 멈춘다. chunk_spans: [{"file","sheet","rows"}]
    묶음 83 U2: unread(등록한 안 읽는 시트 짝)에 있는 짝은 멈추지 않는다(도달 불가로 점수 상한에 반영). 목록 짝이 청크에 있으면 멈춘다."""
    have = {(c["file"], c["sheet"]) for c in chunk_spans}
    stale = sorted(set(unread) & have)
    if stale:
        raise ScoringError(f"안 읽는 시트로 등록한 짝이 청크에 있습니다(목록이 틀림): {stale[:5]}")
    bad = sorted({(e.file, e.sheet, q.id) for q in questions for e in q.evidence if (e.file, e.sheet) not in have and (e.file, e.sheet) not in unread})
    if bad:
        raise ScoringError(f"정답표의 (file, sheet)가 청크에 없습니다: {bad[:5]}"
                           + (f" 외 {len(bad) - 5}개" if len(bad) > 5 else ""))


def uncovered_rows(questions: list, chunk_spans: list) -> list:
    """정답 행 중 어떤 청크도 덮지 않는 행(실패가 아니라 보고). [(질문 id, file, sheet, [행])]"""
    cover = _provided_rows(chunk_spans)
    out = []
    for q in questions:
        for e in q.evidence:
            miss = sorted(e.rows - cover.get((e.file, e.sheet), set()))
            if miss:
                out.append((q.id, e.file, e.sheet, miss))
    return out


# ---------- H7 비교 ----------
def compare(res_b1: dict, res_b0: dict, seed: int = 0, n_boot: int = 10000) -> dict:
    """두 실행의 점수 차이(res_b1 − res_b0, %p)와 95% 구간.

    같은 질문끼리 짝지어 차이를 구하고(쌍대), 질문 목록을 복원 추출로 n_boot번 다시 뽑아 평균 차이를 모은 뒤
    아래 2.5%·위 97.5% 값을 구간으로 쓴다. 시드가 고정이라 다시 돌려도 같은 값이 나온다.
    table에는 질문별로 어느 쪽이 높았는지(b1_higher / b1_lower / same) 개수가 들어 있다.
    """
    s1 = {d["id"]: d["score"] for d in res_b1["questions"]}
    s0 = {d["id"]: d["score"] for d in res_b0["questions"]}
    if set(s1) != set(s0):
        raise ScoringError(f"두 실행의 채점 대상 질문이 다릅니다: 한쪽에만 {sorted(set(s1) ^ set(s0))[:5]}")
    lone = sorted(i for i in s1 if (s1[i] is None) != (s0[i] is None))
    if lone:
        raise ScoringError(f"한쪽 실행에서만 점수가 없는 질문(적용되는 필수 근거가 다름): {lone[:5]}")
    ids = sorted(i for i in s1 if s1[i] is not None)                # Q7: 점수가 없는 질문은 두 실행 모두에서 뺀다
    if not ids:
        raise ScoringError("비교할 질문이 없습니다(모두 적용되는 필수 근거가 없음)")
    diffs = [float(s1[i] - s0[i]) * 100 for i in ids]
    n = len(diffs)
    table = {"b1_higher": sum(1 for i in ids if s1[i] > s0[i]),
             "b1_lower": sum(1 for i in ids if s1[i] < s0[i]),
             "same": sum(1 for i in ids if s1[i] == s0[i])}
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(n_boot))
    lo, hi = means[int(0.025 * n_boot)], means[min(int(0.975 * n_boot), n_boot - 1)]
    checked = res_b1.get("coverage") is not None and res_b0.get("coverage") is not None          # R6
    out = {"n": n, "diff_pp": sum(diffs) / n, "ci95_pp": (lo, hi), "table": table, "seed": seed, "n_boot": n_boot,
           "coverage_checked": checked,
           "score_ceiling_pp": {"b1": res_b1["coverage"]["score_ceiling"], "b0": res_b0["coverage"]["score_ceiling"]} if checked else None}
    if len(s1) != n:
        out["n_no_applicable"] = len(s1) - n
    return out


def judge(diff_pp: float, threshold_pp: float) -> str:
    """점 추정만 보는 옛 판정 함수. 평가 판정에는 쓰지 않는다(묶음 ㉝ Y5). 판정은 compare_runs.judge_pass(신뢰구간 하한, 채점표 변경 22)가 한다."""
    if diff_pp >= threshold_pp:
        return "통과"
    return "이 설정에서는 확인되지 않음"
