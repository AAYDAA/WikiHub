import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


class Settings:
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0"))

    # 问答模式: rag(默认, 普通检索增强问答) | agent(大模型调度工具, 可切回)
    qa_mode: str = os.getenv("QA_MODE", "rag")

    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "local_bge")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    chroma_persist_dir: Path = BASE_DIR / os.getenv("CHROMA_PERSIST_DIR", "./faiss_index")

    wiki_name: str = os.getenv("WIKI_NAME", "isaac")
    wiki_api_url: str = os.getenv("WIKI_API_URL", "https://isaac.huijiwiki.com/api.php")
    wiki_base_url: str = os.getenv("WIKI_BASE_URL", "https://isaac.huijiwiki.com/wiki/")

    data_dir: Path = BASE_DIR / "data"


settings = Settings()
