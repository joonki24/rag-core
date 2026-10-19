"""두 실행 결과(run_result.json)를 질문 단위로 비교한다(읽기 전용, 화면 출력만, 개발용).

기존 `scoring.compare`(질문 단위 쌍대 부트스트랩 95% 신뢰구간, 불일치 표)를 그대로 쓰고 새로 계산하는 것은 없다.
짝이 맞지 않으면 멈춘다: 정답표 지문, 예산, 데이터셋, 청크 방식, 목적이 같아야 한다(다른 조건끼리 비교하면 차이의 원인을 알 수 없다).
첫 번째 실행이 "대상"(예: B0-S 또는 B1), 두 번째가 "기준"(예: B0-R)이다. 판정은 하지 않는다.
rag_core의 기존 파일은 수정하지 않고 import만 한다.

사용(실행 폴더를 직접): python compare_runs.py --target <폴더> --base <폴더> --purpose dev
사용(예산별 묶음): python compare_runs.py --target-root <폴더> --base-root <폴더> --budgets 200 300 --purpose dev
(묶음 폴더는 budget_sweep.py가 만든 `budget_<예산>/` 구조)
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "rag_core"))

import scoring  # noqa: E402

RESULT, INFO = "run_result.json", "run_info.json"


class CompareError(ValueError):
    """두 실행의 짝이 맞지 않을 때."""


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_run(folder: str) -> dict:
    """run_result.json과 run_info.json을 읽어 scoring.compare가 받는 모양과 짝 검사용 값을 돌려준다."""
    res, info = _read(os.path.join(folder, RESULT)), _read(os.path.join(folder, INFO))
    qs = []
    for q in res["questions"]:
        if q.get("answerable") is False:
            continue
        if q["score"] is None:                                  # 묶음 ⑲ Q7: 적용되는 필수 근거가 없는 질문은 점수가 없다(비교에서 뺀다)
            continue
        num, den = q["score"].split("/")
        qs.append({"id": q["id"], "score": Fraction(int(num), int(den)), "n_cards": len(q["chunk_ids"])})
    strat = info.get("strategy") or {"name": "original", "topics_sha256": None}        # 묶음 ⑳: 옛 기록에는 칸이 없으면 original
    return {"arm": res["arm"], "overall": res["overall"], "questions": qs, "coverage": res.get("coverage"),
            "strategy": strat["name"], "topics_sha256": strat["topics_sha256"],
            "freeze_id": (info.get("eval") or {}).get("freeze_id"), "commit": (info.get("eval") or {}).get("commit"),                        # 묶음 ㉓ E9: 평가용 실행만 값이 있다
            "settings_extra": {k: info["run_info"][k] for k in EXTRA_KEYS if info["run_info"].get(k) is not None},   # 묶음 ㉝ Y3
            "doc_priority": info["run_info"].get("doc_priority"),                                              # 묶음 ㊷: 없으면 None(끔)
            "chunk_build": info["chunk_build"],                                              # 묶음 ㉛: 청크 방식은 키가 아니라 팔 조합별로 검사한다
            "key": {"정답표 지문": info["gold"]["json_sha256"], "예산": info["run_info"]["token_budget"], "데이터셋": info["dataset"],
                    "목적": info["purpose"]}}


B0_ARMS, B1_ARMS = ("b0_r", "b0_s"), ("b1_search", "b1_link")
EXTRA_KEYS = ("embedding_model", "candidate_pool", "search_mode")    # 묶음 ㉝ Y3: 양쪽에 있을 때만 같아야 한다
MAIN_QUESTIONS = 30                  # 묶음 ㉜ Z4: 통과 판정은 메인 질문 30개일 때만(채점표 변경 11)


def _check_build(a: dict, b: dict) -> None:
    """청크 방식 검사(묶음 ㉛ P1). 같은 계열(B0끼리, B1끼리)은 같아야 하고, B1 대 B0는 B1이 b1, B0가 b0_profile이어야 한다."""
    a_b1, b_b1 = a["arm"] in B1_ARMS, b["arm"] in B1_ARMS
    if a_b1 != b_b1:
        b1, b0 = (a, b) if a_b1 else (b, a)
        if b1["chunk_build"] != "b1" or b0["chunk_build"] != "b0_profile":
            raise CompareError("B1과 B0를 비교하려면 B1의 청크 방식이 'b1', B0의 청크 방식이 'b0_profile'이어야 합니다"
                               f"(지금 B1 {b1['chunk_build']!r}, B0 {b0['chunk_build']!r}). 프로필 없이 만든 약한 기준선(b0_none)이나 B0+는 판정 비교에 쓰지 않습니다")
    elif a["chunk_build"] != b["chunk_build"]:
        raise CompareError(f"짝이 맞지 않습니다(같은 조건이어야 함): 청크 방식: {a['chunk_build']!r} 대 {b['chunk_build']!r}")


def check_pair(a: dict, b: dict) -> None:
    """짝 검사(묶음 ⑳ Q11). 같은 팔이면 전략이 달라야 하고, 다른 팔이면 전략이 같아야 한다(B0와 B1에 같은 검색 전략을 쓴다는 합의)."""
    diff = [f"{k}: {a['key'][k]!r} 대 {b['key'][k]!r}" for k in a["key"] if a["key"][k] != b["key"][k]]
    if diff:
        raise CompareError("짝이 맞지 않습니다(같은 조건이어야 함): " + "; ".join(diff))
    _check_build(a, b)
    same = [f"{k}: {a['settings_extra'][k]!r} 대 {b['settings_extra'][k]!r}" for k in EXTRA_KEYS
            if k in a["settings_extra"] and k in b["settings_extra"] and a["settings_extra"][k] != b["settings_extra"][k]]
    if same:
        raise CompareError("짝이 맞지 않습니다(같은 조건이어야 함): " + "; ".join(same))
    if a["doc_priority"] != b["doc_priority"]:                                          # 묶음 ㊷: 한쪽만 켜면 우선순위 효과가 섞인다
        raise CompareError("짝이 맞지 않습니다(같은 조건이어야 함): 문서 우선순위 설정이 한쪽에만 있거나 서로 다릅니다")
    if a["arm"] == b["arm"]:
        if a["strategy"] == b["strategy"]:
            raise CompareError(f"두 실행이 같은 팔({a['arm']})이고 같은 전략({a['strategy']})입니다")
    elif a["strategy"] != b["strategy"]:
        raise CompareError(f"다른 팔을 비교하려면 검색 전략이 같아야 합니다: {a['strategy']} 대 {b['strategy']}")
    if a["topics_sha256"] and b["topics_sha256"] and a["topics_sha256"] != b["topics_sha256"]:
        raise CompareError("두 실행의 점검표 파일이 다릅니다(같은 점검표여야 비교할 수 있음)")


def compare_runs(target_dir: str, base_dir: str, seed: int = 0, n_boot: int = 10000, purpose: str = "dev") -> dict:
    a, b = load_run(target_dir), load_run(base_dir)
    check_pair(a, b)                                                                   # 목적이 서로 다르면 여기서 짝이 맞지 않는다고 멈춘다
    for r in (a, b):                                                                   # 묶음 ㉓ E9: 지정한 목적의 실행끼리만 비교한다
        if r["key"]["목적"] != purpose:
            raise CompareError(f"--purpose {purpose}인데 실행의 목적이 {r['key']['목적']}입니다(dev와 eval은 섞지 않음)")
    if purpose == "eval" and (not a["freeze_id"] or a["freeze_id"] != b["freeze_id"]):
        raise CompareError(f"평가용 실행은 같은 동결 id끼리만 비교합니다: {a['freeze_id']!r} 대 {b['freeze_id']!r}")
    unknown = [f"{k}(기록 없음: {', '.join(n for n, r in (('대상', a), ('기준', b)) if k not in r['settings_extra'])})"
               for k in EXTRA_KEYS if k not in a["settings_extra"] or k not in b["settings_extra"]]
    c = scoring.compare(a, b, seed=seed, n_boot=n_boot)         # 대상 대 기준(함수 안의 b1은 '대상', b0는 '기준'을 뜻한다)
    cards = lambda r: statistics.median(q["n_cards"] for q in r["questions"])
    return {"target_arm": a["arm"], "base_arm": b["arm"], "target_strategy": a["strategy"], "base_strategy": b["strategy"],
            "budget": a["key"]["예산"], "dataset": a["key"]["데이터셋"],
            "target_overall": a["overall"], "base_overall": b["overall"], "n": c["n"], "diff_pp": c["diff_pp"], "ci95_pp": c["ci95_pp"],
            "higher": c["table"]["b1_higher"], "lower": c["table"]["b1_lower"], "same": c["table"]["same"],
            "ceilings": c["score_ceiling_pp"], "cards_target": cards(a), "cards_base": cards(b), "warnings": unknown,
            "commit": a["commit"], "base_commit": b["commit"]}                          # 묶음 ㊳ X1: 판정 코드 동결 확인에 쓸 동결 커밋


# 채점표 변경 11(2026-10-10)과 변경 15(2026-10-11, 예산 4,000에서 규칙 ⌈반폭 + 3⌉로 다시 계산, B1 점수를 보기 전에 고정):
# 점 추정 +14%p 이상 그리고 95% 신뢰구간 하한이 0보다 큼. 결과를 보고 바꾸지 않는다.
# 2026-10-14 채점표 변경 22(묶음 77, 결과를 본 뒤의 개정, 이후 평가부터): 통과 = 신뢰구간 하한 > 0, +14%p 이상은 "서비스 출시 목표 달성" 표시.
PASS_MIN_PP = 14.0                   # 채점표 변경 23: 서비스 출시 목표(+14%p, 데이터와 무관하게 고정). 묶음 77(변경 22)부터 통과 조건이 아니라 "서비스 출시 목표 달성" 표시에만 쓴다


def judge_scope(r: dict) -> tuple:
    """묶음 ㉜ Z4: 통과 판정을 낼 수 있는 짝인지. (판정하는가, 사유). 뒤집힌 짝(대상 B0, 기준 B1)은 멈춘다."""
    t, b = r["target_arm"], r["base_arm"]
    if t in B0_ARMS and b in B1_ARMS:
        raise CompareError(f"대상과 기준이 뒤집힌 짝입니다(대상 {t}, 기준 {b}). 대상은 B1, 기준은 B0여야 합니다")
    if (t, b) != ("b1_link", "b0_r"):
        return False, f"대상 {t} 대 기준 {b}: 통과 판정은 b1_link(검색+연결) 대 b0_r만 한다"
    if r["n"] != MAIN_QUESTIONS:
        return False, f"비교한 질문이 {r['n']}개다(주 평가는 {MAIN_QUESTIONS}개)"
    return True, ""


def _git_out(*args: str) -> str:
    import subprocess

    r = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(__file__)), *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise CompareError(f"git {' '.join(args)} 실패(판정 코드가 동결 뒤 바뀌었는지 확인할 수 없어 멈춥니다): {r.stderr.strip()[:200]}")
    return r.stdout


def check_judge_code(commit: str) -> None:
    """묶음 ㉜ Z5: 이 파일이 동결 커밋 이후 바뀌지 않았고 커밋하지 않은 변경도 없을 때만 판정한다."""
    name = os.path.basename(os.path.abspath(__file__))
    if not commit:
        raise CompareError("실행 기록에 동결 커밋이 없어 판정 코드가 바뀌었는지 확인할 수 없습니다")
    if _git_out("status", "--porcelain", "--", name).strip():
        raise CompareError(f"{name}에 커밋하지 않은 변경이 있습니다. 평가용 판정에는 동결한 코드만 씁니다(Z5)")
    if _git_out("diff", "--name-only", commit, "--", name).strip():
        raise CompareError(f"{name}가 동결 커밋 {commit[:12]} 이후 바뀌었습니다. 판정 기준 코드를 바꾸면 새 평가입니다(Z5)")


def judge_commit(r: dict) -> str:
    """묶음 ㊳ X1: 대상과 기준 실행의 동결 커밋이 모두 있고 같아야 한다. 아니면 멈춘다(이슈 33)."""
    c, bc = r.get("commit"), r.get("base_commit")
    if not c or not bc:
        raise CompareError("실행 기록에 동결 커밋이 없어 판정 코드가 바뀌었는지 확인할 수 없습니다")
    if c != bc:
        raise CompareError(f"두 실행의 동결 커밋이 다릅니다({c[:12]} 대 {bc[:12]}). 같은 동결의 실행끼리만 판정합니다")
    return c


def judge_pass(r: dict) -> dict:
    """compare_runs의 결과 한 줄에 통과 기준을 적용한다(묶음 77, 채점표 변경 22 기준 B).
    통과 = 95% 신뢰구간 하한 > 0. 통과이면서 점 추정이 +PASS_MIN_PP 이상이면 "서비스 출시 목표 달성"으로 표시한다."""
    point = r["diff_pp"] >= PASS_MIN_PP
    lower = r["ci95_pp"][0] > 0
    big = bool(lower and point)
    return {"pass": bool(lower), "point_ok": bool(point), "lower_ok": bool(lower), "big": big,
            "text": f"{'통과' if lower else '미통과'} (신뢰구간 하한 {r['ci95_pp'][0]:+.2f}%p {'>' if lower else '<='} 0, "
                    f"점 추정 {r['diff_pp']:+.2f}%p" + (f", 서비스 출시 목표 달성(+{PASS_MIN_PP:g}%p 이상)" if big else "") + ")"}


def format_row(r: dict) -> str:
    lo, hi = r["ci95_pp"]
    verdict = "신뢰구간이 0을 포함(차이 확인되지 않음)" if lo <= 0 <= hi else "신뢰구간이 0을 포함하지 않음"
    ceil = f", 점수 상한 대상 {r['ceilings']['b1']:.1f} 기준 {r['ceilings']['b0']:.1f}" if r["ceilings"] else ", 범위 점검 없음"
    return (f"{r['budget']} | {r['target_overall']:.2f} | {r['base_overall']:.2f} | {r['diff_pp']:+.2f} | [{lo:+.2f}, {hi:+.2f}] | "
            f"{r['higher']}/{r['lower']}/{r['same']} | {r['cards_target']:g} / {r['cards_base']:g} | {verdict}{ceil}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--target"), ap.add_argument("--base")
    ap.add_argument("--target-root"), ap.add_argument("--base-root")
    ap.add_argument("--budgets", nargs="+", type=int)
    ap.add_argument("--purpose", required=True, help="dev 또는 eval(eval은 같은 동결 id의 평가용 실행끼리만)")
    a = ap.parse_args(argv)
    if a.purpose not in ("dev", "eval"):
        print("멈춤: --purpose는 dev 또는 eval만 쓸 수 있습니다", file=sys.stderr)
        return 1
    try:
        if a.target and a.base:
            pairs = [(a.target, a.base)]
        elif a.target_root and a.base_root and a.budgets:
            pairs = [(os.path.join(a.target_root, f"budget_{b}"), os.path.join(a.base_root, f"budget_{b}")) for b in a.budgets]
        else:
            print("멈춤: --target과 --base, 또는 --target-root와 --base-root와 --budgets가 필요합니다", file=sys.stderr)
            return 1
        rows = [compare_runs(t, b, purpose=a.purpose) for t, b in pairs]
    except (CompareError, scoring.ScoringError, FileNotFoundError, KeyError) as err:
        print(f"멈춤: {err}", file=sys.stderr)
        return 1
    print(f"[{'개발용' if a.purpose == 'dev' else '평가용'} 비교] 대상 {rows[0]['target_arm']} 대 기준 {rows[0]['base_arm']}, 데이터셋 {rows[0]['dataset']}, 질문 {rows[0]['n']}개, 쌍대 부트스트랩 시드 0 · 10,000회")
    print("예산 | 대상 점수 | 기준 점수 | 차이(%p) | 95% 신뢰구간(%p) | 대상이 높음/낮음/같음 | 질문당 카드(중앙) 대상 / 기준 | 읽는 법")
    for r in rows:
        print(format_row(r))
        if r["warnings"]:                                                              # 묶음 ㉝ Y3: 옛 기록은 막지 않고 알린다
            print(f"[경고] 같은 조건인지 확인하지 못한 값: {'; '.join(r['warnings'])}")
    if a.purpose == "eval":                                                            # 통과 판정은 평가용에서만, 계산은 코드가 한다
        try:
            for r in rows:
                judged, why = judge_scope(r)
                if judged:
                    check_judge_code(judge_commit(r))
        except CompareError as err:
            print(f"멈춤: {err}", file=sys.stderr)
            return 1
        for r in rows:
            judged, why = judge_scope(r)
            if judged:
                print(f"통과 판정(채점표 변경 22, 기준 B): {judge_pass(r)['text']}")
            else:
                print(f"보조 비교: 판정하지 않음({why})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
