"""
定时擦亮任务服务

功能：
1. 定时检查开启了商品自动擦亮的账号
2. 查询该账号下未擦亮的商品
3. 调用闲鱼API执行商品擦亮
4. 更新商品擦亮状态
5. 记录执行日志
"""
import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import aiohttp
from loguru import logger
from sqlalchemy import delete as sql_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.db.session import async_session_maker
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.models.scheduled_polish_log import ScheduledPolishLog
from common.utils.xianyu_utils import trans_cookies, generate_sign
from common.utils.cookie_refresh import update_account_cookies_in_db
from common.utils.time_utils import get_beijing_now_naive


class PolishTaskService:
    """定时擦亮任务服务"""

    # 擦亮日志保留天数，超过该天数的日志在每次任务执行时主动清理
    LOG_RETENTION_DAYS = 10

    def __init__(self):
        self.task_name = "定时擦亮"

    async def execute(self):
        """执行定时擦亮任务"""
        logger.info(f"【{self.task_name}】开始执行")
        start_time = datetime.now()
        
        # 生成批次ID
        batch_id = str(uuid.uuid4())

        try:
            async with async_session_maker() as session:
                # 主动清理过期的擦亮日志（10天前）
                await self._cleanup_expired_logs(session)

                # 1. 查询开启了商品自动擦亮的账号
                accounts = await self._get_enabled_accounts(session)
                
                if not accounts:
                    logger.info(f"【{self.task_name}】没有开启商品自动擦亮的账号")
                    return

                logger.info(f"【{self.task_name}】找到 {len(accounts)} 个开启商品自动擦亮的账号")

                # 2. 遍历账号，处理每个账号的商品
                total_success = 0
                total_failed = 0
                
                for account in accounts:
                    # 检查账号是否处于Session过期冷却期内
                    from common.utils.cookie_refresh import is_account_session_cooled
                    if is_account_session_cooled(account.account_id):
                        logger.info(
                            f"【{self.task_name}】账号 {account.account_id} "
                            f"处于Session过期冷却期内，跳过"
                        )
                        continue
                    
                    try:
                        success, failed = await self._process_account(session, account, batch_id)
                        total_success += success
                        total_failed += failed
                    except Exception as e:
                        logger.error(f"【{self.task_name}】处理账号 {account.account_id} 失败: {e}")
                        total_failed += 1

                # 3. 记录执行结果
                elapsed = (datetime.now() - start_time).total_seconds()
                logger.info(
                    f"【{self.task_name}】执行完成，批次ID: {batch_id}, "
                    f"成功: {total_success}, 失败: {total_failed}, "
                    f"耗时: {elapsed:.2f}秒"
                )

        except Exception as e:
            logger.error(f"【{self.task_name}】执行失败: {e}")
            raise

    async def _cleanup_expired_logs(self, session: AsyncSession) -> None:
        """
        主动清理过期的擦亮日志

        删除 created_at 早于 (当前北京时间 - LOG_RETENTION_DAYS 天) 的日志记录，
        避免日志表无限增长。使用参数化的 ORM delete 语句，避免 SQL 注入。
        """
        try:
            cutoff_time = get_beijing_now_naive() - timedelta(days=self.LOG_RETENTION_DAYS)
            stmt = sql_delete(ScheduledPolishLog).where(
                ScheduledPolishLog.created_at < cutoff_time
            )
            result = await session.execute(stmt)
            await session.commit()

            deleted_count = result.rowcount or 0
            if deleted_count > 0:
                logger.info(
                    f"【{self.task_name}】已清理 {deleted_count} 条 {self.LOG_RETENTION_DAYS} 天前的擦亮日志"
                    f"（清理时间界限: {cutoff_time}）"
                )
        except Exception as e:
            logger.error(f"【{self.task_name}】清理过期日志失败: {e}")
            await session.rollback()

    async def _get_enabled_accounts(self, session: AsyncSession) -> List[XYAccount]:
        """
        获取开启了商品自动擦亮的账号
        
        条件：
        - status = 'active'（启用）
        - auto_polish = True（商品自动擦亮开启）
        """
        stmt = select(XYAccount).where(
            XYAccount.status == "active",
            XYAccount.auto_polish == True,
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _process_account(self, session: AsyncSession, account: XYAccount, batch_id: str) -> tuple[int, int]:
        """
        处理单个账号的商品擦亮
        
        Args:
            session: 数据库会话
            account: 账号对象
            batch_id: 批次ID
        
        Returns:
            (成功数量, 失败数量)
        """
        logger.info(f"【{self.task_name}】开始处理账号: {account.account_id}")

        try:
            # 1. 查询该账号下未擦亮的商品
            items = await self._get_unpolished_items(session, account.id)
            
            if not items:
                logger.info(f"【{self.task_name}】账号 {account.account_id} 没有需要擦亮的商品")
                return 0, 0

            logger.info(f"【{self.task_name}】账号 {account.account_id} 找到 {len(items)} 个需要擦亮的商品")

            # 2. 遍历商品，执行擦亮
            success_count = 0
            failed_count = 0

            # 使用可变的cookie_str，令牌过期刷新后后续商品能用新cookie
            current_cookie_str = account.cookie
            
            for item in items:
                try:
                    # 上一件商品处理期间可能发生完整同步或人工修改，不能继续使用旧快照。
                    await session.refresh(item)
                    snapshot = (item.metadata_json or {}).get("sale_snapshot") or {}
                    if snapshot.get("status") != "on_sale" or item.is_polished:
                        continue
                    sync_id = snapshot.get("sync_id")
                    # 执行擦亮
                    result = await self._polish_item(current_cookie_str, item.item_id)
                    
                    # 如果返回了更新后的cookie，写入数据库并用于后续商品
                    if result.get("cookie_str") and result["cookie_str"] != current_cookie_str:
                        current_cookie_str = result["cookie_str"]
                        await update_account_cookies_in_db(account.account_id, current_cookie_str)
                        logger.info(f"【{self.task_name}】账号 {account.account_id} Cookie已通过Set-Cookie更新并写入数据库")
                    
                    # 判断是否成功（包括"一天只能擦亮一次"的情况）
                    is_success = result.get("success")
                    error_msg = result.get("message", "")
                    
                    # 如果返回"一天只能擦亮一次"，也视为成功
                    if not is_success and ("一天只能擦亮一次" in error_msg or "POLISH_DUPLICATE" in error_msg):
                        is_success = True
                        logger.info(f"【{self.task_name}】账号 {account.account_id} 商品 {item.item_id} 今天已擦亮过，视为成功")

                    unsupported = 'UNSUPPORTED_ITEM_STATUS' in error_msg or '已下架商品不支持该操作' in error_msg
                    await self._record_polish_result(session, item, sync_id, bool(is_success), unsupported)
                    
                    if is_success:
                        # 擦亮成功，更新商品状态
                        item.is_polished = True
                        session.add(item)
                        success_count += 1
                        logger.info(f"【{self.task_name}】账号 {account.account_id} 商品 {item.item_id} 擦亮成功")
                        
                        # 记录成功日志
                        await self._log_execution(
                            session=session,
                            batch_id=batch_id,
                            account_id=account.account_id,
                            item_id=item.item_id,
                            success=True,
                            error_message=None
                        )
                    else:
                        failed_count += 1
                        logger.warning(f"【{self.task_name}】账号 {account.account_id} 商品 {item.item_id} 擦亮失败: {error_msg}")
                        
                        # 缺少令牌或Session过期时，标记账号冷却并触发后台异步密码登录，跳过该账号剩余商品
                        if 'SESSION_EXPIRED' in error_msg or 'Cookie中没有找到_m_h5_tk' in error_msg or 'TOKEN_EMPTY' in error_msg or '令牌为空' in error_msg or '已掉线' in error_msg or '请重新登录' in error_msg:
                            from common.utils.cookie_refresh import (
                                mark_account_session_expired, trigger_password_login_async
                            )
                            mark_account_session_expired(account.account_id)
                            trigger_password_login_async(account.account_id)
                            logger.warning(
                                f"【{self.task_name}】账号 {account.account_id} 登录态异常，"
                                f"已标记冷却并触发后台密码登录，跳过剩余商品: {error_msg}"
                            )
                            # 记录失败日志后跳出循环
                            await self._log_execution(
                                session=session,
                                batch_id=batch_id,
                                account_id=account.account_id,
                                item_id=item.item_id,
                                success=False,
                                error_message=error_msg
                            )
                            break
                        
                        # 平台拒绝擦亮不能证明具体下架原因，更不能删除商品及其运营配置。
                        if unsupported:
                            logger.info(
                                f"【{self.task_name}】账号 {account.account_id} 商品 {item.item_id} "
                                f"状态不支持擦亮，已保留记录并记录待核实证据"
                            )
                        
                        # 记录失败日志
                        await self._log_execution(
                            session=session,
                            batch_id=batch_id,
                            account_id=account.account_id,
                            item_id=item.item_id,
                            success=False,
                            error_message=error_msg
                        )
                    
                    # 避免请求过快
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    failed_count += 1
                    error_msg = str(e)
                    logger.error(f"【{self.task_name}】账号 {account.account_id} 商品 {item.item_id} 擦亮异常: {error_msg}")
                    
                    # 记录异常日志
                    await self._log_execution(
                        session=session,
                        batch_id=batch_id,
                        account_id=account.account_id,
                        item_id=item.item_id,
                        success=False,
                        error_message=error_msg
                    )

            # 3. 提交数据库更新
            await session.commit()
            
            logger.info(
                f"【{self.task_name}】账号 {account.account_id} 处理完成，"
                f"成功: {success_count}, 失败: {failed_count}"
            )
            
            return success_count, failed_count

        except Exception as e:
            logger.error(f"【{self.task_name}】处理账号 {account.account_id} 失败: {e}")
            raise

    async def _get_unpolished_items(self, session: AsyncSession, account_pk: int) -> List[XYCatalogItem]:
        """
        获取账号下未擦亮的商品
        
        条件：
        - account_pk = 指定账号ID
        - 最近在售快照为 on_sale（未检查和待核实的历史记录不参与）
        - is_polished = False 或 NULL（未擦亮）
        """
        stmt = select(XYCatalogItem).where(
            XYCatalogItem.account_pk == account_pk,
            XYCatalogItem.metadata_json["sale_snapshot"]["status"].as_string() == "on_sale",
            (XYCatalogItem.is_polished == False) | (XYCatalogItem.is_polished == None),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _record_polish_result(
        self, session: AsyncSession, item: XYCatalogItem, sync_id: Optional[str],
        success: bool, unsupported: bool,
    ) -> None:
        # 刷新并锁定当前行，避免擦亮网络请求期间的同步/配置被旧 JSON 覆盖。
        row = (await session.execute(
            select(XYCatalogItem).where(
                XYCatalogItem.id == item.id,
                XYCatalogItem.account_pk == item.account_pk,
                XYCatalogItem.owner_id == item.owner_id,
            ).with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if row is None:
            return
        metadata = dict(row.metadata_json or {})
        snapshot = dict(metadata.get("sale_snapshot") or {})
        metadata["polish_check"] = {
            "status": "success" if success else "unsupported_status" if unsupported else "failed",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        # 新的完整同步拥有更新的证据；旧请求不得覆盖它。
        if not success and unsupported and snapshot.get("sync_id") == sync_id:
            snapshot.update(status="pending_verification", reason="polish_unsupported_status")
            metadata["sale_snapshot"] = snapshot
        row.metadata_json = metadata
        # checked_at / last_seen_at / sync_id 仍表示完整商品同步，不伪造一次完整检查。

    async def _polish_item(self, cookie_str: str, item_id: str, retry_count: int = 0) -> dict:
        """
        擦亮商品
        
        支持令牌过期时存储set-cookie并重试（参照发货服务模式）
        
        Args:
            cookie_str: Cookie字符串
            item_id: 商品ID
            retry_count: 当前重试次数
            
        Returns:
            {"success": bool, "message": str}
        """
        max_retry = 3
        
        try:
            # 解析Cookie
            cookies = trans_cookies(cookie_str)
            
            # 获取token
            token = cookies.get('_m_h5_tk', '').split('_')[0] if cookies.get('_m_h5_tk') else ''
            
            if not token:
                return {"success": False, "message": "Cookie中没有找到_m_h5_tk"}
            
            # 生成时间戳
            t = str(int(time.time() * 1000))
            
            # 构造请求参数
            params = {
                'jsv': '2.7.2',
                'appKey': '34839810',
                't': t,
                'sign': '',
                'v': '2.0',
                'type': 'originaljson',
                'accountSite': 'xianyu',
                'dataType': 'json',
                'timeout': '20000',
                'api': 'mtop.taobao.idle.item.polish',
                'sessionOption': 'AutoLoginOnly',
                'spm_cnt': 'a21ybx.item.0.0',
                'spm_pre': 'a21ybx.personal.feeds.1.42f86ac21eZ9zd',
                'log_id': '42f86ac21eZ9zd'
            }
            
            # 构造请求数据
            data = {
                'itemId': item_id
            }
            
            # 生成签名
            data_val = json.dumps(data, separators=(',', ':'))
            sign = generate_sign(params['t'], token, data_val)
            params['sign'] = sign
            
            # 构造请求头
            headers = {
                'accept': 'application/json',
                'accept-language': 'en,zh-CN;q=0.9,zh;q=0.8,ru;q=0.7',
                'cache-control': 'no-cache',
                'content-type': 'application/x-www-form-urlencoded',
                'pragma': 'no-cache',
                'priority': 'u=1, i',
                'sec-ch-ua': '"Google Chrome";v="141", "Not=A?Brand";v="8", "Not A(Brand)";v="141"',
                'sec-ch-ua-arch': '"x64"',
                'sec-ch-ua-bitness': '"64"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Win32"',
                'sec-ch-ua-platform-version': '"10.0.0"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-site',
                'cookie': cookie_str,
                'Referer': 'https://www.goofish.com/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            # 发送请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://h5api.m.goofish.com/h5/mtop.taobao.idle.item.polish/1.0/',
                    params=params,
                    data={'data': data_val},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    result = await response.json()
                    
                    # 打印接口返回值
                    logger.info(
                        f"擦亮商品接口返回: item_id={item_id}, retry_count={retry_count}, result={result}"
                    )
                    
                    # 处理响应中的set-cookie，更新cookie（令牌过期时服务端会返回新cookie）
                    new_cookie_str = self._handle_polish_response_cookies(response, cookie_str)
                    
                    # 判断结果
                    if result.get('ret') and result['ret'][0] == 'SUCCESS::调用成功':
                        return {"success": True, "message": "擦亮成功", "cookie_str": new_cookie_str}
                    else:
                        error_msg = result.get('ret', ['未知错误'])[0] if result.get('ret') else '未知错误'
                        
                        # 如果返回"宝贝已经擦亮过了"，也视为成功
                        if '宝贝已经擦亮过了' in error_msg or 'IDLEITEM_POLISH_AGAIN' in error_msg:
                            return {"success": True, "message": "商品已经擦亮过了", "cookie_str": new_cookie_str}
                        
                        # 令牌过期时，用更新后的cookie重试
                        ret_list = result.get('ret', [])
                        if any('TOKEN_EXOIRED' in r or 'TOKEN_EXPIRED' in r for r in ret_list):
                            if retry_count < max_retry - 1:
                                logger.info(
                                    f"商品 {item_id} 擦亮令牌过期，"
                                    f"已更新Cookie，准备重试({retry_count + 1}/{max_retry - 1})"
                                )
                                await asyncio.sleep(0.5)
                                return await self._polish_item(new_cookie_str, item_id, retry_count + 1)
                        
                        return {"success": False, "message": error_msg, "cookie_str": new_cookie_str}
                        
        except aiohttp.ClientError as e:
            return {"success": False, "message": f"商品 {item_id} 网络请求失败: {e}"}
        except Exception as e:
            return {"success": False, "message": f"商品 {item_id} 擦亮异常: {e}"}
    
    def _handle_polish_response_cookies(self, response, original_cookie_str: str) -> str:
        """处理擦亮API响应中的set-cookie，返回更新后的cookie字符串
        
        令牌过期时服务端会在响应头中返回新的cookie（包含新的_m_h5_tk），
        存储后重试请求即可使用新的token签名
        
        Args:
            response: HTTP响应对象
            original_cookie_str: 原始Cookie字符串
            
        Returns:
            更新后的Cookie字符串
        """
        try:
            if 'set-cookie' in response.headers:
                new_cookies = {}
                for cookie in response.headers.getall('set-cookie', []):
                    if '=' in cookie:
                        name, value = cookie.split(';')[0].split('=', 1)
                        new_cookies[name.strip()] = value.strip()
                
                if new_cookies:
                    existing_cookies = trans_cookies(original_cookie_str)
                    existing_cookies.update(new_cookies)
                    updated_str = '; '.join([f"{k}={v}" for k, v in existing_cookies.items()])
                    logger.info(f"已从擦亮响应中更新Cookie（含{len(new_cookies)}个字段）")
                    return updated_str
        except Exception as e:
            logger.warning(f"处理擦亮响应Cookie失败: {e}")
        
        return original_cookie_str
    
    async def _log_execution(
        self,
        session: AsyncSession,
        batch_id: str,
        account_id: str,
        item_id: str,
        success: bool,
        error_message: Optional[str] = None,
    ) -> None:
        """
        记录执行日志
        
        Args:
            session: 数据库会话
            batch_id: 批次ID
            account_id: 账号ID
            item_id: 商品ID
            success: 是否成功
            error_message: 错误信息
        """
        try:
            log = ScheduledPolishLog(
                batch_id=batch_id,
                account_id=account_id,
                item_id=item_id,
                status="success" if success else "failed",
                error_message=error_message[:500] if error_message else None,
            )
            session.add(log)
            await session.commit()
            
            # 只有成功时才打印INFO日志，失败时打印DEBUG日志
            if success:
                logger.info(f"【{self.task_name}】账号 {account_id} 商品 {item_id} 处理成功")
            else:
                logger.debug(f"【{self.task_name}】账号 {account_id} 商品 {item_id} 处理失败: {error_message}")
            
        except Exception as e:
            logger.error(f"【{self.task_name}】记录日志失败: {e}")
            await session.rollback()


# 创建全局实例
polish_task_service = PolishTaskService()
