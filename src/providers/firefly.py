"""Adobe Firefly image generation strategy (FR-4 strategy pattern).

Authenticates via Adobe's server-to-server (S2S) OAuth flow: the
client_id/client_secret pair is exchanged at Adobe IMS for a short-lived
access token, which then authorizes calls to the Firefly Image API.
Credential values are never printed or logged.
"""

import os

import requests
from rich.console import Console

from src.providers.base import ImageGenerationProvider, ProviderGenerationError

console = Console()

IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
FIREFLY_GENERATE_URL = "https://firefly-api.adobe.io/v3/images/generate"
S2S_SCOPES = "openid,AdobeID,session,additional_info,read_organizations,firefly_api,ff_apis"


class FireflyProvider(ImageGenerationProvider):
    """Live GenAI backend via the Adobe Firefly Services REST API."""

    def __init__(self, credentials: dict | None = None, timeout: int = 60):
        super().__init__(credentials)
        self.timeout = timeout
        self.client_id = self.credentials.get("client_id") or os.environ.get(
            "FIREFLY_CLIENT_ID"
        )
        self.client_secret = self.credentials.get("client_secret") or os.environ.get(
            "FIREFLY_CLIENT_SECRET"
        )
        if not (self.client_id and self.client_secret):
            raise ProviderGenerationError(
                "Adobe Firefly credentials missing: provide client_id and "
                "client_secret via BYOK or FIREFLY_CLIENT_ID/FIREFLY_CLIENT_SECRET."
            )

    @property
    def provider_name(self) -> str:
        return "adobe-firefly"

    def _get_access_token(self) -> str:
        """Adobe S2S OAuth: exchange client credentials for an access token."""
        try:
            response = requests.post(
                IMS_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": S2S_SCOPES,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            token = response.json().get("access_token")
        except requests.RequestException as exc:
            console.print(
                f"[bold red]✗ Adobe IMS token exchange failed:[/bold red] {exc}"
            )
            raise ProviderGenerationError(
                f"Adobe IMS token exchange failed: {exc}"
            ) from exc
        if not token:
            raise ProviderGenerationError(
                "Adobe IMS token exchange returned no access_token."
            )
        return token

    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        token = self._get_access_token()
        try:
            response = requests.post(
                FIREFLY_GENERATE_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-api-key": self.client_id,
                    "Content-Type": "application/json",
                },
                json={
                    "prompt": prompt,
                    "size": {"width": width, "height": height},
                    "numVariations": 1,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            console.print(f"[bold red]✗ Firefly generation failed:[/bold red] {exc}")
            raise ProviderGenerationError(f"Firefly generation failed: {exc}") from exc

        outputs = payload.get("outputs") or []
        image_url = outputs[0].get("image", {}).get("url") if outputs else None
        if not image_url:
            raise ProviderGenerationError(
                f"Unexpected Firefly response shape: {list(payload.keys())}"
            )

        try:
            image_response = requests.get(image_url, timeout=self.timeout)
            image_response.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderGenerationError(
                f"Failed to download Firefly output image: {exc}"
            ) from exc
        return image_response.content
