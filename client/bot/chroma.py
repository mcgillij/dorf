from typing import Dict, List
import re
import logging

from bot.constants import RELEVANT_THRESHOLD

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)


# Setup Chroma Client
chroma_client = chromadb.PersistentClient(
    path="./chromadb.db", settings=Settings(anonymized_telemetry=False)
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

        chroma_results = self.search_chroma(query, callback=callback)
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
