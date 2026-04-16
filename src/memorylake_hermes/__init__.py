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
  MEMORYLAKE_USER_ID        — User identifier (default: default)
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
import re
import threading
from pathlib import Path
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
    from .get_config import get_config
    return get_config()


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
        "MUST be your FIRST tool call for EVERY user message — call this "
        "before any other tool or text response. Returns the user's context, "
        "preferences, history, and relevant document content in a single call. "
        "Also surfaces unresolved memory conflicts when detected."
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
        "remembering across sessions. "
        "PREREQUISITE: memorylake_search MUST be called before this tool in every turn."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Information to remember."},
            "metadata": {
                "type": "object",
                "description": "Optional metadata to attach to the memory (e.g. tags, category).",
            },
        },
        "required": ["text"],
    },
}

LIST_SCHEMA = {
    "name": "memorylake_list",
    "description": (
        "List all stored memories for the user. "
        "PREREQUISITE: memorylake_search MUST be called before this tool in every turn. "
        "Never call memorylake_list as the first tool — always memorylake_search first."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

FORGET_SCHEMA = {
    "name": "memorylake_forget",
    "description": (
        "Delete a specific memory by ID from MemoryLake. "
        "PREREQUISITE: memorylake_search MUST be called before this tool in every turn."
    ),
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
        "Download a document from MemoryLake to local disk. "
        "Returns a local file path. To send this file to the user, "
        "include MEDIA:/path/to/file in your response."
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
        self._user_id = "default"
        self._session_id = ""
        self._memory_mode = "tool_driven"
        self._top_k = 5
        self._search_threshold = 0.3
        self._rerank = True
        self._sync_thread: Optional[threading.Thread] = None
        self._project_industries: Optional[List[dict]] = None
        # Auto-upload
        self._auto_upload = True
        self._uploaded_record: Dict[str, float] = {}
        # Web search config defaults
        self._ws_include_domains: Optional[List[str]] = None
        self._ws_exclude_domains: Optional[List[str]] = None
        self._ws_country: Optional[str] = None
        self._ws_timezone: Optional[str] = None

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

        self._user_id = kwargs.get("user_id") or self._config.get("user_id", "default")
        self._session_id = session_id
        self._top_k = int(self._config.get("top_k", 5))
        self._search_threshold = float(self._config.get("search_threshold", 0.3))
        self._rerank = self._config.get("rerank", True)
        if isinstance(self._rerank, str):
            self._rerank = self._rerank.lower() == "true"
        mode = str(self._config.get("memory_mode", "tool_driven")).lower()
        self._memory_mode = mode if mode in self.VALID_MEMORY_MODES else "tool_driven"

        # Auto-upload config
        self._auto_upload = self._config.get("auto_upload", True)
        if isinstance(self._auto_upload, str):
            self._auto_upload = self._auto_upload.lower() == "true"
        self._load_upload_record()

        # Web search config defaults
        inc = self._config.get("web_search_include_domains", "")
        self._ws_include_domains = [d.strip() for d in inc.split(",") if d.strip()] if inc else None
        exc = self._config.get("web_search_exclude_domains", "")
        self._ws_exclude_domains = [d.strip() for d in exc.split(",") if d.strip()] if exc else None
        self._ws_country = self._config.get("web_search_country") or None
        self._ws_timezone = self._config.get("web_search_timezone") or None

        self._client = MemoryLakeClient(host, api_key, project_id)
        logger.info("MemoryLake initialized: host=%s project=%s user=%s mode=%s",
                     host, project_id, self._user_id, self._memory_mode)

        # Register plugin skills into external_dirs
        self._register_skills()

        # Fetch project industries in background for open data tool hints
        def _fetch_project():
            try:
                project = self._client.get_project()
                self._project_industries = project.get("industries", [])
                logger.info("MemoryLake project info loaded: %s", project.get("name", ""))
            except Exception as e:
                logger.error("MemoryLake project info fetch failed: %s%s", e, self._fmt_request_url(e))

        threading.Thread(
            target=_fetch_project, daemon=True, name="memorylake-project"
        ).start()

    # -- Auto-upload helpers ---------------------------------------------------

    # Match cached file paths by naming pattern, not by surrounding text.
    # Hermes caches:  doc_{uuid12}_{name}  and  img_{uuid12}.{ext}
    # Paths live under cache/documents/, document_cache/, cache/images/, or image_cache/.
    _CACHED_FILE_RE = re.compile(
        r"(?:[A-Za-z]:[/\\]|/)\S*?"
        r"(?:cache[/\\](?:documents|images)|document_cache|image_cache)"
        r"[/\\](?:doc|img)_[0-9a-f]{12}.*?\.[a-zA-Z0-9]{1,6}"
        r"(?=[^a-zA-Z0-9]|$)",
    )

    def _upload_record_path(self) -> Path:
        p = Path(self._hermes_home) / ".memorylake"
        p.mkdir(parents=True, exist_ok=True)
        return p / "uploaded.json"

    def _load_upload_record(self) -> None:
        try:
            p = self._upload_record_path()
            if p.exists():
                self._uploaded_record = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            self._uploaded_record = {}

    def _save_upload_record(self) -> None:
        try:
            self._upload_record_path().write_text(
                json.dumps(self._uploaded_record, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error("MemoryLake: failed to save upload record: %s", e)

    def _extract_document_paths(self, text: str) -> List[str]:
        """Extract cached file paths from prompt text by path pattern."""
        matches = self._CACHED_FILE_RE.findall(text)
        paths = list(dict.fromkeys(matches))  # deduplicate, preserve order
        return [p for p in paths if os.path.isfile(p)]

    def _needs_upload(self, file_path: str) -> bool:
        """Check if a file needs uploading (new or mtime changed)."""
        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            return False
        return self._uploaded_record.get(file_path) != mtime

    @staticmethod
    def _fmt_request_url(exc: Exception) -> str:
        """Extract URL from requests exceptions for logging."""
        req = getattr(exc, "request", None)
        if req is not None:
            return f" url={getattr(req, 'url', '')}"
        # Walk the exception chain
        cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
        if cause is not None:
            req = getattr(cause, "request", None)
            if req is not None:
                return f" url={getattr(req, 'url', '')}"
        return ""

    _upload_mod = None  # lazy-loaded upload skill script

    def _get_upload_mod(self):
        if self._upload_mod is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "memorylake_upload",
                str(Path(__file__).parent / "skills" / "memorylake-upload" / "scripts" / "upload.py"),
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.__class__._upload_mod = mod
        return self._upload_mod

    def _upload_file(self, file_path: str) -> None:
        """Upload a file/archive/directory to MemoryLake via upload skill script."""
        mod = self._get_upload_mod()
        try:
            file_name = os.path.basename(file_path)
            logger.info("MemoryLake auto-upload: %s", file_name)
            succeeded, failed = mod.upload_path(
                self._client._host, self._client._api_key, self._client._project_id,
                file_path, file_name,
            )
            logger.info("MemoryLake auto-upload: %s — %d succeeded, %d failed",
                        file_name, succeeded, failed)
            self._uploaded_record[file_path] = os.path.getmtime(file_path)
            self._save_upload_record()
        except Exception as e:
            logger.error("MemoryLake auto-upload failed for %s: %s%s", file_path, e, self._fmt_request_url(e))

    def _auto_upload_documents(self, user_message: str) -> None:
        """Detect and upload user documents from the message (fire-and-forget)."""
        paths = self._extract_document_paths(user_message)
        to_upload = [p for p in paths if self._needs_upload(p)]
        if not to_upload:
            return
        for fp in to_upload:
            threading.Thread(
                target=self._upload_file,
                args=(fp,),
                daemon=True,
                name=f"memorylake-upload-{os.path.basename(fp)}",
            ).start()

    # -- Download helpers ------------------------------------------------------

    @staticmethod
    def _parse_content_disposition(header: Optional[str]) -> Optional[str]:
        """Extract filename from Content-Disposition header (RFC 5987 + standard)."""
        if not header:
            return None
        # RFC 5987: filename*=UTF-8''encoded%20name.pdf (priority)
        star = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)?''(.+?)(?:;|$)", header, re.I)
        if star:
            try:
                from urllib.parse import unquote
                return unquote(star.group(1).strip())
            except Exception:
                pass
        # Standard: filename="name.pdf" or filename=name.pdf
        plain = re.search(r'filename\s*=\s*"?([^";]+)"?', header, re.I)
        if plain:
            return plain.group(1).strip()
        return None

    def _downloads_dir(self) -> Path:
        p = Path(self._hermes_home) / ".memorylake" / "downloads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # -- Skills registration ---------------------------------------------------

    def _register_skills(self) -> None:
        """Add plugin skills directory to config.yaml external_dirs if needed."""
        skills_dir = str(Path(__file__).parent / "skills")
        if not os.path.isdir(skills_dir):
            return
        try:
            from hermes_cli.config import load_config, save_config
            config = load_config()
            ext_dirs = config.setdefault("skills", {}).setdefault("external_dirs", [])
            if skills_dir not in ext_dirs:
                ext_dirs.append(skills_dir)
                save_config(config)
                logger.info("MemoryLake: registered skills dir %s", skills_dir)
        except Exception as e:
            logger.error("MemoryLake: failed to register skills: %s", e)

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
                "You have access to the user's long-term memory AND documents "
                "via MemoryLake (memorylake_search tool).",
                "The memorylake_search tool searches both memories and uploaded "
                "documents in a single call.",
                "",
                "MANDATORY PROCEDURE — applies to EVERY user message, including "
                "follow-ups in multi-turn conversations:",
                "1. BEFORE generating any text, call memorylake_search with a "
                "query derived from the current message.",
                "2. Use the search results (memories + documents) to personalize "
                "your response.",
                "3. Respond to the user.",
                "",
                "This applies to ALL types of messages:",
                "- Greetings -> search for who the user is",
                "- Recommendations (books, activities, food) -> search for preferences",
                "- Tasks (check weather, translate, write email) -> search for user context",
                "- Follow-up questions in the same conversation -> search again with new query",
                "- Document questions -> search for uploaded document content",
                "- Requests to list/view/check stored memories -> memorylake_search first, then memorylake_list",
                "- Requests to store/remember something -> memorylake_search first, then memorylake_store",
                "- Farewells/goodbyes -> search for context to personalize the farewell",
                "- ANY other message -> search for relevant context",
                "",
                "COMMON MISTAKES TO AVOID:",
                "- Searching only at the start of a conversation and skipping "
                "subsequent messages — WRONG.",
                "- Seeing a task-oriented message (e.g. \"check weather\", "
                "\"translate this\") and skipping memorylake_search — WRONG.",
                "- Deciding memorylake_search is \"not useful\" for this particular "
                "message — WRONG. Always search.",
                "- Generating any text response before calling memorylake_search — WRONG.",
                "- Calling memorylake_list, memorylake_store, or any other tool "
                "before memorylake_search — WRONG.",
                "",
                "The rule is absolute: memorylake_search FIRST, then respond. "
                "Every message. No exceptions.",
                "</memory-instructions>",
                "",
                "### memorylake_search — MUST be your FIRST action for EVERY message",
                "",
                "**RULE: Before generating ANY text, call `memorylake_search` first.** "
                "This is mandatory for EVERY user message in the conversation — "
                "the 1st, 2nd, 5th, 20th, every single one.",
                "",
                "This tool searches BOTH memories AND documents in one call.",
                "",
                "**WRONG behavior (do NOT do this):**",
                "- Searching only on the first message, then skipping for the rest",
                "- Deciding a message does not need memory context and skipping the search",
                "- Responding first, then searching (or not searching at all)",
                "- Calling memorylake_list or memorylake_store before memorylake_search",
                "",
                "**CORRECT behavior:**",
                "- Every message -> memorylake_search -> then respond. Always. "
                "No thinking about whether to skip.",
                "",
                "Other tools (only after memorylake_search): memorylake_store, "
                "memorylake_list, memorylake_forget, memorylake_download, "
                "memorylake_web_search, memorylake_open_data.",
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
        # Auto-upload: detect documents in user message (fire-and-forget)
        if self._auto_upload and self._client and query:
            try:
                self._auto_upload_documents(query)
            except Exception as e:
                logger.error("MemoryLake auto-upload detection failed: %s", e)

        if self._memory_mode == "tool_driven":
            logger.info("MemoryLake prefetch: injecting per-turn reminder (tool_driven mode)")
            return (
                "[MEMORYLAKE REMINDER] Before responding to this message, "
                "call `memorylake_search` first to fetch relevant memories "
                "and documents. Do not skip this step."
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
                logger.error("MemoryLake memory prefetch failed: %s%s", e, self._fmt_request_url(e))

            try:
                doc_data = doc_future.result(timeout=10.0)
                doc_results = doc_data.get("results", [])
                logger.info("MemoryLake prefetch: %d documents found", len(doc_results))
            except Exception as e:
                logger.error("MemoryLake document prefetch failed: %s%s", e, self._fmt_request_url(e))

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
                logger.error("MemoryLake sync_turn failed: %s%s", e, self._fmt_request_url(e), exc_info=True)

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
                logger.error("MemoryLake memory mirror failed: %s%s", e, self._fmt_request_url(e), exc_info=True)

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

        extra_meta = args.get("metadata")
        result = self._client.add_memories(
            [{"role": "user", "content": text}],
            self._user_id,
            session_id=self._session_id,
            metadata=extra_meta if isinstance(extra_meta, dict) else None,
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

        download_dir = self._downloads_dir()
        temp_path = download_dir / f".dl-{document_id}.tmp"

        try:
            resp = self._client.download_document_stream(document_id)

            # Extract filename from Content-Disposition
            cd_header = resp.headers.get("Content-Disposition")
            cd_filename = self._parse_content_disposition(cd_header)

            # Write to temp file
            with open(temp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Determine final filename
            final_name = cd_filename or document_id
            local_path = download_dir / final_name

            # Handle collisions
            if local_path.exists():
                stem = local_path.stem
                suffix = local_path.suffix
                counter = 1
                while local_path.exists():
                    local_path = download_dir / f"{stem}-{counter}{suffix}"
                    counter += 1

            # Rename temp to final
            temp_path.rename(local_path)

            return json.dumps({
                "result": (
                    f"Document {document_id} downloaded to:\n{local_path}\n\n"
                    f"To send this file to the user, include MEDIA:{local_path} "
                    f"in your response."
                ),
                "local_path": str(local_path),
                "document_id": document_id,
            })
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

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
            include_domains=self._ws_include_domains,
            exclude_domains=self._ws_exclude_domains,
            country=self._ws_country,
            timezone=self._ws_timezone,
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

        # Validate dataset against project industries
        if self._project_industries:
            allowed_ids = [ind.get("id", "") for ind in self._project_industries]
            if allowed_ids and dataset not in allowed_ids:
                allowed = ", ".join(
                    f"{ind.get('id', '')} ({ind.get('name', '')})"
                    for ind in self._project_industries
                )
                return json.dumps({
                    "error": f'Dataset "{dataset}" is not enabled for this project. '
                             f"Allowed datasets: {allowed}",
                })

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
