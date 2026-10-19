# si-prep과 rag_core의 경계

작성: 2026-10-03. 2026-10-04에 `links.jsonl` 추가, **2026-10-06에 경계를 5개로 고치고 경계 안의 약속 표를 더함.** 근거: 스키마 v0.1 §1(역할 경계), PRD v0.4 §6.2·§13, Guide(제목 "스키마부터 파일4개까지"), PRD v0.6 §8.1("출력은 4개 파일이며 Pydantic 모델(schema.py) 하나로 정의한다").

> **정정(2026-10-06):** 처음에는 만나는 곳을 두 군데, 그 뒤 세 군데로 적었는데, 이것은 **우리가 읽는 것만 센 숫자**였다. 파일·코드 경계는 si-prep 출력 **4개 파일과 `schema.py`, 모두 5개**다. 그중 우리가 읽는 것은 처음에 3개였고, 이후 `items.jsonl`을 읽게 되어(묶음 ⑮) 지금은 4개(`schema.py`, `chunks.jsonl`, `links.jsonl`, `items.jsonl`)이며 `warnings.jsonl`만 읽지 않는다. 같은 날 사용자가 지적했다.

## 1. 파일·코드 경계 5개

두 사람의 코드가 충돌하지 않게 만나는 곳을 아래 5개로만 둔다. 방향은 모두 si-prep → rag_core다.

| 경계 | 만드는 쪽 | 우리가 읽나 | 규칙과 비고 |
| --- | --- | --- | --- |
| `si_prep/schema.py` | si-prep이 소유 | **예** (설치본을 import) | `Chunk`, `Item`, `Link`와 함수 `group_contains`, `find_duplicate_links`를 불러다 쓴다. **복사하거나 다시 정의하지 않는다**(어긋남 방지). 청크 관련은 10-06 동결, 항목·연결·경고 모델은 10-06 구현(2차 동결 10-08). 팀 저장소 d3feef7 이후 버전이 설치돼 있어야 한다 |
| `chunks.jsonl` (B1) | si-prep | **예** (색인) | 읽기만 한다. 우리가 쓰는 칸: `chunk_id`, `text`, `source`(파일·시트·행), `meta`, `schema_version`. 연결 따라가기는 meta의 `item_id`, `doc_type`, `category_path_text`도 읽는다 |
| `links.jsonl` | si-prep | **예** (연결 따라가기) | 읽기만 한다. 줄마다 `Link`(항목↔항목 `item_a`/`item_b`, 항목↔묶음 `item`/`group`)로 검증하고, `find_duplicate_links`가 중복을 찾으면 멈춘다. 연결에는 방향이 없어 세 칸을 모두 본다. 청크와는 meta `item_id`로 잇는다(묶음 ⑮, 2026-10-06) |
| `items.jsonl` | si-prep | **예** (연결 따라가기의 묶음 판단) | 읽기만 한다. 줄마다 `Item`으로 검증하고 `item_id`가 겹치거나 연결이 가리키는 항목이 없으면 멈춘다. 묶음 연결은 이 항목의 단계별 경로로 팀의 `group_contains`를 불러 푼다(글자 앞부분 비교 금지, 사용자 결정 2026-10-06). 검색·채점에는 쓰지 않는다 |
| `warnings.jsonl` | si-prep | **아니오** | 평가에는 쓰지 않는다. B1이 B0보다 낮은 질문의 원인을 직접 확인할 때 경고(병합 미복원, 내용 불일치 등)를 참고할 수 있다 |

## 2. 경계 안의 약속

파일과 칸의 이름은 코드가 자동으로 막아 주지만, 아래는 **한쪽이 바꾸면 다른 쪽이 조용히 어긋날 수 있는 약속**이다. 처음에는 이 문서에 없었다.

| 약속 | 관련 경계 | 현재 상태와 할 일 |
| --- | --- | --- |
| **청크 meta 키** | `chunks.jsonl` | 우리가 읽는 키는 `item_id`, `doc_type`, `category_path_text` 셋. si-prep 제안 키는 `item_id, doc_type, doc_file, sheet, category_path_text, source_id, ref_ids`. 이 셋이 유지되는지, `ref_ids`가 평평한 문자열인지, 청크의 `schema_version` 값은 B0 코드가 직접 적지 않고 설치된 `si_prep.schema.SCHEMA_VERSION`을 읽어 적는다(2026-10-11 확인, 묶음 ⑬). 그래서 스키마를 올려 설치하면 B0 청크 파일의 이 칸만 바뀐다(본문은 같음) |
| **프로필 키 이름** | B0가 읽는 문서별 프로필 | 지금 B0는 `include_sheets`, `header_rows`, `data_start_row`를 읽는다. si-prep 제안은 `sheets.include/exclude`, `stop_markers`. 대응표 필요, `stop_markers`는 B0가 아직 처리 안 함 |
| **파일 이름 표기** | `chunks.jsonl`, 정답표 | `source.file`은 **파일 이름만**(경로 없음), 정답표의 `file`도 같은 글자여야 한다. 화면에서 같아 보여도 분해형 한글(NFD)이면 다른 문자열이다청크·정답지·평가기가 같은 글자를 써야 함 |
| **새 버전 처리** | `chunks.jsonl`, 서랍 | 새 버전이 오면 서랍을 비우고 다시 넣는다(`rebuild`). 항목·연결 번호(IT-…, LK-…)는 버전마다 바뀌므로 저장해 쓰지 않는다(PRD v0.6 §8.4). 정답지 위치는 번호 대신 파일·시트·행·원문 ID |
| **연결 따라가기 규칙** | `links.jsonl`, `items.jsonl` | **끝까지, 건넌 횟수 순, 양쪽에서, 본 항목은 뺌, 근거 예산이 상한**으로 확정(채점표 변경 6, 사용자 2026-10-06). 같은 횟수 안은 항목↔항목 먼저, 묶음은 질문과 가까운 순 상위 3개. 설정 `hops: all`(정수 N은 1단계 보조 비교용). 검색/연결 예산 비율(750/750)과 묶음 상위 3개는 임시값이라 si-prep과 맞춘다 |

## 지킬 것

1. `si-prep/` 안의 어떤 파일도 수정하지 않는다. (읽기와 import만. si-prep 저장소와 우리 저장소는 별개이고 서브모듈로 묶지 않는다.)
2. **청크 검증은 si-prep의 `Chunk` 모델로만 한다.** 불러오지 못하면 조용히 건너뛰지 않고 오류로 멈춘다. **팀 저장소(si-prep)를 받아 `pip install -e <si-prep 폴더>`로 설치해서 쓴다**(2026-10-06 변경). 폴더 경로만 알려 주던 `SI_PREP_SRC` 방식은 `si_prep/__init__.py`가 설치 기록을 읽어서 동작하지 않는다. 옛 `SI_PREP_SRC`가 설치본을 가릴 수 있으므로 `.env`에서 쓰지 않는다.
3. **청크 번호 접두어로 소유자를 가른다.** `B1-`은 si-prep, `B0-R-`·`B0-S-`·`B0-RC-`는 rag_core. rag_core가 만드는 B0 청크도 같은 `Chunk` 모델을 통과해야 한다.
4. 우리가 Chroma에 붙이는 메타데이터 키(`source_file`, `source_sheet`, `row_start`, `row_end`, `variant`)는 스키마 `meta` 키와 겹치지 않는 이름으로 쓴다.
5. 스키마를 바꿀 일이 생기면 si-prep이 `schema.py` 버전을 올린다. 우리 계약 테스트가 깨지면 그것이 알림이다.
6. 테스트에는 **합성 청크만** 쓴다. 마스킹본 파생 파일(`out/`)은 저장소 밖에 둔다.
7. 우리 코드의 이름 공간은 `scripts/rag_core/` 안으로 한정한다. `si_prep`이라는 이름은 쓰지 않는다.

## 예외 (해소됨)
- si-prep 스키마의 `ChunkSource.rows`에 값 검사가 없어서 어댑터가 `rows` 검사를 추가로 했다. **2026-10-06 1차 동결 스키마의 `RowRange`가 같은 검사(1 이상, 시작 ≤ 끝)를 하므로 어댑터의 임시 검사를 지웠다.** 이제 예외는 없다. 스키마를 다시 정의하지 않는다.
