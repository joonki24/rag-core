"""연결 따라가기: B1(검색+연결) 팔의 근거 모으기. 기준은 CHANGES.md의 '묶음 ⑥ 구현 기준 확정'(K1~K8).

[쉽게 말하면]
  si-prep은 "이 요구사항은 저 기능과 같은 것" 같은 문서 간 연결(links.jsonl)을 만든다.
  이 파일은 그 연결을 검색에 써 본다. 바구니(4,000토큰)를 반으로 나눠서
    ① 앞 절반(2,000) : 보통 검색처럼 순위대로 담는다(검색 몫)
    ② 뒤 절반(2,000) : ①에서 담은 카드의 연결 이웃을 담는다(연결 몫, 질문과 가까운 순)
    ③ 남은 자리      : 다시 검색 순위대로 채운다
  연결로 들어온 카드에는 via="link"가 붙어 나중에 구분할 수 있다.

[이 파일에서 먼저 볼 것]
  gather_with_links  : 위 ①②③을 하는 입구(eval_runner가 부름)
  LinkIndex          : 연결 파일을 한 번 읽어 두고 "이 항목의 이웃은?"에 답함
  LinkIndex.neighbors: 한 항목의 1단계 이웃(짝 연결 먼저, 묶음 연결은 가까운 3개)

검색 몫(예산의 search_share)에 담긴 청크를 seed로, `links.jsonl`의 연결을 끝까지(설정 hops: all, 정수면 그 단계까지) 건넌 횟수 순으로 따라가 연결된 항목의 청크를
연결 몫에 함께 담는다. 연결에는 방향이 없다(묶음 판단은 팀의 group_contains). `links.jsonl`과 `items.jsonl`은 읽기만 하고
줄마다 `si_prep.schema`의 Link·Item으로 검증한다(묶음 ⑮, 채점표 변경 6).
B1(검색만)과 B0는 이 모듈을 쓰지 않고 `evidence.gather`를 그대로 쓴다.
"""
from __future__ import annotations

import json
from collections import defaultdict

import evidence as ev
from adapter import safe_error


class LinkError(RuntimeError):
    """연결 입력이나 서랍이 기준(K1, K6)에 맞지 않을 때."""


def _schema():
    import adapter

    adapter._chunk_model()                       # si_prep을 못 불러오면 오류로 멈춘다
    try:
        from si_prep import schema
        schema.Link, schema.Item, schema.group_contains, schema.find_duplicate_links
    except (ImportError, AttributeError) as e:
        raise LinkError("si_prep.schema에 Link·Item 모델이나 group_contains가 없습니다(팀 저장소 d3feef7 이후 버전을 pip install -e로 설치).") from e
    return schema


def _link_model():
    return _schema().Link


def _read_lines(path: str, model, what: str) -> list:
    """jsonl을 줄마다 모델로 검증해 읽는다. 위반이 하나라도 있으면 줄 번호와 함께 멈춘다."""
    rows, errors = [], []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(model.model_validate(json.loads(line)))
            except Exception as e:
                errors.append(f"{n}줄: {safe_error(e)[:160]}")
    if errors:
        more = f"\n... 외 {len(errors) - 5}개" if len(errors) > 5 else ""
        raise LinkError(f"{path}: {what}가 스키마와 맞지 않는 줄 {len(errors)}개\n" + "\n".join(errors[:5]) + more)
    return rows


def read_links(path: str) -> list:
    """links.jsonl을 읽는다(M1). 같은 규칙의 같은 짝·같은 항목↔묶음이 두 번 나오면 멈춘다."""
    schema = _schema()
    links = _read_lines(path, schema.Link, "연결")
    dups = schema.find_duplicate_links(links)
    if dups:
        raise LinkError(f"{path}: 중복 연결 {len(dups)}개(예: {dups[0]})")
    return links


def read_items(path: str) -> dict:
    """items.jsonl을 읽어 {item_id: Item}으로(M2). item_id가 겹치면 멈춘다."""
    items = {}
    for it in _read_lines(path, _schema().Item, "항목"):
        if it.item_id in items:
            raise LinkError(f"{path}: item_id가 겹칩니다: {it.item_id}")
        items[it.item_id] = it
    return items


def _item_key(item_id: str) -> int:
    return int(item_id.split("-")[1])


class LinkIndex:
    """서랍의 청크, items.jsonl의 항목, links.jsonl의 연결을 한 번 읽어 두고, 항목의 이웃을 찾는다. 연결에는 방향이 없다."""

    def __init__(self, links: list, items: dict, cfg: dict, collection_name: str, item_models: dict):
        self.items = items                         # item_id -> {"chunk_id", "text", "metadata", "doc_type", "cpt"}  (서랍의 청크)
        self.item_models = item_models             # item_id -> si_prep Item (items.jsonl)
        self.cfg = cfg
        self.collection_name = collection_name
        allowed = set(cfg["grades"])
        self.excluded_by_grade = sum(1 for l in links if l.grade not in allowed)
        links = [l for l in links if l.grade in allowed]
        missing = sorted({i for l in links for i in ((l.item_a, l.item_b) if l.item_a is not None else (l.item,)) if i not in item_models})
        if missing:
            raise LinkError(f"연결이 가리키는 항목이 items.jsonl에 없습니다({len(missing)}개, 예: {missing[0]})")
        self.pairs = defaultdict(list)             # item_id -> [(상대 item_id, link)]
        self.group_links = []
        for l in sorted(links, key=lambda x: x.link_id):
            if l.item_a is not None:
                self.pairs[l.item_a].append((l.item_b, l))
                self.pairs[l.item_b].append((l.item_a, l))
            else:
                self.group_links.append(l)
        self.group_by_item = defaultdict(list)
        for l in self.group_links:
            self.group_by_item[l.item].append(l)

    @classmethod
    def build(cls, links_path: str, items_path: str, collection_name: str, settings):
        from pipeline import embed_store

        links = read_links(links_path)
        item_models = read_items(items_path)
        got = embed_store.get_collection(collection_name, create=False).get(include=["documents", "metadatas"])
        items = {}
        for cid, text, md in zip(got["ids"], got["documents"], got["metadatas"]):
            iid = md.get("item_id")
            if iid:
                items[iid] = {"chunk_id": cid, "text": text, "metadata": md,
                              "doc_type": md.get("doc_type"), "cpt": md.get("category_path_text") or ""}
        if not items:
            raise LinkError(f"서랍 '{collection_name}'에 item_id 메타가 있는 청크가 없습니다(B0 서랍에는 연결 따라가기를 쓸 수 없음)")
        return cls(links, items, settings.links, collection_name, item_models)

    # ---- 이웃 찾기 ----
    def _nearest(self, item_ids: list, qe: list, n: int) -> list:
        """item_ids 중 질문과 코사인 거리가 가까운 상위 n개를 [(item_id, distance)]로. 동률은 항목 번호 순."""
        from pipeline import embed_store

        present = [i for i in item_ids if i in self.items]
        if not present:
            return []
        coll = embed_store.get_collection(self.collection_name, create=False)
        res = coll.query(query_embeddings=[qe], n_results=min(n, len(present)), where={"item_id": {"$in": present}},
                         include=["metadatas", "distances"])
        got = [(md["item_id"], d) for md, d in zip(res["metadatas"][0], res["distances"][0])]
        return sorted(got, key=lambda t: (t[1], _item_key(t[0])))

    def neighbors(self, seed_item: str, qe: list, stats: dict) -> list:
        """seed 항목의 1단계 이웃(M3). [{"item_id", "kind", "link_id", "distance"}].
        순서: 항목↔항목(상대 항목 번호 순) 먼저, 그다음 묶음 안 항목, 그다음 묶음을 가리키는 항목."""
        schema = _schema()
        out = []
        pair_rows = sorted(self.pairs.get(seed_item, []), key=lambda t: (_item_key(t[0]), t[1].link_id))
        dist = {}
        if self.cfg.get("link_order") == "distance" and pair_rows:           # 묶음 ㊾: 항목↔항목도 질문과 가까운 순(청크가 없는 항목은 뒤, 항목 번호 순)
            ids = sorted({nid for nid, _ in pair_rows})
            dist = dict(self._nearest(ids, qe, len(ids)))
            pair_rows = sorted(pair_rows, key=lambda t: (t[0] not in dist, dist.get(t[0], 0.0), _item_key(t[0]), t[1].link_id))
        for nid, l in pair_rows:
            out.append({"item_id": nid, "kind": "item_pair", "link_id": l.link_id, "distance": dist.get(nid),
                        "grade": l.grade, "method": l.method})
        n = self.cfg["group_top_n"]
        for l in self.group_by_item.get(seed_item, []):                          # seed가 item: 묶음 안 항목
            members = [i for i, m in self.item_models.items() if i != seed_item and schema.group_contains(l.group, m)]
            if not members:
                stats["empty_groups"] += 1
            for i, d in self._nearest(members, qe, n):
                out.append({"item_id": i, "kind": "group_member", "link_id": l.link_id, "distance": d, "grade": l.grade, "method": l.method})
        seed = self.item_models.get(seed_item)                                    # seed가 묶음 안: 묶음을 가리키는 item
        if seed is not None:
            owners, owner_link, link_by_id = [], {}, {}
            for l in self.group_links:
                if l.item != seed_item and l.item not in owner_link and schema.group_contains(l.group, seed):
                    owners.append(l.item)
                    owner_link[l.item] = l.link_id
                    link_by_id[l.link_id] = l
            for i, d in self._nearest(owners, qe, n):
                out.append({"item_id": i, "kind": "group_owner", "link_id": owner_link[i], "distance": d,
                            "grade": link_by_id[owner_link[i]].grade, "method": link_by_id[owner_link[i]].method})
        return out


def _priority_first(query: str, collection_name: str, settings, search_cap: int, index: "LinkIndex", count, capper=None):
    """묶음 ㊷: 검색 몫을 요구사항 먼저(직접 찾은 것 + 연결 한 번으로 닿은 것, 상한 안) -> 남은 몫은 우선순위 순으로 채운다.

    (첫 몫 Evidence, 남은 후보(우선순위 순), 전체 검색 후보, 보고)를 돌려준다.
    capper(묶음 73)가 있으면 나머지 검색 카드와 그 미리 담기에 다양화 상한을 쓴다(요구사항 카드는 그대로).
    """
    import priority
    from pipeline import embed_store

    cfg = settings.doc_priority
    cap = min(cfg["first_cap_tokens"], search_cap)
    req_all, rest, pool = ev.priority_parts(query, collection_name, settings)
    req_chosen, used_req = priority.take_until(req_all, cap, count)

    # 검색으로 찾은 항목(요구사항 직접 + 나머지 검색 몫)에서 연결 한 번으로 닿는 요구사항을 요구사항 상한 안에서 이어 담는다
    if capper is None:
        probe, _ = priority.take_until(rest, search_cap - used_req, count)
    else:
        probe = capper.take(rest, search_cap - used_req, count, commit=False)[0]
    seed_items = [c["metadata"]["item_id"] for c in req_chosen + probe if c["metadata"].get("item_id")]
    n_link_req = 0
    if seed_items and cfg.get("first_enabled", True):                       # 묶음 ㊹: 요구사항 먼저 담기를 껐으면 연결로 닿는 요구사항도 따로 담지 않는다
        qe = embed_store.embed_texts([query])[0]
        taken = {c["id"] for c in req_chosen}
        extra, stats = [], {"empty_groups": 0}
        for item_id in seed_items:
            for nb in index.neighbors(item_id, qe, stats):
                m = index.items.get(nb["item_id"])
                if m is None or m["chunk_id"] in taken:
                    continue
                if priority.doc_type_of(m["metadata"], cfg) != cfg["first_group"]:
                    continue
                taken.add(m["chunk_id"])
                extra.append({"id": m["chunk_id"], "text": m["text"], "metadata": m["metadata"], "distance": None,
                              "via": "req_link", "link_kind": nb["kind"], "link_id": nb["link_id"], "link_hops": 1})
        added, used_added = priority.take_until(extra, cap - used_req, count)
        req_chosen, used_req, n_link_req = req_chosen + added, used_req + used_added, len(added)
    if capper is None:
        rest_chosen, used_rest = priority.take_until(rest, search_cap - used_req, count)
        stop = len(rest_chosen)
    else:
        rest_chosen, used_rest, stop = capper.take(rest, search_cap - used_req, count)
    chunks = [dict(c, via=c.get("via", "req_search")) for c in req_chosen] + [dict(c, via="search") for c in rest_chosen]
    first = ev.Evidence(chunks=chunks, used_tokens=used_req + used_rest, budget=search_cap, n_candidates=len(pool),
                        stopped_reason="budget" if stop < len(rest) else "all_fit",
                        pool_exhausted=False)
    report = {"req_direct": len(req_chosen) - n_link_req, "req_by_link": n_link_req, "req_tokens": used_req,
              "req_cap": cap, "req_available": len(req_all)}
    return first, rest[stop:], pool, report


def gather_with_links(query: str, collection_name: str, settings, index: LinkIndex, count=ev.count_tokens, div=None) -> ev.Evidence:
    """검색 몫 -> 연결 몫(건넌 횟수 순) -> 남은 예산으로 검색 이어 담기(K4, M5).

    순서(코드에 (1)(2)(3) 주석으로 표시):
      (1) 검색 몫(예산 × search_share)만큼 검색 순위대로 담는다. 요구사항 먼저 설정이 켜져 있으면 그 규칙으로.
      (2) (1)에서 담은 카드들의 연결 이웃을 hops 단계까지 따라가며 연결 몫이 찰 때까지 담는다.
      (3) 그래도 남은 예산을 (1)에서 멈춘 다음 검색 순위부터 채운다.
    반환값 Evidence.link_report에 연결로 몇 장 들어왔는지 등이 들어 있다.
    div(묶음 73, diversity.Context)가 있으면 검색 카드(검색 몫·이어 담기)에만 다양화 상한을 쓴다. 연결 카드는 상한을 받지 않고 세지도 않는다."""
    from pipeline import embed_store, query_engine

    cfg = settings.links
    budget = settings.token_budget
    search_cap = int(budget * cfg["search_share"])
    link_cap = budget - search_cap
    capper = div.new() if div is not None else None

    pr_report = None
    if getattr(settings, "doc_priority", None):                                   # 묶음 ㊷: 켰을 때만. 꺼져 있으면 아래 기존 경로 그대로
        first, leftover, candidates, pr_report = _priority_first(query, collection_name, settings, search_cap, index, count, capper)
    elif capper is not None:                                                       # 묶음 73: 검색 몫에 다양화, 멈춘 자리부터 이어 담기
        candidates = query_engine.search(query, settings.candidate_pool, collection_name=collection_name)
        first, stop = ev.collect_diverse(candidates, search_cap, capper, count)
        leftover = candidates[stop:]
    else:
        candidates = query_engine.search(query, settings.candidate_pool, collection_name=collection_name)
        first = ev.collect_evidence(candidates, search_cap, count)                # (1) 검색 몫
        leftover = None
    seeds = [c for c in first.chunks if c["metadata"].get("item_id")]
    if first.chunks and not seeds:
        raise LinkError("검색된 청크에 item_id 메타가 없습니다(B0 서랍에는 연결 따라가기를 쓸 수 없음)")
    chosen = [dict(c, via=c.get("via", "search")) for c in first.chunks]
    taken = {c["id"] for c in chosen}

    stats = {"empty_groups": 0}
    link_chunks, used_link, link_stopped = [], 0, False
    n_candidates = n_no_chunk = n_not_fitted = 0
    per_level = {}
    qe = embed_store.embed_texts([query])[0] if seeds else None
    hops = cfg["hops"]
    seen = {c["metadata"]["item_id"] for c in seeds}
    frontier = [(c["metadata"]["item_id"], c["id"]) for c in seeds]              # (항목, 그 항목의 청크)
    level = 1
    while frontier and not link_stopped and (hops == "all" or level <= hops):    # (2) 연결 몫: 건넌 횟수 순(M5)
        found = [(nb, chunk_id) for item_id, chunk_id in frontier for nb in index.neighbors(item_id, qe, stats)]
        pairs, groups = [], []
        for want_pair, bucket in ((True, pairs), (False, groups)):                # 항목↔항목을 먼저 확정하고 묶음은 나머지에서
            for nb, chunk_id in found:
                if (nb["kind"] == "item_pair") == want_pair and nb["item_id"] not in seen:
                    seen.add(nb["item_id"])
                    bucket.append((nb, chunk_id))
        new_frontier = []
        for nb, parent in pairs + groups:                                         # 같은 횟수 안: 항목↔항목 먼저, 묶음 나중
            n_candidates += 1
            m = index.items.get(nb["item_id"])
            if m is None:
                n_no_chunk += 1
                continue
            if m["chunk_id"] in taken:
                continue
            if link_stopped:
                n_not_fitted += 1
                continue
            n = count(m["text"])
            if used_link + n > link_cap:
                link_stopped = True
                n_not_fitted += 1
                continue
            link_chunks.append({"id": m["chunk_id"], "text": m["text"], "metadata": m["metadata"], "distance": None,
                                "via": "link", "from_chunk": parent, "link_kind": nb["kind"], "link_id": nb["link_id"],
                                "link_grade": nb.get("grade"), "link_method": nb.get("method"),
                                "link_distance": nb["distance"], "link_hops": level})
            taken.add(m["chunk_id"])
            used_link += n
            per_level[level] = per_level.get(level, 0) + 1
            new_frontier.append((nb["item_id"], m["chunk_id"]))
        frontier = new_frontier
        level += 1

    avail = budget - first.used_tokens - used_link                                 # (3) 남은 예산으로 검색을 이어 담기
    rest_src = leftover if leftover is not None else candidates[len(first.chunks):]
    rest = [c for c in rest_src if c["id"] not in taken]
    topup = ev.collect_evidence(rest, avail, count) if capper is None else ev.collect_diverse(rest, avail, capper, count)[0]
    chunks = chosen + link_chunks + [dict(c, via="search_topup") for c in topup.chunks]
    used = first.used_tokens + used_link + topup.used_tokens

    if not candidates:
        reason = "no_candidates"
    elif not chunks:
        reason = "first_exceeds"
    elif topup.stopped_reason == "all_fit" and not link_stopped:
        reason = "all_fit"
    else:
        reason = "budget"
    return ev.Evidence(
        chunks=chunks, used_tokens=used, budget=budget, n_candidates=len(candidates), stopped_reason=reason,
        pool_exhausted=(reason == "all_fit" and used < budget),
        link_report={"seeds": len(seeds), "neighbor_candidates": n_candidates, "link_chunks": len(link_chunks),
                     "skipped_no_chunk": n_no_chunk, "empty_groups": stats["empty_groups"],
                     "excluded_by_grade": index.excluded_by_grade, "not_fitted": n_not_fitted,
                     "search_cap": search_cap, "link_cap": link_cap, "used_link_tokens": used_link,
                     "link_chunks_by_hops": per_level, "closed_by_budget": link_stopped,
                     **({"priority": pr_report} if pr_report is not None else {})},
        diversity_report=capper.report() if capper is not None else None,
    )
