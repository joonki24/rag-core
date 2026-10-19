"""평가 실행기: 질문 -> 검색 -> 근거 모으기 -> 채점을 한 번에 돌려 결과 파일로 남긴다. 기준은 CHANGES.md의 '묶음 ⑰'(R1~R9).

[쉽게 말하면]
  "방식 하나(팔 하나)로 평가를 한 번 돌리는" 프로그램이다. run_evaluation을 위에서 아래로 읽으면 순서 그대로다.
    1. 입력 확인   : 팔 이름, 청크 방식, 실행 목적(dev=개발용 / eval=평가용, eval은 동결 파일 필요)
    2. 정답표 읽기 : 사람이 확인한 정답표만(gold_xlsx_convert.load_confirmed)
    3. 청크 읽기   : adapter.read_chunks, 팔과 청크 방식이 맞는지 확인
    4. 색인 확인   : 서랍 안 카드가 청크 파일과 id·본문까지 같은지(check_index). --reindex면 먼저 새로 색인
    5. 질문마다    : 검색하고 예산까지 담기(run_questions → evidence.gather 또는 link_expansion.gather_with_links)
    6. 채점        : scoring.score_run
    7. 저장        : run_result.json(질문별 카드·점수), run_info.json(설정·지문·시간), run_summary.md(요약)
  팔 이름: b0_r(행 단위), b0_s(500자 조각, B0-RC도 이 칸을 씀), b1_search(B1 검색만), b1_link(B1 검색+연결)

실행기는 통과/미통과를 내지 않는다(판정은 compare_runs.judge_pass가 신뢰구간 하한으로 한다(채점표 변경 22)). 점수를 보고 설정·질문·기준을 바꾸지 않는다.
정답표는 gold_xlsx_convert.load_confirmed로만 읽는다(R1). 청크 본문과 질문 글은 출력하지 않는다(R6, id와 위치만).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
import time
from fractions import Fraction

import adapter
import config
import evidence as ev
import gold_xlsx_convert as gc
import scoring
import settings as settings_mod
import eval_gate
import topic_search

ARMS = ("b0_r", "b0_s", "b1_search", "b1_link")
_ARM_VARIANT = {"b0_r": "B0-R", "b0_s": "B0-S", "b1_search": "B1", "b1_link": "B1"}
CHUNK_BUILDS = {"b0_none": ("b0_r", "b0_s"), "b0_profile": ("b0_r", "b0_s"), "b0_profile_plus": ("b0_r",),
                "b1": ("b1_search", "b1_link")}   # 청크를 만든 방식 -> 쓸 수 있는 팔(R6)
MIN_GROUP = 5                      # 나눈 집단의 질문이 이보다 적으면 평균 없이 개수만 보고한다(R5)
RESULT, SUMMARY, INFO = "run_result.json", "run_summary.md", "run_info.json"


class RunnerError(RuntimeError):
    """입력이 R1~R8 기준에 맞지 않을 때. 해결하지 않고 멈춰서 알린다."""


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: str) -> str:
    with open(path, "rb") as f:
        return _sha_bytes(f.read())


def _plain(obj):
    """JSON으로 쓸 수 있게 분수는 글자("1/2")로 바꾼다."""
    if isinstance(obj, Fraction):
        return f"{obj.numerator}/{obj.denominator}"
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _dump(obj) -> bytes:
    return (json.dumps(_plain(obj), ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


# ---------- R3 색인 확인 ----------
def check_index(collection_name: str, chunk_ids: set, texts: dict | None = None, embedding_model: str | None = None,
                purpose: str = "dev") -> dict:
    """서랍의 청크 수와 id 집합이 입력 청크 파일과 같은지 본다. 다르면 멈추고 --reindex를 안내한다.

    묶음 ㉜ Z2: texts가 있으면 id마다 서랍의 본문도 파일의 본문과 같아야 한다(id만 같고 본문이 다른 서랍을 막는다).
    embedding_model이 있으면 서랍에 적힌 모델 이름과 같아야 한다. 이름이 없는 옛 서랍은 평가용에서 멈추고 개발용에서는 경고한다."""
    from pipeline import embed_store

    try:
        col = embed_store.get_collection(collection_name, create=False)
    except embed_store.CollectionNotFoundError:
        raise RunnerError(f"서랍 '{collection_name}'이 없습니다. --reindex로 색인하세요") from None
    stored = set(col.get(include=[])["ids"])
    if stored != chunk_ids:
        raise RunnerError(f"서랍 '{collection_name}'의 청크 {len(stored)}개가 입력 파일의 {len(chunk_ids)}개와 다릅니다"
                          f"(서랍에만 {len(stored - chunk_ids)}개, 파일에만 {len(chunk_ids - stored)}개). 다시 색인하려면 --reindex")
    if len(chunk_ids) > config.HNSW_EF_SEARCH:                                     # 묶음 ㉝ Y2: 검색 폭이 말뭉치보다 작으면 흔들림(이슈 24)이 돌아온다
        raise RunnerError(f"청크가 {len(chunk_ids)}개로 검색 폭 {config.HNSW_EF_SEARCH}보다 많습니다. 이 상태로는 이슈 24의 흔들림이 돌아올 수 있어 "
                          "멈춥니다. 검색 폭(config.HNSW_EF_SEARCH)을 올리려면 결정을 기록하세요")
    if texts is not None:
        got = col.get(include=["documents"])
        stored_text = dict(zip(got["ids"], got["documents"]))
        different = sorted(i for i in chunk_ids if stored_text.get(i) != texts.get(i))
        if different:
            raise RunnerError(f"서랍 '{collection_name}'의 본문이 입력 청크 파일과 다른 청크가 {len(different)}개 있습니다(예: {different[:3]}). "
                              "id가 같아도 본문이 다르면 다른 입력입니다. 다시 색인하려면 --reindex")
    if embedding_model is not None:
        recorded = (col.metadata or {}).get("embedding_model")
        if recorded is None:
            if purpose == "eval":
                raise RunnerError(f"서랍 '{collection_name}'에는 색인에 쓴 임베딩 모델 이름이 적혀 있지 않습니다. 평가용은 --reindex로 "
                                  "다시 색인해 이름을 남겨야 합니다(Z2)")
            print(f"[경고] 서랍 '{collection_name}'에는 색인에 쓴 임베딩 모델 이름이 적혀 있지 않습니다(옛 서랍). 개발용이라 진행합니다.")
        elif recorded != embedding_model:
            raise RunnerError(f"서랍 '{collection_name}'은 임베딩 모델 {recorded!r}로 색인했는데 지금 설정은 {embedding_model!r}입니다. "
                              "다시 색인하려면 --reindex")
    return {"collection": collection_name, "n_chunks": len(stored)}


# ---------- R5 보고용 계산(계산은 코드가, 5개 미만 집단은 평균을 숨김) ----------
def _full_marks(details: list) -> int:
    return sum(1 for d in details if d["score"] == 1)


def group_view(part: dict) -> dict:
    """나눈 집단의 보고. 질문이 MIN_GROUP개 미만이면 평균(overall)을 내지 않고 개수만 둔다."""
    n = part["n_scored"]
    full = _full_marks(part["questions"])
    if n < MIN_GROUP:
        return {"n": n, "full": full, "overall": None, "note": f"질문 {n}개(<{MIN_GROUP})라 평균을 내지 않고 개수만 보고"}
    return {"n": n, "full": full, "overall": part["overall"], "note": None}


def by_type_view(result: dict) -> dict:
    by_type: dict = {}
    for d in result["questions"]:
        by_type.setdefault(d["type"] or scoring.UNCLASSIFIED, []).append(d)
    out = {}
    for t, all_ds in sorted(by_type.items()):
        ds = [d for d in all_ds if d["score"] is not None]          # 묶음 ⑲ Q7: 적용되는 필수 근거가 없는 질문은 뺀다
        if not ds:
            out[t] = {"n": 0, "full": 0, "score": None, "note": f"점수가 있는 질문이 없음(적용되는 필수 근거 없음 {len(all_ds)}개)"}
            continue
        full = _full_marks(ds)
        mean = float(sum(d["score"] for d in ds) / len(ds) * 100)
        out[t] = {"n": len(ds), "full": full, "score": mean if len(ds) >= MIN_GROUP else None,
                  "note": None if len(ds) >= MIN_GROUP else f"질문 {len(ds)}개(<{MIN_GROUP})라 평균을 내지 않고 개수(full/n)만 보고"}
    return out


def by_group_view(result: dict) -> dict:
    """"상위 > 하위" 유형의 통합 보고(묶음 ⑲ Q18). 상위 묶음의 질문이 MIN_GROUP개 미만이면 평균 없이 개수만 둔다."""
    out = {}
    for g, v in result.get("by_group", {}).items():
        ds = [d for d in result["questions"] if d["score"] is not None and scoring.type_group(d["type"]) == g]
        n, full = len(ds), _full_marks(ds)
        out[g] = {"n": n, "full": full, "score": v["score"] if n >= MIN_GROUP else None,
                  "note": None if n >= MIN_GROUP else f"질문 {n}개(<{MIN_GROUP})라 평균을 내지 않고 개수(full/n)만 보고"}
    return out


def build_report(questions: list, result: dict, run: dict) -> dict:
    spans = scoring.span_report(questions)
    split = scoring.split_by_span(result, questions)
    report = {"by_type": by_type_view(result), "span": spans,
              "single_doc": group_view(split["single"]), "multi_doc": group_view(split["multi"]),
              "unanswerable": scoring.unanswerable_report(questions, run)}
    if "by_group" in result:                                        # 묶음 ⑲: 신규·복합 같은 상위 묶음이 있을 때만 더한다(옛 정답표는 보고가 그대로)
        report["by_group"] = by_group_view(result)
    if "phase19" in result:
        report["phase19"] = result["phase19"]
    if result["coverage"] is not None:
        reach = scoring.split_by_reachability(result)
        report["reachable"] = group_view(reach["full"])
        report["limited"] = group_view(reach["limited"])
    return report


# ---------- R4 팔 실행 ----------
def run_questions(arm: str, questions: list, settings, collection: str, index=None, plan: dict | None = None, div=None) -> tuple[dict, dict]:
    """질문마다 근거를 모아 채점 입력을 만든다. 질문 id 순서로 돌린다(결정성). (run, via)를 돌려준다.

    plan(묶음 ⑳): None이면 지금 방식(original). {"strategy", "topics"(점검표), "original_share"}이면 주제별·결합 검색.
    검색 입력은 질문 글(q.text)과 점검표뿐이다. 정답표의 다른 칸은 이 함수가 검색에 넘기지 않는다(Q2).
    div(묶음 73): 다양화 Context. None이면 지금 방식 그대로.
    """
    import link_expansion

    if getattr(settings, "search_mode", "dense") != "dense" and (arm == "b1_link" or plan is not None):
        raise RunnerError("search_mode hybrid는 original 전략의 연결 없는 팔에서만 쓸 수 있습니다(묶음 ㉘ X4). 연결·주제별 전략과는 쓰지 않습니다")
    if div is not None and plan is not None:
        raise RunnerError("다양화(evidence.diversity)는 original 전략에서만 쓸 수 있습니다(묶음 73)")
    run, via, link_reports, topic_reports, req_reports, label_reports, div_reports = {}, {}, {}, {}, {}, {}, {}
    for q in sorted(questions, key=lambda x: x.id):
        kw = {"div": div} if div is not None else {}                 # 묶음 73: 끈 상태에서는 예전과 같은 모양으로 부른다
        if arm == "b1_link":
            evid = link_expansion.gather_with_links(q.text, collection, settings, index, **kw)
            link_reports[q.id] = evid.link_report
        elif plan is not None:
            evid = topic_search.gather(q.text, collection, settings, plan["topics"], plan["strategy"], plan["original_share"])
            topic_reports[q.id] = evid.topic_report
        else:
            evid = ev.gather(q.text, collection, settings, **kw)
        if getattr(evid, "diversity_report", None) is not None:
            div_reports[q.id] = evid.diversity_report
        run[q.id] = scoring.provided_from_evidence(evid)
        via[q.id] = {c["id"]: c.get("via", "search") for c in evid.chunks}
        if getattr(settings, "doc_priority", None):                  # 묶음 ㊷ G3: 켰을 때만 요구사항 지표를 낸다
            import priority

            req_reports[q.id] = priority.requirement_report(q.evidence, run[q.id]["chunks"], settings.doc_priority)
            if settings.doc_priority.get("label_cards"):                 # 묶음 ㊺ H3·H4: 표시만, 점수에 쓰지 않는다
                label_reports[q.id] = priority.card_labels(evid.chunks, settings.doc_priority)
    extra = {"via": via, "link_reports": link_reports}
    if req_reports:
        extra["req_reports"] = req_reports
    if label_reports:
        extra["label_reports"] = label_reports
    if div_reports:                                                   # 묶음 73: 다양화를 켰을 때만
        extra["diversity_reports"] = div_reports
    if plan is not None:
        extra["topic_reports"] = topic_reports
    return run, extra


# ---------- 출력 ----------
def _result_doc(arm, questions, result, run, extra) -> dict:
    items = []
    for d in result["questions"]:
        d = dict(d, via=extra["via"][d["id"]], score_pct=float(d["score"] * 100) if d["score"] is not None else None)
        if d["id"] in extra["link_reports"]:
            d["link_report"] = extra["link_reports"][d["id"]]
        if d["id"] in extra.get("topic_reports", {}):             # 묶음 ⑳ Q9: 전략이 original이 아닐 때만
            d["topic_report"] = extra["topic_reports"][d["id"]]
        if d["id"] in extra.get("label_reports", {}):             # 묶음 ㊺: label_cards를 켰을 때만
            d["card_labels"] = extra["label_reports"][d["id"]]
        if d["id"] in extra.get("req_reports", {}):               # 묶음 ㊷ G3: 우선순위를 켰을 때만
            d["req_report"] = extra["req_reports"][d["id"]]
        if d["id"] in extra.get("diversity_reports", {}):         # 묶음 73: 다양화를 켰을 때만
            d["diversity_report"] = extra["diversity_reports"][d["id"]]
        items.append(d)
    scored = {d["id"] for d in items}
    for q in sorted(questions, key=lambda x: x.id):             # 답변 불가 질문은 점수 없이 검색 1위만 남긴다
        if q.id not in scored:
            items.append({"id": q.id, "type": q.type, "answerable": False, "chunk_ids": [c["chunk_id"] for c in run[q.id]["chunks"]],
                          "used_tokens": run[q.id]["used_tokens"], "stopped_reason": run[q.id]["stopped_reason"],
                          "top1": run[q.id]["top1"], "via": extra["via"][q.id]})
    items.sort(key=lambda d: d["id"])
    doc = {"arm": arm, "overall": result["overall"], "n_scored": result["n_scored"],
           "n_unanswerable_excluded": result["n_unanswerable_excluded"], "coverage": result["coverage"],
           "warnings": result["warnings"], "questions": items}
    for k in ("by_group", "phase19"):                               # 묶음 ⑲ Q13: 새 정답표 칸을 쓸 때만 필드를 더한다
        if k in result:
            doc[k] = result[k]
    rr = extra.get("req_reports")
    if rr:                                                          # 묶음 ㊷ G3: 요구사항 지표 요약(판정에 쓰지 않음)
        with_ev = [v for v in rr.values() if v["n_req_evidence"] > 0]
        ev_n, hit_n = sum(v["n_req_evidence"] for v in with_ev), sum(v["n_req_hit"] for v in with_ev)
        doc["requirements"] = {
            "n_questions_with_req_evidence": len(with_ev), "n_req_evidence": ev_n, "n_req_hit": hit_n,
            "recall": (hit_n / ev_n) if ev_n else None,
            "n_req_chunks": sum(v["n_req_chunks"] for v in rr.values()),
            "n_contaminated_chunks": sum(v["n_contaminated"] for v in rr.values()),
            "n_questions_with_contamination": sum(1 for v in rr.values() if v["n_contaminated"] > 0),
        }
    return doc


def _pct(v) -> str:
    return "없음" if v is None else f"{v:.1f}%"


def _summary_md(arm, purpose, dataset, chunk_build, result, report) -> str:
    lines = [f"# 평가 실행 요약 — {'개발용 실행' if purpose == 'dev' else '평가용 실행' if purpose == 'eval' else purpose}", "",
             f"- 팔: `{arm}`, 데이터셋: `{dataset}`, 청크를 만든 방식: `{chunk_build}`", "- 이 파일은 통과/미통과를 판정하지 않는다. 판정은 사람이 기준 숫자를 줄 때 한다.",
             "- 이 실행의 점수를 보고 설정·질문·기준을 바꾸지 않는다(재실행은 결과가 같은지 확인용).", "",
             f"## 전체: {_pct(result['overall'])} (채점 질문 {result['n_scored']}개, 답변 불가 {result['n_unanswerable_excluded']}개는 점수에서 뺌)", ""]
    if result["coverage"]:
        c = result["coverage"]
        lines += [f"- 점수 상한 {_pct(c['score_ceiling'])}: 청크가 정답 행을 아예 덮지 못하는 질문 {c['n_unreachable']}개, 일부만 덮는 질문 {c['n_partial']}개"]
    lines += [f"- 경고: {w}" for w in result["warnings"]]
    lines += ["", "## 유형별 (full = 필수 근거를 모두 적중한 질문 수. 이 정의는 사전 등록에 없어 구현 때 해석한 것)", "", "| 유형 | 질문 | full | 평균 |", "|---|---|---|---|"]
    for t, v in report["by_type"].items():
        lines.append(f"| {t} | {v['n']} | {v['full']}/{v['n']} | {_pct(v['score']) if v['score'] is not None else '개수만'} |")
    lines += ["", "## 한 문서 / 여러 문서 질문 (보조, 판정에 쓰지 않음)", ""]
    for name, key in (("한 문서", "single_doc"), ("여러 문서", "multi_doc")):
        v = report[key]
        lines.append(f"- {name}: 질문 {v['n']}개, full {v['full']}/{v['n']}, 평균 {_pct(v['overall']) if v['overall'] is not None else '내지 않음'}" + (f" ({v['note']})" if v["note"] else ""))
    if "reachable" in report:
        lines += ["", "## 점수 상한이 1인 질문 / 그 외 (보조)", ""]
        for name, key in (("상한 1", "reachable"), ("그 외", "limited")):
            v = report[key]
            lines.append(f"- {name}: 질문 {v['n']}개, full {v['full']}/{v['n']}, 평균 {_pct(v['overall']) if v['overall'] is not None else '내지 않음'}")
    u = report["unanswerable"]
    lines += ["", "## 답변 불가 질문 보조 지표 (임계값·합격 기준 없음)", "",
              f"- 답변 불가 질문의 검색 1위 거리: {u['unanswerable_distance']}", f"- 참고, 답변 가능 질문의 1위 거리: {u['answerable_distance_reference']}", ""]
    return "\n".join(lines)


def _gold_state_banner(prof: dict) -> str:
    """읽기 설정으로 변환한 정답표의 상태를 요약 문서 맨 위에 적는다(묶음 ㉒ V24). 값은 기획팀 쪽 상태를 그대로 옮긴다."""
    held = ", ".join(h["id"] for h in prof["held_questions"]) or "없음"
    return ("> **예비 정답표:** 기획팀 쪽 상태 " + json.dumps(prof["source_state"], ensure_ascii=False)
            + ". 이 점수는 통과 기준·천장 판단에 쓰지 않는다. 보류한 질문: " + held + f"(보류 전 {prof['n_questions_before_hold']}문항)")


def _write_outputs(out_dir, files: dict) -> str:
    """run_result.json이 이미 있으면 덮어쓰지 않는다. 바이트까지 같으면 '같음', 다르면 멈춘다(R7)."""
    os.makedirs(out_dir, exist_ok=True)
    existing = os.path.join(out_dir, RESULT)
    status = "새로 씀"
    if os.path.exists(existing):
        with open(existing, "rb") as f:
            old = f.read()
        if old != files[RESULT]:
            raise RunnerError(f"{existing}이 이미 있고 이번 결과와 다릅니다. 덮어쓰지 않았습니다. 입력이나 설정이 바뀐 것이니 새 출력 폴더를 쓰세요")
        status = "이전 결과와 같음"
    for name, data in files.items():
        if name == RESULT and status == "이전 결과와 같음":
            continue
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(data)
    return status


# ---------- 본체 ----------
def run_evaluation(arm: str, settings, gold_dir: str, chunks_path: str, out_dir: str, purpose: str, chunk_build: str,
                   links_path: str | None = None, items_path: str | None = None, reindex: bool = False,
                   strategy: str = "original", topics_path: str | None = None, original_share=None, freeze_path: str | None = None) -> dict:
    """팔 하나로 평가를 한 번 돌리고 결과 파일 세 개를 out_dir에 쓴다(파일 맨 위 설명의 1~7 순서).

    arm         : b0_r | b0_s | b1_search | b1_link
    chunk_build : 청크를 만든 방식(b0_none | b0_profile | b0_profile_plus | b1). 팔과 맞아야 한다
    purpose     : dev(개발용) 또는 eval(평가용, freeze_path의 동결 파일과 입력이 모두 같아야 함)
    links_path, items_path : b1_link일 때만 필요(si-prep의 links.jsonl, items.jsonl)
    reindex     : True면 서랍을 비우고 청크 파일로 다시 색인
    돌려주는 값 : 전체 점수, 채점한 질문 수, 단계별 시간 등
    """
    if purpose == "eval" and strategy != "original":                                  # 묶음 ㉓ E6: 다른 검사보다 먼저 원인을 분명히 알린다
        raise RunnerError("평가용 실행의 검색 전략은 original뿐입니다(E6)")
    try:
        topic_search.check_plan(strategy, topics_path, original_share)                  # Q10: 쓰지 않는 인자는 조용히 무시하지 않는다
    except topic_search.TopicError as e:
        raise RunnerError(str(e)) from None
    if strategy != "original" and arm == "b1_link":
        raise RunnerError("b1_link에는 아직 주제별·결합 전략을 쓸 수 없습니다(B1 연결 확장은 si-prep 새 버전이 온 뒤 같은 코드로 붙임, 묶음 ⑳ 범위 밖)")
    if chunk_build not in CHUNK_BUILDS:
        raise RunnerError(f"청크를 만든 방식(chunk_build)은 {tuple(CHUNK_BUILDS)} 중 하나를 직접 적어야 합니다(기본값 없음): {chunk_build!r}")
    if arm in ARMS and arm not in CHUNK_BUILDS[chunk_build]:
        raise RunnerError(f"청크 방식 {chunk_build}는 팔 {CHUNK_BUILDS[chunk_build]}에만 쓸 수 있는데 팔은 {arm}입니다")
    if purpose not in ("dev", "eval"):
        raise RunnerError("--purpose는 dev 또는 eval이어야 합니다(eval은 동결 파일 필요, 묶음 ㉓)")
    freeze = None
    if purpose == "eval":                                                            # 묶음 ㉓ E1: 동결 파일이 없으면 dev만 가능
        try:
            freeze = eval_gate.load_freeze(freeze_path)
        except eval_gate.EvalGateError as e:
            raise RunnerError(str(e)) from None
        if strategy != "original":
            raise RunnerError("평가용 실행의 검색 전략은 original뿐입니다(E6)")
    elif freeze_path:
        raise RunnerError("--freeze는 --purpose eval에서만 쓸 수 있습니다")
    if not out_dir:
        raise RunnerError("출력 폴더가 필요합니다(기본값 없음)")
    if arm not in ARMS:
        raise RunnerError(f"팔은 {ARMS} 중 하나여야 합니다: {arm!r}")
    stages, t0 = {}, time.perf_counter()

    def stage(name, count=None):
        nonlocal t0
        now = time.perf_counter()
        stages[name] = {"seconds": round(now - t0, 3), "count": count}
        t0 = now

    eval_info = None
    if freeze is not None:                                                         # 묶음 ㉓ E3~E7: 동결 파일과 지금 입력이 모두 같아야 한다
        try:
            eval_info = eval_gate.verify(freeze, arm, settings, gold_dir, chunks_path, links_path, items_path, strategy, out_dir,
                                         chunk_build=chunk_build)
        except eval_gate.EvalGateError as e:
            raise RunnerError(str(e)) from None
    questions = gc.load_confirmed(gold_dir)                                       # R1: 확인된 정답표만
    stage("정답표 읽기", len(questions))
    chunks = adapter.read_chunks(chunks_path)                                     # R1: 스키마 검증 통과한 파일만
    variants = {adapter.variant_of(c.chunk_id) for c in chunks}
    if variants != {_ARM_VARIANT[arm]}:
        raise RunnerError(f"팔 {arm}에는 {_ARM_VARIANT[arm]} 청크가 필요한데 파일에는 {sorted(variants)}가 있습니다")
    chunk_ids = {c.chunk_id for c in chunks}
    stage("청크 읽기", len(chunks))
    collection = adapter.collection_for(_ARM_VARIANT[arm], settings)
    if reindex:
        adapter.ingest_chunks(chunks_path, settings=settings, rebuild=True)
    stage("색인(--reindex일 때만)", len(chunks) if reindex else 0)
    check_index(collection, chunk_ids, {c.chunk_id: c.text for c in chunks}, settings.embedding_model, purpose)       # R3, 묶음 ㉜ Z2
    spans = scoring.spans_from_jsonl(chunks_path)
    scoring.check_gold_against_chunks(questions, spans, settings_mod.unread_pairs(settings))  # R5: 파일·시트가 청크에 하나도 없으면 멈춤(묶음 83: 등록한 짝은 통과)
    index = None
    if arm == "b1_link":
        import link_expansion

        for p in (links_path, items_path):
            if not p or not os.path.isfile(p):
                raise RunnerError("b1_link에는 links.jsonl과 items.jsonl이 필요합니다(없거나 경로가 틀림)")
        index = link_expansion.LinkIndex.build(links_path, items_path, collection, settings)
    from pipeline import embed_store

    try:
        embed_store.embed_texts(["준비 확인"])                                      # R8: 모델을 못 불러오면 여기서 멈춘다
    except Exception as e:
        raise RunnerError(f"임베딩 모델을 불러오지 못했습니다(오프라인, 몰래 내려받지 않음): {e!r}"[:300]) from e
    plan = None
    if strategy != "original":                                                     # 묶음 ⑳: 점검표는 정답표와 별도 파일
        try:
            plan = {"strategy": strategy, "topics": topic_search.load_topics(topics_path, purpose), "original_share": original_share}
            topic_search.split_budget(settings.token_budget, strategy, len(plan["topics"]["topics"]), original_share)   # 몫이 0이면 시작 전에 멈춘다
        except (topic_search.TopicError, OSError) as e:
            raise RunnerError(f"점검표 문제: {e}") from None
    div = None
    if getattr(settings, "diversity", None):                                       # 묶음 73 F1: 켰을 때만. 묶음을 정할 수 없으면 시작 전에 멈춘다
        import diversity

        if plan is not None:
            raise RunnerError("다양화(evidence.diversity)는 original 전략에서만 쓸 수 있습니다(묶음 73)")
        items = None
        if settings.diversity["mode"] == "parent_path":
            if index is not None:
                items = index.item_models
            elif items_path and os.path.isfile(items_path):
                import link_expansion

                items = link_expansion.read_items(items_path)
            else:
                raise RunnerError("다양화 mode parent_path에는 items.jsonl이 필요합니다(--items)")
        try:
            div = diversity.Context(settings.diversity, items)
            for d in adapter.to_documents(chunks)[0]:                              # 모든 청크의 묶음을 미리 정해 본다(실행 중간에 멈추지 않게)
                div.key(d)
        except diversity.DiversityError as e:
            raise RunnerError(str(e)) from None
    stage("점검(색인 일치, 정답 위치, 모델 준비)")
    run, extra = run_questions(arm, questions, settings, collection, index, plan, div)
    stage("검색과 근거 모으기", sum(len(r["chunks"]) for r in run.values()))
    result = scoring.score_run(questions, run, spans)                             # R5: 항상 chunk_spans와 함께
    report = build_report(questions, result, run)
    stage("채점", result["n_scored"])

    snap = gc._read_snapshot(gold_dir)
    info = {"purpose": purpose, "arm": arm, "chunk_build": chunk_build, "dataset": settings.dataset, "collection": collection,
            "run_info": settings_mod.run_info(settings),
            "settings_sha256": _sha_bytes(json.dumps(settings_mod.fingerprint_dict(settings), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")),
            "gold": {k: snap[k] for k in ("gold_file", "gold_sha256", "json_sha256", "status", "confirmed_by", "confirmed_date")},
            "chunks_file": os.path.basename(chunks_path), "chunks_sha256": _sha_file(chunks_path), "n_chunks": len(chunks),
            "stages": stages}
    if plan is not None:                                                           # Q10: original이면 이 칸이 없다(옛 기록과 같은 모양)
        t = plan["topics"]
        info["strategy"] = {"name": strategy, "original_share": original_share, "topics_file": os.path.basename(topics_path),
                            "topics_sha256": t["sha256"], "n_topics": len(t["topics"]), "kind": t["kind"], "author": t["author"],
                            "date": t["date"], "query_template": t["query_template"]}
    if eval_info is not None:                                                      # E8: dev의 run_info는 옛 모양 그대로
        info["eval"] = eval_info
    summary = _summary_md(arm, purpose, settings.dataset, chunk_build, result, report)
    if "profile" in snap:                                                          # 묶음 ㉒ V23·V24: 읽기 설정으로 변환한 정답표만 이 칸이 있다
        prof = snap["profile"]
        info["gold_state"] = {"source_state": prof["source_state"], "held_questions": [h["id"] for h in prof["held_questions"]],
                              "n_questions_before_hold": prof["n_questions_before_hold"]}
        head, _, rest = summary.partition("\n")
        summary = head + "\n\n" + _gold_state_banner(prof) + "\n" + rest
    if plan is not None:
        summary += f"\n## 검색 전략\n\n- `{strategy}`, 점검표 주제 {len(plan['topics']['topics'])}개(지문 `{plan['topics']['sha256'][:12]}`), 원래 질문 비율 {original_share}\n"
    files = {RESULT: _dump(_result_doc(arm, questions, result, run, extra)),
             SUMMARY: summary.encode("utf-8"),
             INFO: _dump(info)}
    status = _write_outputs(out_dir, files)
    if eval_info is not None:                                                      # E7: 등록부에는 추가만 한다
        eval_gate.register(out_dir, {"freeze_id": eval_info["freeze_id"], "arm": arm, "status": status, "commit": eval_info["commit"],
                                     "result_sha256": _sha_bytes(files[RESULT]), "dataset": settings.dataset, "chunk_build": chunk_build})
    return {"overall": result["overall"], "n_scored": result["n_scored"], "status": status, "report": report, "stages": stages,
            "result_sha256": _sha_bytes(files[RESULT])}


# ---------- 명령줄(얇게) ----------
def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="평가 실행기(개발용): 질문 -> 검색 -> 근거 -> 채점")
    ap.add_argument("--arm", required=True, choices=ARMS)
    ap.add_argument("--chunks", required=True, help="청크 jsonl(한 방식만)")
    ap.add_argument("--gold-dir", required=True, help="gold_xlsx_convert가 쓴 폴더(확인됨 상태여야 함)")
    ap.add_argument("--out", required=True, help="출력 폴더(기본값 없음)")
    ap.add_argument("--purpose", required=True, choices=("dev", "eval"), help="dev 또는 eval(eval은 --freeze 필요)")
    ap.add_argument("--freeze", help="평가용 동결 파일(eval_gate.py freeze로 만듦)")
    ap.add_argument("--chunk-build", required=True, choices=tuple(CHUNK_BUILDS), help="청크를 만든 방식(기본값 없음): b0_none, b0_profile, b1")
    ap.add_argument("--settings", default=None)
    ap.add_argument("--links")
    ap.add_argument("--items")
    ap.add_argument("--reindex", action="store_true")
    ap.add_argument("--strategy", default="original", choices=topic_search.STRATEGIES, help="검색 전략(기본 original = 지금 방식)")
    ap.add_argument("--topics", help="점검표 파일(topics, combined에서 필수)")
    ap.add_argument("--original-share", type=float, help="combined에서 원래 질문이 가지는 예산 비율(기본값 없음)")
    a = ap.parse_args(argv)
    os.environ["HF_HUB_OFFLINE"] = "1"                                            # R8: 몰래 내려받지 않는다
    import settings as st

    try:
        s = st.load_settings(a.settings) if a.settings else st.load_settings()
        st.apply_settings(s)
        r = run_evaluation(a.arm, s, a.gold_dir, a.chunks, a.out, a.purpose, a.chunk_build, a.links, a.items, a.reindex,
                           a.strategy, a.topics, a.original_share, a.freeze)
    except (RunnerError, gc.XlsxGoldError, scoring.ScoringError, adapter.ContractError, st.SettingsError) as err:
        print(f"멈춤: {err}", file=sys.stderr)
        return 1
    print(f"[{'개발용' if a.purpose == 'dev' else '평가용'} 실행] 팔 {a.arm}, 채점 질문 {r['n_scored']}개, 전체 {_pct(r['overall'])}, {r['status']}")
    for name, v in r["stages"].items():
        print(f"  {name}: {v['seconds']}초" + (f", {v['count']}건" if v["count"] is not None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
