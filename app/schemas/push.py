from pydantic import BaseModel, Field, field_validator

from app.services.push import is_allowed_endpoint


class PushConfigResponse(BaseModel):
    enabled: bool
    # VAPID public key (base64url) the browser subscribes with; None when push is off
    public_key: str | None


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class PushSubscribeRequest(BaseModel):
    """The JSON a browser produces with PushSubscription.toJSON()."""

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def _known_push_service(cls, value: str) -> str:
        # The server POSTs to this URL, so only real push services are accepted (no SSRF).
        if not is_allowed_endpoint(value):
            raise ValueError("Unsupported push service.")
        return value


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)


class PushTestResponse(BaseModel):
    devices: int
    delivered: int
