import hashlib
import json
import os
import pickle
import shutil
import time
from pathlib import Path
from typing import List, Dict, Optional, Set

import numpy as np

# 低内存/沙箱环境：强制单线程，避免 BLAS/OpenMP 多线程在内存紧张时原生崩溃 (segfault)
for _k, _v in {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "TOKENIZERS_PARALLELISM": "false",
}.items():
    os.environ.setdefault(_k, _v)

from langchain_community.vectorstores import FAISS

from .config import settings
from .games import data_file, index_dir_for


def get_embedding_function():
    provider = settings.embedding_provider.lower()

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    else:
        from langchain_community.embeddings import HuggingFaceBgeEmbeddings

        model_kwargs = {"device": "cpu"}
        encode_kwargs = {"normalize_embeddings": True}
        return HuggingFaceBgeEmbeddings(
            model_name=settings.embedding_model,
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs,
        )


def _cache_path() -> Path:
    return settings.data_dir / "embeddings_cache.npz"


def _load_cache_if_valid(n: int) -> Optional[np.ndarray]:
    cp = _cache_path()
    if not cp.exists():
        return None
    try:
        d = np.load(cp, allow_pickle=False)
        # 注: numpy>=2 不允许 int() 转换 shape=(1,) 的数组, 须先取 [0]
        # (否则缓存读取永远抛异常 → 每次都全量重向量化, 缓存形同虚设)
        if int(d["vecs"].shape[0]) == n and int(d["n_texts"][0]) == n:
            print(f"[Index] 命中 embedding 缓存 ({n} 条)，跳过向量化")
            return d["vecs"]
    except Exception as e:
        print(f"[Index] 缓存读取失败，重新向量化: {e}")
    return None


def _save_cache(vecs: np.ndarray, n: int) -> None:
    np.savez(_cache_path(), vecs=np.asarray(vecs, dtype=np.float32), n_texts=np.array([n]))


def _embed_in_batches(embeddings, texts: List[str], batch_size: int = 128) -> np.ndarray:
    out: List[List[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vecs = embeddings.embed_documents(batch)
        out.extend(vecs)
        print(f"[Index] 已向量化 {min(i + batch_size, len(texts))}/{len(texts)}")
    return np.asarray(out, dtype=np.float32)


def _chunk_key(title: str, section: str, text: str) -> str:
    """每块内容的指纹。text 一变指纹必变 —— 所以改切分逻辑会自动被判成「旧块失效」。"""
    raw = f"{title}\x00{section}\x00{text}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _metadata_of(c: Dict) -> Dict[str, str]:
    return {
        "title": c.get("title", ""),
        "section": c.get("section", ""),
        "url": c.get("url", ""),
        "category": c.get("category", ""),
    }


def _existing_keys(index_dir: Path) -> Optional[Set[str]]:
    """从已落盘的 index.pkl 读出所有已有块的指纹。

    只读 pickle、不加载 embedding 模型，所以这一步是秒级的。
    返回 None 表示没有可用旧索引（调用方应回退全量重建）。
    """
    faiss_file = index_dir / "index.faiss"
    pkl_file = index_dir / "index.pkl"
    if not faiss_file.exists() or not pkl_file.exists():
        return None
    try:
        with open(pkl_file, "rb") as f:
            payload = pickle.load(f)
        docstore = payload[0] if isinstance(payload, tuple) else payload
        docs = getattr(docstore, "_dict", None)
        if docs is None:
            return None
        keys: Set[str] = set()
        for doc in docs.values():
            meta = getattr(doc, "metadata", {}) or {}
            keys.add(
                _chunk_key(
                    meta.get("title", ""),
                    meta.get("section", ""),
                    doc.page_content,
                )
            )
        return keys
    except Exception as e:
        print(f"[Index] 读取现有索引失败，回退全量重建: {e}")
        return None


def _backup_index(index_dir: Path) -> Optional[str]:
    """增量写回前备份现有索引。

    只做复制，绝不删除任何已有文件 —— 沙箱会拦截 Python 的 unlink，
    且误删索引的代价太大，备份文件请自行清理。
    """
    faiss_file = index_dir / "index.faiss"
    pkl_file = index_dir / "index.pkl"
    if not faiss_file.exists():
        return None
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        shutil.copy2(faiss_file, index_dir / f"index_backup_{stamp}.faiss")
        shutil.copy2(pkl_file, index_dir / f"index_backup_{stamp}.pkl")
        print(f"[Index] 已备份现有索引 -> index_backup_{stamp}.*")
        return stamp
    except Exception as e:
        print(f"[Index] 备份失败（继续增量）: {e}")
        return None


def build_index_incremental(chunks: List[Dict], dry_run: bool = False,
                            game: Optional[str] = None) -> None:
    """增量更新索引：只对「新增」的块做向量化，追加进已有 FAISS 索引。

    game: 目标游戏，索引落在 faiss_index/<game>/；不传则用默认游戏。
    适用：抓了新页面 / 补了新数据，而旧的 chunk 文本没变。
    不适用：改了切分逻辑（所有块文本都变）或某些块被删除 —— FAISS 不支持原地删除，
            这两种情况必须全量重建，本函数会直接中止并提示。
    """
    index_dir = index_dir_for(game)
    print(f"[Index] 使用 embedding: {settings.embedding_provider} - {settings.embedding_model}")

    old_keys = _existing_keys(index_dir)
    if old_keys is None:
        print("[Index] 未检测到可用旧索引 → 转为全量重建")
        build_index_from_chunks(chunks, collection_name=game)
        return

    new_map: Dict[str, Dict] = {}
    for c in chunks:
        k = _chunk_key(c.get("title", ""), c.get("section", ""), c["text"])
        new_map[k] = c

    added_keys = set(new_map) - old_keys
    removed_keys = old_keys - set(new_map)

    print(f"[Index] 现有索引 {len(old_keys)} 块 / 当前 chunks {len(new_map)} 块")
    print(f"[Index] 新增 {len(added_keys)} 块，失效（删除或内容被修改）{len(removed_keys)} 块")

    if removed_keys:
        print(
            "[Index][中止] 检测到旧块被删除或内容被修改。\n"
            "  FAISS 不支持原地删除，强行增量会留下无法召回的幽灵向量。\n"
            f"  请改用全量重建：  python -m src.build_index --full --game {game or settings.wiki_name}"
        )
        return

    if not added_keys:
        print("[Index] 没有新增数据，索引已是最新，无需重建")
        return

    est_min = len(added_keys) / 3.4 / 60
    print(f"[Index] 仅向量化新增的 {len(added_keys)} 块（按 3.4 块/秒 估算约 {est_min:.1f} 分钟）")

    if dry_run:
        print("[Index] dry-run 模式：以上为预演结果，未写入任何文件")
        return

    _backup_index(index_dir)

    added_chunks = [new_map[k] for k in added_keys]
    texts = [c["text"] for c in added_chunks]
    metadatas = [_metadata_of(c) for c in added_chunks]

    print("[Index] 加载已有索引 + embedding 模型...")
    embeddings = get_embedding_function()
    vecs = _embed_in_batches(embeddings, texts)

    vectorstore = FAISS.load_local(
        str(index_dir), embeddings, allow_dangerous_deserialization=True
    )
    text_embeddings = list(zip(texts, (v.tolist() for v in vecs)))
    added_ids = vectorstore.add_embeddings(text_embeddings, metadatas=metadatas)

    vectorstore.save_local(str(index_dir))

    print(
        f"[Index] 增量完成！新增 {len(added_ids)} 块，"
        f"索引总数 {len(old_keys)} → {len(old_keys) + len(added_ids)}"
    )
    print(
        "[Index] 注意：data/embeddings_cache.npz 仍是全量时期的缓存，"
        "下次全量重建若条数不匹配会自动重算，无需手动处理。"
    )


def build_incremental_from_json(json_path: str, dry_run: bool = False,
                                game: Optional[str] = None) -> None:
    print(f"[Index] 读取数据: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    build_index_incremental(chunks, dry_run=dry_run, game=game)


def build_index_from_chunks(
    chunks: List[Dict],
    collection_name: Optional[str] = None,
) -> None:
    collection_name = collection_name or settings.wiki_name
    index_dir = index_dir_for(collection_name)

    print(f"[Index] 使用 embedding: {settings.embedding_provider} - {settings.embedding_model}")
    print(f"[Index] 构建向量索引 [{collection_name}]，共 {len(chunks)} 个文档块...")

    index_dir.mkdir(parents=True, exist_ok=True)

    texts = [c["text"] for c in chunks]
    metadatas = [
        {
            "title": c.get("title", ""),
            "section": c.get("section", ""),
            "url": c.get("url", ""),
            "category": c.get("category", ""),
        }
        for c in chunks
    ]

    vecs = _load_cache_if_valid(len(texts))
    if vecs is None:
        embeddings = get_embedding_function()
        vecs = _embed_in_batches(embeddings, texts)
        _save_cache(vecs, len(texts))
        # 缓存已落盘；下方构建仍需 embedding 函数用于查询期向量化
        embeddings_for_build = embeddings
    else:
        embeddings_for_build = get_embedding_function()

    text_embeddings = list(zip(texts, (v.tolist() for v in vecs)))
    vectorstore = FAISS.from_embeddings(
        text_embeddings, embedding=embeddings_for_build, metadatas=metadatas
    )

    # 直接保存；FAISS.save_local 会覆盖同名文件，无需手动 unlink
    # （沙箱会拦截 Python 的 unlink，参见 safe-delete 限制）
    vectorstore.save_local(str(index_dir))

    print(f"[Index] 构建完成！索引目录: {index_dir}, 文档数: {len(chunks)}")


def build_from_json(json_path: str, collection_name: Optional[str] = None) -> None:
    print(f"[Index] 读取数据: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    build_index_from_chunks(chunks, collection_name)


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    full = "--full" in args
    dry_run = "--dry-run" in args
    # --game <id>：为哪个游戏建索引（落在 faiss_index/<id>/），不传则用默认游戏
    game = None
    if "--game" in args:
        i = args.index("--game")
        game = args[i + 1] if i + 1 < len(args) else None
    game = game or settings.wiki_name

    chunks_file = data_file(game, "chunks.json")
    if not chunks_file.exists():
        print(f"找不到切分数据: {chunks_file}")
        print(f"请先处理该游戏的数据:  python -m src.data_processor --game {game}")
    elif full:
        build_from_json(str(chunks_file), collection_name=game)
    else:
        print(f"[Index] 游戏 [{game}] 默认增量模式（只向量化新增块）；"
              "要全量重建加 --full，只看预演加 --dry-run")
        build_incremental_from_json(str(chunks_file), dry_run=dry_run, game=game)
