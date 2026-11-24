import asyncio
import httpx
import pytest
import crawler


def test_disconnected_download_retries_and_recovers(monkeypatch):
    calls=[]
    async def fetch_once(client,url,seed):
        calls.append(url)
        if len(calls)<3:raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return url,"application/pdf",b"pdf"
    async def sleep(delay):pass
    monkeypatch.setattr(crawler,"_fetch_once",fetch_once)
    monkeypatch.setattr(crawler.asyncio,"sleep",sleep)
    result=asyncio.run(crawler.fetch(None,"https://example.com/prices.pdf","https://example.com"))
    assert len(calls)==3 and result[2]==b"pdf"


def test_persistent_disconnect_stops_after_three_attempts(monkeypatch):
    calls=[]
    async def fetch_once(client,url,seed):
        calls.append(url)
        raise httpx.RemoteProtocolError("Disconnected")
    async def sleep(delay):pass
    monkeypatch.setattr(crawler,"_fetch_once",fetch_once)
    monkeypatch.setattr(crawler.asyncio,"sleep",sleep)
    with pytest.raises(httpx.RemoteProtocolError):
        asyncio.run(crawler.fetch(None,"https://example.com/prices.pdf","https://example.com"))
    assert len(calls)==3
