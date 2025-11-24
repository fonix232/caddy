import os
import requests
import sys
import json
import uuid
from datetime import datetime, timezone

# --- Configuration ---
GITHUB_REPO = "caddyserver/caddy"
OFFICIAL_CADDY_IMAGE = "library/caddy"
CUSTOM_IMAGE = os.environ.get('DOCKERHUB_REPOSITORY_NAME', "caddybuilds/caddy-cloudflare")
CUSTOM_TAG_PREFIX = ""
# Which registry to use for custom image: 'dockerhub' or 'ghcr' (default: 'ghcr')
CUSTOM_REGISTRY = os.environ.get('CUSTOM_REGISTRY', 'ghcr').lower()
REQUIRED_PLATFORMS = {
    "linux/amd64",
    "linux/arm64",
}

def log_error(message):
    """Prints an error message formatted for GitHub Actions."""
    print(f"::error::ACTION_SCRIPT::{message}", file=sys.stderr)

def log_info(message):
    print(message, file=sys.stdout)

def set_action_output(output_name, value):
    """Sets the GitHub Action output using the GITHUB_OUTPUT environment file."""
    if "GITHUB_OUTPUT" not in os.environ:
        print(f"::warning::GITHUB_OUTPUT environment variable not found. Cannot set output '{output_name}'.", file=sys.stderr)
        return

    output_path = os.environ["GITHUB_OUTPUT"]
    output_value = str(value)

    try:
        with open(output_path, "a", encoding='utf-8') as f:
            if '\n' in output_value:
                delimiter = f"ghadelimiter_{uuid.uuid4()}"
                print(f"{output_name}<<{delimiter}", file=f)
                print(output_value, file=f)
                print(delimiter, file=f)
            else:
                print(f"{output_name}={output_value}", file=f)
    except OSError as e:
        log_error(f"Error writing to GITHUB_OUTPUT file at {output_path}: {e}")
        sys.exit(1)


def get_latest_caddy_release(token=None):
    """Fetches the latest release tag from the Caddy GitHub repository."""
    url = f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
    headers = {}
    
    if token:
        headers['Authorization'] = f'Bearer {token}'
        log_info(f"Fetching latest release from {url} (authenticated)")
    else:
        log_info(f"Fetching latest release from {url} (unauthenticated - may hit rate limits)")
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        release = response.json()
        tag_name = release.get('tag_name')
        if not tag_name or not tag_name.startswith('v'):
             log_error(f"Invalid or missing 'tag_name' in GitHub release response: {tag_name}")
             sys.exit(1)
        log_info(f"Latest Caddy GitHub release tag found: {tag_name}")
        return tag_name
    except requests.exceptions.Timeout:
         log_error(f"Timeout fetching latest GitHub release from {url}")
         sys.exit(1)
    except requests.exceptions.HTTPError as e:
        log_error(f"HTTP Error fetching latest GitHub release: {e.response.status_code} {e}")
        if 400 <= e.response.status_code < 500:
            log_error(f"Response body: {e.response.text[:500]}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        log_error(f"Network error fetching latest GitHub release: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log_error(f"Error decoding GitHub API JSON response: {e}")
        sys.exit(1)


def check_docker_hub_tag(image_name, tag):
    """Checks if a specific tag exists for a Docker Hub image. Returns tag data or None."""
    url = f"https://hub.docker.com/v2/repositories/{image_name}/tags/{tag}"
    try:
        response = requests.get(url, timeout=45)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return None
        else:
            log_error(f"Unexpected status {response.status_code} checking Docker Hub tag '{tag}' for '{image_name}'. Response: {response.text[:200]}")
            return None
    except requests.exceptions.Timeout:
        log_error(f"Timeout checking Docker Hub tag '{tag}' for '{image_name}' at {url}")
        return None
    except requests.exceptions.RequestException as e:
        log_error(f"Network error checking Docker Hub tag '{tag}' for '{image_name}': {e}")
        return None
    except json.JSONDecodeError as e:
        log_error(f"Error decoding Docker Hub API response for tag '{tag}' of '{image_name}': {e}. Response: {response.text[:200]}")
        return None


def check_ghcr_tag(image_name, tag, token=None):
    """Checks if a specific tag exists for GHCR.io and returns manifest data."""
    if not token:
        log_error("GITHUB_TOKEN environment variable not set for GHCR registry check.")
        return None
    
    # For GHCR, we need to exchange the GitHub token for a registry token
    # First, try to get an anonymous token for public packages, then fall back to authenticated
    registry_token = None
    
    # Try to get a registry-specific token
    auth_url = f"https://ghcr.io/token?scope=repository:{image_name}:pull"
    try:
        auth_response = requests.get(
            auth_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30
        )
        if auth_response.status_code == 200:
            registry_token = auth_response.json().get('token')
        else:
            log_info(f"Could not get GHCR registry token, status: {auth_response.status_code}")
            # Fall back to using the GitHub token directly
            registry_token = token
    except Exception as e:
        log_info(f"Error getting GHCR registry token: {e}, falling back to direct token")
        registry_token = token
    
    manifest_url = f"https://ghcr.io/v2/{image_name}/manifests/{tag}"
    headers = {
        "Authorization": f"Bearer {registry_token}",
        "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"
    }

    try:
        response = requests.get(manifest_url, headers=headers, timeout=45)
        if response.status_code == 404:
            return None
        elif response.status_code == 401 or response.status_code == 403:
            log_error(f"Authentication failed for GHCR.io (status {response.status_code}). Response: {response.text[:200]}")
            log_error(f"Make sure the package '{image_name}' is public or GITHUB_TOKEN has 'packages: read' permission.")
            return None
        elif response.status_code != 200:
            log_error(f"Unexpected status {response.status_code} checking GHCR.io tag '{tag}' for '{image_name}'. Response: {response.text[:200]}")
            return None
        
        manifest = response.json()
        
        # Handle manifest list (multi-arch)
        if manifest.get('mediaType') in [
            'application/vnd.oci.image.index.v1+json',
            'application/vnd.docker.distribution.manifest.list.v2+json'
        ]:
            images = []
            for m in manifest.get('manifests', []):
                platform = m.get('platform', {})
                os_name = platform.get('os')
                arch = platform.get('architecture')
                variant = platform.get('variant')
                
                if os_name and arch:
                    image_entry = {
                        'os': os_name,
                        'architecture': arch
                    }
                    if variant:
                        image_entry['variant'] = variant
                    images.append(image_entry)
            
            return {'images': images} if images else None
        
        # Handle single-arch manifest
        elif manifest.get('mediaType') in [
            'application/vnd.oci.image.manifest.v1+json',
            'application/vnd.docker.distribution.manifest.v2+json'
        ]:
            config_digest = manifest.get('config', {}).get('digest')
            if config_digest:
                config_url = f"https://ghcr.io/v2/{image_name}/blobs/{config_digest}"
                config_headers = {
                    "Authorization": f"Bearer {registry_token}",
                    "Accept": "application/vnd.oci.image.config.v1+json"
                }
                config_response = requests.get(config_url, headers=config_headers, timeout=45)
                if config_response.status_code == 200:
                    config = config_response.json()
                    os_name = config.get('os')
                    arch = config.get('architecture')
                    variant = config.get('variant')
                    
                    if os_name and arch:
                        image_entry = {
                            'os': os_name,
                            'architecture': arch
                        }
                        if variant:
                            image_entry['variant'] = variant
                        return {'images': [image_entry]}
            
            log_error(f"Could not extract platform info from single-arch manifest for '{image_name}:{tag}'")
            return None
        
        else:
            log_error(f"Unknown manifest type for GHCR.io '{image_name}:{tag}': {manifest.get('mediaType')}")
            return None
            
    except requests.exceptions.Timeout:
        log_error(f"Timeout checking GHCR.io tag '{tag}' for '{image_name}' at {manifest_url}")
        return None
    except requests.exceptions.RequestException as e:
        log_error(f"Network error checking GHCR.io tag '{tag}' for '{image_name}': {e}")
        return None
    except json.JSONDecodeError as e:
        log_error(f"Error decoding GHCR.io API response for tag '{tag}' of '{image_name}': {e}. Response: {response.text[:200]}")
        return None


def get_platforms_from_tag_data(tag_data):
    """Extracts required linux platform strings from tag API response."""
    platforms = set()
    if not tag_data or 'images' not in tag_data or not isinstance(tag_data['images'], list):
        log_info("Could not find valid 'images' list in tag data.")
        return platforms

    for img in tag_data['images']:
        if not isinstance(img, dict):
            continue
        os_name = img.get('os')
        arch = img.get('architecture')
        variant = img.get('variant')

        if os_name != "linux" or not arch:
            continue

        platform_str = ""
        if arch == "arm" and variant == "v7":
            platform_str = f"{os_name}/{arch}/{variant}"
        elif f"{os_name}/{arch}" in REQUIRED_PLATFORMS:
             platform_str = f"{os_name}/{arch}"

        if platform_str in REQUIRED_PLATFORMS:
             platforms.add(platform_str)

    return platforms


def main():
    start_time = datetime.now(timezone.utc)
    github_token = os.environ.get("GITHUB_TOKEN")
    log_info(f"--- Starting Caddy Check at {start_time.isoformat()} ---")
    log_info(f"Using registry: {CUSTOM_REGISTRY.upper()}")

    if not REQUIRED_PLATFORMS:
        log_error("Configuration error: REQUIRED_PLATFORMS set is empty.")
        sys.exit(1)
    
    if CUSTOM_REGISTRY not in ['dockerhub', 'ghcr']:
        log_error(f"Configuration error: CUSTOM_REGISTRY must be 'dockerhub' or 'ghcr', got '{CUSTOM_REGISTRY}'")
        sys.exit(1)
    
    log_info(f"Required platforms: {REQUIRED_PLATFORMS}")

    latest_gh_tag = get_latest_caddy_release(github_token)
    official_docker_tag = latest_gh_tag.lstrip('v')
    custom_docker_tag = f"{CUSTOM_TAG_PREFIX}{official_docker_tag}"

    # 1. Check if the official Caddy image tag exists AND has required platforms
    log_info(f"Step 1: Checking official image '{OFFICIAL_CADDY_IMAGE}:{official_docker_tag}'...")
    official_image_data = check_docker_hub_tag(OFFICIAL_CADDY_IMAGE, official_docker_tag)
    official_image_ready = False
    
    if official_image_data:
        log_info(f"  Official tag '{official_docker_tag}' found. Verifying platforms...")
        found_official_platforms = get_platforms_from_tag_data(official_image_data)
        log_info(f"  Found official platforms relevant to requirements: {found_official_platforms or '{}'}")
        required_platforms_missing_in_official = REQUIRED_PLATFORMS - found_official_platforms

        if not required_platforms_missing_in_official:
            log_info(f"  Official image has all required platforms.")
            official_image_ready = True
        else:
            log_info(f"  Official image is MISSING required platforms: {required_platforms_missing_in_official}.")
    else:
        log_info(f"  Official Caddy image tag '{official_docker_tag}' not found.")

    # Exit if official image isn't ready
    if not official_image_ready:
        log_info("Result: Official image is not ready. No build triggered.")
        set_action_output('NEEDS_BUILD', 'false') 
        set_action_output('LATEST_VERSION', latest_gh_tag)
        sys.exit(0)

    # 2. Check custom image on configured registry
    log_info(f"Step 2: Checking custom image '{CUSTOM_IMAGE}:{custom_docker_tag}' on {CUSTOM_REGISTRY.upper()}...")
    custom_image_complete = False

    if CUSTOM_REGISTRY == 'dockerhub':
        custom_tag_data = check_docker_hub_tag(CUSTOM_IMAGE, custom_docker_tag)
        registry_name = "Docker Hub"
    else:  # ghcr
        custom_tag_data = check_ghcr_tag(CUSTOM_IMAGE, custom_docker_tag, github_token)
        registry_name = "GHCR.io"

    if custom_tag_data:
        log_info(f"  Custom image tag '{custom_docker_tag}' found on {registry_name}. Verifying platforms...")
        found_custom_platforms = get_platforms_from_tag_data(custom_tag_data)
        log_info(f"  Found custom platforms relevant to requirements: {found_custom_platforms or '{}'}")
        required_platforms_missing_in_custom = REQUIRED_PLATFORMS - found_custom_platforms
        
        if not required_platforms_missing_in_custom:
            custom_image_complete = True
            log_info(f"  Custom image on {registry_name} already exists and is complete.")
        else:
            log_info(f"  Custom image on {registry_name} exists but is MISSING required platforms: {required_platforms_missing_in_custom}.")
    else:
        log_info(f"  Custom image tag '{custom_docker_tag}' NOT found on {registry_name}.")
    
    # 3. Decide if a build is needed
    needs_build = not custom_image_complete

    log_info(f"Step 3: Final decision for Caddy {latest_gh_tag}: Needs build = {needs_build}")
    set_action_output('NEEDS_BUILD', 'true' if needs_build else 'false') 
    set_action_output('LATEST_VERSION', latest_gh_tag) 

    end_time = datetime.now(timezone.utc)
    log_info(f"--- Check finished at {end_time.isoformat()} (Duration: {end_time - start_time}) ---")


if __name__ == "__main__":
    main()
