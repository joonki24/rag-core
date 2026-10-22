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
  <a href="#무엇을-확인했나">무엇을 확인했나</a> ·
  <a href="#결과">결과</a> ·
  <a href="#한-질문이-처리되는-모습">시연</a> ·
  <a href="#만들면서-해결한-문제">해결한 문제</a> ·
  <a href="#직접-실행하기">직접 실행하기</a> ·
  <a href="https://github.com/sSFireFoxSs/si-prep">si-prep</a>
</p>

---

## 무엇을 확인했나

시스템 구축 프로젝트에서는 요구사항·정책·기능·테스트 문서를 엑셀로 주고받습니다. 이 문서에 질문하면 AI가 관련 행을 찾아 답해 주는 서비스를 만들려면, 먼저 **질문에 맞는 근거 행을 잘 찾아와야** 합니다(이렇게 문서에서 근거를 찾아 AI에게 건네는 방식을 RAG라고 부릅니다).

팀원이 만든 전처리 도구 [si-prep](https://github.com/sSFireFoxSs/si-prep)은 엑셀을 항목 단위로 정리하고, 문서 사이의 연결(같은 ID, 같은 분류)을 복원합니다. rag-core는 **"si-prep을 거치면 흔히 쓰는 방식보다 정답 근거를 더 많이 찾는가"를 같은 조건에서 숫자로 확인하는 평가 코드**입니다.

| 원칙 | 내용 |
|---|---|
| 같은 조건 | 다섯 방식 모두 같은 질문 30개, 같은 검색 모델, 질문당 같은 분량(4,000토큰)의 근거만 담습니다. |
| 정답으로만 채점 | 질문마다 사람이 미리 정해 둔 정답 행을 찾아왔는지 코드가 셉니다. AI에게 채점을 맡기지 않습니다. |
| 우연과 구분 | 질문을 다시 뽑아 10,000번 계산해, 차이가 0보다 확실히 클 때만 "차이가 있다"고 봅니다. |
| 결과를 보고 바꾸지 않기 | 평가 전에 코드·설정·정답·입력 파일을 고정하고, 하나라도 바뀌면 평가용 실행이 멈춥니다. |
| 내 PC에서만 실행 | 검색 모델은 로컬에서 돌리고, 외부 API 호출은 기본으로 막혀 있습니다. |

<p align="center">
  <img src="docs/images/architecture.png" alt="엑셀 문서가 si-prep 처리와 흔히 쓰는 자르기 방식으로 나뉘어 들어가고, rag-core가 같은 조건에서 정답과 대조해 채점하는 흐름" width="100%">
</p>

<p align="center"><sub>청크 = 검색하려고 잘라 둔 문서 조각 · 기준선 = 전처리 없이 흔히 쓰는 비교 기준(B0-R, B0-S, B0-RC) · 정답표 = 사람이 정해 둔 정답 근거 목록</sub></p>

## 결과

공개용으로 만든 가상 서비스 문서 세트(엑셀 5개, 질문 30개)의 결과입니다. 결과를 보기 전에 방식과 판정 기준을 등록하고 한 번만 실행했습니다.

<p align="center">
  <img src="docs/images/recall.png" alt="정답 근거 회수율(%): B0-R 18.33, B0-RC 24.17, B0-S 26.39, si-prep 검색만 34.17, si-prep 검색+링크 39.44" width="100%">
</p>

| 방식 | 문서를 어떻게 나눠 찾나 | 정답 근거를 찾은 비율(%) |
|---|---|---|
| B0-R | 엑셀 한 행 = 카드 한 장 | 18.33 |
| B0-RC | 글자를 500자씩 자르기 | 24.17 |
| B0-S | 행을 "칸 이름: 값"으로 바꾼 뒤 500자씩 | 26.39 |
| si-prep 검색만 | 분류 경로가 붙은 si-prep 항목으로 검색 | 34.17 |
| si-prep 검색 + 링크 | 검색한 뒤 링크로 이어진 항목까지 | **39.44** |

B0로 시작하는 세 방식은 전처리 없이 흔히 쓰는 비교 기준입니다.

- **si-prep 검색 + 링크는 비교 기준 세 개를 모두 앞섰습니다.** 차이는 +13.06 ~ +21.11 포인트이고, 질문을 다시 뽑아 계산한 범위(95% 신뢰구간)가 세 비교 모두 0보다 큽니다.
- si-prep 검색만도 B0-R보다 +15.83 포인트 높았습니다(범위 +9.44 ~ +22.22).
- **링크 따라가기만의 효과는 확인하지 못했습니다.** 검색만보다 +5.28 포인트 높았지만, 범위가 −2.22 ~ +12.50으로 0을 포함합니다.
- 다른 문서 세트에서는 차이가 없었던 경우도 있어, "전처리가 항상 이긴다"고 보지 않습니다.

비교별 수치, 방식별 카드 수, 보조 지표는 [평가 결과 상세](docs/평가결과_상세.md)에 있습니다.

## 한 질문이 처리되는 모습

같은 질문(Q-02, 게시글 예약 공개)에서 다섯 방식이 받은 카드와 정답 근거를 몇 개 찾았는지를 차례로 보여 주는 시연 화면입니다. 이 질문에서는 비교 기준 세 개가 0/2, si-prep 검색만이 1/2, si-prep 검색 + 링크가 2/2를 찾았습니다. 질문 하나의 예시이며, 30문항 전체 결과는 위와 같습니다.

<p align="center">
  <img src="docs/images/demo-compare.gif" alt="질문 Q-02의 정답 근거 2단위를 B0-R, B0-S, B0-RC는 하나도 찾지 못하고, si-prep 검색만은 1단위, si-prep 검색 + 링크는 2단위를 찾는 비교 화면" width="100%">
</p>

<details>
<summary>찾은 근거로 AI가 답하는 화면 보기 (Q-28)</summary>

검색 → 링크 따라가기 → AI에게 전달 → 답 작성 순서로 처리됩니다. 답의 번호를 누르면 그 근거 카드와, 링크로 받은 카드가 어느 카드에서 이어졌는지가 보입니다. 답은 로컬 AI 모델(EXAONE 3.5 7.8B)을 한 번 실행한 기록입니다.

<p align="center">
  <img src="docs/images/demo-answer.gif" alt="질문 Q-28을 고르면 검색으로 카드 7장, 링크로 5장, 남은 분량을 채우는 검색으로 3장을 받고, AI가 카드 번호를 붙여 답을 쓰는 과정" width="100%">
</p>

</details>

시연 화면은 평가 점수가 아닌 설명용이며, 화면 코드와 데이터는 이 저장소에 없습니다.

## 만들면서 해결한 문제

| 문제 | 어떻게 했나 |
|---|---|
| 비교 기준의 점수를 원하는 수준(70~80%)에 맞추려고, 모든 방식이 담는 근거 분량을 올리려 했습니다. | 목표 점수에 맞춰 조건을 바꾸면 비교가 의미를 잃어서 되돌리고, 실제 이유를 기록에 남겼습니다. 분량은 나중에 점수를 보지 않는 규칙(질문마다 필요한 정답 근거를 모두 담을 수 있는 크기)으로 다시 정했습니다. |
| "비교 기준이 너무 약하지 않냐"는 의견이 반복됐습니다. | 원래 기준은 그대로 두고, 흔히 쓰는 방식 두 개(B0-S, B0-RC)를 더해 세 기준과 비교했습니다. 모두 결과를 보기 전에 등록했습니다. |
| 시트 이름 끝의 공백을 코드가 지워, 정답 근거 한 건이 문서와 맞지 않았습니다. | 점수가 조용히 0이 되지 않고 실행이 멈추게 해 두어서 발견했습니다. 이름은 원본 그대로 쓰게 고치고, 지저분한 이름을 넣은 시험을 더했습니다. |
| 평가 코드와 기획 쪽 규칙이 "같은 묶음의 정답"을 세는 방식을 서로 반대로 정하고 있었습니다. | 규칙 문서와 채점 코드를 한 줄씩 대조해 찾았고, 기존 결과가 바뀌지 않게 새 규칙을 따로 더했습니다. |
| 개발 중에 계속 돌리다 보면 결과를 보고 고치게 됩니다. | 평가용 실행은 코드·설정·정답·입력 파일을 고정한 뒤 한 번만 돌리게 하고, 통과 판정도 코드가 내리게 했습니다. |

## 직접 실행하기

평가 데이터와 정답은 이 저장소에 없습니다. si-prep으로 처리한 자기 엑셀 문서와 정답지로 돌릴 수 있습니다. 평가에 쓴 si-prep 버전은 태그 `main-b1-frozen`입니다.

```sh
# 1. si-prep 설치(입력 파일 형식을 확인하는 데 씁니다)
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

실제 출력 모양은 [출력 예시](docs/출력예시.md), 평가용 실행 절차는 [사용법](scripts/rag_core/README.md), 코드 구조는 [코드 안내](scripts/rag_core/코드_안내.md)에 있습니다.

## 한계

- 평가는 **근거를 찾아오는 단계**까지입니다. AI가 쓴 답의 품질은 재지 않습니다.
- 질문이 30개라 작은 차이는 우연과 가려내기 어렵고, 문서 세트마다 결과가 달랐습니다.
- si-prep의 출력 형식(버전 1.2~1.4)에 맞춰져 있습니다. 다른 형식을 쓰려면 읽는 부분을 바꿔야 합니다.
- 베타(v0.1.0)입니다. 설정 형식과 결과 파일의 칸은 바뀔 수 있습니다.

## 만든 사람

- 검색 · 평가 코드(rag-core) 설계 · 구현: Joonki Yoon
- 전처리 도구(si-prep) 설계 · 구현: KimSeungJu — [si-prep](https://github.com/sSFireFoxSs/si-prep)

## 라이선스

[MIT](LICENSE)
