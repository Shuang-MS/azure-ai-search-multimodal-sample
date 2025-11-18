import logging
from typing import Optional

import aiohttp
from aiohttp import ClientSession, web

logger = logging.getLogger("speech")


class SpeechTokenService:
    """Lightweight helper to mint Azure Speech Service tokens for the frontend."""

    def __init__(
        self,
        speech_key: Optional[str],
        speech_region: Optional[str],
        default_voice: Optional[str] = None,
    ):
        self._speech_key = speech_key
        self._speech_region = speech_region
        self._default_voice = default_voice

    def _has_credentials(self) -> bool:
        return bool(self._speech_key and self._speech_region)

    def _token_url(self) -> str:
        if not self._speech_region:
            raise ValueError("Speech region is not configured.")
        return f"https://{self._speech_region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"

    async def _fetch_token(self) -> str:
        if not self._has_credentials():
            raise ValueError("Azure Speech credentials are not configured.")

        timeout = aiohttp.ClientTimeout(total=10)
        headers = {"Ocp-Apim-Subscription-Key": self._speech_key}

        async with ClientSession(timeout=timeout) as session:
            async with session.post(self._token_url(), headers=headers) as response:
                if response.status != 200:
                    body = await response.text()
                    raise RuntimeError(
                        f"Failed to fetch Azure Speech token: {response.status} {body}"
                    )
                return await response.text()

    async def handle_token_request(self, _: web.Request) -> web.Response:
        if not self._has_credentials():
            return web.json_response(
                {"error": "Azure Speech is not configured for this app."},
                status=400,
            )

        try:
            token = await self._fetch_token()
        except Exception as exc:
            logger.exception("Unable to fetch Azure Speech token: %s", exc)
            return web.json_response(
                {"error": "Failed to fetch Azure Speech token."},
                status=500,
            )

        payload = {"token": token, "region": self._speech_region}
        if self._default_voice:
            payload["voice"] = self._default_voice

        return web.json_response(payload)
