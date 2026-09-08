from typing import Dict, List
import re
import time
import logging
import asyncio
from pathlib import Path

from bot.constants import RELEVANT_THRESHOLD

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)

# Anchor to the repo root: a different CWD used to silently create an
# empty vector store.
_CHROMA_PATH = str(Path(__file__).resolve().parent.parent / "chromadb.db")

# Setup Chroma Client
chroma_client = chromadb.PersistentClient(
    path=_CHROMA_PATH, settings=Settings(anonymized_telemetry=False)
)

# Create (or get) a collection
collection = chroma_client.get_or_create_collection(name="search_results")


def summarize_text(text: str, max_sentences: int = 5) -> str:
    """Quick summarization by extracting the first few sentences."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    summary_sentences = sentences[:max_sentences]
    summary = "\n".join(
        f"- {sentence.strip()}" for sentence in summary_sentences if sentence.strip()
    )
    return summary


async def sweep_old_documents(max_age_days: int = 90) -> int:
    """Delete stored article documents older than max_age_days.

    searxng_search upserts the FULL extracted article text per URL forever;
    this bounds the vector store. Requires the 'stored_at' metadata the
    upsert now writes — pre-existing entries without it are skipped (their
    age is unknowable).

    Returns the number of documents deleted. Runs the blocking calls off
    the event loop.
    """
    cutoff = time.time() - max_age_days * 86400.0

    def _sweep() -> int:
        found = collection.get(where={"stored_at": {"$lt": cutoff}})
        ids = found.get("ids") or []
        if ids:
            collection.delete(ids=ids)
        return len(ids)

    try:
        deleted = await asyncio.to_thread(_sweep)
        if deleted:
            logger.info("chroma.sweep_done deleted=%s max_age_days=%s", deleted, max_age_days)
        return deleted
    except Exception:
        logger.exception("chroma.sweep_failed")
        return 0


async def query_chromadb(q: str, top_k: int = 5) -> List[Dict]:
    """Query ChromaDB for relevant documents matching the query."""
    logger.info(f"Querying ChromaDB for: {q}")

    results = collection.query(
        query_texts=[q],
        n_results=top_k,
    )

    matches = []
    for doc, metadata, distance in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        if distance < RELEVANT_THRESHOLD:  # Lower distance = more relevant
            matches.append(
                {
                    "content": doc,
                    "title": metadata.get("title", "Unknown Title"),
                    "url": metadata.get("source_url", "Unknown URL"),
                    "distance": distance,
                }
            )
    logger.info(f"Found {len(matches)} ChromaDB matches for query '{q}'")
    return matches


class RAGContextBuilder:
    def __init__(self, chroma_collection, search_fn, similarity_threshold=0.5):
        self.collection = chroma_collection
        self.search_fn = search_fn
        self.similarity_threshold = similarity_threshold

    async def retrieve(self, query: str, callback=None) -> List[Dict]:
        """Main entrypoint: retrieve relevant documents from ChromaDB and live search."""
        logger.info(f"Retrieving context for query: {query}")

        chroma_results = await asyncio.to_thread(
            self.search_chroma, query, callback=callback
        )
        live_search_results = await self.search_fn(query, callback=callback)

        combined = chroma_results + live_search_results
        logger.info(f"Retrieved {len(combined)} documents total")
        return combined

    def search_chroma(self, query: str, callback=None) -> List[Dict]:
        """Semantic search from Chroma."""
        logger.info(f"Searching Chroma for query: {query}")
        try:
            search_result = self.collection.query(
                query_texts=[query],
                n_results=5,  # adjustable
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.exception(f"Chroma search failed: {e}")
            return []

        results = []
        for doc, meta, distance in zip(
            search_result["documents"][0],
            search_result["metadatas"][0],
            search_result["distances"][0],
        ):
            if distance <= self.similarity_threshold:
                summary = summarize_text(doc, max_sentences=5)
                result = {
                    "url": meta.get("source_url"),
                    "title": meta.get("title"),
                    "score": 1.0 - distance,
                    "content": summary,
                }
                results.append(result)

                # 🛎️ NEW: Send a message to the callback
                if callback:
                    title = result["title"] or "Untitled"
                    url = result["url"] or "#"
                    discord_formatted_message = (
                        f"Retrieving from VectorDB: [{title}](<{url}>)"
                    )
                    callback(param=discord_formatted_message)
            else:
                logger.info(f"Skipping Chroma result, distance {distance:.2f} too high")
        return results

    def build_context_text(self, documents: List[Dict]) -> str:
        """Merge documents into a single context string."""
        sections = []
        for doc in documents:
            title = doc.get("title", "Untitled")
            url = doc.get("url", "No URL")
            content = doc.get("content", "")
            sections.append(f"**{title}**\n{content}\nSource: {url}\n")

        return "\n\n".join(sections)
