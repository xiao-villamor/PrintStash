"""Unit coverage for ``makerworld_auth`` — the Bambu login flow.

The Bambu account API calls are patched at ``get_http_client`` so these tests
exercise the two-step login/verify dispatch (token-outright, emailed code, and
authenticator code) without any real network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import makerworld_auth as auth


def _client_returning(*responses):
    """A fake http client whose ``.post`` yields each given response in order."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=list(responses))
    return client


def _resp(status_code=200, json_body=None, cookies=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body if json_body is not None else {})
    resp.cookies = cookies or {}
    return resp


@pytest.mark.asyncio
async def test_login_token_outright() -> None:
    client = _client_returning(_resp(json_body={"accessToken": "JWT123"}))
    with patch.object(auth, "get_http_client", return_value=client):
        result = await auth.begin_login("a@b.com", "pw")
    assert result.status == "ok"
    assert result.token == "JWT123"
    assert result.login_token is None


@pytest.mark.asyncio
async def test_login_then_email_code() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "verifyCode"}),
        _resp(json_body={"accessToken": "JWT-AFTER-CODE"}),
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
        assert begun.status == "need_email_code"
        assert begun.login_token
        done = await auth.submit_code(begun.login_token, "123456")
    assert done.status == "ok"
    assert done.token == "JWT-AFTER-CODE"


@pytest.mark.asyncio
async def test_login_then_tfa_code_from_cookie() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "tfa", "tfaKey": "KEY"}),
        _resp(cookies={"token": "JWT-TFA"}),
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
        assert begun.status == "need_tfa_code"
        done = await auth.submit_code(begun.login_token, "000111")
    assert done.token == "JWT-TFA"


@pytest.mark.asyncio
async def test_invalid_credentials() -> None:
    client = _client_returning(_resp(status_code=401))
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.begin_login("a@b.com", "wrong")
    assert exc.value.code == "invalid_credentials"


@pytest.mark.asyncio
async def test_missing_credentials() -> None:
    with pytest.raises(auth.MakerWorldAuthError) as exc:
        await auth.begin_login("", "")
    assert exc.value.code == "missing_credentials"


@pytest.mark.asyncio
async def test_submit_code_unknown_token() -> None:
    with pytest.raises(auth.MakerWorldAuthError) as exc:
        await auth.submit_code("nope", "123")
    assert exc.value.code == "login_expired"


@pytest.mark.asyncio
async def test_submit_wrong_email_code() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "verifyCode"}),
        _resp(status_code=400),
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.submit_code(begun.login_token, "000000")
    assert exc.value.code == "invalid_code"


@pytest.mark.asyncio
async def test_begin_login_network_error() -> None:
    client = MagicMock()
    client.post = AsyncMock(side_effect=RuntimeError("connection reset"))
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.begin_login("a@b.com", "pw")
    assert exc.value.code == "network_error"


@pytest.mark.asyncio
async def test_begin_login_non_200_maps_to_login_failed() -> None:
    client = _client_returning(_resp(status_code=500))
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.begin_login("a@b.com", "pw")
    assert exc.value.code == "login_failed"


@pytest.mark.asyncio
async def test_begin_login_non_json_response() -> None:
    resp = _resp(status_code=200)
    resp.json = MagicMock(side_effect=ValueError("not json"))
    client = _client_returning(resp)
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.begin_login("a@b.com", "pw")
    assert exc.value.code == "login_failed"


@pytest.mark.asyncio
async def test_begin_login_unrecognized_shape_falls_back_to_email_code() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "somethingNew"})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        result = await auth.begin_login("a@b.com", "pw")
    assert result.status == "need_email_code"
    assert result.login_token


@pytest.mark.asyncio
async def test_submit_code_missing_code() -> None:
    with pytest.raises(auth.MakerWorldAuthError) as exc:
        await auth.submit_code("some-token", "  ")
    assert exc.value.code == "missing_code"


@pytest.mark.asyncio
async def test_submit_code_prunes_expired_pending_logins() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "verifyCode"})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
    # Force the pending entry past its TTL so submit_code's _prune() drops it
    # before the lookup, hitting the expiry-eviction branch.
    auth._pending[begun.login_token].created_at -= auth._PENDING_TTL + 1
    with pytest.raises(auth.MakerWorldAuthError) as exc:
        await auth.submit_code(begun.login_token, "123456")
    assert exc.value.code == "login_expired"


@pytest.mark.asyncio
async def test_submit_email_code_network_error() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "verifyCode"})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
    client.post = AsyncMock(side_effect=RuntimeError("reset"))
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.submit_code(begun.login_token, "123456")
    assert exc.value.code == "network_error"


@pytest.mark.asyncio
async def test_submit_email_code_non_json_response() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "verifyCode"})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
    bad_resp = _resp(status_code=200)
    bad_resp.json = MagicMock(side_effect=ValueError("not json"))
    client.post = AsyncMock(return_value=bad_resp)
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.submit_code(begun.login_token, "123456")
    assert exc.value.code == "invalid_code"


@pytest.mark.asyncio
async def test_submit_email_code_no_token_in_body() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "verifyCode"})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
    client.post = AsyncMock(return_value=_resp(json_body={}))
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.submit_code(begun.login_token, "123456")
    assert exc.value.code == "invalid_code"


@pytest.mark.asyncio
async def test_submit_tfa_network_error() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "tfa", "tfaKey": "KEY"})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
    client.post = AsyncMock(side_effect=RuntimeError("reset"))
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.submit_code(begun.login_token, "123456")
    assert exc.value.code == "network_error"


@pytest.mark.asyncio
async def test_submit_tfa_non_200_status() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "tfa", "tfaKey": "KEY"})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
    client.post = AsyncMock(return_value=_resp(status_code=403))
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.submit_code(begun.login_token, "123456")
    assert exc.value.code == "invalid_code"


@pytest.mark.asyncio
async def test_submit_tfa_falls_back_to_json_body_token() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "tfa", "tfaKey": "KEY"})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
    # No Set-Cookie token; some Bambu variants echo it in the JSON body instead.
    client.post = AsyncMock(
        return_value=_resp(json_body={"accessToken": "JWT-BODY"}, cookies={})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        done = await auth.submit_code(begun.login_token, "123456")
    assert done.token == "JWT-BODY"


@pytest.mark.asyncio
async def test_submit_tfa_non_json_body_with_no_cookie_is_invalid_code() -> None:
    client = _client_returning(
        _resp(json_body={"accessToken": "", "loginType": "tfa", "tfaKey": "KEY"})
    )
    with patch.object(auth, "get_http_client", return_value=client):
        begun = await auth.begin_login("a@b.com", "pw")
    bad_resp = _resp(status_code=200, cookies={})
    bad_resp.json = MagicMock(side_effect=ValueError("not json"))
    client.post = AsyncMock(return_value=bad_resp)
    with patch.object(auth, "get_http_client", return_value=client):
        with pytest.raises(auth.MakerWorldAuthError) as exc:
            await auth.submit_code(begun.login_token, "123456")
    assert exc.value.code == "invalid_code"
