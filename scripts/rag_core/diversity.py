"""다양화 담기(묶음 73 F1, 기준은 CHANGES.md). 설정 evidence.diversity가 있을 때만 쓴다.

검색 순위대로 담다가 카드의 묶음에 이미 max_per_group장이 담겼으면 그 카드는 건너뛰고(예산을 쓰지 않음) 다음 순위로 간다.
예산을 넘는 카드에서 멈추는 규칙(뒤의 작은 카드를 골라 담지 않음)은 그대로다.
묶음: parent_path(B1) = (파일, 시트, 항목의 category_path에서 마지막 단계를 뺀 (level, value) 목록, basis 제외. 깊이 1이면 그 항목 하나),
      row_block(B0 대조군) = (파일, 시트, (행 - 데이터 시작 행) // block_rows). 데이터 시작 행은 B0 프로필에서 읽는다.
요구사항 먼저 담기와 연결 카드는 이 규칙을 쓰지 않는다(부르는 쪽이 검색 카드에만 쓴다).
"""
from __future__ import annotations

import yaml


class DiversityError(RuntimeError):
    """다양화 묶음을 정할 수 없을 때(item_id·items·프로필이 없음). 조용히 넘기지 않는다."""


def _get(p, name):
    return p.get(name) if isinstance(p, dict) else getattr(p, name)


def parent_keys(items: dict) -> dict:
    """item_id -> 부모 경로((level, value) 목록, basis 제외). 깊이 1이면 None(그 항목 하나가 묶음)."""
    out = {}
    for item_id, it in items.items():
        path = list(_get(it, "category_path"))
        out[item_id] = tuple((_get(p, "level"), _get(p, "value")) for p in path[:-1]) if len(path) > 1 else None
    return out


class Context:
    """한 실행의 다양화 설정. 질문마다 new()로 새 Capper를 만든다(질문끼리 개수를 나누지 않음)."""

    def __init__(self, cfg: dict, items: dict | None = None):
        self.cfg = cfg
        self.parents = None
        if cfg["mode"] == "parent_path":
            if not items:
                raise DiversityError("diversity mode parent_path에는 items.jsonl이 필요합니다(--items)")
            self.parents = parent_keys(items)
        self._profiles, self._starts = {}, {}

    def data_start(self, file: str, sheet: str) -> int:
        key = (file, sheet)
        if key not in self._starts:
            spec = self.cfg["b0_profiles"].get(file)
            if spec is None:
                raise DiversityError(f"diversity row_block: B0 프로필 대응표에 없는 파일입니다: {file!r}")
            import b0_chunks

            if file not in self._profiles:
                with open(spec["path"], encoding="utf-8") as f:
                    self._profiles[file] = b0_chunks.parse_profile(yaml.safe_load(f) or {}, file)
            start = b0_chunks._sheet_config(self._profiles[file], sheet, file).get("data_start_row")
            if not start:
                raise DiversityError(f"diversity row_block: {file} / {sheet}의 데이터 시작 행을 프로필에서 찾지 못했습니다")
            self._starts[key] = start
        return self._starts[key]

    def key(self, chunk: dict) -> tuple:
        m = chunk.get("metadata") or {}
        f, s = m.get("source_file"), m.get("source_sheet")
        if f is None or s is None:
            raise DiversityError(f"{chunk.get('id')}: 메타데이터에 source_file·source_sheet가 없습니다")
        if self.cfg["mode"] == "parent_path":
            item_id = m.get("item_id")
            if not item_id:
                raise DiversityError(f"{chunk.get('id')}: item_id가 없어 parent_path로 묶을 수 없습니다(B0 청크에는 row_block을 씀)")
            if item_id not in self.parents:
                raise DiversityError(f"{chunk.get('id')}: item_id {item_id}가 items.jsonl에 없습니다")
            p = self.parents[item_id]
            return (f, s, p if p is not None else ("(깊이 1 항목)", item_id))
        return (f, s, (m["row_start"] - self.data_start(f, s)) // self.cfg["block_rows"])

    def new(self) -> "Capper":
        return Capper(self)


class Capper:
    """한 질문의 묶음별 개수. 검색 몫과 이어 담기에 걸쳐 이어진다."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.limit = ctx.cfg["max_per_group"]
        self.counts: dict = {}
        self.skipped: set = set()

    def take(self, candidates: list, cap_tokens: int, count, commit: bool = True) -> tuple[list, int, int]:
        """(담은 카드, 쓴 토큰, 멈춘 자리). 멈춘 자리는 예산을 넘은 카드의 순번이고, 끝까지 갔으면 len(candidates)다.
        commit=False면 개수를 남기지 않는다(연결의 출발점을 정하는 미리 담기)."""
        counts = self.counts if commit else dict(self.counts)
        chosen, used = [], 0
        for i, c in enumerate(candidates):
            k = self.ctx.key(c)
            if counts.get(k, 0) >= self.limit:
                if commit:
                    self.skipped.add(c["id"])
                continue
            n = count(c["text"])
            if used + n > cap_tokens:
                return chosen, used, i
            chosen.append(c)
            used += n
            counts[k] = counts.get(k, 0) + 1
        return chosen, used, len(candidates)

    def report(self) -> dict:
        return {"mode": self.ctx.cfg["mode"], "skipped_by_cap": len(self.skipped),
                "groups_at_cap": sum(1 for v in self.counts.values() if v >= self.limit)}
