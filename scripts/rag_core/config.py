"""전역 설정. .env 파일에서 값을 읽어온다."""
import os
import sys
from dotenv import load_dotenv

# Windows 콘솔 기본 코드페이지(cp949 등)에서 한글 출력이 깨지는 것을 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

# [rag_core 보정 C3] Chroma의 익명 사용 통계 전송을 끈다. 외부 전송 금지 원칙에 맞춰 .env로도 켜지 못하게 덮어쓴다.
# chromadb 클라이언트를 만들기 전에 정해져야 하므로 chromadb를 쓰는 모든 모듈보다 먼저 이 파일이 import돼야 한다.
os.environ["ANONYMIZED_TELEMETRY"] = "False"

# [사용 안 함 표시, 2026-10-08 폴더 정리] 아래 OpenAI·CHAT·OLLAMA 값은 답변 생성용이다. 이번 평가(검색까지)에서는 쓰지 않고 외부 호출 차단(F1) 시험이 참조한다.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# EMBEDDING_BACKEND: "local"(무료, sentence-transformers) | "openai"(유료)
# CHAT_BACKEND:      "local"(무료, transformers 직접 구동) | "ollama"(무료, Ollama 서버) | "openai"(유료)
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "local")
CHAT_BACKEND = os.getenv("CHAT_BACKEND", "local")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")

# local 백엔드용 (이 프로젝트 환경의 HuggingFace 캐시에 이미 받아져 있는 모델을 기본값으로 사용)
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-m3")
LOCAL_CHAT_MODEL = os.getenv("LOCAL_CHAT_MODEL", "Qwen/Qwen2.5-3B-Instruct")

# CHAT_BACKEND=ollama 를 쓸 경우에만 사용
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# [rag_core F1] 외부 전송 차단. 마스킹본을 외부 API로 보내지 않는다(프로젝트 CLAUDE.md).
# 합성·샘플 글로만 외부 API를 쓸 때에 한해 ALLOW_EXTERNAL_API=1로 명시적으로 켠다.
ALLOW_EXTERNAL_API = os.getenv("ALLOW_EXTERNAL_API", "0") == "1"
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def assert_external_allowed(what: str) -> None:
    """외부 서비스로 글이 나가는 호출 직전에 부른다. 허용 플래그가 없으면 호출 전에 실패시킨다."""
    if not ALLOW_EXTERNAL_API:
        raise RuntimeError(
            f"{what} 호출은 외부 전송이라 막혀 있습니다. 마스킹본은 외부로 보내지 않는 규칙입니다. "
            "합성·샘플 글로만 쓸 때에 한해 ALLOW_EXTERNAL_API=1로 명시적으로 허용하세요."
        )


def assert_ollama_host_allowed() -> None:
    """Ollama 주소가 이 컴퓨터가 아니면 외부 전송으로 본다."""
    from urllib.parse import urlparse

    host = urlparse(OLLAMA_HOST).hostname
    if host not in _LOCAL_HOSTS:
        assert_external_allowed(f"Ollama({OLLAMA_HOST})")


# [rag_core F6] 평가용 답변 생성 설정(채점표 §0 ⑤): 온도 0, 시드 0.
# experiment.yaml의 값과 같아야 하고 settings.apply_settings가 맞춘다. 환경 변수로 바꾸지 못하게 상수로 둔다.
GENERATION_TEMPERATURE = 0
GENERATION_SEED = 0

CHROMA_DIR = os.getenv("CHROMA_DIR", "chroma_db")
# [이슈 24, 2026-10-10] HNSW 검색 폭. 기본값(100)이면 후보 경계(상위 50개 부근)에서 다시 색인한 저장소끼리 목록이 달라질 수 있다.
# 말뭉치 크기 이상으로 크게 두면 근사 검색이 사실상 전수 검색이 된다(합성 500개 160개 질문에서 100 -> 1건 차이, 500 -> 0건). 점수를 올리려는 값이 아니다.
# [2026-10-16] 2000 → 4000. 서비스 E 카드가 2,060개라 2000보다 많아 check_index가 멈춤. 사용자 결정("응"), 정답·결과 보기 전.
# 이전 데이터(최대 1,842개)도 검색 폭 ≥ 카드 수인 같은 조건이라 결과가 바뀌지 않는다. 모든 팔에 똑같이 적용된다.
HNSW_EF_SEARCH = 4000

# 임베딩 백엔드마다 벡터 차원이 다르므로(local=bge-m3 1024차원, openai=1536차원) 컬렉션을 분리한다.
# 같은 이름의 컬렉션에 서로 다른 차원의 임베딩을 섞으면 Chroma가 에러를 낸다.
COLLECTION_NAME = os.getenv("COLLECTION_NAME", f"appliance_manuals_{EMBEDDING_BACKEND}")

RAW_DATA_PATH = os.getenv("RAW_DATA_PATH", "data/raw_documents.json")
