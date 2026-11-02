import httpx

BASE_URL = "https://paypal.test"


def token_response(token: str = "tok-1", expires_in: int = 32400) -> httpx.Response:  # noqa: S107
    return httpx.Response(
        200, json={"access_token": token, "token_type": "Bearer", "expires_in": expires_in}
    )


async def no_sleep(_: float) -> None:
    return None
