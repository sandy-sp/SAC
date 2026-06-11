"""AWS Bedrock image generation strategy (FR-4.2 from docs/SPEC.md).

Concrete ImageGenerationProvider backed by Amazon Bedrock. Three payload
families are supported, selected automatically from the model_id:

- Stability next-gen (stable-image-core / sd3-5 / stable-image-ultra) —
  the default, as the only family with ACTIVE lifecycle status on
  Bedrock as of mid-2026 (Titan v1/v2, Nova Canvas, and SDXL are
  EOL/legacy-restricted).
- Stability classic SDXL (stable-diffusion-xl).
- Amazon Titan / Nova Canvas (TEXT_IMAGE task payload).
"""

import base64
import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from rich.console import Console

from src.providers.base import ImageGenerationProvider, ProviderGenerationError

console = Console()

DEFAULT_MODEL_ID = "stability.stable-image-core-v1:1"
DEFAULT_REGION = "us-west-2"  # Stability image models live here

# Aspect ratios accepted by Stability next-gen models; requests are
# snapped to the closest one (downstream ImageProcessor center-crops to
# exact campaign ratios anyway).
_STABILITY_ASPECTS = ["1:1", "16:9", "9:16", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21"]

# Titan only accepts specific output resolutions; requests are snapped to
# the closest supported size (the downstream ImageProcessor center-crops
# to the exact campaign ratios anyway, so this is lossless in practice).
_TITAN_SIZES: list[tuple[int, int]] = [
    (1024, 1024), (768, 768), (512, 512),
    (768, 1152), (1152, 768),
    (768, 1280), (1280, 768),
    (896, 1152), (1152, 896),
    (768, 1408), (1408, 768),
    (640, 1408), (1408, 640),
]


class BedrockGenerationError(ProviderGenerationError):
    """Raised when Bedrock invocation fails (credentials, access, throttling…)."""


def _nearest_stability_aspect(width: int, height: int) -> str:
    requested = width / height
    return min(
        _STABILITY_ASPECTS,
        key=lambda ratio: abs(
            (lambda w, h: w / h)(*map(int, ratio.split(":"))) - requested
        ),
    )


def _nearest_titan_size(width: int, height: int) -> tuple[int, int]:
    requested_aspect = width / height
    return min(
        _TITAN_SIZES,
        key=lambda size: (
            abs(size[0] / size[1] - requested_aspect),
            abs(size[0] * size[1] - width * height),
        ),
    )


class AwsBedrockProvider(ImageGenerationProvider):
    """Live GenAI backend via Amazon Bedrock (boto3 runtime client)."""

    def __init__(
        self,
        model_id: str | None = None,
        region_name: str | None = None,
        credentials: dict | None = None,
    ):
        super().__init__(credentials)
        self.model_id = model_id or os.environ.get(
            "SAC_BEDROCK_MODEL_ID", DEFAULT_MODEL_ID
        )

        client_kwargs: dict = {
            "region_name": region_name
            or os.environ.get("SAC_BEDROCK_REGION")
            or os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION),
        }

        # BYOK: explicit credentials take precedence; otherwise boto3's
        # default chain (env vars, ~/.aws, instance profile) applies.
        # Values are passed straight through — never printed or logged.
        access_key = self.credentials.get("aws_access_key_id")
        secret_key = self.credentials.get("aws_secret_access_key")
        session_token = self.credentials.get("aws_session_token")
        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
            if session_token:
                client_kwargs["aws_session_token"] = session_token

        self._client = boto3.client("bedrock-runtime", **client_kwargs)

    @property
    def provider_name(self) -> str:
        return f"aws-bedrock ({self.model_id})"

    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        payload = self._build_payload(prompt, width, height)
        try:
            response = self._client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(payload),
                contentType="application/json",
                accept="application/json",
            )
            body = json.loads(response["body"].read())
        except NoCredentialsError as exc:
            console.print(
                "[bold red]✗ AWS Bedrock: no credentials found.[/bold red] "
                "Configure .env (see .env.example) or an AWS profile."
            )
            raise BedrockGenerationError(f"AWS credentials missing: {exc}") from exc
        except ClientError as exc:
            error = exc.response.get("Error", {})
            console.print(
                f"[bold red]✗ AWS Bedrock invocation failed[/bold red] "
                f"([yellow]{error.get('Code', 'Unknown')}[/yellow]): "
                f"{error.get('Message', str(exc))}"
            )
            raise BedrockGenerationError(
                f"Bedrock invoke_model failed for {self.model_id}: "
                f"{error.get('Code', 'Unknown')} — {error.get('Message', str(exc))}"
            ) from exc
        except BotoCoreError as exc:
            console.print(f"[bold red]✗ AWS SDK error:[/bold red] {exc}")
            raise BedrockGenerationError(f"AWS SDK error: {exc}") from exc

        return self._extract_image(body)

    # -- payload formatting per model family --------------------------------

    def _is_classic_sdxl(self) -> bool:
        return "stable-diffusion-xl" in self.model_id

    def _is_stability_nextgen(self) -> bool:
        return self.model_id.startswith("stability.") and not self._is_classic_sdxl()

    def _build_payload(self, prompt: str, width: int, height: int) -> dict:
        if self._is_stability_nextgen():
            return {
                "prompt": prompt,
                "mode": "text-to-image",
                "aspect_ratio": _nearest_stability_aspect(width, height),
                "output_format": "png",
            }
        if self._is_classic_sdxl():
            return {
                "text_prompts": [{"text": prompt}],
                "cfg_scale": 7,
                "steps": 30,
                "width": width,
                "height": height,
            }
        # Amazon Titan family (default)
        titan_width, titan_height = _nearest_titan_size(width, height)
        return {
            "taskType": "TEXT_IMAGE",
            "textToImageParams": {"text": prompt},
            "imageGenerationConfig": {
                "numberOfImages": 1,
                "width": titan_width,
                "height": titan_height,
                "cfgScale": 8.0,
            },
        }

    def _extract_image(self, body: dict) -> bytes:
        if self._is_classic_sdxl():
            artifacts = body.get("artifacts") or []
            if not artifacts or "base64" not in artifacts[0]:
                raise BedrockGenerationError(
                    f"Unexpected SDXL response shape from {self.model_id}: "
                    f"{list(body.keys())}"
                )
            return base64.b64decode(artifacts[0]["base64"])

        # Stability next-gen and Titan/Nova both return {"images": [b64, …]}
        images = body.get("images") or []
        if not images:
            raise BedrockGenerationError(
                f"Unexpected response shape from {self.model_id}: "
                f"{list(body.keys())} (error: {body.get('error')})"
            )
        return base64.b64decode(images[0])
