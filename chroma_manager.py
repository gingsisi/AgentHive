"""
ChromaDB Manager for Bot Collective Cache.
Handles all vector storage, retrieval, and expiration.
"""

import hashlib
import os
import time
import uuid
from typing import Optional

import chromadb
from chromadb.config import Settings


class ChromaManager:
    """Manages ChromaDB collections for the knowledge mesh."""

    def __init__(self, persist_dir: str = None):
        if persist_dir is None:
            persist_dir = os.path.join(os.path.dirname(__file__), "chroma_data")
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self._ensure_collections()
        self._embedder = None  # Lazy load

    def _ensure_collections(self):
        """Create collections if they don't exist."""
        existing = [c.name for c in self.client.list_collections()]
        defaults = {
            "web_cache": "Cached web search results",
            "skills_library": "Shared skill templates",
            "verified_solutions": "Verified, reproducible solutions",
        }
        for name, desc in defaults.items():
            if name not in existing:
                self.client.create_collection(name=name, metadata={"description": desc})

    def _get_embedder(self):
        """Lazy-load embedding function (ONNX for speed, Ollama if available)."""
        if self._embedder is None:
            from chromadb.utils import embedding_functions
            
            # Tier 1: Ollama local nomic-embed-text (best if running)
            try:
                self._embedder = embedding_functions.OllamaEmbeddingFunction(
                    url="http://localhost:11434/api/embeddings",
                    model_name="nomic-embed-text",
                )
                self._embedder(["test"])
                return self._embedder
            except Exception:
                pass
            
            # Tier 2: ONNX MiniLM (fast, pre-downloaded, ~80MB)
            # Note: English-only, but combined with query-in-document
            # and keyword pre-filter, delivers usable Chinese results
            self._embedder = embedding_functions.DefaultEmbeddingFunction()
        return self._embedder

    # ── CONTRIBUTE ──────────────────────────────────────────

    def contribute_web_result(
        self,
        query: str,
        content: str,
        source_url: str = "",
        tags: list[str] = None,
        privacy_class: str = "public",
    ) -> str:
        """Add a web search result to the cache. Deduplicates via embedding similarity. Returns item ID."""
        
        # ── Deduplication check ──
        collection = self.client.get_collection("web_cache")
        
        # Method 1: Embedding similarity with query-text guard.
        # Two entries are only merged if BOTH embedding AND query text are similar.
        # This prevents merging different questions about the same domain
        # (e.g., "長者津貼" vs "傷殘津貼" — both welfare, but different questions).
        DEDUP_EMBED_THRESHOLD = 0.15  # Conservative: only near-identical content
        dedup_text = f"{query} {content[:500]}"
        try:
            existing = collection.query(query_texts=[dedup_text], n_results=3)
            if existing["ids"] and existing["ids"][0] and existing["distances"][0]:
                for i, dist in enumerate(existing["distances"][0]):
                    if dist < DEDUP_EMBED_THRESHOLD:
                        # Check query text similarity — don't merge different questions
                        existing_meta = existing["metadatas"][0][i] if existing.get("metadatas") else {}
                        existing_query = existing_meta.get("query", "")
                        if self._query_similar(query, existing_query):
                            return self._merge_entry(collection, existing, i)
        except Exception:
            pass
        
        # Method 2: Same source URL + semantic query overlap (fallback for non-English embeddings)
        if source_url:
            try:
                # Search by metadata: find entries from same source
                all_data = collection.get()
                for i, eid in enumerate(all_data["ids"]):
                    meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
                    existing_url = meta.get("source_url", "")
                    existing_query = meta.get("query", "")
                    if existing_url == source_url and self._query_similar(query, existing_query):
                        old_repro = int(meta.get("reproductions", 0))
                        new_meta = {**meta, "reproductions": str(old_repro + 1)}
                        if old_repro + 1 >= 3:
                            new_meta["verification"] = "verified"
                        collection.update(ids=[eid], metadatas=[new_meta])
                        return eid
            except Exception:
                pass
        
        # ── New entry ──
        item_id = f"wr_{uuid.uuid4().hex[:12]}"
        now = int(time.time())
        expiry = now + 30 * 86400  # 30 days

        metadata = {
            "type": "web_cache",
            "query": query,
            "source_url": source_url,
            "tags": ",".join(tags or []),
            "privacy_class": privacy_class,
            "created": str(now),
            "expires": str(expiry),
            "verification": "unverified",
            "reproductions": "0",
        }

        collection.add(
            ids=[item_id],
            # Include query in document so query terms contribute to embedding vector.
            # This helps even with English-only embedders for Chinese — different queries
            # produce different vectors instead of all Chinese text mapping to random noise.
            documents=[f"{query}\n{content[:7900]}"],  # ChromaDB doc limit
            metadatas=[metadata],
        )
        return item_id

    def contribute_skill(
        self,
        name: str,
        content: str,
        tags: list[str] = None,
    ) -> str:
        """Add a skill template to the library."""
        item_id = f"sk_{uuid.uuid4().hex[:12]}"
        now = int(time.time())

        collection = self.client.get_collection("skills_library")
        collection.add(
            ids=[item_id],
            documents=[content[:8000]],
            metadatas=[{
                "type": "skill",
                "name": name,
                "tags": ",".join(tags or []),
                "created": str(now),
                "verification": "unverified",
            }],
        )
        return item_id

    # ── SEARCH ─────────────────────────────────────────────

    def search_web_cache(
        self,
        query: str,
        n_results: int = 3,
        min_similarity: float = 0.4,
    ) -> list[dict]:
        """Search web cache. Uses keyword-first for Chinese, embedding for English."""
        try:
            collection = self.client.get_collection("web_cache")
            
            # Detect if query is predominantly Chinese/CJK
            cjk_count = sum(1 for c in query if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f')
            is_chinese = cjk_count >= 2  # At least 2 CJK chars = Chinese query
            
            if is_chinese:
                # Keyword-first: fetch all, rank by character overlap
                all_data = collection.get()
                hits = self._format_results(
                    {"ids": [all_data["ids"]], "documents": [all_data.get("documents", [])],
                     "distances": [[0.5]*len(all_data["ids"])], "metadatas": [all_data.get("metadatas", [])]},
                    "web_cache"
                )
                
                query_chars = set(query.replace(" ", ""))
                for hit in hits:
                    doc_chars = set((hit.get("content", "") + hit.get("query", "")).replace(" ", ""))
                    if query_chars:
                        hit["_keyword_score"] = len(query_chars & doc_chars) / len(query_chars)
                    else:
                        hit["_keyword_score"] = 0
                
                hits = [h for h in hits if h["_keyword_score"] > 0.1]
                hits.sort(key=lambda h: -h["_keyword_score"])
                return hits[:n_results]
            else:
                # Embedding search for English/other queries
                results = collection.query(
                    query_texts=[query],
                    n_results=n_results,
                )
                return self._format_results(results, "web_cache")
        except Exception:
            return []

    def search_all(self, query: str, n_results: int = 5) -> list[dict]:
        """Search all collections. Returns merged, ranked hits."""
        all_hits = []
        for col_name in ["web_cache", "skills_library", "verified_solutions"]:
            try:
                collection = self.client.get_collection(col_name)
                results = collection.query(
                    query_texts=[query],
                    n_results=n_results,
                )
                all_hits.extend(self._format_results(results, col_name))
            except Exception:
                continue

        # Sort by distance (lower = more similar)
        all_hits.sort(key=lambda h: h.get("distance", 1.0))
        return all_hits[:n_results]

    def _format_results(self, results: dict, collection: str) -> list[dict]:
        """Format ChromaDB results into consistent dicts."""
        hits = []
        if not results.get("ids") or not results["ids"][0]:
            return hits

        ids = results["ids"][0]
        docs = results.get("documents", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        for i, item_id in enumerate(ids):
            hit = {
                "id": item_id,
                "collection": collection,
                "content": docs[i] if i < len(docs) else "",
                "distance": distances[i] if i < len(distances) else 1.0,
            }
            if i < len(metadatas) and metadatas[i]:
                hit.update({
                    "type": metadatas[i].get("type", ""),
                    "query": metadatas[i].get("query", ""),
                    "source_url": metadatas[i].get("source_url", ""),
                    "tags": metadatas[i].get("tags", ""),
                    "verification": metadatas[i].get("verification", "unverified"),
                })
            hits.append(hit)
        return hits

    # ── MAINTENANCE ────────────────────────────────────────

    def expire_old_entries(self) -> int:
        """Remove entries past their expiry date. Returns count removed."""
        count = 0
        now = int(time.time())
        for col_name in ["web_cache", "skills_library", "verified_solutions"]:
            try:
                collection = self.client.get_collection(col_name)
                all_data = collection.get()
                if not all_data["ids"]:
                    continue
                expired = []
                for i, item_id in enumerate(all_data["ids"]):
                    meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
                    expiry_str = meta.get("expires", "0")
                    if int(expiry_str) < now and expiry_str != "0":
                        expired.append(item_id)
                if expired:
                    collection.delete(ids=expired)
                    count += len(expired)
            except Exception:
                continue
        return count

    def _query_similar(self, q1: str, q2: str) -> bool:
        """Query similarity check. Handles both English (word split) and Chinese (character overlap)."""
        if not q1 or not q2:
            return False
        q1l = q1.strip().lower()
        q2l = q2.strip().lower()
        # Exact match
        if q1l == q2l:
            return True
        # Substring (one contains the other)
        if q1l in q2l or q2l in q1l:
            return True
        # Word overlap (English)
        words1 = set(q1l.split())
        words2 = set(q2l.split())
        shared = words1 & words2
        if len(shared) >= 2:
            return True
        # Character overlap (Chinese) — 80% chars in common
        chars1 = set(q1l.replace(" ", ""))
        chars2 = set(q2l.replace(" ", ""))
        if chars1 and chars2:
            overlap = len(chars1 & chars2) / max(len(chars1), len(chars2))
            return overlap >= 0.7
        return False

    def _merge_entry(self, collection, existing_results, idx: int) -> str:
        """Merge a new contribution into an existing entry. Returns the existing ID."""
        existing_id = existing_results["ids"][0][idx]
        existing_meta = existing_results["metadatas"][0][idx] if existing_results.get("metadatas") else {}
        old_repro = int(existing_meta.get("reproductions", 0))
        new_meta = {**existing_meta, "reproductions": str(old_repro + 1)}
        if old_repro + 1 >= 3:
            new_meta["verification"] = "verified"
        collection.update(ids=[existing_id], metadatas=[new_meta])
        return existing_id

    def get_stats(self) -> dict:
        """Return collection statistics."""
        stats = {}
        for col_name in ["web_cache", "skills_library", "verified_solutions"]:
            try:
                collection = self.client.get_collection(col_name)
                stats[col_name] = collection.count()
            except Exception:
                stats[col_name] = 0
        return stats
