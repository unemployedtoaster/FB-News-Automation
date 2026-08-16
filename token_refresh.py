"""
Token refresh — exchanges the current FB token for a new long-lived one
and writes it back to the GitHub repo secret via the API.

Requires a GitHub Personal Access Token (classic) with secrets:write scope
stored as GH_PAT in your repo secrets.
"""
import os
import base64
import requests
from nacl import encoding, public  # PyNaCl — for encrypting the secret


FB_APP_ID      = os.environ["FB_APP_ID"]
FB_APP_SECRET  = os.environ["FB_APP_SECRET"]
FB_ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]
GH_TOKEN       = os.environ["GH_TOKEN"]
GH_REPO        = os.environ["GH_REPO"]  # e.g. "yourname/reel-pipeline"


def refresh_fb_token(current_token: str) -> str | None:
    """Exchange a token for a new long-lived token (valid ~60 days)."""
    resp = requests.get(
        "https://graph.facebook.com/oauth/access_token",
        params={
            "grant_type":        "fb_exchange_token",
            "client_id":         FB_APP_ID,
            "client_secret":     FB_APP_SECRET,
            "fb_exchange_token": current_token,
        },
        timeout=15,
    )
    if resp.status_code == 200:
        new_token = resp.json().get("access_token")
        expires_in = resp.json().get("expires_in", "unknown")
        print(f"[TokenRefresh] New token obtained. Expires in: {expires_in}s")
        return new_token
    else:
        print(f"[TokenRefresh] FB refresh failed: {resp.status_code} {resp.text}")
        return None


def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Encrypt a secret value using the repo's public key (required by GitHub API)."""
    pub_key = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder)
    sealed_box = public.SealedBox(pub_key)
    encrypted = sealed_box.encrypt(secret_value.encode())
    return base64.b64encode(encrypted).decode()


def update_github_secret(secret_name: str, secret_value: str):
    """Write an encrypted secret to the GitHub repo."""
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Get repo public key for encryption
    key_resp = requests.get(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key",
        headers=headers,
        timeout=10,
    )
    key_resp.raise_for_status()
    key_data = key_resp.json()

    encrypted = encrypt_secret(key_data["key"], secret_value)

    # PUT the new secret
    put_resp = requests.put(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/{secret_name}",
        headers=headers,
        json={
            "encrypted_value": encrypted,
            "key_id": key_data["key_id"],
        },
        timeout=10,
    )

    if put_resp.status_code in (201, 204):
        print(f"[TokenRefresh] GitHub secret '{secret_name}' updated successfully.")
    else:
        print(f"[TokenRefresh] Failed to update secret: {put_resp.status_code} {put_resp.text}")
        raise RuntimeError("GitHub secret update failed")


def main():
    print("[TokenRefresh] Refreshing Facebook access token...")
    new_token = refresh_fb_token(FB_ACCESS_TOKEN)

    if not new_token:
        raise SystemExit("Token refresh failed — manual intervention required.")

    print("[TokenRefresh] Writing new token to GitHub secrets...")
    update_github_secret("FB_ACCESS_TOKEN", new_token)
    print("[TokenRefresh] Done.")


if __name__ == "__main__":
    main()
