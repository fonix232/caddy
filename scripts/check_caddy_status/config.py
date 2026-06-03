import os
from pathlib import Path

# GitHub
GITHUB_REPO = "caddyserver/caddy"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Derive image names from GITHUB_REPOSITORY (e.g. "CaddyBuilds/caddy-cloudflare")
# This makes the script work for any fork without configuration.
_github_repository = os.environ.get("GITHUB_REPOSITORY", "").lower()

# Target registry: 'ghcr', 'dockerhub', or 'both' (default)
CADDY_REGISTRY = os.environ.get("CADDY_REGISTRY", "both").lower()

OFFICIAL_CADDY_IMAGE = "library/caddy"
CUSTOM_IMAGE = os.environ.get("DOCKERHUB_REPOSITORY_NAME", "") or _github_repository
GHCR_IMAGE = os.environ.get("GHCR_IMAGE", "") or _github_repository

# Platforms required in both official and custom images
# Override via env var: REQUIRED_PLATFORMS=linux/amd64,linux/arm64
_platforms_env = os.environ.get("REQUIRED_PLATFORMS", "")
REQUIRED_PLATFORMS = (
    {p.strip() for p in _platforms_env.split(",") if p.strip()}
    if _platforms_env
    else {
        "linux/amd64",
        "linux/arm64",
        "linux/arm/v7",
        "linux/ppc64le",
        "linux/s390x",
    }
)

# Modules built into the Docker image — used to detect upstream updates.
# Extend via CADDY_EXTRA_MODULES (same comma-separated xcaddy spec format as
# used by build-caddy.sh, e.g. "github.com/foo/bar,github.com/orig=github.com/fork@ref").
# The GitHub repo for tracking is derived from the spec: for fork replacements
# (orig=fork@ref), the fork repo is tracked; otherwise the module path is used.
def _parse_module_spec(spec):
    spec = spec.strip()
    if not spec:
        return None
    module_path, _, fork_spec = spec.partition("=")
    tracking = (fork_spec.partition("@")[0] if fork_spec else module_path).strip()
    parts = [p for p in tracking.replace("github.com/", "").split("/") if p]
    if len(parts) < 2:
        return None
    return {"module": module_path.strip(), "repo": f"{parts[0]}/{parts[1]}"}

_modules_base = [
    {"module": "github.com/caddy-dns/cloudflare", "repo": "caddy-dns/cloudflare"},
    {"module": "github.com/WeidiDeng/caddy-cloudflare-ip", "repo": "WeidiDeng/caddy-cloudflare-ip"},
    {"module": "github.com/fvbommel/caddy-combine-ip-ranges", "repo": "fvbommel/caddy-combine-ip-ranges"},
]
_modules_extra_env = os.environ.get("CADDY_EXTRA_MODULES", "")
MODULES = _modules_base + [
    m for m in (_parse_module_spec(s) for s in _modules_extra_env.split(","))
    if m is not None
] if _modules_extra_env else _modules_base

# Path to module version tracking file (repo root)
MODULE_VERSIONS_FILE = Path(__file__).resolve().parent.parent.parent / "module-versions.json"

# HTTP retry settings
MAX_RETRIES = 3
BACKOFF_BASE = 2
