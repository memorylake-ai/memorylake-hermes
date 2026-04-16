#!/usr/bin/env python3
"""Read MemoryLake config from env vars and memorylake.json, merge, and print JSON.

Env vars (from $HERMES_HOME/.env, loaded into the process by hermes):
    MEMORYLAKE_HOST, MEMORYLAKE_API_KEY, MEMORYLAKE_PROJECT_ID

Config file (optional overlay, takes precedence):
    $HERMES_HOME/memorylake.json

Output: merged config as JSON to stdout.
Exit 1 with error message if api_key or project_id are missing.
"""

import json
import os
import sys
from pathlib import Path

try:
    from hermes_constants import get_hermes_home
except ImportError:
    def get_hermes_home():
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def get_config() -> dict:
    hermes_home = str(get_hermes_home())

    # Base: all MEMORYLAKE_ env vars (mirrors __init__.py _load_config)
    config = {
        "host": os.environ.get("MEMORYLAKE_HOST", "https://app.memorylake.ai"),
        "api_key": os.environ.get("MEMORYLAKE_API_KEY", ""),
        "project_id": os.environ.get("MEMORYLAKE_PROJECT_ID", ""),
        "user_id": os.environ.get("MEMORYLAKE_USER_ID", "default"),
        "top_k": int(os.environ.get("MEMORYLAKE_TOP_K", "5")),
        "search_threshold": float(os.environ.get("MEMORYLAKE_SEARCH_THRESHOLD", "0.3")),
        "rerank": os.environ.get("MEMORYLAKE_RERANK", "true").lower() == "true",
        "memory_mode": os.environ.get("MEMORYLAKE_MEMORY_MODE", "tool_driven"),
        "auto_upload": os.environ.get("MEMORYLAKE_AUTO_UPLOAD", "true").lower() == "true",
        "web_search_include_domains": os.environ.get("MEMORYLAKE_WEB_SEARCH_INCLUDE_DOMAINS", ""),
        "web_search_exclude_domains": os.environ.get("MEMORYLAKE_WEB_SEARCH_EXCLUDE_DOMAINS", ""),
        "web_search_country": os.environ.get("MEMORYLAKE_WEB_SEARCH_COUNTRY", ""),
        "web_search_timezone": os.environ.get("MEMORYLAKE_WEB_SEARCH_TIMEZONE", ""),
    }

    # Overlay: memorylake.json (takes precedence)
    config_path = Path(hermes_home) / "memorylake.json"
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(file_cfg, dict):
                for k, v in file_cfg.items():
                    if v not in (None, ""):
                        config[k] = v
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: Failed to read {config_path}: {e}", file=sys.stderr)

    config["host"] = config["host"].rstrip("/")
    config["hermes_home"] = hermes_home
    return config


def main():
    config = get_config()

    if not config.get("api_key") or not config.get("project_id"):
        print(
            "ERROR: api_key and project_id are required. "
            "Set MEMORYLAKE_API_KEY/MEMORYLAKE_PROJECT_ID in $HERMES_HOME/.env "
            "or add them to $HERMES_HOME/memorylake.json",
            file=sys.stderr,
        )
        sys.exit(1)

    print(json.dumps(config, indent=2))


if __name__ == "__main__":
    main()
