<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.png">
    <img src="docs/images/logo-light.png" alt="rag-core" width="360">
  </picture>
</p>

<p align="center">
  <i>전처리가 정말 검색을 낫게 했는지, 같은 조건에서 숫자로 확인하는 RAG 평가 코드</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.14%20tested-blue" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
  <img src="https://img.shields.io/badge/embedding-local%20(bge--m3)-orange" alt="embedding">
  <img src="https://img.shields.io/badge/LLM%20grading-none-lightgrey" alt="no LLM grading">
  <img src="https://img.shields.io/badge/status-beta-yellow" alt="status">
</p>

<p align="center">
  <a href="#빠르게-시작하기">빠르게 시작하기</a> ·
  <a href="#평가-결과">평가 결과</a> ·
  <a href="#출력-예시">출력 예시</a> ·
  <a href="scripts/rag_core/README.md">사용법</a> ·
  <a href="scripts/rag_core/코드_안내.md">코드 안내</a> ·
  <a href="https://github.com/sSFireFoxSs/si-prep">si-prep</a>
</p>

> **베타(v0.1.0)입니다.** 팀 프로젝트의 SI 문서 다섯 세트로 검증했지만, si-prep 출력 스키마 1.2~1.4에 맞춰져 있고 설정 형식과 결과 파일 칸은 0.x 동안 바뀔 수 있습니다. 평가 데이터와 정답지는 포함하지 않았습니다.

---

전처리 도구 [si-prep](https://github.com/sSFireFoxSs/si-prep)이 만든 청크·항목·링크를 받아, **흔히 쓰는 청킹 방식보다 정답 근거를 더 잘 찾는지** 같은 조건에서 비교합니다. 점수는 사람이 확인한 정답 근거 행을 찾아왔는지로만 매기며, LLM에게 채점을 맡기지 않습니다.

<p align="center">
  <img src="docs/images/architecture.png" alt="SI 엑셀 산출물이 si-prep과 기준선 청크로 나뉘어 rag-core에서 정답표와 함께 채점되는 흐름" width="100%">
</p>

## 특징

| 특징 | 내용 |
|---|---|
| **같은 조건 비교** | 기준선 3종과 si-prep 2종을 같은 임베딩, 같은 근거 예산(4,000토큰), 같은 후보 수(100)로 나란히 잽니다. |
| **정답 근거로만 채점** | 질문마다 필수 근거 행을 찾아왔는지(회수율)를 코드가 셉니다. LLM 채점은 쓰지 않습니다. |
| **우연과 구분** | 질문 단위 쌍대 부트스트랩 10,000회로 95% 신뢰구간을 내고, 하한이 0보다 클 때만 통과로 봅니다. |
| **기준 고정** | 평가 전에 코드·설정·입력의 지문을 동결하고, 하나라도 바뀌면 실행을 멈춥니다. |
| **링크 따라가기** | 검색으로 찾은 항목에서 si-prep 링크를 한 칸 따라가 함께 볼 근거를 더 담습니다. |
| **로컬 실행** | 임베딩(bge-m3)은 로컬에서만 실행하며, 외부 API 호출은 기본으로 막혀 있습니다. |

## 평가 결과

> 공개용 합성 SI 문서 세트 하나(질문 30개)의 결과입니다. 실행 전에 등록하고 한 번만 실행했습니다. 데이터와 정답지는 이 저장소에 없으므로 이 저장소만으로는 재현할 수 없습니다.

<p align="center">
  <img src="docs/images/recall.png" alt="정답 근거 회수율(%): B0-R 18.33, B0-RC 24.17, B0-S 26.39, si-prep 검색만 34.17, si-prep 검색+링크 39.44" width="100%">
</p>

| 방식 | 하는 일 | 회수율(%) |
|---|---|---|
| B0-R | 엑셀 한 행 = 카드 한 장 | 18.33 |
| B0-RC | 글자를 500자씩 자르기 | 24.17 |
| B0-S | 행을 "칸 이름: 값"으로 바꾼 뒤 500자씩 | 26.39 |
| si-prep 검색만 | 분류 경로가 붙은 si-prep 청크로 검색 | 34.17 |
| si-prep 검색 + 링크 | 검색한 뒤 링크로 이어진 항목까지 | **39.44** |

- si-prep 검색만 − B0-R = **+15.83** [+9.44, +22.22]
- si-prep 검색 + 링크는 세 기준선을 모두 앞섰습니다: B0-RC +15.28 · B0-S +13.06 · B0-R +21.11 (모두 하한 > 0)
- **링크 자체의 효과**(검색 + 링크 − 검색만)는 +5.28 [−2.22, +12.50]으로 **확인하지 못했습니다.**
- 다른 데이터에서는 차이가 없었던 경우도 있어, "구조화가 항상 이긴다"고 보지 않습니다.

### 비교별 상세

| 비교 | 차이(%p) | 95% 신뢰구간 | 높음 / 낮음 / 같음 | 가려낼 수 있는 최소 차이* | 판정 |
|---|---|---|---|---|---|
| 검색 + 링크 − B0-RC | +15.28 | [+5.56, +25.56] | 14 / 3 / 13 | 15.06 | 통과 |
| 검색 + 링크 − B0-S | +13.06 | [+1.67, +25.00] | 14 / 5 / 11 | 17.94 | 통과 |
| 검색 + 링크 − B0-R | +21.11 | [+12.22, +30.56] | 17 / 1 / 12 | 13.79 | 통과 |
| 검색만 − B0-R | +15.83 | [+9.44, +22.22] | 14 / 0 / 16 | 9.72 | 통과 |
| 검색 + 링크 − 검색만 (링크 효과) | +5.28 | [−2.22, +12.50] | 8 / 3 / 19 | 11.08 | 미통과 |

\* 질문 30개, 이번 차이의 흩어짐으로 계산한 값입니다. 이보다 작은 차이는 우연과 구분하기 어렵습니다. 높음/낮음/같음은 30문항 중 앞쪽 방식이 높았던·낮았던·같았던 문항 수입니다.

### 같은 예산 안에서 비교했는지

| 방식 | 질문당 카드 수(중앙값) | 질문당 쓴 토큰(중앙값, 예산 4,000) |
|---|---|---|
| B0-R | 14 | 3,841 |
| B0-RC | 13 | 3,824.5 |
| B0-S | 10 | 3,850.5 |
| si-prep 검색만 | 16 | 3,902 |
| si-prep 검색 + 링크 | 15 | 3,834 |

다섯 방식 모두 예산을 거의 다 채웠고, 질문당 카드 수도 10~16장으로 비슷합니다.

### 보조 지표 (판정에 쓰지 않음)

| 방식 | 근거 정밀도(정답 행 / 담은 행) | 요구사항 근거를 찾은 질문 | 정책 근거를 찾은 질문 |
|---|---|---|---|
| B0-R | 3.88% (17/442) | 21/21 | 20/25 |
| si-prep 검색만 | 7.62% (35/467) | 21/21 | 24/25 |
| si-prep 검색 + 링크 | 8.86% (40/454) | 21/21 | 24/25 |

B0-RC·B0-S는 보조 지표 도구의 범위 밖이라 계산하지 않았습니다.

### [사후] 문서 종류별로 나눠 본 점수

결과를 본 뒤 원인을 찾으려고 나눠 본 값이라 판정에는 쓰지 않습니다.

| 질문 묶음 | B0-RC | B0-S | B0-R | 검색만 | 검색 + 링크 |
|---|---|---|---|---|---|
| 필수 근거에 요구사항이 있는 질문(21개) | 7.14 | 23.02 | 16.67 | 19.05 | 46.83 |
| 필수 근거에 정책이 있는 질문(25개) | 30.67 | 27.67 | 19.67 | 46.00 | 42.00 |

### 재현성

- 같은 입력과 설정으로 다섯 방식을 다시 돌리면 30문항 모두 담은 카드와 점수가 같게 나옵니다.
- 검색 폭(`HNSW_EF_SEARCH` 4000)이 카드 수보다 커서 사실상 전수 검색이므로, 다시 색인해도 결과가 바뀌지 않습니다.

## 빠르게 시작하기

평가에 쓴 si-prep 버전은 태그 `main-b1-frozen`(출력 스키마 1.4)입니다.

```sh
# 1. si-prep 설치(청크 검증에 스키마를 불러다 씁니다)
git clone https://github.com/sSFireFoxSs/si-prep
cd si-prep && git checkout main-b1-frozen && pip install -e . && cd ..

# 2. rag-core 설치
git clone https://github.com/joonki24/rag-core
cd rag-core && pip install -r requirements.txt
```

```sh
cd scripts/rag_core
# 정답지 엑셀 → 정답표, 사람이 확인
python gold_xlsx_convert.py convert <정답지.xlsx> <정답표 폴더>
python gold_xlsx_convert.py confirm <정답표 폴더> --by <이름> --reason <사유>

# 한 방식 실행(b0_r, b0_s, b1_search, b1_link)
python eval_runner.py --arm b1_link --chunks <chunks.jsonl> --links <links.jsonl> --items <items.jsonl> \
       --gold-dir <정답표 폴더> --out <출력 폴더> --purpose dev --chunk-build b1 --reindex

# 두 실행 비교(점수 차이, 95% 신뢰구간, 통과 판정)
python ../compare_runs.py --target <실행 폴더> --base <실행 폴더> --purpose dev
```

## 출력 예시

공개용 합성 문서 세트(서비스 E) 평가에서 나온 실제 출력을 줄여서 보여 드립니다. `…`는 생략한 부분입니다.

**1. 정답표** (`gold_xlsx_convert.py`가 정답지 엑셀에서 만든 JSON, 질문 하나)
```json
{"case_id": "Q-02", "type": "신규 기능 N1",
 "request": "게시글을 작성할 때 내일 오전 9시에 자동 공개되도록 예약 기능을 추가하려 합니다. 현재 문서 기준 영향 범위를 알려주세요.",
 "gold_evidence": [
   {"file": "01_요구사항정의_분석서_v0.3.xlsx", "sheet": "요구사항_정규화", "cells": ["F20:H20"], "required": true,  "unit": "Q-02-U1", "bundle": "대체 A"},
   {"file": "02_서비스_정책_정의서_v0.1.xlsx",   "sheet": "게시글·피드",     "cells": ["C5:G5"],   "required": true,  "unit": "Q-02-U1", "bundle": "대체 B"},
   {"file": "01_요구사항정의_분석서_v0.3.xlsx", "sheet": "요구사항_정규화", "cells": ["F22:H22"], "required": true,  "unit": "Q-02-U2", "bundle": "대체 A"},
   {"file": "03_사용자_관리자_기능정의서_v0.3.xlsx", "sheet": "기능_정규화", "cells": ["N70:N70"], "required": false, "unit": "Q-02-US1", "bundle": "보조 기능"},
   "…"],
 "slot_rule": "unit_bundle"}
```
사람이 `confirm`으로 확인한 정답표만 평가에 쓰입니다. `required: true`인 근거만 점수에 들어가고, 같은 `unit` 안에서는 묶음(`bundle`) 하나만 맞혀도 그 단위를 찾은 것으로 봅니다.

**2. 실행 결과** (`eval_runner.py`, `run_result.json`의 질문 하나, 검색 + 링크)
```json
{"id": "Q-02", "type": "신규 기능 N1", "score": "1/1", "score_pct": 100.0, "used_tokens": 3948,
 "required": [
   {"file": "01_요구사항정의_분석서_v0.3.xlsx", "sheet": "요구사항_정규화", "rows": [20], "hit": true, "ratio": 1.0},
   {"file": "02_서비스_정책_정의서_v0.1.xlsx",   "sheet": "게시글·피드",     "rows": [5],  "hit": true, "ratio": 1.0},
   {"file": "01_요구사항정의_분석서_v0.3.xlsx", "sheet": "요구사항_정규화", "rows": [22], "hit": true, "ratio": 1.0}],
 "via": {"B1-IT-000005": "req_search", "B1-IT-000027": "link", "B1-IT-000147": "search", "B1-IT-000484": "search_topup", "…": "…"},
 "link_report": {"seeds": 10, "link_chunks": 5, "link_chunks_by_hops": {"1": 5}, "search_cap": 2000, "link_cap": 2000,
                 "used_link_tokens": 1523, "priority": {"req_cap": 500, "req_direct": 3, "req_tokens": 346}}}
```
`via`는 카드마다 어떻게 담겼는지(요구사항 먼저 검색, 일반 검색, 링크, 남은 예산 채우기)를 남깁니다. 이 질문에서는 요구사항 20행이 링크로 들어온 카드(`B1-IT-000027`)로 채워졌습니다.

**3. 두 실행 비교** (`compare_runs.py` 출력)
```
[평가용 비교] 대상 b1_link 대 기준 b0_r, 데이터셋 finalc, 질문 30개, 쌍대 부트스트랩 시드 0 · 10,000회
예산 | 대상 점수 | 기준 점수 | 차이(%p) | 95% 신뢰구간(%p) | 대상이 높음/낮음/같음 | 질문당 카드(중앙) 대상 / 기준 | 읽는 법
4000 | 39.44 | 18.33 | +21.11 | [+12.22, +30.56] | 17/1/12 | 15 / 14 | 신뢰구간이 0을 포함하지 않음, 점수 상한 대상 100.0 기준 100.0
통과 판정(채점표 변경 22, 기준 B): 통과 (신뢰구간 하한 +12.22%p > 0, 점 추정 +21.11%p, 서비스 출시 목표 달성(+14%p 이상))
```

**4. 동결 파일** (`eval_gate.py freeze`, 평가 전에 만들고 실행 때마다 대조)
```json
{"id": "B1-FINALC", "date": "2026-10-16", "freeze_version": 2, "commit": "3e8942c5…",
 "token_budget": 4000, "candidate_pool": 100, "strategy": "original",
 "settings_sha256": "ff7a0095…", "gold_json_sha256": "c89cc751…",
 "chunks_sha256": {"b0_r": "802b4207…", "b0_s": "ab2325ba…", "b1_search": "384ee8ce…", "b1_link": "384ee8ce…"},
 "items_sha256": "7a3687f5…", "links_sha256": "6267ba9e…",
 "reason": "… 최종 평가(B0-R·B0-S·B1 검색만·B1 검색+연결, 결과 전 등록)"}
```
코드 커밋, 설정, 정답표, 입력 파일의 지문을 결과 전에 고정합니다. 하나라도 바뀌면 평가용 실행이 멈춥니다.

## 더 읽을 곳

평가용 실행 절차와 기준선 B0-RC 비교 방법은 [사용법](scripts/rag_core/README.md)에 있습니다. 처음 코드를 읽는다면 [코드 안내](scripts/rag_core/코드_안내.md)부터 보시기 바랍니다.

## 한계

- 판정은 **검색 단계**까지입니다. LLM 답변의 품질은 재지 않습니다.
- 데이터마다 결과가 달랐습니다. 질문이 30개라 작은 차이는 가려내기 어렵습니다.
- si-prep 출력 스키마(1.2~1.4)에 맞춰져 있습니다. 다른 청크 형식을 쓰려면 어댑터를 바꿔야 합니다.

## 만든 사람

- 검색 · 평가 코드(rag-core) 설계 · 구현: Joonki Yoon
- 전처리 도구(si-prep) 설계 · 구현: KimSeungJu — [si-prep](https://github.com/sSFireFoxSs/si-prep)

## 라이선스

[MIT](LICENSE)
