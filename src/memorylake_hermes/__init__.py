"""MemoryLake memory plugin — MemoryProvider interface for hermes-agent.

Long-term memory via the MemoryLake platform with unified memory + document
search, auto-capture, web search, and open data access.

Features:
  - 7 tools: memorylake_search, memorylake_store, memorylake_list,
    memorylake_forget, memorylake_download, memorylake_web_search,
    memorylake_open_data
  - Sync prefetch: unified memory + document search with current-turn query
  - Auto-capture: background memory extraction after each turn
  - Conflict detection: surfaces unresolved memory conflicts in search results

Config via environment variables (profile-scoped via each profile's .env):
  MEMORYLAKE_API_KEY        — API key (required)
  MEMORYLAKE_PROJECT_ID     — Project ID (required)
  MEMORYLAKE_HOST           — Server URL (default: https://app.memorylake.ai)
  MEMORYLAKE_USER_ID        — User identifier (default: hermes-user)
  MEMORYLAKE_TOP_K          — Max recall results (default: 5)
  MEMORYLAKE_SEARCH_THRESHOLD — Min similarity 0-1 (default: 0.3)
  MEMORYLAKE_RERANK         — Rerank results (default: true)
  MEMORYLAKE_MEMORY_MODE    — tool_driven (default) or prefetch

Or via $HERMES_HOME/memorylake.json.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

from .client import MemoryLakeClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config(hermes_home: str = "") -> dict:
    """Load config from env vars, with $HERMES_HOME/memorylake.json overrides."""
    from pathlib import Path

    config = {
        "host": os.environ.get("MEMORYLAKE_HOST", "https://app.memorylake.ai"),
        "api_key": os.environ.get("MEMORYLAKE_API_KEY", ""),
        "project_id": os.environ.get("MEMORYLAKE_PROJECT_ID", ""),
        "user_id": os.environ.get("MEMORYLAKE_USER_ID", "hermes-user"),
        "top_k": int(os.environ.get("MEMORYLAKE_TOP_K", "5")),
        "search_threshold": float(os.environ.get("MEMORYLAKE_SEARCH_THRESHOLD", "0.3")),
        "rerank": os.environ.get("MEMORYLAKE_RERANK", "true").lower() == "true",
        "memory_mode": os.environ.get("MEMORYLAKE_MEMORY_MODE", "tool_driven"),
    }

    if hermes_home:
        config_path = Path(hermes_home) / "memorylake.json"
    else:
        from hermes_constants import get_hermes_home
        config_path = get_hermes_home() / "memorylake.json"

    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({k: v for k, v in file_cfg.items()
                           if v is not None and v != ""})
        except Exception:
            pass

    return config


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_document_result(doc: dict) -> str:
    """Format a single document search result for display."""
    doc_type = doc.get("type", "paragraph")
    doc_name = (doc.get("document_name") or
                (doc.get("source_document") or {}).get("file_name") or
                "unknown")
    doc_id = doc.get("document_id", "")

    parts = [f"[{doc_type}] {doc_name}"]
    if doc_id:
        parts[0] += f" (id: {doc_id})"

    highlight = doc.get("highlight") or {}

    # Paragraph chunks
    for chunk in (highlight.get("chunks") or []):
        text = chunk.get("text", "")
        if text:
            parts.append(f"  {text[:500]}")

    # Table info
    for table in (highlight.get("inner_tables") or []):
        cols = [c.get("name", "") for c in (table.get("columns") or [])]
        rows = table.get("num_rows", 0)
        if cols:
            parts.append(f"  Table: {', '.join(cols)} ({rows} rows)")

    # Figure
    figure = highlight.get("figure")
    if figure:
        caption = figure.get("caption") or figure.get("summary_text") or ""
        if caption:
            parts.append(f"  Figure: {caption[:300]}")

    return "\n".join(parts)


def _format_conflict(conflict: dict) -> str:
    """Format a single conflict for display."""
    name = conflict.get("name", "")
    desc = conflict.get("description", "")
    category = conflict.get("category", "")
    conflict_type = conflict.get("conflict_type", "")

    parts = [f"- {name}"]
    if desc:
        parts.append(f"  {desc}")
    if category or conflict_type:
        parts.append(f"  Type: {conflict_type} ({category})")

    for snap in (conflict.get("memory_snapshots") or []):
        text = snap.get("memory_text", "")
        if text:
            parts.append(f"  Memory: {text[:200]}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "memorylake_search",
    "description": (
        "Search long-term memories AND uploaded documents in MemoryLake. "
        "Returns the user's context, preferences, history, and relevant "
        "document content in a single call. Also surfaces unresolved "
        "memory conflicts when detected."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {
                "type": "integer",
                "description": "Max results (default: configured top_k).",
            },
        },
        "required": ["query"],
    },
}

STORE_SCHEMA = {
    "name": "memorylake_store",
    "description": (
        "Save important information in long-term memory via MemoryLake. "
        "Use for preferences, facts, decisions, and anything worth "
        "remembering across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Information to remember."},
        },
        "required": ["text"],
    },
}

LIST_SCHEMA = {
    "name": "memorylake_list",
    "description": (
        "List all stored memories for the user. Use when the user wants to "
        "see everything that has been remembered."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

FORGET_SCHEMA = {
    "name": "memorylake_forget",
    "description": "Delete a specific memory by ID from MemoryLake.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Memory ID to delete."},
        },
        "required": ["memory_id"],
    },
}

DOWNLOAD_SCHEMA = {
    "name": "memorylake_download",
    "description": (
        "Get a download URL for a document stored in MemoryLake. "
        "Returns a pre-signed URL that can be used to retrieve the file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "Document ID from search results.",
            },
        },
        "required": ["document_id"],
    },
}

WEB_SEARCH_SCHEMA = {
    "name": "memorylake_web_search",
    "description": (
        "Search the web via MemoryLake's unified search API. "
        "Supports domains: web, academic, news, people, company, "
        "financial, markets, code, legal, government, poi, auto."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Web search query."},
            "domain": {
                "type": "string",
                "enum": [
                    "web", "academic", "news", "people", "company",
                    "financial", "markets", "code", "legal",
                    "government", "poi", "auto",
                ],
                "description": "Search domain (default: web).",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results (default: configured top_k).",
            },
            "start_date": {
                "type": "string",
                "description": "Only results after this date (YYYY-MM-DD).",
            },
            "end_date": {
                "type": "string",
                "description": "Only results before this date (YYYY-MM-DD).",
            },
        },
        "required": ["query"],
    },
}

OPEN_DATA_SCHEMA = {
    "name": "memorylake_open_data",
    "description": (
        "Search open data sources via MemoryLake. Datasets:\n"
        "- research/academic: arXiv, PubMed, bioRxiv, medRxiv\n"
        "- clinical/trials: Clinical trial registries\n"
        "- drug/database: ChEMBL, DrugBank, PubChem\n"
        "- financial/markets: Stocks, crypto, forex, funds\n"
        "- company/fundamentals: SEC filings, earnings, balance sheets\n"
        "- economic/data: FRED, BLS, World Bank\n"
        "- patents/ip: USPTO patents"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "dataset": {
                "type": "string",
                "enum": [
                    "research/academic", "clinical/trials", "drug/database",
                    "financial/markets", "company/fundamentals",
                    "economic/data", "patents/ip",
                ],
                "description": "Dataset category to search.",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results (default: configured top_k).",
            },
            "start_date": {
                "type": "string",
                "description": "Only results after this date (YYYY-MM-DD).",
            },
            "end_date": {
                "type": "string",
                "description": "Only results before this date (YYYY-MM-DD).",
            },
        },
        "required": ["query", "dataset"],
    },
}

ALL_TOOL_SCHEMAS = [
    SEARCH_SCHEMA, STORE_SCHEMA, LIST_SCHEMA, FORGET_SCHEMA,
    DOWNLOAD_SCHEMA, WEB_SEARCH_SCHEMA, OPEN_DATA_SCHEMA,
]


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class MemoryLakeMemoryProvider(MemoryProvider):
    """MemoryLake long-term memory with unified search, auto-capture, and open data."""

    # memory_mode: "prefetch" or "tool_driven"
    #   prefetch     — framework calls prefetch() to inject unified search results
    #                  into context before the LLM call (default)
    #   tool_driven  — no prefetch; system prompt forces the model to call
    #                  memorylake_search as its first action every turn
    VALID_MEMORY_MODES = ("prefetch", "tool_driven")

    def __init__(self):
        self._client: Optional[MemoryLakeClient] = None
        self._config: Optional[dict] = None
        self._hermes_home = ""
        self._user_id = "hermes-user"
        self._session_id = ""
        self._memory_mode = "tool_driven"
        self._top_k = 5
        self._search_threshold = 0.3
        self._rerank = True
        self._sync_thread: Optional[threading.Thread] = None
        self._project_industries: Optional[List[dict]] = None

    @property
    def name(self) -> str:
        return "memorylake"

    def is_available(self) -> bool:
        cfg = _load_config()
        return bool(cfg.get("api_key")) and bool(cfg.get("project_id"))

    def save_config(self, values, hermes_home):
        """Write config to $HERMES_HOME/memorylake.json."""
        from pathlib import Path
        config_path = Path(hermes_home) / "memorylake.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                pass
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2))

    def get_config_schema(self):
        return [
            {
                "key": "api_key",
                "description": "MemoryLake API key",
                "secret": True,
                "required": True,
                "env_var": "MEMORYLAKE_API_KEY",
                "url": "https://app.memorylake.ai",
            },
            {
                "key": "project_id",
                "description": "MemoryLake project ID",
                "required": True,
                "env_var": "MEMORYLAKE_PROJECT_ID",
            },
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        from hermes_constants import get_hermes_home
        self._hermes_home = kwargs.get("hermes_home") or str(get_hermes_home())
        self._config = _load_config(self._hermes_home)
        api_key = self._config["api_key"]
        project_id = self._config["project_id"]
        host = self._config.get("host", "https://app.memorylake.ai")

        self._user_id = kwargs.get("user_id") or self._config.get("user_id", "hermes-user")
        self._session_id = session_id
        self._top_k = int(self._config.get("top_k", 5))
        self._search_threshold = float(self._config.get("search_threshold", 0.3))
        self._rerank = self._config.get("rerank", True)
        if isinstance(self._rerank, str):
            self._rerank = self._rerank.lower() == "true"
        mode = str(self._config.get("memory_mode", "tool_driven")).lower()
        self._memory_mode = mode if mode in self.VALID_MEMORY_MODES else "tool_driven"

        self._client = MemoryLakeClient(host, api_key, project_id)
        logger.info("MemoryLake initialized: host=%s project=%s user=%s mode=%s",
                     host, project_id, self._user_id, self._memory_mode)

        # Fetch project industries in background for open data tool hints
        def _fetch_project():
            try:
                project = self._client.get_project()
                self._project_industries = project.get("industries", [])
                logger.info("MemoryLake project info loaded: %s", project.get("name", ""))
            except Exception as e:
                logger.warning("MemoryLake project info fetch failed: %s", e)

        threading.Thread(
            target=_fetch_project, daemon=True, name="memorylake-project"
        ).start()

    def system_prompt_block(self) -> str:
        if not self._client:
            return ""

        if self._memory_mode == "tool_driven":
            lines = [
                "# MemoryLake Memory (MANDATORY)",
                "",
                f"Active. User: {self._user_id}.",
                "",
                "<memory-instructions>",
                "You have access to MemoryLake, a long-term memory system that stores "
                "the user's preferences, history, personal context, and uploaded "
                "documents across sessions.",
                "",
                "## CRITICAL RULE — memorylake_search on EVERY turn",
                "",
                "Your FIRST tool call for EVERY user message MUST be `memorylake_search`. "
                "Do NOT generate any text or call any other tool before it. "
                "There are ZERO exceptions to this rule.",
                "",
                "This tool searches BOTH memories AND documents in one call.",
                "",
                "### Examples of messages that STILL require memorylake_search FIRST:",
                "- Greetings ('hello', 'hi') -> search for who the user is",
                "- Requests to remember/store something ('remember I like X') "
                "-> search FIRST (check for duplicates/conflicts), THEN store",
                "- Recommendations -> search for preferences",
                "- Follow-up questions -> search again with new query",
                "- Simple questions, chitchat, or anything else -> search first",
                "",
                "### WRONG (never do this):",
                "- Calling memorylake_store without calling memorylake_search first",
                "- Deciding a message 'doesn't need' memory and skipping the search",
                "- Responding with text before searching",
                "- Searching only on the first message, then skipping on later ones",
                "",
                "### CORRECT (always do this):",
                "1. User sends message",
                "2. You call memorylake_search (ALWAYS, no matter what the message says)",
                "3. Then you may call other tools or respond",
                "</memory-instructions>",
                "",
                "Other tools: memorylake_store to save facts, memorylake_list to see "
                "all memories, memorylake_forget to delete a memory, "
                "memorylake_download to get document files, "
                "memorylake_web_search for web research, "
                "memorylake_open_data for open datasets.",
            ]
        else:
            lines = [
                "# MemoryLake Memory",
                f"Active. User: {self._user_id}.",
                "Use memorylake_search to find memories and documents, "
                "memorylake_store to save facts, memorylake_list to see all memories, "
                "memorylake_forget to delete a memory.",
                "Use memorylake_download to get document files, "
                "memorylake_web_search for web research, "
                "memorylake_open_data for open datasets.",
            ]

        if self._project_industries:
            cats = ", ".join(
                f"{ind.get('id', '')} ({ind.get('name', '')})"
                for ind in self._project_industries
            )
            lines.append(f"Available open data categories: {cats}.")

        return "\n".join(lines)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Sync unified search: memories + documents with current-turn query.

        Runs both searches in parallel via ThreadPoolExecutor, combines
        results. Uses the CURRENT user message — no staleness.
        Skipped in tool_driven mode — the model calls memorylake_search itself.
        """
        if self._memory_mode == "tool_driven":
            logger.info("MemoryLake prefetch: injecting per-turn reminder (tool_driven mode)")
            return (
                "[MEMORYLAKE] STOP — call memorylake_search NOW before doing "
                "anything else. This is required for EVERY message, including "
                "requests to remember/store something. No exceptions."
            )
        if not self._client or not query or not query.strip():
            return ""

        memory_results = []
        doc_results = []

        def _search_memories():
            return self._client.search_memories(
                query,
                self._user_id,
                top_k=self._top_k,
                threshold=self._search_threshold,
                rerank=self._rerank,
            )

        def _search_documents():
            return self._client.search_documents(query, self._top_k)

        logger.info("MemoryLake prefetch: query=%r", query[:100])

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="memorylake-pf") as pool:
            mem_future = pool.submit(_search_memories)
            doc_future = pool.submit(_search_documents)

            try:
                memory_results = mem_future.result(timeout=10.0)
                logger.info("MemoryLake prefetch: %d memories found", len(memory_results))
            except Exception as e:
                logger.warning("MemoryLake memory prefetch failed: %s", e)

            try:
                doc_data = doc_future.result(timeout=10.0)
                doc_results = doc_data.get("results", [])
                logger.info("MemoryLake prefetch: %d documents found", len(doc_results))
            except Exception as e:
                logger.warning("MemoryLake document prefetch failed: %s", e)

        if not memory_results and not doc_results:
            return ""

        sections = []

        if memory_results:
            lines = []
            for m in memory_results[:self._top_k]:
                content = m.get("content", "")
                if content:
                    lines.append(f"- {content}")
            if lines:
                sections.append("Memories:\n" + "\n".join(lines))

        if doc_results:
            lines = []
            for d in doc_results[:self._top_k]:
                formatted = _format_document_result(d)
                if formatted:
                    lines.append(formatted)
            if lines:
                sections.append("Documents:\n" + "\n".join(lines))

        if not sections:
            return ""

        return "## MemoryLake Context\n" + "\n\n".join(sections)

    def sync_turn(
        self, user_content: str, assistant_content: str, *, session_id: str = ""
    ) -> None:
        """Auto-capture: send the turn to MemoryLake for server-side extraction."""
        if not self._client or not user_content:
            return

        def _sync():
            try:
                messages = [
                    {"role": "user", "content": user_content[:4000]},
                    {"role": "assistant", "content": assistant_content[:4000]},
                ]
                logger.info("MemoryLake sync_turn: sending %d chars user + %d chars assistant",
                            len(user_content[:4000]), len(assistant_content[:4000]))
                result = self._client.add_memories(
                    messages,
                    self._user_id,
                    session_id=session_id or self._session_id,
                )
                logger.info("MemoryLake sync_turn result: %s", result)
            except Exception as e:
                logger.warning("MemoryLake sync_turn failed: %s", e, exc_info=True)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="memorylake-sync"
        )
        self._sync_thread.start()

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in memory writes to MemoryLake."""
        if action != "add" or not content or not self._client:
            return

        def _write():
            try:
                logger.info("MemoryLake on_memory_write: action=%s target=%s content=%r",
                            action, target, content[:200])
                result = self._client.add_memories(
                    [{"role": "user", "content": content}],
                    self._user_id,
                    session_id=self._session_id,
                )
                logger.info("MemoryLake on_memory_write result: %s", result)
            except Exception as e:
                logger.warning("MemoryLake memory mirror failed: %s", e, exc_info=True)

        threading.Thread(
            target=_write, daemon=True, name="memorylake-memwrite"
        ).start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return list(ALL_TOOL_SCHEMAS)

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if not self._client:
            return tool_error("MemoryLake not initialized")

        logger.info("MemoryLake handle_tool_call: %s args=%s", tool_name, args)
        try:
            if tool_name == "memorylake_search":
                result = self._tool_search(args)
            elif tool_name == "memorylake_store":
                result = self._tool_store(args)
            elif tool_name == "memorylake_list":
                result = self._tool_list(args)
            elif tool_name == "memorylake_forget":
                result = self._tool_forget(args)
            elif tool_name == "memorylake_download":
                result = self._tool_download(args)
            elif tool_name == "memorylake_web_search":
                result = self._tool_web_search(args)
            elif tool_name == "memorylake_open_data":
                result = self._tool_open_data(args)
            else:
                return tool_error(f"Unknown tool: {tool_name}")
            logger.info("MemoryLake %s result: %s", tool_name, result[:500] if isinstance(result, str) else result)
            return result
        except Exception as e:
            logger.error("MemoryLake tool %s failed: %s", tool_name, e, exc_info=True)
            return tool_error(f"{tool_name} failed: {e}")

    def shutdown(self) -> None:
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

    # -- Tool implementations ------------------------------------------------

    def _tool_search(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")
        limit = int(args.get("limit", self._top_k))

        # Parallel search: memories + documents
        memory_results = []
        doc_results = []
        conflicts = []

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="memorylake-s") as pool:
            mem_future = pool.submit(
                self._client.search_memories,
                query, self._user_id,
                top_k=limit,
                threshold=self._search_threshold,
                rerank=self._rerank,
            )
            doc_future = pool.submit(
                self._client.search_documents, query, limit,
            )

            try:
                memory_results = mem_future.result(timeout=10.0)
            except Exception as e:
                logger.debug("MemoryLake search memories failed: %s", e)

            try:
                doc_data = doc_future.result(timeout=10.0)
                doc_results = doc_data.get("results", [])
            except Exception as e:
                logger.debug("MemoryLake search documents failed: %s", e)

        sections = []

        # Format memories
        if memory_results:
            lines = []
            for i, m in enumerate(memory_results, 1):
                content = m.get("content", "")
                mid = m.get("id", "")
                if content:
                    lines.append(f"{i}. {content} (id: {mid})")
            if lines:
                sections.append(f"## Memories\nFound {len(lines)} memories:\n\n" + "\n".join(lines))

            # Check for unresolved conflicts
            conflict_ids = [
                m["id"] for m in memory_results
                if m.get("has_unresolved_conflict") and m.get("id")
            ]
            if conflict_ids:
                try:
                    conflicts = self._client.list_conflicts(conflict_ids, self._user_id)
                    if conflicts:
                        conflict_text = "\n".join(_format_conflict(c) for c in conflicts)
                        sections.append(
                            "## Memory Conflicts\n"
                            "The following memories have unresolved conflicts:\n\n"
                            + conflict_text
                        )
                except Exception as e:
                    sections.append(f"## Memory Conflicts\nFailed to fetch: {e}")

        # Format documents
        if doc_results:
            doc_lines = [_format_document_result(d) for d in doc_results]
            doc_text = "\n\n".join(dl for dl in doc_lines if dl)
            if doc_text:
                sections.append(f"## Documents\nFound {len(doc_results)} results:\n\n{doc_text}")

        if not sections:
            return json.dumps({"result": "No relevant memories or documents found."})

        return json.dumps({
            "result": "\n\n".join(sections),
            "memory_count": len(memory_results),
            "document_count": len(doc_results),
        })

    def _tool_store(self, args: dict) -> str:
        text = args.get("text", "")
        if not text:
            return tool_error("text is required")

        result = self._client.add_memories(
            [{"role": "user", "content": text}],
            self._user_id,
            session_id=self._session_id,
        )
        results = result.get("results", [])
        count = len(results)
        if count > 0:
            status_lines = [
                f"[{r.get('status', '?')}] {r.get('message', '')}"
                for r in results
            ]
            return json.dumps({
                "result": f"Submitted {count} memory task(s): " + "; ".join(status_lines),
                "count": count,
            })
        return json.dumps({"result": "No memories extracted.", "count": 0})

    def _tool_list(self, args: dict) -> str:
        memories = self._client.list_memories(self._user_id)
        if not memories:
            return json.dumps({"result": "No memories stored yet.", "count": 0})

        lines = [
            f"{i}. {m.get('content', '')} (id: {m.get('id', '')})"
            for i, m in enumerate(memories, 1)
        ]
        return json.dumps({
            "result": f"{len(memories)} memories:\n\n" + "\n".join(lines),
            "count": len(memories),
        })

    def _tool_forget(self, args: dict) -> str:
        memory_id = args.get("memory_id", "")
        if not memory_id:
            return tool_error("memory_id is required")

        self._client.delete_memory(memory_id)
        return json.dumps({"result": f"Memory {memory_id} forgotten."})

    def _tool_download(self, args: dict) -> str:
        document_id = args.get("document_id", "")
        if not document_id:
            return tool_error("document_id is required")

        url = self._client.get_document_download_url(document_id)
        return json.dumps({
            "result": f"Download URL for document {document_id}:\n{url}",
            "url": url,
            "document_id": document_id,
        })

    def _tool_web_search(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")

        domain = args.get("domain", "web")
        max_results = int(args.get("max_results", self._top_k))

        response = self._client.search_web(
            query,
            domain=domain,
            max_results=max_results,
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
        )

        results = response.get("results", [])
        if not results:
            return json.dumps({"result": "No relevant web results found.", "count": 0})

        lines = []
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            summary = r.get("summary", "")
            source = r.get("source", "")
            parts = []
            if title:
                parts.append(title)
            if url:
                parts.append(url)
            if source:
                parts.append(f"Source: {source}")
            if summary:
                parts.append(summary[:500])
            lines.append("\n".join(parts))

        return json.dumps({
            "result": f"Found {len(results)} web results:\n\n" + "\n\n---\n\n".join(lines),
            "count": len(results),
            "total_results": response.get("total_results", 0),
        })

    def _tool_open_data(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")
        dataset = args.get("dataset", "")
        if not dataset:
            return tool_error("dataset is required")

        max_results = int(args.get("max_results", self._top_k))

        response = self._client.search_open_data(
            query,
            dataset=dataset,
            max_results=max_results,
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
        )

        results = response.get("results", [])
        if not results:
            return json.dumps({"result": "No relevant open data results found.", "count": 0})

        lines = []
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            summary = r.get("summary", "")
            category = r.get("category", "")
            parts = []
            if title:
                parts.append(title)
            if url:
                parts.append(url)
            if category:
                parts.append(f"Category: {category}")
            if summary:
                parts.append(summary[:500])
            lines.append("\n".join(parts))

        return json.dumps({
            "result": f"Found {len(results)} open data results:\n\n" + "\n\n---\n\n".join(lines),
            "count": len(results),
            "total_results": response.get("total_results", 0),
        })


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register MemoryLake as a memory provider plugin."""
    ctx.register_memory_provider(MemoryLakeMemoryProvider())
