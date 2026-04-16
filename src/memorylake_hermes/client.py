"""MemoryLake HTTP client — thin wrapper over the MemoryLake V2/V1 REST API.

All methods are synchronous and raise on HTTP errors.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_UPLOAD_TIMEOUT = 120.0


def _read_plugin_version() -> str:
    """Read plugin version from plugin.yaml."""
    try:
        import yaml
        p = Path(__file__).parent / "plugin.yaml"
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            return str(data.get("version", ""))
    except Exception:
        # Fallback: simple parse without pyyaml
        try:
            p = Path(__file__).parent / "plugin.yaml"
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


_PLUGIN_VERSION = _read_plugin_version()


def _base_metadata() -> Dict[str, Any]:
    """Base metadata included in all add_memories calls."""
    meta: Dict[str, Any] = {"source": "HERMES"}
    if _PLUGIN_VERSION:
        meta["plugin_version"] = _PLUGIN_VERSION
    return meta


class MemoryLakeClient:
    """HTTP client for the MemoryLake platform API."""

    def __init__(self, host: str, api_key: str, project_id: str):
        self._host = host.rstrip("/")
        self._api_key = api_key
        self._project_id = project_id

        # Endpoint prefixes
        self._mem_v2 = f"openapi/memorylake/api/v2/projects/{project_id}/memories"
        self._doc_v1 = f"openapi/memorylake/api/v1/projects/{project_id}/documents"
        self._search_v1 = "openapi/memorylake/api/v1/search"
        self._project_v1 = f"openapi/memorylake/api/v1/projects/{project_id}"
        self._upload_v1 = "openapi/memorylake/api/v1/upload"

    def _url(self, path: str) -> str:
        return f"{self._host}/{path}"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _unwrap(self, resp: requests.Response) -> Any:
        """Parse JSON response and unwrap the MemoryLake envelope.

        MemoryLake API returns ``{success: bool, message?: str, data?: T}``.
        Raises on HTTP or application-level errors.
        """
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and "success" in body:
            if not body["success"]:
                raise RuntimeError(body.get("message", "API call failed"))
            return body.get("data")
        # Some endpoints (web search, open data) return raw payloads
        return body

    # -- Memories (V2) --------------------------------------------------------

    def search_memories(
        self,
        query: str,
        user_id: str,
        *,
        top_k: int = 5,
        threshold: float = 0.3,
        rerank: bool = True,
    ) -> List[Dict[str, Any]]:
        """Semantic search across stored memories."""
        payload: Dict[str, Any] = {
            "query": query,
            "user_id": user_id,
            "with_conflicts": True,
        }
        if top_k:
            payload["top_k"] = top_k
        if threshold is not None:
            payload["threshold"] = threshold
        if rerank is not None:
            payload["rerank"] = rerank

        resp = requests.post(
            self._url(f"{self._mem_v2}/search"),
            json=payload,
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        data = self._unwrap(resp)
        # Normalize: API may return list or {results: [...]}
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results", data.get("items", []))
        return []

    def add_memories(
        self,
        messages: List[Dict[str, str]],
        user_id: str,
        *,
        session_id: Optional[str] = None,
        infer: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send messages for server-side memory extraction."""
        meta = _base_metadata()
        if metadata:
            meta.update(metadata)
        payload: Dict[str, Any] = {
            "messages": messages,
            "user_id": user_id,
            "infer": infer,
            "metadata": meta,
        }
        if session_id:
            payload["chat_session_id"] = session_id

        resp = requests.post(
            self._url(self._mem_v2),
            json=payload,
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        data = self._unwrap(resp)
        if isinstance(data, dict):
            return data
        return {"results": data if isinstance(data, list) else []}

    def list_memories(
        self, user_id: str, *, page: Optional[int] = None, size: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List all stored memories for a user."""
        params: Dict[str, Any] = {"user_id": user_id}
        if page is not None:
            params["page"] = page
        if size is not None:
            params["size"] = size

        resp = requests.get(
            self._url(self._mem_v2),
            params=params,
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        data = self._unwrap(resp)
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        if isinstance(data, list):
            return data
        return []

    def delete_memory(self, memory_id: str) -> None:
        """Delete a memory by ID."""
        resp = requests.delete(
            self._url(f"{self._mem_v2}/{quote(memory_id, safe='')}"),
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        self._unwrap(resp)

    def list_conflicts(
        self, memory_ids: List[str], user_id: str
    ) -> List[Dict[str, Any]]:
        """List unresolved conflicts for the given memory IDs."""
        if not memory_ids:
            return []
        params = {
            "resolved": "false",
            "memory_ids": ",".join(memory_ids),
        }
        resp = requests.get(
            self._url(f"{self._mem_v2}/conflicts"),
            params=params,
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        data = self._unwrap(resp)
        if isinstance(data, dict):
            return data.get("items", [])
        return data if isinstance(data, list) else []

    # -- Documents (V1) -------------------------------------------------------

    def search_documents(
        self, query: str, top_n: int = 5
    ) -> Dict[str, Any]:
        """Search uploaded project documents."""
        resp = requests.post(
            self._url(f"{self._doc_v1}/search"),
            json={"query": query, "top_N": top_n},
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        data = self._unwrap(resp)
        if isinstance(data, dict):
            return {
                "count": data.get("count", 0),
                "results": data.get("results", []),
            }
        return {"count": 0, "results": []}

    def get_document_download_url(self, document_id: str) -> str:
        """Get a pre-signed download URL for a document (follows 303 redirect)."""
        resp = requests.get(
            self._url(f"{self._doc_v1}/{quote(document_id, safe='')}/download"),
            headers=self._headers(),
            timeout=_TIMEOUT,
            allow_redirects=False,
        )
        if resp.status_code in (302, 303):
            location = resp.headers.get("Location")
            if not location:
                raise RuntimeError("Download redirect missing Location header")
            return location
        if resp.status_code == 404:
            raise RuntimeError(f"Document not found: {document_id}")
        resp.raise_for_status()
        raise RuntimeError(f"Unexpected download response: {resp.status_code}")

    # -- Web Search (V1) ------------------------------------------------------

    def search_web(
        self,
        query: str,
        *,
        domain: str = "web",
        max_results: int = 5,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        country: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Unified web search across 12 domains."""
        payload: Dict[str, Any] = {
            "query": query,
            "domain": domain,
        }
        if max_results:
            payload["max_results"] = max_results
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains
        if country or timezone:
            payload["user_location"] = {}
            if country:
                payload["user_location"]["country"] = country
            if timezone:
                payload["user_location"]["timezone"] = timezone

        resp = requests.post(
            self._url(self._search_v1),
            json=payload,
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        data = resp.json()
        return {
            "results": data.get("results", []),
            "total_results": data.get("total_results", 0),
        }

    # -- Open Data Search (V1) ------------------------------------------------

    def search_open_data(
        self,
        query: str,
        dataset: str,
        *,
        max_results: int = 5,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search open data sources (arXiv, PubMed, SEC, FRED, etc.)."""
        payload: Dict[str, Any] = {"query": query, "dataset": dataset}
        if max_results:
            payload["max_results"] = max_results
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date

        resp = requests.post(
            self._url(f"{self._search_v1}/opendata"),
            json=payload,
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        data = resp.json()
        return {
            "results": data.get("results", []),
            "total_results": data.get("total_results", 0),
        }

    # -- Upload (V1) -----------------------------------------------------------

    def create_multipart_upload(self, file_size: int) -> Dict[str, Any]:
        """Create a multipart upload session. Returns upload_id, object_key, part_items."""
        resp = requests.post(
            self._url(f"{self._upload_v1}/create-multipart"),
            json={"file_size": file_size},
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        return self._unwrap(resp)

    def upload_part(self, upload_url: str, data: bytes) -> str:
        """Upload a single part to the pre-signed URL. Returns the ETag."""
        resp = requests.put(
            upload_url,
            data=data,
            headers={"Content-Type": "application/octet-stream"},
            timeout=_UPLOAD_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.headers.get("ETag", "").strip('"')

    def complete_multipart_upload(
        self, upload_id: str, object_key: str, part_etags: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Complete the multipart upload."""
        resp = requests.post(
            self._url(f"{self._upload_v1}/complete-multipart"),
            json={
                "upload_id": upload_id,
                "object_key": object_key,
                "part_etags": part_etags,
            },
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        return self._unwrap(resp)

    def quick_add_document(self, object_key: str, file_name: str) -> Dict[str, Any]:
        """Associate an uploaded object with the project as a document."""
        resp = requests.post(
            self._url(f"{self._doc_v1}/quick-add"),
            json={"object_key": object_key, "file_name": file_name},
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        return self._unwrap(resp)

    # -- Download (stream) ----------------------------------------------------

    def download_document_stream(self, document_id: str) -> requests.Response:
        """Get a streaming response for a document download (follows redirect)."""
        url = self.get_document_download_url(document_id)
        resp = requests.get(url, stream=True, timeout=_UPLOAD_TIMEOUT)
        resp.raise_for_status()
        return resp

    # -- Project Info (V1) ----------------------------------------------------

    def get_project(self) -> Dict[str, Any]:
        """Get project info including enabled open data categories."""
        resp = requests.get(
            self._url(self._project_v1),
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        data = self._unwrap(resp)
        if not isinstance(data, dict):
            return {"id": "", "name": "", "industries": []}
        return {
            "id": data.get("id", ""),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "industries": data.get("industries", []),
        }
