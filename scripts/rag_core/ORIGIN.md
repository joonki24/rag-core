# rag_core 출처

- **원본:** 이전 팀 프로젝트의 RAG 기능 `ask-my-appliance` (https://github.com/Broogun/ask-my-appliance), MIT 라이선스. 원작자 저작권 표시는 저장소 맨 위 `LICENSE`에 그대로 두었습니다.
- **가져온 시점의 커밋:** `863901142cb0e9b068ad278e0b9a1eb8c1cf1e5d` (2026-09-28), 가져온 날 2026-10-03.
- **가져온 이유:** 검색 부분을 처음부터 만들지 않고, 검증된 코드 위에 평가 기능을 얹기 위해서입니다.
- **가져온 파일:** `config.py`, `pipeline/embed_store.py`, `pipeline/query_engine.py`, `pipeline/__init__.py`. PDF 청킹, 그림 추출, 웹, DB는 가져오지 않았습니다.
- 그 위에 평가에 필요한 부분(청크 검증, 기준선 청크, 근거 담기, 연결 따라가기, 정답표 변환, 채점, 동결, 비교)을 새로 만들었습니다.
