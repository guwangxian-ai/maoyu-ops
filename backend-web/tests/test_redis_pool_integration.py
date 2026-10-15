"""Real Redis regressions; run with XY_TEST_REDIS_SOCKET pointing to a test server."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import redis.asyncio as redis
from redis.asyncio.retry import Retry
from redis.asyncio.connection import UnixDomainSocketConnection
from redis.backoff import NoBackoff
from redis.exceptions import TimeoutError as RedisTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common.db import redis_client as service
from common.db.redis_pool import HealthyRedisConnectionPool


class LostResponseConnection(UnixDomainSocketConnection):
    lose_value = None
    loss_count = 0

    async def read_response(self, *args, **kwargs):
        response = await super().read_response(*args, **kwargs)
        if self.lose_value is not None and response == self.lose_value:
            type(self).lose_value = None
            type(self).loss_count += 1
            raise RedisTimeoutError("test: response lost after Redis executed command")
        return response


@unittest.skipUnless(os.environ.get("XY_TEST_REDIS_SOCKET"), "requires isolated test Redis socket")
class RealRedisPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.prefix = "xianyu:upstream-test:" + uuid4().hex
        self.created_keys: set[str] = set()
        self.pool = HealthyRedisConnectionPool.from_url(
            "unix://" + os.environ["XY_TEST_REDIS_SOCKET"] + "?db=15",
            max_connections=32, decode_responses=True, protocol=2,
            retry=Retry(NoBackoff(), 3), socket_timeout=2,
        )
        self.client = redis.Redis(connection_pool=self.pool)
        self.assertTrue(await self.client.ping())

    async def asyncTearDown(self):
        if self.created_keys:
            await self.client.delete(*self.created_keys)
        await self.client.aclose(close_connection_pool=False)
        await self.pool.aclose()

    async def test_closed_connection_is_discarded_and_next_command_reconnects(self):
        connection = await self.pool.get_connection()
        await connection.disconnect(nowait=True)
        await self.pool.release(connection)
        self.assertFalse(self.pool._in_use_connections)
        self.assertTrue(await self.client.ping())

    async def test_concurrent_locks_have_one_owner_and_preserve_other_owner(self):
        locks = [service.DistributedLock(self.prefix + ":lock", expire=30) for _ in range(20)]
        self.created_keys.update(lock.lock_key for lock in locks)
        outcomes = await asyncio.gather(*(lock._try_acquire(self.client) for lock in locks))
        self.assertEqual(sum(outcomes), 1)
        winner = locks[outcomes.index(True)]
        self.assertEqual(await self.client.get(winner.lock_key), winner.lock_value)
        loser = locks[outcomes.index(False)]
        # A stale holder must not delete the current owner's lock.
        loser._locked = True
        with patch.object(service, "get_redis_client", AsyncMock(return_value=self.client)):
            self.assertFalse(await loser.release())
            self.assertEqual(await self.client.get(winner.lock_key), winner.lock_value)
            self.assertTrue(await winner.release())
        self.assertIsNone(await self.client.get(winner.lock_key))

    async def test_lost_response_recovers_own_lock_without_overwriting_another(self):
        pool = HealthyRedisConnectionPool(
            path=os.environ["XY_TEST_REDIS_SOCKET"], db=15,
            connection_class=LostResponseConnection, max_connections=2,
            decode_responses=True, protocol=2, retry=Retry(NoBackoff(), 3),
        )
        client = redis.Redis(connection_pool=pool)
        LostResponseConnection.lose_value = None
        LostResponseConnection.loss_count = 0
        try:
            self.assertTrue(await client.ping())
            # A real SET NX control reproduces the ambiguous lost-response case.
            key = self.prefix + ":control"
            self.created_keys.add(key)
            LostResponseConnection.lose_value = "OK"
            self.assertFalse(await client.set(key, "own-token", nx=True, ex=30))
            self.assertEqual(await client.get(key), "own-token")
            self.assertEqual(LostResponseConnection.loss_count, 1)

            lock = service.DistributedLock(self.prefix + ":idempotent", expire=30)
            self.created_keys.add(lock.lock_key)
            LostResponseConnection.lose_value = 1
            self.assertTrue(await lock._try_acquire(client))
            self.assertEqual(LostResponseConnection.loss_count, 2)
            self.assertEqual(await client.get(lock.lock_key), lock.lock_value)
            self.assertGreater(await client.ttl(lock.lock_key), 0)

            other = service.DistributedLock(self.prefix + ":other", expire=30)
            self.created_keys.add(other.lock_key)
            await client.set(other.lock_key, "different-owner", ex=30)
            self.assertFalse(await other._try_acquire(client))
            self.assertEqual(await client.get(other.lock_key), "different-owner")
        finally:
            LostResponseConnection.lose_value = None
            await client.aclose(close_connection_pool=False)
            await pool.aclose()

    async def test_cancelled_blocking_command_restores_pool_capacity(self):
        self.pool.max_connections = 1
        command = asyncio.create_task(self.client.blpop(self.prefix + ":empty", timeout=30))
        for _ in range(100):
            if self.pool._in_use_connections:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.pool._in_use_connections)
        command.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await command
        self.assertFalse(self.pool._in_use_connections)
        self.assertTrue(await asyncio.wait_for(self.client.ping(), timeout=2))


if __name__ == "__main__":
    unittest.main()
