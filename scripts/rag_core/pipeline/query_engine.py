"""자연어 질문 -> 벡터 검색 -> LLM 답변 생성.

[rag_core F2·F3·F4] 가전제품 전용 부분(에러코드 DB 경로, 모델명 필터, 섹션 중복 제거)을 지웠다.
검색은 컬렉션 전체에서 가장 가까운 청크를 거리 순서대로 그대로 돌려준다.
원본은 `_origin/pipeline/query_engine.py`에 있다.

CHAT_BACKEND=local  (기본값, 무료): transformers로 로컬 GPU/CPU에서 LLM 직접 구동
CHAT_BACKEND=ollama (무료): 별도 설치한 Ollama 서버 호출
CHAT_BACKEND=openai (유료): OpenAI Chat Completions API (F1: ALLOW_EXTERNAL_API=1 없이는 막힘)
"""
import argparse

import config
from pipeline.embed_store import embed_texts, get_collection

# [사용 안 함 표시, 2026-10-08 폴더 정리] 이 파일에서 `search`만 평가에 쓰인다. 아래 답변 생성(프롬프트, _generate_*, _answer_from_vector, answer)은
# 이번 평가(검색까지)에서 쓰지 않는다. 외부 호출 차단(F1) 시험이 이 함수들을 참조하므로 지우지 않는다.
# 답변 프롬프트는 가전제품용 원본 그대로다. 우리 문제에 맞게 바꿔야 하며(공지 3번, 후순위),
# 검색 점검과 점수 계산에는 쓰이지 않는다. [F3] 모델명 필터 전용 프롬프트는 지웠다.
GENERAL_SYSTEM_PROMPT = (
    "너는 가전제품 사용법과 문제 해결을 도와주는 어시스턴트야. 반드시 한국어로만 답해. "
    "아래 검색 결과는 사용자가 말한 모델과 정확히 일치하지 않을 수 있는 유사 제품의 "
    "사용설명서 내용이야. 이걸 바탕으로 마치 일반적으로 잘 알고 있는 것처럼 자연스럽게 "
    "답변해. '이 정보는 없지만', '설명서에 없지만', '정확한 모델은 아니지만' 같은 "
    "양해를 구하는 표현은 쓰지 말고, 바로 유용한 답변부터 자연스럽게 시작해. "
    "다만 검색된 문서에 실제로 있는 내용만 근거로 삼고 사실을 지어내지는 마. "
    "안전에 관련될 수 있는 내용(감전, 화재, 가스 누출 등)이면 임의로 판단하지 말고 "
    "전원을 끄고 제조사 서비스센터에 문의하라고 안내해."
)

_local_model = None
_local_tokenizer = None


def search(query: str, top_k: int = 3, collection_name: str | None = None) -> list[dict]:
    """질문과 가장 가까운 청크를 거리 오름차순으로 최대 top_k개 돌려준다.

    [F3] 메타데이터 필터 없음. [F4] 같은 섹션 중복 제거 없음. [F8] 컬렉션 이름 지정 가능.
    결과 개수는 min(top_k, 컬렉션 크기)이고, 컬렉션이 비어 있으면 빈 목록이다.
    [보정 B1] 컬렉션이 아예 없으면 새로 만들지 않고 CollectionNotFoundError로 멈춘다.
    """
    collection = get_collection(collection_name, create=False)
    n = min(top_k, collection.count())
    if n <= 0:
        return []

    [q_embedding] = embed_texts([query])
    result = collection.query(query_embeddings=[q_embedding], n_results=n)

    candidates = []
    for i in range(len(result["ids"][0])):
        candidates.append(
            {
                "id": result["ids"][0][i],
                "text": result["documents"][0][i],
                "metadata": result["metadatas"][0][i],
                "distance": result["distances"][0][i],
            }
        )
    return candidates


def _generate_openai(system_prompt: str, user_message: str) -> str:
    config.assert_external_allowed("OpenAI Chat API")  # [rag_core F1]
    from openai import OpenAI

    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=config.GENERATION_TEMPERATURE,   # [rag_core F6] 온도 0, 시드 고정
        seed=config.GENERATION_SEED,
    )
    return resp.choices[0].message.content


def _generate_local(system_prompt: str, user_message: str) -> str:
    """transformers로 로컬 GPU/CPU에서 LLM(기본: Qwen2.5-3B-Instruct)을 직접 구동한다.
    별도 서버(Ollama 등) 설치가 필요 없다. 모델은 최초 1회만 로드해서 재사용한다."""
    global _local_model, _local_tokenizer
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if _local_model is None:
        print(f"[chat] 로컬 LLM 로딩 중: {config.LOCAL_CHAT_MODEL} (최초 1회는 다운로드 필요)")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _local_tokenizer = AutoTokenizer.from_pretrained(config.LOCAL_CHAT_MODEL)
        _local_model = AutoModelForCausalLM.from_pretrained(
            config.LOCAL_CHAT_MODEL,
            dtype=torch.bfloat16 if device == "cuda" else torch.float32,
            device_map=device,
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    prompt = _local_tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = _local_tokenizer(prompt, return_tensors="pt").to(_local_model.device)
    # [rag_core F6] 온도 0이면 샘플링 없이(greedy) 같은 입력에 같은 출력을 내고, 시드도 고정한다.
    torch.manual_seed(config.GENERATION_SEED)
    gen_kwargs = {"max_new_tokens": 512, "pad_token_id": _local_tokenizer.eos_token_id}
    if config.GENERATION_TEMPERATURE > 0:
        gen_kwargs.update(do_sample=True, temperature=config.GENERATION_TEMPERATURE)
    else:
        gen_kwargs.update(do_sample=False)
    output_ids = _local_model.generate(**inputs, **gen_kwargs)
    new_tokens = output_ids[0][inputs["input_ids"].shape[1]:]
    return _local_tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


def _generate_ollama(system_prompt: str, user_message: str) -> str:
    """Ollama(https://ollama.com)로 로컬에서 돌아가는 LLM을 호출한다.
    사전에 `ollama pull qwen2.5:7b` 로 모델을 받아두고 Ollama 앱이 실행 중이어야 한다.

    think=False로 고정한다: qwen3.5 같은 추론(thinking) 지원 모델은 기본으로 답변 전에
    안 보이는 "생각" 토큰을 대량 생성한다 (실측: 짧은 RAG 답변 하나에 1,756 think 토큰,
    43초). 이 프로젝트는 문서에서 찾은 내용을 그대로 정리해서 답하는 단순 RAG 챗봇이라
    긴 추론이 필요 없고, think=False로 끄면 같은 질문이 3초로 줄어든다 (약 17배)."""
    config.assert_ollama_host_allowed()  # [rag_core F1]
    import requests

    try:
        resp = requests.post(
            f"{config.OLLAMA_HOST}/api/chat",
            json={
                "model": config.OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "think": False,
                # [rag_core F6] 온도 0, 시드 고정
                "options": {"temperature": config.GENERATION_TEMPERATURE, "seed": config.GENERATION_SEED},
            },
            timeout=120,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(
            "Ollama 서버에 연결할 수 없습니다. https://ollama.com 에서 설치 후 "
            f"`ollama pull {config.OLLAMA_MODEL}` 로 모델을 받고 Ollama 앱을 실행해두세요."
        ) from e

    return resp.json()["message"]["content"]


def _generate(system_prompt: str, user_message: str) -> str:
    if config.CHAT_BACKEND == "openai":
        return _generate_openai(system_prompt, user_message)
    if config.CHAT_BACKEND == "ollama":
        return _generate_ollama(system_prompt, user_message)
    return _generate_local(system_prompt, user_message)


def _answer_from_vector(query: str, top_k: int, collection_name: str | None = None) -> dict:
    candidates = search(query, top_k, collection_name=collection_name)
    context = "\n".join(f"- {c['text']}" for c in candidates)
    user_message = f"[검색된 문서]\n{context}\n\n[사용자 질문]\n{query}"
    text = _generate(GENERAL_SYSTEM_PROMPT, user_message)
    return {"answer": text, "candidates": candidates, "source": "vector"}


def answer(query: str, top_k: int = 3, collection_name: str | None = None) -> dict:
    """[F2] 에러코드(RDB) 경로를 지우고 벡터 검색 경로만 남겼다."""
    return _answer_from_vector(query, top_k, collection_name)


def main():
    parser = argparse.ArgumentParser(description="자연어 문서 검색/질의응답")
    parser.add_argument("query", help="자연어 질문")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--collection", default=None, help="컬렉션 이름(기본: config의 COLLECTION_NAME)")
    args = parser.parse_args()

    result = answer(args.query, args.top_k, args.collection)
    print("\n=== 답변 ===")
    print(result["answer"])
    print("\n=== 근거 문서 ===")
    for c in result["candidates"]:
        print(f"- (distance={c['distance']:.4f}) {c['text'][:80]}")


if __name__ == "__main__":
    main()
