"""평가 실험 설정(experiment.yaml)을 읽고 채점표 고정값을 어기면 멈춘다.

채점표 §0, §1, §6 근거. 설정 값을 코드 곳곳에 흩어 두지 않고 이 파일 하나로 모은다.
"""
from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass
from importlib import metadata

import yaml

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiment.yaml")

_SECTIONS = {
    "embedding": {"backend", "model"},
    "retrieval": {"distance", "candidate_pool", "rerank", "query_decomposition"},
    "evidence": {"token_budget", "tokenizer"},
    "generation": {"temperature", "seed"},
    "storage": {"chroma_dir"},
    "links": {"search_share", "grades", "hops", "group_top_n"},
}
_OPTIONAL = {"retrieval": {"search_mode"}, "evidence": {"doc_priority", "diversity", "unread_sheets"}, "links": {"link_order", "allow_inferred"}}   # 선택 칸: search_mode(㉘ X3, 없으면 dense), doc_priority(㊷, 없으면 끔), diversity·allow_inferred(묶음 73, 없으면 지금 동작)
_DIV_MODES = ("parent_path", "row_block")                 # 묶음 73 F1: B1은 부모 경로, B0 대조군은 고정 행 칸
_SEARCH_MODES = ("dense", "hybrid")
_COLLECTION_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,510}[a-zA-Z0-9]$")  # Chroma 규칙: 3~512자
_DATASET = re.compile(r"^[a-z0-9][a-z0-9_]*$")          # 묶음 ⑰ R2: 소문자·숫자·밑줄, 서랍 이름의 접두가 된다
_VARIANT_KEYS = ("b1", "b0_r", "b0_s")


class SettingsError(ValueError):
    """설정이 채점표의 고정값을 어기거나 형식이 틀렸다."""


@dataclass(frozen=True)
class Settings:
    embedding_backend: str
    embedding_model: str
    distance: str
    candidate_pool: int
    token_budget: int
    tokenizer: str
    temperature: float
    seed: int
    collections: dict            # 설정의 dataset에서 만든 `{dataset}_{방식}`(R2). 직접 적지 않는다
    chroma_dir: str
    links: dict = None
    dataset: str = ""
    search_mode: str = "dense"      # 묶음 ㉘ X3: dense | hybrid
    doc_priority: dict | None = None  # 묶음 ㊷: 문서 우선순위와 요구사항 먼저 담기(None이면 끔, 기존 동작과 같음)
    diversity: dict | None = None     # 묶음 73 F1: 다양화 담기(None이면 끔, 기존 동작과 같음)
    unread_sheets: list | None = None  # 묶음 83 U1: 등록한 안 읽는 시트 [{file, sheet, reason}](None이면 지금 동작과 같음)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def load_settings(path: str = DEFAULT_PATH) -> Settings:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise SettingsError(f"{path}: 최상위가 매핑이어야 합니다")

    # S1: 빠진 칸이나 모르는 칸이 있으면 오류(dataset은 칸 이름 하나, 나머지는 묶음)
    extra, missing = set(raw) - set(_SECTIONS) - {"dataset"}, (set(_SECTIONS) | {"dataset"}) - set(raw)
    if extra or missing:
        raise SettingsError(f"최상위 칸 불일치: 모르는 칸 {sorted(extra)}, 빠진 칸 {sorted(missing)}")
    for name, keys in _SECTIONS.items():
        sec = raw[name]
        if not isinstance(sec, dict):
            raise SettingsError(f"'{name}'은 매핑이어야 합니다")
        extra, missing = set(sec) - keys - _OPTIONAL.get(name, set()), keys - set(sec)
        if extra or missing:
            raise SettingsError(f"'{name}' 칸 불일치: 모르는 칸 {sorted(extra)}, 빠진 칸 {sorted(missing)}")

    emb, ret, evi, gen, sto, lnk = (raw[k] for k in ("embedding", "retrieval", "evidence", "generation", "storage", "links"))

    # S2: 채점표 고정값
    if emb["backend"] != "local":
        raise SettingsError("embedding.backend는 local만 허용합니다(마스킹본은 외부로 보내지 않음)")
    if not isinstance(emb["model"], str) or not emb["model"].strip():
        raise SettingsError("embedding.model이 비어 있습니다")
    if ret["distance"] != "cosine":
        raise SettingsError("retrieval.distance는 cosine이어야 합니다(채점표 §1)")
    if not _is_int(ret["candidate_pool"]) or ret["candidate_pool"] < 1:
        raise SettingsError("retrieval.candidate_pool은 1 이상의 정수여야 합니다")
    mode = ret.get("search_mode", "dense")
    if mode not in _SEARCH_MODES:
        raise SettingsError(f"retrieval.search_mode는 {list(_SEARCH_MODES)} 중 하나여야 합니다: {mode!r}")
    if ret["rerank"] is not False or ret["query_decomposition"] is not False:
        raise SettingsError("retrieval.rerank와 query_decomposition은 false여야 합니다(메인 비교에서 끔)")
    if not _is_int(evi["token_budget"]) or evi["token_budget"] <= 0:
        raise SettingsError("evidence.token_budget은 양의 정수여야 합니다")
    if evi["tokenizer"] != "cl100k_base":
        raise SettingsError("evidence.tokenizer는 cl100k_base여야 합니다(채점표 §0 ②)")
    if isinstance(gen["temperature"], bool) or gen["temperature"] != 0:
        raise SettingsError("generation.temperature는 0이어야 합니다(채점표 §0 ⑤)")
    if not _is_int(gen["seed"]):
        raise SettingsError("generation.seed는 정수여야 합니다")
    dataset = raw["dataset"]                                      # R2: 데이터셋 이름. 다르면 서랍이 달라 섞이지 않는다
    if not isinstance(dataset, str) or not _DATASET.match(dataset):
        raise SettingsError(f"dataset은 소문자·숫자·밑줄로 된 이름이어야 합니다(예: shop, masked): {dataset!r}")
    col = {k: f"{dataset}_{k}" for k in _VARIANT_KEYS}
    for n in col.values():
        if not _COLLECTION_NAME.match(n):
            raise SettingsError(f"dataset '{dataset}'로 만든 서랍 이름 '{n}'이 Chroma 규칙(3글자 이상)에 맞지 않습니다")
    if not isinstance(sto["chroma_dir"], str) or not sto["chroma_dir"].strip():
        raise SettingsError("storage.chroma_dir이 비어 있습니다")
    doc_priority = _parse_doc_priority(evi.get("doc_priority"), os.path.dirname(os.path.abspath(path)))
    if doc_priority is not None and mode != "dense":
        raise SettingsError("evidence.doc_priority는 retrieval.search_mode가 dense일 때만 쓸 수 있습니다")
    diversity = _parse_diversity(evi.get("diversity"), evi.get("doc_priority"), os.path.dirname(os.path.abspath(path)))
    if diversity is not None and mode != "dense":
        raise SettingsError("evidence.diversity는 retrieval.search_mode가 dense일 때만 쓸 수 있습니다")
    unread_sheets = _parse_unread_sheets(evi.get("unread_sheets"))
    # 묶음 ⑥ K2: 연결 따라가기 설정
    share = lnk["search_share"]
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share < 1:
        raise SettingsError("links.search_share는 0 초과 1 미만의 수여야 합니다")
    link_order = lnk.get("link_order")                       # 묶음 ㊾: 선택 칸(없으면 항목 번호 순, 기존 동작)
    if link_order is not None and link_order not in ("item_number", "distance"):
        raise SettingsError("links.link_order는 item_number(항목 번호 순) 또는 distance(질문과 가까운 순)여야 합니다")
    allow_inferred = lnk.get("allow_inferred")               # 묶음 73 F2: 보조 팔에서만 적는다(없으면 지금처럼 inferred 금지)
    if allow_inferred is not None and allow_inferred is not True:
        raise SettingsError("links.allow_inferred는 적을 때 true만 쓸 수 있습니다(보조 팔에서 추정 연결을 따라갈 때만)")
    grades = lnk["grades"]
    allowed_grades = {"explicit", "structural"} | ({"inferred"} if allow_inferred else set())
    if not isinstance(grades, list) or not grades or not set(grades) <= allowed_grades:
        raise SettingsError("links.grades는 explicit, structural 중에서만 고를 수 있습니다(추정 연결 inferred는 links.allow_inferred: true를 함께 적은 보조 팔에서만)")
    hops = lnk["hops"]
    if not (hops == "all" or (_is_int(hops) and hops >= 1)):
        raise SettingsError("links.hops는 all(끝까지) 또는 1 이상의 정수(그 단계까지)여야 합니다")
    if not _is_int(lnk["group_top_n"]) or lnk["group_top_n"] < 1:
        raise SettingsError("links.group_top_n은 1 이상의 정수여야 합니다")
    # C1-2: 상대 경로는 설정 파일이 있는 폴더 기준 절대 경로로(어느 폴더에서 실행해도 같은 위치)
    chroma_dir = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), sto["chroma_dir"]))

    return Settings(
        embedding_backend=emb["backend"], embedding_model=emb["model"].strip(),
        distance=ret["distance"], candidate_pool=ret["candidate_pool"],
        token_budget=evi["token_budget"], tokenizer=evi["tokenizer"],
        temperature=gen["temperature"], seed=gen["seed"], collections=col, dataset=dataset,
        chroma_dir=chroma_dir,
        links={"search_share": share, "grades": list(grades),
               "hops": hops, "group_top_n": lnk["group_top_n"], **({"link_order": link_order} if link_order else {}),
               **({"allow_inferred": True} if allow_inferred else {})},
        search_mode=mode, doc_priority=doc_priority, diversity=diversity, unread_sheets=unread_sheets,
    )


def _parse_unread_sheets(v):
    """묶음 83 U1: evidence.unread_sheets(선택). 없으면 None. [{file, sheet, reason}], 모두 비어 있지 않은 글자, 같은 짝 중복 금지."""
    if v is None:
        return None
    if not isinstance(v, list) or not v:
        raise SettingsError("evidence.unread_sheets는 비어 있지 않은 목록이어야 합니다")
    seen, out = set(), []
    for i, e in enumerate(v):
        if not isinstance(e, dict) or set(e) != {"file", "sheet", "reason"} or not all(isinstance(e[k], str) and e[k].strip() for k in e):
            raise SettingsError(f"evidence.unread_sheets[{i}]는 file, sheet, reason(모두 비어 있지 않은 글자)만 가져야 합니다")
        key = (e["file"], e["sheet"])                      # 시트 이름은 글자 그대로(이슈 26)
        if key in seen:
            raise SettingsError(f"evidence.unread_sheets에 같은 짝이 두 번 있습니다: {key}")
        seen.add(key)
        out.append({"file": e["file"], "sheet": e["sheet"], "reason": e["reason"]})
    return out


def unread_pairs(s) -> frozenset:
    """묶음 83 U2: 설정의 안 읽는 시트 짝. 칸이 없으면 빈 집합."""
    return frozenset((e["file"], e["sheet"]) for e in (s.unread_sheets or []))


def _parse_diversity(v, raw_priority, base_dir: str):
    """묶음 73 F1: evidence.diversity(선택). 없으면 None(지금 동작, 설정 지문 불변).

    parent_path: {mode, max_per_group}. row_block: {mode, max_per_group, block_rows}이고 시트별 데이터 시작 행을
    doc_priority.b0_project_yaml의 B0 프로필에서 읽으므로 그 칸이 있어야 한다. 프로필은 경로와 sha256을 같이 남긴다(지문에는 sha256만).
    """
    if v is None:
        return None
    if not isinstance(v, dict):
        raise SettingsError("evidence.diversity는 매핑이어야 합니다")
    mode = v.get("mode")
    if mode not in _DIV_MODES:
        raise SettingsError(f"evidence.diversity.mode는 {list(_DIV_MODES)} 중 하나여야 합니다: {mode!r}")
    want = {"mode", "max_per_group"} | ({"block_rows"} if mode == "row_block" else set())
    if set(v) != want:
        raise SettingsError(f"evidence.diversity 칸 불일치(mode {mode}): 모르는 칸 {sorted(set(v) - want)}, 빠진 칸 {sorted(want - set(v))}")
    n = v["max_per_group"]
    if not _is_int(n) or n < 1:
        raise SettingsError("evidence.diversity.max_per_group은 1 이상의 정수여야 합니다")
    out = {"mode": mode, "max_per_group": n}
    if mode == "row_block":
        w = v["block_rows"]
        if not _is_int(w) or w < 1:
            raise SettingsError("evidence.diversity.block_rows는 1 이상의 정수여야 합니다")
        if not isinstance(raw_priority, dict) or "b0_project_yaml" not in raw_priority:
            raise SettingsError("diversity mode row_block은 evidence.doc_priority.b0_project_yaml이 있어야 합니다(B0 프로필에서 데이터 시작 행을 읽음)")
        out["block_rows"] = w
        out["b0_profiles"] = _read_b0_profiles(raw_priority["b0_project_yaml"], base_dir)
    return out


def _read_b0_profiles(pp, base_dir: str) -> dict:
    """묶음 73 F1: B0 프로젝트 설정의 documents에서 {파일 이름: {path, sha256}}. 파일 이름 규칙은 _read_b0_doc_types와 같다."""
    import hashlib

    full = pp if os.path.isabs(pp) else os.path.normpath(os.path.join(base_dir, pp))
    with open(full, encoding="utf-8") as f:
        docs = (yaml.safe_load(f) or {}).get("documents") or []
    out = {}
    for d in docs:
        name = re.split(r"[\\/]", d["file"])[-1]
        prof = d["profile"]
        prof_full = prof if os.path.isabs(prof) else os.path.normpath(os.path.join(os.path.dirname(full), prof))
        with open(prof_full, "rb") as f:
            out[name] = {"path": prof_full, "sha256": hashlib.sha256(f.read()).hexdigest()}
    return out


def _read_b0_doc_types(pp, base_dir: str) -> tuple[dict, dict]:
    """묶음 51 D1·D5: B0 프로젝트 설정의 documents에서 {파일 이름: 프로필 id} 대응표와 출처(파일 이름, sha256)를 만든다.

    파일 이름은 B0 source.file과 같은 규칙(경로를 떼고 이름만)이다. 프로필에 id가 없거나 같은 파일 이름이 둘이면 멈춘다.
    """
    import hashlib

    if not isinstance(pp, str) or not pp.strip():
        raise SettingsError("doc_priority.b0_project_yaml은 경로 글자여야 합니다")
    full = pp if os.path.isabs(pp) else os.path.normpath(os.path.join(base_dir, pp))
    if not os.path.isfile(full):
        raise SettingsError(f"doc_priority.b0_project_yaml 파일이 없습니다: {pp}")
    raw_bytes = open(full, "rb").read()
    proj = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    docs = proj.get("documents") if isinstance(proj, dict) else None
    if not isinstance(docs, list) or not docs:
        raise SettingsError(f"{pp}에 documents 목록이 없습니다")
    file_types = {}
    for d in docs:
        if not isinstance(d, dict) or not d.get("file") or not d.get("profile"):
            raise SettingsError(f"{pp}: documents 항목에 file과 profile이 모두 있어야 합니다: {d!r}")
        name = re.split(r"[\\/]", d["file"])[-1]
        prof = d["profile"]
        prof_full = prof if os.path.isabs(prof) else os.path.normpath(os.path.join(os.path.dirname(full), prof))
        if not os.path.isfile(prof_full):
            raise SettingsError(f"{pp}: {name}의 프로필 파일이 없습니다: {prof}")
        with open(prof_full, encoding="utf-8") as f:
            pid = (yaml.safe_load(f) or {}).get("id")
        if not isinstance(pid, str) or not pid.strip():
            raise SettingsError(f"{pp}: {name}의 프로필에 id가 없습니다(문서 종류를 가를 수 없어 멈춥니다)")
        if name in file_types:
            raise SettingsError(f"{pp}: 같은 파일 이름이 둘 이상입니다(문서 종류를 하나로 정할 수 없음): {name}")
        file_types[name] = pid
    return file_types, {"project_yaml_name": os.path.basename(full), "project_yaml_sha256": hashlib.sha256(raw_bytes).hexdigest()}


def _parse_doc_priority(v, base_dir: str = "."):
    """묶음 ㊷: evidence.doc_priority를 검사한다. 없으면 None(끔).

    order: 우선순위 순서. 문자열 하나는 한 순위, 문자열 목록은 같은 순위(예: [fo, bo]). 적지 않은 문서 종류는 마지막.
    first_group: 먼저 담을 문서 종류(요구사항). first_cap_tokens: 그 몫의 토큰 상한.
    file_patterns: 청크에 doc_type 메타가 없을 때(B0) 파일 이름에서 문서 종류를 가르는 글자 조각.
    b0_project_yaml: file_patterns 대신 B0 프로젝트 설정의 프로필 id로 가른다(묶음 51). 둘 중 정확히 하나.
    """
    if v is None:
        return None
    keys = {"first_group", "first_cap_tokens"}
    # order 또는 project_yaml 중 정확히 하나, file_patterns 또는 b0_project_yaml(묶음 51) 중 정확히 하나
    optional = {"first_scope", "first_enabled", "rest_order", "label_cards", "order", "project_yaml", "file_patterns", "b0_project_yaml",
                "coverage_groups"}
    if not isinstance(v, dict) or not keys <= set(v) or set(v) - keys - optional:
        raise SettingsError(f"evidence.doc_priority는 {sorted(keys)} 칸, order 또는 project_yaml 중 하나, "
                            "file_patterns 또는 b0_project_yaml 중 하나를 가진 매핑이어야 합니다")
    if ("order" in v) == ("project_yaml" in v):
        raise SettingsError("doc_priority는 order(직접 적기)와 project_yaml(si-prep project.yaml에서 읽기) 중 정확히 하나만 적어야 합니다")
    if ("file_patterns" in v) == ("b0_project_yaml" in v):                 # 묶음 51 D2
        raise SettingsError("doc_priority는 file_patterns(파일 이름 조각)와 b0_project_yaml(B0 프로젝트 설정의 프로필 id) 중 "
                            "정확히 하나만 적어야 합니다")
    source = None
    if "project_yaml" in v:                                  # 묶음 ㊼: 합의대로 project.yaml의 doc_priority 목록에서 읽는다
        import hashlib

        pp = v["project_yaml"]
        if not isinstance(pp, str) or not pp.strip():
            raise SettingsError("doc_priority.project_yaml은 경로 글자여야 합니다")
        full = pp if os.path.isabs(pp) else os.path.normpath(os.path.join(base_dir, pp))
        if not os.path.isfile(full):
            raise SettingsError(f"doc_priority.project_yaml 파일이 없습니다: {pp}")
        raw_bytes = open(full, "rb").read()
        proj = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
        lst = proj.get("doc_priority") if isinstance(proj, dict) else None
        if not isinstance(lst, list) or not lst:
            raise SettingsError(f"{pp}에 doc_priority 목록이 없습니다(없으면 우선순위 없음이라 조용히 끄지 않고 멈춥니다)")
        flat = []
        for x in lst:                                        # 안쪽 목록 = 같은 순위(한 단계만, ARCHITECTURE 5.3)
            members = x if isinstance(x, list) else [x]
            if not members or not all(isinstance(m, str) and m for m in members):
                raise SettingsError(f"{pp}의 doc_priority 항목은 문서 종류 id(글자) 또는 그 목록이어야 합니다: {x!r}")
            flat += members
        if len(set(flat)) != len(flat):
            raise SettingsError(f"{pp}의 doc_priority에 같은 종류가 두 번 있습니다: {lst!r}")
        declared = [d.get("id") for d in (proj.get("doc_types") or []) if isinstance(d, dict)]
        if declared and not set(flat) <= set(declared):
            raise SettingsError(f"{pp}의 doc_priority에 doc_types에 선언되지 않은 종류가 있습니다: {sorted(set(flat) - set(declared))}")
        v = dict(v, order=list(lst))
        source = {"project_yaml_sha256": hashlib.sha256(raw_bytes).hexdigest(), "project_yaml_name": os.path.basename(full)}
    if "first_scope" in v and v["first_scope"] not in ("all", "pool"):
        raise SettingsError("doc_priority.first_scope는 all(서랍 전체) 또는 pool(후보 한도 안)이어야 합니다")
    if "first_enabled" in v and not isinstance(v["first_enabled"], bool):
        raise SettingsError("doc_priority.first_enabled는 true 또는 false여야 합니다")
    if "label_cards" in v and not isinstance(v["label_cards"], bool):
        raise SettingsError("doc_priority.label_cards는 true 또는 false여야 합니다")
    if "rest_order" in v and v["rest_order"] not in ("priority", "distance"):
        raise SettingsError("doc_priority.rest_order는 priority(문서 종류 순서) 또는 distance(검색 순위)여야 합니다")
    order = v["order"]
    if not isinstance(order, list) or not order:
        raise SettingsError("doc_priority.order는 비어 있지 않은 목록이어야 합니다")
    seen = []
    for g in order:
        members = g if isinstance(g, list) else [g]
        if not members or not all(isinstance(m, str) and m for m in members):
            raise SettingsError(f"doc_priority.order의 항목이 문서 종류 이름(글자) 또는 그 목록이어야 합니다: {g!r}")
        seen += members
    if len(set(seen)) != len(seen):
        raise SettingsError("doc_priority.order에 같은 문서 종류가 두 번 있습니다")
    if "file_patterns" in v:
        pats = v["file_patterns"]
        if not isinstance(pats, dict) or not pats or not all(isinstance(k, str) and isinstance(p, str) and p for k, p in pats.items()):
            raise SettingsError("doc_priority.file_patterns는 {문서 종류: 파일 이름 조각} 매핑이어야 합니다")
        if v["first_group"] not in pats:
            raise SettingsError("doc_priority.first_group은 file_patterns에 있는 문서 종류여야 합니다")
        unknown = [m for m in seen if m not in pats]
        if unknown:
            raise SettingsError(f"doc_priority.order에 file_patterns에 없는 문서 종류가 있습니다: {unknown}")
        types = {"file_patterns": dict(pats)}
        known_ids = set(pats)
    else:                                                    # 묶음 51 D1·D3: B0 프로젝트 설정의 프로필 id로 문서 종류를 가른다
        file_types, types_source = _read_b0_doc_types(v["b0_project_yaml"], base_dir)
        known = set(file_types.values())
        if v["first_group"] not in known:
            raise SettingsError(f"doc_priority.first_group이 B0 프로필 id {sorted(known)}에 없습니다: {v['first_group']!r}")
        unknown = [m for m in seen if m not in known]
        if unknown:
            raise SettingsError(f"doc_priority.order에 B0 프로필 id {sorted(known)}에 없는 문서 종류가 있습니다: {unknown}")
        types = {"b0_file_types": file_types, "b0_types_source": types_source}
        known_ids = known
    groups = _parse_coverage_groups(v["coverage_groups"], known_ids) if "coverage_groups" in v else None
    cap = v["first_cap_tokens"]
    if not _is_int(cap) or cap <= 0:
        raise SettingsError("doc_priority.first_cap_tokens는 양의 정수여야 합니다")
    out = {"order": [list(g) if isinstance(g, list) else g for g in order], "first_group": v["first_group"],
           "first_cap_tokens": cap, **types}
    if source is not None:                                   # 묶음 ㊼: project.yaml에서 읽었을 때만 지문에 출처를 남긴다(옛 방식 지문 불변)
        out["order_source"] = source
    for k in ("first_scope", "first_enabled", "rest_order", "label_cards"):    # 묶음 ㊸·㊹: 적었을 때만 담는다(없으면 설정 지문이 ㊷ 때와 같다)
        if k in v:
            out[k] = v[k]
    if groups is not None:                                   # 묶음 71: 적었을 때만 담는다(없으면 지문 불변)
        out["coverage_groups"] = groups
    return out


def _parse_coverage_groups(g, known: set) -> dict:
    """묶음 71 A1: 문서 종류 커버리지(보조 지표)의 묶음 {이름: [문서 종류 id]}. 아는 문서 종류를 빠짐없이, 한 번씩만 덮어야 한다."""
    if not isinstance(g, dict) or not g:
        raise SettingsError("doc_priority.coverage_groups는 {묶음 이름: [문서 종류 id, ...]} 매핑이어야 합니다")
    out, seen = {}, []
    for name, ids in g.items():
        if not isinstance(name, str) or not name.strip():
            raise SettingsError(f"doc_priority.coverage_groups의 묶음 이름은 비어 있지 않은 글자여야 합니다: {name!r}")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            raise SettingsError(f"doc_priority.coverage_groups의 {name!r}는 문서 종류 id(글자)의 비어 있지 않은 목록이어야 합니다")
        out[name] = list(ids)
        seen += ids
    dup = sorted({i for i in seen if seen.count(i) > 1})
    if dup:
        raise SettingsError(f"doc_priority.coverage_groups에서 한 문서 종류가 두 묶음에 있습니다: {dup}")
    unknown = sorted(set(seen) - known)
    if unknown:
        raise SettingsError(f"doc_priority.coverage_groups에 설정이 아는 문서 종류 {sorted(known)} 밖의 id가 있습니다: {unknown}")
    missing = sorted(known - set(seen))
    if missing:
        raise SettingsError(f"doc_priority.coverage_groups가 덮지 않는 문서 종류가 있습니다: {missing}")
    return out


def fingerprint_dict(s: Settings) -> dict:
    """설정 지문에 넣는 값. search_mode가 기본(dense)이고 doc_priority가 없으면 빼서 구현 전 지문과 같게 둔다(묶음 ㉘ X5, ㊷)."""
    import dataclasses

    d = dataclasses.asdict(s)
    if d.get("search_mode") == "dense":
        d.pop("search_mode")
    if d.get("doc_priority") is None:
        d.pop("doc_priority", None)
    if d.get("unread_sheets") is None:                            # 묶음 83: 적었을 때만 지문에 들어간다
        d.pop("unread_sheets", None)
    if d.get("diversity") is None:                                # 묶음 73: 켰을 때만 지문에 들어간다(프로필은 경로 빼고 sha256만)
        d.pop("diversity", None)
    elif "b0_profiles" in d["diversity"]:
        d["diversity"]["b0_profiles"] = {k: v["sha256"] for k, v in d["diversity"]["b0_profiles"].items()}
    return d


def apply_settings(s: Settings) -> None:
    """S3: 설정을 코드 쪽 값에 반영한다. 임베딩 모델이 바뀌면 불러 둔 모델을 비워 다시 불러오게 한다."""
    import config
    from pipeline import embed_store

    config.EMBEDDING_BACKEND = s.embedding_backend
    config.GENERATION_TEMPERATURE = s.temperature
    config.GENERATION_SEED = s.seed
    config.CHROMA_DIR = s.chroma_dir                 # C1-3
    if config.LOCAL_EMBEDDING_MODEL != s.embedding_model:
        config.LOCAL_EMBEDDING_MODEL = s.embedding_model
        embed_store._local_model = None


def _version(package: str):
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def model_revision(model_name: str):
    """[보정 C4] 로컬 캐시에 있는 모델의 허깅페이스 커밋 해시(가장 최근 것). 로컬에 없으면 None(빈 문자열로 얼버무리지 않는다)."""
    try:
        from huggingface_hub import scan_cache_dir

        for repo in scan_cache_dir().repos:
            if repo.repo_type == "model" and repo.repo_id == model_name:
                revs = sorted(repo.revisions, key=lambda r: r.last_modified, reverse=True)
                return revs[0].commit_hash if revs else None
    except Exception:
        return None
    return None


def _embedding_device() -> str:
    """임베딩이 돌 장치(sentence-transformers의 자동 선택과 같은 기준). 모르면 'unknown'(빈 값으로 얼버무리지 않는다)."""
    try:
        import torch

        return "cuda:" + torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    except Exception:
        return "unknown"


def run_info(s: Settings) -> dict:
    """S4: 실행 기록에 남길 값(채점표 §6: 모델 이름, 시드, 도구 버전)."""
    import config

    return {
        "embedding_model": s.embedding_model,
        "embedding_model_revision": model_revision(s.embedding_model),
        "seed": s.seed,
        "temperature": s.temperature,
        "token_budget": s.token_budget,
        "candidate_pool": s.candidate_pool,                      # 묶음 ㉝ Y3: 비교 짝 검사가 쓴다(옛 기록에는 없음)
        "search_mode": s.search_mode,
        "hnsw_ef_search": config.HNSW_EF_SEARCH,                 # 묶음 ㉝ Y2
        **({"doc_priority": s.doc_priority} if s.doc_priority is not None else {}),     # 묶음 ㊷: 켰을 때만 기록
        **({"diversity": fingerprint_dict(s)["diversity"]} if s.diversity is not None else {}),   # 묶음 73: 켰을 때만 기록
        **({"unread_sheets": s.unread_sheets} if s.unread_sheets is not None else {}),   # 묶음 83: 적었을 때만 기록
        **({"links_allow_inferred": True} if (s.links or {}).get("allow_inferred") else {}),
        "tokenizer": s.tokenizer,
        "python": platform.python_version(),
        "tiktoken": _version("tiktoken"),
        "chromadb": _version("chromadb"),
        "sentence-transformers": _version("sentence-transformers"),
        "torch": _version("torch"),
        "device": _embedding_device(),
        "si_prep_schema": _si_prep_schema(),                     # 묶음 57 M6: 기록만(설정 지문·점수·동결 검사에 쓰지 않음)
    }


def _si_prep_schema():
    """청크 검증에 쓴 si-prep 스키마의 버전과 schema.py 파일 sha256. 불러오지 못하면 'unknown'(빈 값으로 얼버무리지 않는다)."""
    try:
        import hashlib

        import si_prep.schema as schema

        with open(schema.__file__, "rb") as f:
            return {"version": schema.SCHEMA_VERSION, "sha256": hashlib.sha256(f.read()).hexdigest()}
    except Exception:
        return "unknown"
