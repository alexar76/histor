"""Public protocol readers reject an oversized chunked body before buffering it."""
import asyncio
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from histor.app import read_bounded_body


def test_chunked_body_stops_at_limit_without_reading_the_tail():
    calls = 0
    async def receive():
        nonlocal calls
        calls += 1
        assert calls <= 3, 'the oversized request must not be drained into memory'
        return {'type': 'http.request', 'body': b'x' * 8, 'more_body': True}
    request = Request({'type': 'http', 'headers': []}, receive)
    with pytest.raises(HTTPException) as error:
        asyncio.run(read_bounded_body(request, 16))
    assert error.value.status_code == 413
    assert calls == 3


def test_body_exactly_at_limit_is_accepted():
    chunks = iter([{'type': 'http.request', 'body': b'abc', 'more_body': True},
                   {'type': 'http.request', 'body': b'def', 'more_body': False}])
    async def receive():
        return next(chunks)
    assert asyncio.run(read_bounded_body(Request({'type': 'http', 'headers': []}, receive), 6)) == b'abcdef'
