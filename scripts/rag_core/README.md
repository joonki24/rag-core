# rag_core

SI 문서(엑셀 산출물) RAG의 **검색, 근거 모으기, 채점**을 하는 평가 코드입니다. 청크 파일을 받아 색인하고, 질문마다 검색해 정해진 토큰 예산까지 근거를 모은 뒤, 정답표와 비교해 점수를 냅니다. 기준선(B0)과 전처리 결과(B1)를 같은 조건으로 비교하는 것이 목적입니다.

> **처음 보는 사람은 [`코드_안내.md`](코드_안내.md)부터 읽으세요**(흐름 그림, 읽는 순서, 파일 지도, 용어).

> **포함 범위:** 평가를 한 번 돌리는 데 쓰는 코드와 설정만 담은 최소 구성입니다. 시험, 개발 변경 기록, 보조 실험용 코드, 평가 데이터와 정답지는 포함하지 않았습니다. 코드 주석의 "묶음 N"은 비공개 개발 기록의 번호입니다. 데이터는 평가 순서대로 서비스 A~E로 부릅니다.

## 하는 일과 하지 않는 일
| 하는 일 | 하지 않는 일 |
|---|---|
| 청크 파일 읽기와 검증, 색인(로컬 임베딩) | 청크를 만드는 일(B1은 si-prep이 만든다. 기준선 B0 청크만 여기서 만든다) |
| 질문마다 검색 → 토큰 예산까지 근거 담기 | 답변 생성·답변 품질 평가(판정은 검색 단계까지) |
| 정답지 엑셀 → 정답표 변환, 변경 감지, 사람 확인 | 점수를 보고 설정이나 기준을 바꾸는 일 |
| 채점(필수 근거 적중률, 단위·묶음 규칙), 두 실행 비교(신뢰구간)와 통과 판정 | |
| B1 연결 따라가기, 문서 우선순위로 근거 담기 | |

## 파일 구조
| 파일 | 역할 |
|---|---|
| `adapter.py` | 청크 파일을 읽고 검증(si-prep의 `si_prep.schema`), 색인 연결 |
| `b0_chunks.py` | 기준선 청크 생성. B0-R: 행 하나가 청크(`열이름: 값`). B0-S: 같은 읽기 범위의 `열이름: 값` 줄을 500자 창(겹침 50)으로 자름. 프로필의 읽기 범위 칸(시트, 머리글 행, 데이터 시작 행)만 읽고, 판단 칸(분류 열, 계층, 한 시트 여러 표 `tables` 등)은 읽지 않고 보고만 함. 시트별 보고에 숨김 행·숨김 열 수(`hidden_rows`, `hidden_cols`, `hidden_col_cells`)도 보고만 하고 청크는 바꾸지 않음(B0는 숨김 열 글도 담음) |
| `settings.py` + `experiment.yaml` | 실험 고정값(모델, 예산, 후보 수 등)을 읽고 어기면 멈춤. 선택 칸 `evidence.doc_priority` 검사 |
| `evidence.py` | 검색 순위대로 토큰 예산을 넘기 직전까지 담기, 우선순위 설정이 있으면 요구사항 먼저 담기, 다양화 설정이 있으면 무리당 장수 제한 |
| `b0_recursive.py` | 기준선 B0-RC 청크 생성: 일반 리커시브 분할(500자, 겹침 50). 모든 시트를 프로필 없이 읽고 열 이름은 붙이지 않음. 평가기에는 B0-S 칸(`--arm b0_s --chunk-build b0_none`)으로 넣는다 |
| `diversity.py` | 선택 기능: 다양화 담기(같은 분류 경로 또는 같은 행 구간 카드를 무리당 N장까지). 설정이 없으면 쓰지 않음 |
| `priority.py` | 문서 종류 판별, 우선순위 순위, 요구사항 지표, 카드 표시(문서 종류·등급) |
| `link_expansion.py` | B1 연결 따라가기(`links.jsonl`, `items.jsonl` 필요) |
| `hybrid_search.py`, `topic_search.py` | 예전에 시험한 검색 방식(키워드+벡터, 점검표 주제별). `eval_runner.py`가 불러오지만 평가용 실행에서는 기본 방식만 허용 |
| `gold_xlsx_convert.py` + `gold_format*.yaml` | 정답지 엑셀 → 정답표 JSON, 열 이름·양식 설정. 양식 프로필 v0.5, v0.2, b-v0.1(서비스 B), c-v0.1(서비스 C), e-v0.2(서비스 E 공개 문서, `gold_format_fmte02.yaml` 포함), d-v0.1. 다른 양식의 설정 파일(실제 파일 이름·지문이 든 것 포함)은 공유본에 없음 |
| `scoring.py` | 정답표 읽기, 채점, 비교 계산 |
| `eval_runner.py` | 질문 → 검색 → 근거 → 채점을 한 번에 돌려 결과 파일로 남김 |
| `eval_gate.py` | 평가용 실행의 동결 파일(코드·설정·입력 지문)을 만들고 검사 |
| `../compare_runs.py` | 두 실행을 비교(질문 단위 쌍대 부트스트랩)하고 통과 판정(신뢰구간 하한 > 0, +14%p 이상이면 "서비스 출시 목표 달성" 표시) |
| `pipeline/` | Chroma 서랍과 로컬 임베딩, 검색 |
| `config.py` | `.env` 읽기, 외부 API 호출 차단 |
| `코드_안내.md` | 처음 보는 사람용 안내(흐름, 읽는 순서, 파일 지도, 용어) |
| `INTERFACE.md` | si-prep과의 경계 |

## 흐름
```
정답지 엑셀 --convert--> 정답표 JSON --confirm(사람)--> 확인된 정답표
청크 jsonl --색인--> Chroma 서랍
확인된 정답표 + 서랍 --eval_runner--> 질문마다 검색 → 근거 → 채점 → run_result.json
두 실행 --compare_runs--> 점수 차이, 95% 신뢰구간, 통과 판정
```

## 설치
1. `pip install -r requirements.txt` (`pydantic>=2.12`가 필요합니다)
2. si-prep(스키마 **1.4**, 1.3·1.2로 만든 파일도 통과)을 받아 `pip install -e <si-prep 폴더>`. 청크 검증은 si-prep의 `schema.py`를 불러다 쓰고 복사하지 않습니다. 실행 기록(`run_info`)에 스키마 버전과 `schema.py` sha가 남습니다.
3. 임베딩 모델 `BAAI/bge-m3`을 로컬에 내려받아 둡니다(실행은 외부로 보내지 않습니다).

## 평가 한 번 돌리기(개발용)
```
python gold_xlsx_convert.py convert <정답지.xlsx> <정답표 폴더>
python gold_xlsx_convert.py confirm <정답표 폴더> --by <이름> --reason <사유>
python eval_runner.py --arm b0_r --chunks <청크.jsonl> --gold-dir <정답표 폴더> --out <출력 폴더> \
       --purpose dev --chunk-build b0_profile [--reindex] [--settings <설정.yaml>]
# b1_link 팔: --links <links.jsonl> --items <items.jsonl> 추가
python ../compare_runs.py --target <실행 폴더> --base <실행 폴더> --purpose dev
```
평가용(`--purpose eval`)은 먼저 `python eval_gate.py freeze ...`로 동결 파일을 만들어야 합니다.

기준선 B0-RC는 청크를 따로 만들고, 평가기의 B0-S 칸으로 넣습니다(B0-R·B0-S와 데이터셋 이름을 달리 한 설정과 동결을 따로 둠).
```
python b0_recursive.py --files <엑셀 ...> --out <저장소 밖 폴더>
python eval_runner.py --arm b0_s --chunks <b0_rc_chunks.jsonl> --chunk-build b0_none ...
```
`compare_runs.py`는 두 실행의 데이터셋 이름이 같아야 비교하므로, B1과 B0-RC 비교는 멈춥니다. 이 비교는 같은 계산을 직접 부릅니다(점수·구간·판정 문구가 `compare_runs`와 같음).
```python
# scripts 폴더에서 실행
import sys; sys.path.insert(0, "rag_core")
import scoring, compare_runs
b1, rc = compare_runs.load_run("<b1_link 실행 폴더>"), compare_runs.load_run("<B0-RC 실행 폴더>")
c = scoring.compare(b1, rc)                      # 차이, 95% 구간, 높음/낮음/같음
print(c["diff_pp"], c["ci95_pp"], compare_runs.judge_pass(c)["text"])
```

## 평가 순서(평가용)
```
python eval_gate.py freeze --id <동결 이름> --reason <사유> --settings <설정> --gold-dir <정답표> \n       --chunks b0_r=<청크> b1_link=<청크> ... --builds b0_r=b0_profile b1_link=b1 ... --links <links> --items <items> --out <동결 파일>
# → 팔마다 eval_runner(--purpose eval --freeze <동결 파일>), 결과는 <출력 루트>/<동결 이름>/<팔>/ → compare_runs --purpose eval
```
비교는 기준선 3개(B0-RC / B0-S / B0-R)와 B1 2개(검색만 / 검색+연결)를 나란히 둡니다(2026-10-16 결정, 주 비교 없음): B1 검색+연결 − 각 기준선, B1 검색만 − B0-R, 링크 효과(검색+연결 − 검색만). 각 비교는 95% 신뢰구간 하한 > 0이면 통과이고, 서비스 출시 목표 +14%p는 따로 표시합니다(2026-10-14 변경. 그 전에는 +14%p 이상이고 하한 > 0. 이미 낸 판정은 그대로). 질문이 30개보다 적으면 코드는 판정 문구를 내지 않고 사람이 같은 기준을 적용합니다. 예산 4,000, 후보 100, 요구사항 상한 500은 고정입니다.

## 문서 우선순위 설정(선택)
`experiment.yaml`의 `evidence.doc_priority`를 적으면 요구사항을 상한까지 먼저 담고 남은 예산을 채웁니다. 적지 않으면 기본 동작(검색 순위대로)입니다.
```yaml
evidence:
  doc_priority:
    project_yaml: ../project.yaml     # 순서는 이 파일의 doc_priority: [req, fo, policy]에서 읽는다
    first_group: req                  # 먼저 담을 문서 종류
    first_cap_tokens: 500             # 그 몫의 토큰 상한
    b0_project_yaml: ../project_b0.yaml   # 청크에 doc_type이 없을 때(B0) 이 파일의 documents(파일 → 프로필 id)로 가름
    # file_patterns: {req: 요구사항, fo: F_O, bo: B_O, policy: 정책, test: 테스트}   # 또는 파일 이름 조각으로 가름(둘 중 하나만)
    rest_order: distance              # 나머지는 검색 순위대로(priority면 문서 종류 순서로)
    label_cards: true                 # 카드마다 문서 종류와 우선순위 등급을 결과에 기록(점수에는 영향 없음)
```
순서를 직접 적으려면 `project_yaml` 대신 `order: [req, [fo, bo], policy]`를 씁니다(둘 중 하나만). 문서 종류를 가를 수 없는 파일이 있으면 조용히 기타로 두지 않고 멈춥니다.

보조 지표 "문서 종류 커버리지"의 묶음은 기본이 요구사항 `req`, 기능 `fo`·`bo`, 정책 `policy`, 테스트 `test`입니다. 문서 종류 id가 다르면(예: 기능 문서가 `func`) `doc_priority` 아래에 `coverage_groups: {요구사항: [req], 기능: [func], 정책: [policy], 테스트: [test]}`처럼 적습니다. 아는 문서 종류를 빠짐없이 한 번씩만 넣어야 하고, 어기면 멈춥니다. 적었을 때만 동결 지문에 들어갑니다.

## 다양화 담기(선택)
`experiment.yaml`의 `evidence.diversity`를 적으면 같은 무리의 카드를 `max_per_group`장까지만 담습니다. 적지 않으면 지금 동작과 같고 동결 지문도 바뀌지 않습니다.
```yaml
evidence:
  diversity:
    mode: parent_path        # 같은 분류 경로끼리 한 무리
    max_per_group: 3
    # mode: row_block + block_rows: 10   # 같은 시트의 행 구간끼리 한 무리(doc_priority.b0_project_yaml 필요)
```
검색 방식이 dense일 때만 쓸 수 있습니다.

## 연결 따라가기 기본값
`experiment.yaml`의 `links`: `hops: 1`(1단계까지), `link_order: distance`(질문과 가까운 순), 등급은 확정(explicit, structural)만. 끝까지 따라가는 옛 방식은 `hops: all`로 바꾼 보조 설정에서만 씁니다(판정에는 쓰지 않음). 추정 연결(inferred)은 보조 팔에서 `links.allow_inferred: true`를 함께 적었을 때만 `grades`에 넣을 수 있습니다(true 외의 값은 멈춤). 연결 후보 순서: 같은 단계에서 모든 출발점의 항목↔항목 이웃을 출발점 순서대로(출발점 안에서는 질문과 가까운 순) 먼저 모으고, 그다음 묶음 이웃(묶음 안 항목 상위 3개, 묶음을 가리키는 항목 상위 3개)을 모읍니다. 이미 본 항목은 처음 나온 자리에서만 셉니다.

## 지키는 규칙(코드가 막는다)
- 임베딩은 로컬만, 외부 API는 `ALLOW_EXTERNAL_API=1` 없이는 막힘. 실제 문서는 외부로 보내지 않는다.
- 실행 목적은 `dev`와 `eval`뿐이고 `eval`은 동결 파일이 있어야 한다. 정답표는 사람이 확인한 것만 읽는다. 질문 글과 청크 본문은 출력하지 않는다.
- 같은 결과 파일이 있으면 덮어쓰지 않고, 입력이 바뀌면 멈춘다.
- 점수를 보고 설정·질문·기준을 바꾸지 않는다.

## 알아둘 점
- 이 저장소만으로는 평가 전체를 재현할 수 없습니다. 정답표와 실제 문서, 시험은 포함하지 않았습니다.
- 검색 폭 `config.HNSW_EF_SEARCH`는 4000입니다. 카드 수보다 크면 사실상 전수 검색이라 다시 색인해도 결과가 같습니다(카드가 더 많으면 `check_index`가 멈춤).
- `INTERFACE.md`는 si-prep과의 경계를 설명합니다(읽는 파일: `schema.py`, `chunks.jsonl`, `items.jsonl`, `links.jsonl`).
