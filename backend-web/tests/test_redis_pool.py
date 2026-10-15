"""Regression checks for the upstream Redis pool and service lifecycle."""
from __future__ import annotations

import ast
import asyncio
import socket
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.db import redis_client as service
from common.db.redis_pool import HealthyRedisConnectionPool


class FakeConnection:
    def __init__(self, **kwargs):
        self.closing = False
        self.disconnect = AsyncMock(side_effect=self._disconnect)
        self.re_auth = AsyncMock()
        self._writer = SimpleNamespace(
            is_closing=lambda: self.closing,
            transport=SimpleNamespace(is_closing=lambda: self.closing),
        )

    async def _disconnect(self, **kwargs):
        self.closing = True


class RedisPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_health_check_discards_reserved_connection(self):
        pool = HealthyRedisConnectionPool(connection_class=FakeConnection, max_connections=1)
        pool.ensure_connection = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await pool.get_connection()
        self.assertEqual(len(pool._in_use_connections), 0)
        self.assertEqual(len(pool._available_connections), 0)
        # Discarding a failed checkout must restore the pool's available capacity.
        pool.ensure_connection = AsyncMock()
        connection = await pool.get_connection()
        self.assertIsInstance(connection, FakeConnection)
        await pool.release(connection)
        await pool.aclose()

    async def test_closed_connection_is_not_returned_to_idle_pool(self):
        pool = HealthyRedisConnectionPool(connection_class=FakeConnection, max_connections=1)
        pool.ensure_connection = AsyncMock()
        connection = await pool.get_connection()
        connection.closing = True
        await pool.release(connection)
        connection.disconnect.assert_awaited_once()
        self.assertFalse(pool._available_connections)
        self.assertFalse(pool._in_use_connections)

    async def test_one_slow_health_check_does_not_block_other_checkouts(self):
        pool = HealthyRedisConnectionPool(connection_class=FakeConnection, max_connections=2)
        entered, proceed = asyncio.Event(), asyncio.Event()
        calls = 0

        async def ensure(connection):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await proceed.wait()

        pool.ensure_connection = ensure
        first = asyncio.create_task(pool.get_connection())
        await entered.wait()
        try:
            second = await asyncio.wait_for(pool.get_connection(), timeout=1)
        finally:
            proceed.set()
        first_connection = await first
        self.assertIsNot(first_connection, second)
        await pool.release(first_connection)
        await pool.release(second)
        await pool.aclose()


class RedisInitializationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = patch.multiple(service, redis_pool=None, redis_client=None, _redis_init_lock=asyncio.Lock())
        self.state.start()
        self.addCleanup(self.state.stop)
        self.pool = SimpleNamespace(aclose=AsyncMock())
        self.client = SimpleNamespace(ping=AsyncMock(), aclose=AsyncMock())
        self.addCleanup(patch.stopall)

    async def test_cancelled_ping_closes_unpublished_resources(self):
        self.client.ping.side_effect = asyncio.CancelledError
        with patch.object(service, "_create_redis_pool", return_value=self.pool), patch.object(service.redis, "Redis", return_value=self.client):
            with self.assertRaises(asyncio.CancelledError):
                await service.initialize_redis_client()
        self.client.aclose.assert_awaited_once_with(close_connection_pool=False)
        self.pool.aclose.assert_awaited_once()
        self.assertIsNone(service.redis_client)

    async def test_close_during_initialization_leaves_no_published_client(self):
        started, proceed = asyncio.Event(), asyncio.Event()

        async def ping():
            started.set()
            await proceed.wait()

        self.client.ping.side_effect = ping
        with patch.object(service, "_create_redis_pool", return_value=self.pool), patch.object(service.redis, "Redis", return_value=self.client):
            initialization = asyncio.create_task(service.initialize_redis_client())
            await started.wait()
            closing = asyncio.create_task(service.close_redis_client())
            await asyncio.sleep(0)
            proceed.set()
            await asyncio.gather(initialization, closing)
        self.assertIsNone(service.redis_client)
        self.assertIsNone(service.redis_pool)
        self.pool.aclose.assert_awaited_once()

    async def test_concurrent_initialization_publishes_one_verified_client(self):
        with patch.object(service, "_create_redis_pool", return_value=self.pool) as create, patch.object(service.redis, "Redis", return_value=self.client):
            clients = await asyncio.gather(*(service.initialize_redis_client() for _ in range(20)))
        create.assert_called_once()
        self.client.ping.assert_awaited_once()
        self.assertTrue(all(client is self.client for client in clients))
        await service.close_redis_client()

    def test_macos_keepalive_idle_uses_tcp_keepalive_constant(self):
        settings = SimpleNamespace(
            redis_url="redis://localhost/15", redis_pool_size=800,
            redis_socket_timeout=5, redis_socket_connect_timeout=3,
            redis_socket_keepalive=True, redis_socket_keepalive_idle=60,
            redis_socket_keepalive_interval=10, redis_socket_keepalive_count=3,
            redis_health_check_interval=30, redis_protocol=2,
        )
        with patch.object(service, "settings", settings), patch.object(socket, "TCP_KEEPIDLE", None, create=True), patch.object(socket, "TCP_KEEPALIVE", 16, create=True), patch.object(HealthyRedisConnectionPool, "from_url") as create:
            service._create_redis_pool()
        self.assertEqual(create.call_args.kwargs["socket_keepalive_options"].get(16), 60)


class ServiceLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_redis_closes_when_service_startup_or_shutdown_fails(self):
        for folder in ("backend-web", "websocket", "scheduler"):
            source = ast.parse((ROOT / folder / "_bootstrap.py").read_text())
            lifespan = next(node for node in source.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan")
            for phase in ("startup", "shutdown"):
                with self.subTest(service=folder, phase=phase):
                    close = AsyncMock()

                    @asynccontextmanager
                    async def application_lifespan(app):
                        if phase == "startup":
                            raise RuntimeError("test startup failure")
                        yield
                        raise RuntimeError("test shutdown failure")

                    namespace = {"asynccontextmanager": asynccontextmanager, "FastAPI": object, "_application_lifespan": application_lifespan, "close_redis_client": close}
                    @asynccontextmanager
                    async def mcp_lifespan():
                        yield

                    drain = AsyncMock()
                    app = SimpleNamespace(state=SimpleNamespace(mcp_gateway=SimpleNamespace(
                        manager=SimpleNamespace(run=mcp_lifespan),
                        close=drain, bridge=SimpleNamespace(close=drain))))
                    module = ast.Module(body=[lifespan], type_ignores=[])
                    exec(compile(module, filename=folder + "/_bootstrap.py", mode="exec"), namespace)
                    with self.assertRaises(RuntimeError):
                        async with namespace["lifespan"](app):
                            pass
                    close.assert_awaited_once()
                    if folder == "backend-web" and phase == "shutdown":
                        drain.assert_awaited_once()
                    else:
                        drain.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
