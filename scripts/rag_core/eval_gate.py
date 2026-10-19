"""평가용(eval) 실행의 동결 파일과 검사(묶음 ㉓, 기준은 CHANGES.md의 E1~E12).

개발용(dev)은 자유롭게 돌리고, 평가용은 입력과 코드를 동결한 뒤 한 번만 돌린다. 이 모듈은 UI와 모델을 부르지 않는다.
  freeze  동결 파일을 만든다(입력 지문, 설정 지문, 커밋 해시)
  verify  지금 입력이 동결 파일과 모두 같은지 검사한다(runner가 부른다)
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import subprocess
import sys

FREEZE_VERSION = 2          # 묶음 ㉜ Z3: 2부터 팔별 청크 방식(chunk_builds)을 기록한다. 1은 옛 동결(검사 대상 아님)
REGISTER = "eval_register.jsonl"


class EvalGateError(ValueError):
    """평가용 실행의 규칙(E1~E9)을 어겼을 때."""


def sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def settings_sha(settings) -> str:
    """설정 객체의 지문. eval_runner가 run_info에 남기는 settings_sha256과 같은 방식이다."""
    import settings as settings_mod

    return hashlib.sha256(json.dumps(settings_mod.fingerprint_dict(settings), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def git_state(repo_dir: str) -> dict:
    """repo_dir 폴더의 커밋 해시와 커밋하지 않은 변경 목록. git을 못 쓰면 멈춘다(조용히 넘어가지 않음)."""
    def run(*args):
        r = subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            raise EvalGateError(f"git {' '.join(args)} 실패: {r.stderr.strip()[:200]}")
        return r.stdout
    commit = run("rev-parse", "HEAD").strip()
    dirty = [ln for ln in run("status", "--porcelain", "--untracked-files=no", "--", ".").splitlines() if ln.strip()]
    return {"commit": commit, "dirty": dirty}


def _gold_json_sha(gold_dir: str) -> str:
    import gold_xlsx_convert as gc

    snap = gc._read_snapshot(gold_dir)
    if snap is None or snap.get("status") != gc.CONFIRMED:
        raise EvalGateError(f"{gold_dir}: 확인된 정답표가 아닙니다(평가용은 확인된 정답표만)")
    return snap["json_sha256"]


def make_freeze(out_path: str, freeze_id: str, reason: str, settings, gold_dir: str, chunks: dict, links: str | None = None,
                items: str | None = None, repo_dir: str | None = None, strategy: str = "original", today: str | None = None,
                chunk_builds: dict | None = None) -> dict:
    """동결 파일을 쓴다. chunks: {팔 이름: 청크 파일 경로}. 이미 있으면 덮어쓰지 않는다."""
    if not freeze_id or not str(reason).strip():
        raise EvalGateError("동결 이름(id)과 사유가 필요합니다")
    if os.path.sep in freeze_id or "/" in freeze_id or freeze_id in (".", ".."):
        raise EvalGateError("동결 이름에는 경로 구분 글자를 쓸 수 없습니다")
    if strategy != "original":
        raise EvalGateError("평가용 실행의 검색 전략은 original뿐입니다(E6)")
    if not chunks:
        raise EvalGateError("팔별 청크 파일이 하나는 있어야 합니다")
    if "b1_link" in chunks and (not links or not items):                              # 묶음 ㉜ Z1
        raise EvalGateError("b1_link 팔을 동결하려면 links와 items 파일이 모두 필요합니다(Z1)")
    if not chunk_builds or set(chunk_builds) != set(chunks):                           # 묶음 ㉜ Z3
        raise EvalGateError("팔마다 청크 방식(chunk_builds)을 빠짐없이 적어야 합니다(Z3)")
    if os.path.exists(out_path):
        raise EvalGateError(f"{out_path}가 이미 있습니다. 동결 파일은 덮어쓰지 않습니다(바꾸려면 새 이름으로 새 평가)")
    g = git_state(repo_dir or os.path.dirname(os.path.abspath(__file__)))
    if g["dirty"]:
        raise EvalGateError(f"rag_core에 커밋하지 않은 변경이 있어 동결할 수 없습니다(E4): {g['dirty'][:5]}")
    fz = {"freeze_version": FREEZE_VERSION, "id": freeze_id, "date": today or datetime.date.today().isoformat(), "reason": str(reason).strip(),
          "commit": g["commit"], "settings_sha256": settings_sha(settings), "token_budget": settings.token_budget,
          "candidate_pool": settings.candidate_pool, "strategy": strategy, "gold_json_sha256": _gold_json_sha(gold_dir),
          "chunks_sha256": {arm: sha_file(p) for arm, p in sorted(chunks.items())},
          "links_sha256": sha_file(links) if links else None, "items_sha256": sha_file(items) if items else None,
          "chunk_builds": dict(sorted(chunk_builds.items()))}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(fz, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    return fz


def load_freeze(path: str) -> dict:
    if not path or not os.path.isfile(path):
        raise EvalGateError("평가용(eval) 실행에는 동결 파일(--freeze)이 필요합니다. freeze 없이는 dev만 쓸 수 있습니다(E1)")
    with open(path, encoding="utf-8") as f:
        fz = json.load(f)
    need = {"freeze_version", "id", "commit", "settings_sha256", "gold_json_sha256", "chunks_sha256", "strategy"}
    if not need <= set(fz):
        raise EvalGateError(f"{path}: 동결 파일에 칸이 모자랍니다: {sorted(need - set(fz))}")
    if fz["freeze_version"] >= 2 and not isinstance(fz.get("chunk_builds"), dict):
        raise EvalGateError(f"{path}: 동결 파일 버전 2에는 chunk_builds가 있어야 합니다(Z3)")
    fz["_sha256"] = sha_file(path)
    return fz


def verify(fz: dict, arm: str, settings, gold_dir: str, chunks_path: str, links_path: str | None, items_path: str | None,
           strategy: str, out_dir: str, repo_dir: str | None = None, chunk_build: str | None = None) -> dict:
    """현재 입력이 동결 파일과 모두 같은지 본다(E3~E7). 다르면 무엇이 다른지 모두 나열해 멈춘다."""
    bad = []
    if strategy != "original" or fz["strategy"] != "original":
        bad.append(f"검색 전략은 original뿐입니다(E6): 지금 {strategy!r}, 동결 {fz['strategy']!r}")
    g = git_state(repo_dir or os.path.dirname(os.path.abspath(__file__)))
    if g["dirty"]:
        bad.append(f"rag_core에 커밋하지 않은 변경이 있습니다(E4): {g['dirty'][:5]}")
    if g["commit"] != fz["commit"]:
        bad.append(f"커밋이 다릅니다: 지금 {g['commit'][:12]} 동결 {fz['commit'][:12]}")
    if settings_sha(settings) != fz["settings_sha256"]:
        bad.append("설정(예산, 후보 수, 임베딩 등)이 동결 때와 다릅니다")
    try:
        if _gold_json_sha(gold_dir) != fz["gold_json_sha256"]:
            bad.append("정답표 JSON이 동결 때와 다릅니다")
    except EvalGateError as e:
        bad.append(str(e))
    want = fz["chunks_sha256"].get(arm)
    if want is None:
        bad.append(f"동결 파일에 팔 {arm}의 청크가 없습니다(동결한 팔: {sorted(fz['chunks_sha256'])})")
    elif sha_file(chunks_path) != want:
        bad.append(f"팔 {arm}의 청크 파일이 동결 때와 다릅니다")
    if fz.get("freeze_version", 1) >= 2:                                               # 묶음 ㉜ Z3: 청크 방식 이름표도 동결한다
        want_build = (fz.get("chunk_builds") or {}).get(arm)
        if want_build is None:
            bad.append(f"동결 파일에 팔 {arm}의 청크 방식이 없습니다(동결한 팔: {sorted(fz.get('chunk_builds') or {})})")
        elif chunk_build != want_build:
            bad.append(f"청크 방식이 동결 때와 다릅니다: 지금 {chunk_build!r}, 동결 {want_build!r}")
    if arm == "b1_link":                                                               # 묶음 ㉜ Z1: 연결 팔만 links·items를 쓴다
        for label, path, key in (("links.jsonl", links_path, "links_sha256"), ("items.jsonl", items_path, "items_sha256")):
            if not fz.get(key):
                bad.append(f"동결 파일에 {label} 지문이 없어 b1_link를 평가용으로 돌릴 수 없습니다(새로 동결해야 함)")
            elif not path or sha_file(path) != fz[key]:
                bad.append(f"{label}가 동결 때와 다르거나 없습니다")
    norm = os.path.normpath(out_dir).split(os.path.sep)
    if len(norm) < 2 or norm[-1] != arm or norm[-2] != fz["id"]:
        bad.append(f"결과 위치는 <출력 루트>/{fz['id']}/{arm}/ 이어야 합니다(E7): {out_dir}")
    if bad:
        raise EvalGateError("동결 파일과 맞지 않아 평가용 실행을 멈춥니다:\n- " + "\n- ".join(bad))
    return {"freeze_id": fz["id"], "freeze_sha256": fz["_sha256"], "commit": g["commit"]}


def register(out_dir: str, record: dict) -> None:
    """출력 루트(<동결 id>의 부모)의 등록부에 한 줄을 추가한다(추가만)."""
    root = os.path.dirname(os.path.dirname(os.path.normpath(out_dir)))
    with open(os.path.join(root, REGISTER), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _main(argv):
    import argparse

    import settings as st

    ap = argparse.ArgumentParser(description="평가용 동결 파일 만들기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--id", required=True)
    f.add_argument("--reason", required=True)
    f.add_argument("--settings", required=True)
    f.add_argument("--gold-dir", required=True)
    f.add_argument("--chunks", nargs="+", required=True, help="팔=청크파일 (예: b0_r=C:/.../b0_r_chunks.jsonl)")
    f.add_argument("--builds", nargs="+", required=True, help="팔=청크 방식 (예: b0_r=b0_profile b1_link=b1), --chunks의 팔과 같아야 함")
    f.add_argument("--links")
    f.add_argument("--items")
    f.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        chunks = dict(x.split("=", 1) for x in a.chunks)
        builds = dict(x.split("=", 1) for x in a.builds)
        s = st.load_settings(a.settings)
        fz = make_freeze(a.out, a.id, a.reason, s, a.gold_dir, chunks, a.links, a.items, chunk_builds=builds)
    except (EvalGateError, st.SettingsError, ValueError) as err:
        print(f"멈춤: {err}", file=sys.stderr)
        return 1
    print(f"동결: {a.out} (id {fz['id']}, 커밋 {fz['commit'][:12]}, 예산 {fz['token_budget']}, 팔 {sorted(fz['chunks_sha256'])})")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
