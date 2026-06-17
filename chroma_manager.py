"""ChromaDB Manager for Bot Collective Cache.
Handles all vector storage, retrieval, and expiration.
"""

import hashlib
import os
import time
import uuid
import re
from typing import Optional

import chromadb
from chromadb.config import Settings


# ── Content helpers ────────────────────────────────────────

def _detect_language(text: str) -> str:
    """Simple heuristic: count CJK vs ASCII. Returns 'zh' or 'en'."""
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')
    ascii_chars = sum(1 for c in text if c.isascii() and c.isalpha())
    total = cjk + ascii_chars
    if total == 0:
        return "en"
    return "zh" if cjk / total > 0.5 else "en"

def _estimate_tokens(text: str) -> int:
    """Rough token estimation: ~1.5 chars/token for CJK, ~4 for English."""
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en = max(len(text) - cjk, 0)
    return max(1, int(cjk / 1.5 + en / 4))

def _compute_hash(text: str) -> str:
    """SHA-256 hex of content (first 32 chars)."""
    return hashlib.sha256(text.encode()).hexdigest()[:32]


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
        resolve_action: str = "",
        target_id: str = "",
        language: str = "",
        region: str = "",
        filtration_status: str = "scanned",
        contributor_id: str = "",
        is_human_bridged: bool = False,
    ) -> str:
        """Add a web search result to the cache.
        
        resolve_action:
          - "" (default): normal dedup. Auto-merge near-identical entries.
          - "keep_both": force create new entry, skipping dedup.
          - "update": overwrite target_id with new content and increment reproductions.
        """
        collection = self.client.get_collection("web_cache")
        
        # ── Handle resolve actions ──
        if resolve_action == "update":
            if not target_id:
                raise ValueError("resolve_id required for update action")
            now = int(time.time())
            all_data = collection.get(ids=[target_id])
            if not all_data["ids"]:
                raise ValueError(f"Target entry not found: {target_id}")
            meta = all_data["metadatas"][0] if all_data["metadatas"] else {}
            old_repro = int(meta.get("reproductions", 0))
            new_meta = {
                **meta,
                "query": query,
                "reproductions": str(old_repro + 1),
                "created": str(now),
            }
            new_doc = f"{query}\n{content[:7900]}"
            collection.update(
                ids=[target_id],
                documents=[new_doc],
                metadatas=[new_meta],
            )
            return target_id
        
        if resolve_action == "keep_both":
            return self._create_entry(collection, query, content, source_url, tags, privacy_class,
                                     language=language, region=region, filtration_status=filtration_status,
                                     contributor_id=contributor_id, is_human_bridged=is_human_bridged)
        
        # ── Normal dedup (default) ──
        DEDUP_EMBED_THRESHOLD = 0.15
        dedup_text = f"{query} {content[:500]}"
        try:
            existing = collection.query(query_texts=[dedup_text], n_results=3)
            if existing["ids"] and existing["ids"][0] and existing["distances"][0]:
                for i, dist in enumerate(existing["distances"][0]):
                    if dist < DEDUP_EMBED_THRESHOLD:
                        existing_meta = existing["metadatas"][0][i] if existing.get("metadatas") else {}
                        existing_query = existing_meta.get("query", "")
                        if self._query_similar(query, existing_query):
                            return self._merge_entry(collection, existing, i)
        except Exception:
            pass
        
        # Method 2: Same source URL + semantic query overlap
        if source_url:
            try:
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
        return self._create_entry(collection, query, content, source_url, tags, privacy_class,
                                 language=language, region=region, filtration_status=filtration_status,
                                 contributor_id=contributor_id, is_human_bridged=is_human_bridged)

    def _create_entry(
        self,
        collection,
        query: str,
        content: str,
        source_url: str = "",
        tags: list[str] = None,
        privacy_class: str = "public",
        language: str = "",
        region: str = "",
        filtration_status: str = "scanned",
        contributor_id: str = "",
        is_human_bridged: bool = False,
    ) -> str:
        item_id = f"wr_{uuid.uuid4().hex[:12]}"
        now = int(time.time())
        expiry = now + 30 * 86400

        # Auto-detect language if not provided
        if not language:
            language = _detect_language(content)

        metadata = {
            # Core
            "type": "web_cache",
            "query": query,
            "source_url": source_url,
            "content_hash": _compute_hash(content),
            # Context
            "language": language,
            "region": region or "Global",
            "token_size": str(_estimate_tokens(content)),
            # Classification
            "tags": ",".join(tags or []),
            "privacy_class": privacy_class,
            "filtration_status": filtration_status,
            "verification": "unverified",
            # Attribution
            "contributor_id": contributor_id,
            "is_human_bridged": str(is_human_bridged).lower(),
            # Lifecycle
            "created": str(now),
            "expires": str(expiry),
            "reproductions": "0",
        }

        collection.add(
            ids=[item_id],
            documents=[f"{query}\n{content[:7900]}"],
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

    # ── CONFLICT DETECTION (lightweight, read-only) ──────────
    
    def detect_conflicts(
        self,
        query: str,
        content: str,
        threshold: float = 0.15,
    ) -> list[dict]:
        """Lightweight conflict detection. Returns potential matching entries."""
        collection = self.client.get_collection("web_cache")
        dedup_text = f"{query} {content[:500]}"
        conflicts = []
        
        try:
            existing = collection.query(query_texts=[dedup_text], n_results=5)
            if existing["ids"] and existing["ids"][0] and existing["distances"][0]:
                for i, dist in enumerate(existing["distances"][0]):
                    if dist < threshold:
                        existing_meta = existing["metadatas"][0][i] if existing.get("metadatas") else {}
                        existing_query = existing_meta.get("query", "")
                        if self._query_similar(query, existing_query):
                            existing_doc = existing["documents"][0][i] if existing.get("documents") else ""
                            conflicts.append({
                                "id": existing["ids"][0][i],
                                "query": existing_query,
                                "content_preview": existing_doc[:200] if existing_doc else "",
                                "distance": round(dist, 4),
                                "verification": existing_meta.get("verification", "unverified"),
                                "reproductions": int(existing_meta.get("reproductions", 0)),
                            })
        except Exception:
            pass
        
        return conflicts

    def search_web_cache(
        self,
        query: str,
        n_results: int = 3,
        min_similarity: float = 0.4,
    ) -> list[dict]:
        """Search web cache. Uses keyword-first for Chinese, embedding for English."""
        try:
            collection = self.client.get_collection("web_cache")
            
            cjk_count = sum(1 for c in query if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f')
            is_chinese = cjk_count >= 2
            
            if is_chinese:
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
                    "language": metadatas[i].get("language", ""),
                    "region": metadatas[i].get("region", "Global"),
                    "token_size": int(metadatas[i].get("token_size", "0")),
                    "content_hash": metadatas[i].get("content_hash", ""),
                    "filtration_status": metadatas[i].get("filtration_status", ""),
                    "is_human_bridged": metadatas[i].get("is_human_bridged", "false"),
                    "contributor_id": metadatas[i].get("contributor_id", ""),
                    "verification": metadatas[i].get("verification", "unverified"),
                    "reproductions": int(metadatas[i].get("reproductions", "0")),
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
        """Query similarity check."""
        if not q1 or not q2:
            return False
        q1l = q1.strip().lower()
        q2l = q2.strip().lower()
        if q1l == q2l:
            return True
        if q1l in q2l or q2l in q1l:
            return True
        words1 = set(q1l.split())
        words2 = set(q2l.split())
        shared = words1 & words2
        if len(shared) >= 2:
            return True
        chars1 = set(q1l.replace(" ", ""))
        chars2 = set(q2l.replace(" ", ""))
        if chars1 and chars2:
            overlap = len(chars1 & chars2) / max(len(chars1), len(chars2))
            return overlap >= 0.7
        return False

    def _merge_entry(self, collection, existing_results, idx: int) -> str:
        """Merge a new contribution into an existing entry."""
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
