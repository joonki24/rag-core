<h1 align="center">rag-core</h1>

<p align="center">
  <i>전처리가 정말 검색을 낫게 했는지, 같은 조건에서 숫자로 확인하는 RAG 평가 코드</i>
</p>

<div align="center">
<table>
  <tr>
    <td align="left">
      <img src="https://img.shields.io/badge/python-3.14%20tested-2563EB?style=flat-square" alt="python"><br>
      <img src="https://img.shields.io/badge/license-MIT-2563EB?style=flat-square" alt="license"><br>
      <img src="https://img.shields.io/badge/embedding-local%20bge--m3-2563EB?style=flat-square" alt="embedding"><br>
      <img src="https://img.shields.io/badge/LLM%20grading-none-2563EB?style=flat-square" alt="LLM grading"><br>
      <img src="https://img.shields.io/badge/status-beta-2563EB?style=flat-square" alt="status">
    </td>
  </tr>
</table>
</div>

<p align="center">
  <a href="#빠르게-시작하기">빠르게 시작하기</a> ·
  <a href="#평가-결과">평가 결과</a> ·
  <a href="scripts/rag_core/README.md">사용법</a> ·
  <a href="scripts/rag_core/코드_안내.md">코드 안내</a> ·
  <a href="https://github.com/sSFireFoxSs/si-prep">si-prep</a>
</p>

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

평가용 실행(`--purpose eval`)과 기준선 B0-RC 비교 방법은 [사용법](scripts/rag_core/README.md)에 있습니다. 처음 코드를 읽는다면 [코드 안내](scripts/rag_core/코드_안내.md)부터 보시기 바랍니다.

## 한계

- 판정은 **검색 단계**까지입니다. LLM 답변의 품질은 재지 않습니다.
- 데이터마다 결과가 달랐습니다. 질문이 30개라 작은 차이는 가려내기 어렵습니다.
- si-prep 출력 스키마(1.2~1.4)에 맞춰져 있습니다. 다른 청크 형식을 쓰려면 어댑터를 바꿔야 합니다.

## 만든 사람

- 검색 · 평가 코드(rag-core) 설계 · 구현: Joonki Yoon
- 전처리 도구(si-prep) 설계 · 구현: KimSeungJu — [si-prep](https://github.com/sSFireFoxSs/si-prep)

## 라이선스

[MIT](LICENSE)
