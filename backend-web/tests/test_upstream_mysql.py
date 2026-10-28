"""MySQL migration regression; requires an explicitly named disposable database."""
import os
import unittest
from sqlalchemy import text
from common.db.init_database import DatabaseInitializer
from common.db.session import async_engine, async_session_maker
from common.models.user_login_log import XYUserLoginLog
from common.models.email_log import XYEmailLog
from common.models.data_dictionary import XYDataDictionary


class UpstreamMysqlTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_ddl_is_repeatable_and_preserves_existing_data(self):
        self.assertEqual(os.environ.get('MYSQL_DATABASE'), 'maoyu_upstream_test')
        tables = ['xy_user_login_logs', 'xy_email_logs', 'xy_data_dictionaries']
        async with async_engine.begin() as conn:
            for name in tables:
                await conn.execute(text(DatabaseInitializer.TABLES_DDL[name]))
        init = DatabaseInitializer()
        await init.init_data_dictionaries()
        async with async_session_maker() as db:
            db.add(XYUserLoginLog(login_account='fixture', login_method='username_password', success=True))
            db.add(XYEmailLog(recipient_email='fixture@example.invalid', trigger_operation='test_email', success=False))
            db.add(XYDataDictionary(dict_type='fixture', dict_value='keep', dict_label='保留自定义项', sort_order=1, enabled=True))
            await db.commit()
        async with async_engine.begin() as conn:
            for name in tables:
                await conn.execute(text(DatabaseInitializer.TABLES_DDL[name]))
        await init.init_data_dictionaries()
        async with async_engine.connect() as conn:
            for name in tables[:2]:
                self.assertEqual((await conn.execute(text('SELECT COUNT(*) FROM ' + name))).scalar_one(), 1)
                indexes = (await conn.execute(text('SHOW INDEX FROM ' + name))).all()
                self.assertGreaterEqual(len(indexes), 4)
            self.assertEqual((await conn.execute(text('SELECT COUNT(*) FROM xy_data_dictionaries'))).scalar_one(), 9)
            self.assertEqual((await conn.execute(text("SELECT dict_label FROM xy_data_dictionaries WHERE dict_type='fixture'"))).scalar_one(), '保留自定义项')
        await async_engine.dispose()
