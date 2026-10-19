# rag-core

**si-prep 출력(청크·항목·링크)으로 RAG 검색 품질을 재는 평가 코드**

SI 프로젝트 엑셀 산출물을 전처리 도구 [si-prep](https://github.com/sSFireFoxSs/si-prep)으로 바꾼 결과가, 흔히 쓰는 청킹 방식보다 정답 근거를 더 잘 찾는지를 같은 조건에서 비교합니다.

- 기준선 3종(500자 분할, 칸 이름 + 500자, 행 단위)과 si-prep 두 방식(검색만, 검색 + 링크)을 나란히 비교
- 점수는 질문마다 정답 근거 행을 찾아온 비율(회수율). LLM으로 채점하지 않습니다
- 질문 단위 쌍대 부트스트랩 10,000회, 95% 신뢰구간 하한 > 0이면 통과
- 평가 전에 코드·설정·입력의 지문을 동결하고, 입력이 바뀌면 멈춥니다
- 임베딩은 로컬(bge-m3)만 쓰고 외부로 보내지 않습니다

자세한 사용법은 [`scripts/rag_core/README.md`](scripts/rag_core/README.md), 처음 읽는 분은 [`scripts/rag_core/코드_안내.md`](scripts/rag_core/코드_안내.md)부터 보세요.

## si-prep과의 관계

- rag-core는 si-prep이 만든 `chunks.jsonl`, `items.jsonl`, `links.jsonl`을 **읽기만** 하고, 청크 검증은 si-prep의 `schema.py`를 불러다 씁니다. 경계는 [`scripts/rag_core/INTERFACE.md`](scripts/rag_core/INTERFACE.md)에 있습니다.
- 평가에 쓴 si-prep 버전: 태그 `main-b1-frozen`(커밋 `ce9aa68`, 출력 스키마 1.4)

```
git clone https://github.com/sSFireFoxSs/si-prep && cd si-prep && git checkout main-b1-frozen && pip install -e .
cd <rag-core> && pip install -r requirements.txt
```

## 평가 결과 (공개 가능한 데이터 하나)

> **재현 범위:** 아래 수치는 평가 데이터와 정답지가 있는 팀 환경에서 잰 값입니다. 데이터와 정답지는 이 저장소에 없어서, 이 저장소만으로는 재현할 수 없습니다.

- 데이터: 공개용 합성 SI 문서 세트 1개(서비스 E), 질문 30개. 정답지는 AI 초안을 사람이 표본 검수
- 조건: 같은 임베딩(bge-m3), 같은 근거 예산 4,000토큰, 같은 후보 100개, 리랭커 없음. 실행 전에 등록하고 한 번만 실행

| 방식 | 회수율(%) |
|---|---|
| B0-R: 엑셀 한 행 = 카드 한 장 | 18.33 |
| B0-RC: 글자 500자씩 분할 | 24.17 |
| B0-S: 행을 "칸 이름: 값"으로 바꾼 뒤 500자씩 | 26.39 |
| si-prep, 검색만 | 34.17 |
| si-prep, 검색 + 링크 | 39.44 |

- si-prep(검색만) − B0-R = **+15.83** [+9.44, +22.22]
- si-prep(검색 + 링크)은 세 기준선을 모두 앞섰습니다: B0-RC +15.28 · B0-S +13.06 · B0-R +21.11 (모두 하한 > 0)
- **링크 자체의 효과**(검색 + 링크 − 검색만)는 +5.28 [−2.22, +12.50]으로 **확인하지 못했습니다.**
- 다른 데이터에서는 차이가 없었던 경우도 있어, "구조화가 항상 이긴다"고 보지 않습니다.

## 만든 사람

- 검색 · 평가 코드(rag-core) 설계 · 구현: Joonki Yoon
- 전처리 도구(si-prep) 설계 · 구현: KimSeungJu — [si-prep](https://github.com/sSFireFoxSs/si-prep)

## 라이선스

[MIT](LICENSE)
