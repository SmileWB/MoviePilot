
import hashlib
import base64
import hmac
import time
import threading
import json as json_lib
import requests
from typing import Optional, List, Dict, Any
from urllib.parse import quote
from collections import OrderedDict

from app.core.config import settings
from app.core.context import MediaInfo, Context
from app.core.metainfo import MetaInfo
from app.log import logger
from app.utils.string import StringUtils
from app.utils.http import RequestUtils
from app.schemas.types import EventType, MessageChannel

# 导入官方 SDK
try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import *
    from lark_oapi.api.im.v2 import *
    from lark_oapi.ws.client import Client as WsClient
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    from lark_oapi.core.enum import LogLevel
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger, P2CardActionTriggerResponse
    SDK_AVAILABLE = True
except ImportError as e:
    SDK_AVAILABLE = False
    logger.warning(f"未安装 lark-oapi SDK，长连接功能将不可用：{e}")


class Feishu:
    """
    飞书机器人通知与交互实现
    使用官方 lark-oapi SDK 实现长连接
    参考：https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN
    """

    def __init__(self, FEISHU_BOT_WEBHOOK: Optional[str] = None,
                 FEISHU_BOT_SECRET: Optional[str] = None,
                 FEISHU_BOT_APP_ID: Optional[str] = None,
                 FEISHU_BOT_APP_SECRET: Optional[str] = None,
                 FEISHU_BOT_WS_URL: Optional[str] = None,
                 FEISHU_BOT_LONG_CONNECTION: Optional[bool] = False,
                 FEISHU_MODE: Optional[str] = None,
                 FEISHU_DEFAULT_USER_ID: Optional[str] = None,
                 **kwargs):
        """
        初始化飞书机器人
        :param FEISHU_BOT_WEBHOOK: 飞书机器人 Webhook URL（Webhook 模式必填）
        :param FEISHU_BOT_SECRET: 飞书机器人签名密钥（Webhook 模式可选）
        :param FEISHU_BOT_APP_ID: 飞书应用 App ID（SDK 模式必填）
        :param FEISHU_BOT_APP_SECRET: 飞书应用 App Secret（SDK 模式必填）
        :param FEISHU_BOT_WS_URL: 飞书长连接 WebSocket URL（已废弃，官方 SDK 自动获取）
        :param FEISHU_BOT_LONG_CONNECTION: 是否使用长连接模式接收事件（SDK 模式默认 True）
        :param FEISHU_MODE: 配置模式（sdk/webhook）
        :param FEISHU_DEFAULT_USER_ID: 默认通知用户 Open ID（SDK 模式必填）
        """
        logger.info(f"[Feishu] 初始化飞书实例：name={kwargs.get('name')}")

        self._mode = FEISHU_MODE or 'sdk'
        self._name = kwargs.get("name", "")

        if self._mode == 'webhook':
            if not FEISHU_BOT_WEBHOOK:
                logger.error("Webhook 模式需要配置 Webhook URL！")
                return
        else:  # SDK 模式
            if not FEISHU_BOT_APP_ID:
                logger.error("SDK 模式需要配置 App ID！")
                return

        self._webhook_url = FEISHU_BOT_WEBHOOK
        self._secret = FEISHU_BOT_SECRET
        self._app_id = FEISHU_BOT_APP_ID
        self._app_secret = FEISHU_BOT_APP_SECRET
        self._ws_url = FEISHU_BOT_WS_URL  # 已废弃，保留用于兼容
        self._use_long_connection = FEISHU_BOT_LONG_CONNECTION or (self._mode == 'sdk')
        self._default_user_id = FEISHU_DEFAULT_USER_ID

        # 消息回调地址（HTTP Push 模式需要）
        base_ds_url = f"http://127.0.0.1:{settings.PORT}/api/v1/message/"
        self._ds_url = f"{base_ds_url}?token={settings.API_TOKEN}"
        if self._name:
            encoded_name = quote(self._name, safe='')
            self._ds_url = f"{self._ds_url}&source={encoded_name}"

        # 用户会话映射，用于回复到正确的聊天
        self._user_chat_mapping: Dict[str, str] = {}

        # 消息去重缓存，防止重复处理同一条消息
        # 使用 OrderedDict 实现 LRU 缓存，最多保留 1000 条记录
        self._processed_messages: OrderedDict[str, float] = OrderedDict()
        self._message_cache_lock = threading.Lock()
        self._message_cache_ttl = 300  # 消息缓存 TTL（秒），5 分钟内的消息不会重复处理

        # 长连接相关 - 使用官方 SDK
        self._ws_client: Optional[WsClient] = None
        self._event_handler: Optional[EventDispatcherHandler] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_running = False

        # SDK 模式下自动启用长连接
        if self._mode == 'sdk' and SDK_AVAILABLE:
            self._init_official_sdk()

        logger.info(f"飞书机器人初始化完成，模式：{self._mode}")

    def _init_official_sdk(self):
        """
        初始化官方 lark-oapi SDK（长连接模式）
        参考：https://open.feishu.cn/document/server-docs/server-side-sdk/python--sdk
        """
        if not self._app_id or not self._app_secret:
            logger.error("SDK 模式需要配置 App ID 和 App Secret")
            return

        if not SDK_AVAILABLE:
            logger.error("lark-oapi SDK 未安装，无法使用长连接功能")
            return

        try:
            # 使用 builder 创建事件处理器并注册事件（注册方法在 builder 上，不在 build() 后的 handler 上）
            builder = EventDispatcherHandler.builder("", "")

            # 注册接收消息事件处理器
            builder.register_p2_im_message_receive_v1(self._handle_receive_message)

            # 注册按钮回调事件处理器
            builder.register_p2_card_action_trigger(self._handle_card_action)

            # 构建事件处理器
            self._event_handler = builder.build()

            # 创建长连接客户端
            self._ws_client = WsClient(
                app_id=self._app_id,
                app_secret=self._app_secret,
                event_handler=self._event_handler,
                log_level=LogLevel.INFO,
                auto_reconnect=True
            )

            # 在后台线程中启动长连接
            self._ws_running = True
            self._ws_thread = threading.Thread(
                target=self._run_ws_loop,
                daemon=True
            )
            self._ws_thread.start()
            logger.info("官方 SDK 长连接客户端已启动")

        except Exception as e:
            logger.error(f"初始化官方 SDK 失败：{e}", exc_info=True)

    def _run_ws_loop(self):
        """在线程中运行 SDK 长连接"""
        # 导入官方 SDK 模块级的 loop 并重新赋值
        import lark_oapi.ws.client as ws_client_module
        import asyncio

        # 为线程创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 更新 SDK 模块的 loop 引用
        ws_client_module.loop = loop

        try:
            self._ws_client.start()
        except Exception as e:
            logger.error(f"飞书长连接运行失败：{e}", exc_info=True)
        finally:
            try:
                loop.close()
            except:
                pass

    def stop_long_connection(self):
        """停止长连接"""
        self._ws_running = False
        # 停止线程
        if self._ws_thread and self._ws_thread.is_alive():
            # 等待线程结束
            self._ws_thread.join(timeout=3)
        logger.info("飞书长连接已停止")

    def get_ws_status(self) -> Dict[str, Any]:
        """
        获取长连接状态
        :return: 状态信息
        """
        if not SDK_AVAILABLE:
            return {
                "connected": False,
                "status": "sdk_not_installed",
                "message": "lark-oapi SDK 未安装，请运行 pip install lark-oapi 安装"
            }

        if self._mode != 'sdk':
            return {
                "connected": False,
                "status": "not_sdk_mode",
                "message": "当前为 Webhook 模式，不支持长连接"
            }

        if self._ws_running and self._ws_thread and self._ws_thread.is_alive():
            return {
                "connected": True,
                "status": "connected",
                "message": "官方 SDK 长连接已运行"
            }
        else:
            return {
                "connected": False,
                "status": "disconnected",
                "message": "长连接未运行，保存配置并重启模块后自动连接"
            }

    def reconnect_ws(self) -> bool:
        """
        重新连接长连接
        注意：官方 SDK 支持自动重连（auto_reconnect=True），无需手动触发
        此方法仅在用户主动点击重连按钮时调用，会完全重建 SDK 实例
        """
        if not SDK_AVAILABLE:
            logger.error("lark-oapi SDK 未安装，无法重连")
            return False

        if self._mode != 'sdk':
            logger.error("当前为 Webhook 模式，不支持长连接")
            return False

        try:
            # 标记停止
            self._ws_running = False

            # 停止旧线程
            if self._ws_thread and self._ws_thread.is_alive():
                self._ws_thread.join(timeout=3)

            time.sleep(0.5)

            # 清空旧实例
            self._ws_client = None
            self._ws_thread = None

            # 重新初始化 SDK（创建全新实例）
            self._init_official_sdk()

            # 等待新连接建立
            time.sleep(2)

            logger.info("飞书长连接重新初始化完成")
            return True
        except Exception as e:
            logger.error(f"重连失败：{e}", exc_info=True)
            return False

    def get_state(self) -> bool:
        """
        获取状态
        """
        return bool(self._webhook_url or (self._app_id and self._app_secret))

    def _is_message_processed(self, message_id: str) -> bool:
        """
        检查消息是否已处理过
        :param message_id: 消息 ID
        :return: True 表示已处理过，False 表示未处理
        """
        if not message_id:
            return False

        current_time = time.time()

        with self._message_cache_lock:
            # 清理过期消息
            self._cleanup_expired_messages(current_time)

            # 检查是否在缓存中
            if message_id in self._processed_messages:
                logger.debug(f"消息已处理过，跳过：{message_id}")
                return True

            # 添加到缓存
            self._processed_messages[message_id] = current_time

            # 限制缓存大小
            if len(self._processed_messages) > 1000:
                self._processed_messages.popitem(last=False)

            return False

    def _cleanup_expired_messages(self, current_time: float):
        """
        清理过期的消息记录
        :param current_time: 当前时间戳
        """
        expired_keys = []
        for msg_id, timestamp in self._processed_messages.items():
            if current_time - timestamp > self._message_cache_ttl:
                expired_keys.append(msg_id)
            else:
                # OrderedDict 按插入顺序排序，遇到第一个未过期的就可以停止
                break

        for msg_id in expired_keys:
            del self._processed_messages[msg_id]

    def _handle_receive_message(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        """
        处理接收到的消息事件
        """
        try:
            logger.info(f"收到飞书消息：{data}")

            # 从事件数据中提取信息
            event = getattr(data, 'event', None)
            if not event:
                return

            sender = getattr(event, 'sender', {})
            message = getattr(event, 'message', {})

            # 获取消息 ID 用于去重
            message_id = getattr(message, 'message_id', None)

            # 检查消息是否已处理过
            if self._is_message_processed(message_id):
                logger.info(f"消息已处理过，跳过：{message_id}")
                return

            # 获取用户 ID
            sender_id = getattr(sender, 'sender_id', {})
            userid = getattr(sender_id, 'open_id', None) or getattr(sender_id, 'union_id', None) or getattr(sender_id, 'user_id', None)
            username = getattr(sender, 'name', '')

            # 获取消息内容
            message_content = getattr(message, 'content', '')
            chat_id = getattr(message, 'chat_id', None)

            # 解析消息内容
            text = message_content
            try:
                content_data = json_lib.loads(message_content)
                text = content_data.get('text', '')
            except:
                pass

            logger.info(f"收到飞书消息：userid={userid}, username={username}, text={text}")

            # 检查是否是绑定默认通知用户的命令
            if text and text.strip() == "绑定默认通知用户":
                self._bind_default_user(userid, username, message_id, chat_id)

            # 存储用户会话映射，用于回复
            if userid and chat_id:
                self._user_chat_mapping[userid] = chat_id

            # 调用 MessageChain 处理消息
            from app.chain.message import MessageChain
            MessageChain().handle_message(
                channel=MessageChannel.Feishu,
                source=self._name,
                userid=userid,
                username=username,
                text=text,
                original_message_id=message_id,
                original_chat_id=chat_id
            )
        except Exception as e:
            logger.error(f"处理飞书消息失败：{e}", exc_info=True)

    def _handle_card_action(self, data: P2CardActionTrigger) -> Optional[P2CardActionTriggerResponse]:
        """
        处理按钮回调事件
        注意：飞书要求 3 秒内返回响应，所以需要异步处理消息
        """
        try:
            logger.info(f"收到飞书按钮回调：{data}")

            event = getattr(data, 'event', None)
            if not event:
                return None

            # 注意：按钮回调使用 operator 字段，不是 sender
            operator = getattr(event, 'operator', {})
            action = getattr(event, 'action', {})
            message = getattr(event, 'message', {})

            # 获取消息 ID 用于去重
            message_id = getattr(message, 'message_id', None)

            # 检查消息是否已处理过
            if self._is_message_processed(message_id):
                logger.info(f"按钮回调消息已处理过，跳过：{message_id}")
                return None  # 返回 None 表示使用默认响应

            # 从 operator 获取用户 ID
            userid = getattr(operator, 'open_id', None) or getattr(operator, 'union_id', None) or getattr(operator, 'user_id', None)
            username = getattr(operator, 'name', '')
            # action.value 是一个字典，包含 {"action": "callback_data"}
            action_value = getattr(action, 'value', {})
            if isinstance(action_value, dict):
                callback_data = action_value.get('action', '')
            else:
                callback_data = str(action_value)
            chat_id = getattr(message, 'chat_id', None)

            logger.info(f"飞书按钮回调：userid={userid}, callback_data={callback_data}")

            # 存储用户会话映射
            if userid and chat_id:
                self._user_chat_mapping[userid] = chat_id

            # 在后台线程中处理消息（避免阻塞回调响应）
            import threading
            def handle_callback():
                from app.chain.message import MessageChain
                MessageChain().handle_message(
                    channel=MessageChannel.Feishu,
                    source=self._name,
                    userid=userid,
                    username=username,
                    text=f"CALLBACK:{callback_data}",
                    original_message_id=message_id,
                    original_chat_id=chat_id
                )
            threading.Thread(target=handle_callback, daemon=True).start()

            # 立即返回成功响应（飞书要求 3 秒内）
            return None  # 返回 None 表示使用默认响应，飞书 SDK 会自动处理

        except Exception as e:
            logger.error(f"处理飞书按钮回调失败：{e}", exc_info=True)
            return None

    def _bind_default_user(self, userid: str, username: str,
                           original_message_id: str = None,
                           original_chat_id: str = None) -> None:
        """
        绑定默认通知用户
        :param userid: 用户 ID（open_id）
        :param username: 用户名
        :param original_message_id: 原消息 ID（用于回复）
        :param original_chat_id: 原聊天 ID（用于回复）
        """
        if not userid:
            logger.warn("无法获取用户 ID，绑定失败")
            self.send_msg(
                title="绑定失败",
                text=f"抱歉，无法获取您的用户 ID，绑定失败。",
                userid=userid
            )
            return

        try:
            from app.db.systemconfig_oper import SystemConfigOper

            # 获取所有通知配置
            notifications = SystemConfigOper().get('Notifications') or []

            # 找到对应的飞书配置并更新
            updated = False
            for conf in notifications:
                if conf.get('name') == self._name and conf.get('type') == 'feishu':
                    # 更新默认用户 ID
                    conf['config']['FEISHU_DEFAULT_USER_ID'] = userid
                    updated = True
                    logger.info(f"已将 {username}（{userid}）绑定为飞书配置 {self._name} 的默认通知用户")
                    break

            if updated:
                # 保存配置
                SystemConfigOper().set('Notifications', notifications)
                # 发送确认消息（使用回复模式）
                self.send_msg(
                    title="绑定成功",
                    text=f"您好 {username}，您已成功绑定为默认通知用户！\n后续通知消息将发送给您。",
                    userid=userid,
                    original_message_id=original_message_id,
                    original_chat_id=original_chat_id
                )
            else:
                logger.warn(f"未找到对应的飞书配置：{self._name}")
                # 发送失败消息
                self.send_msg(
                    title="绑定失败",
                    text=f"抱歉，未找到飞书配置「{self._name}」。\n请在通知设置中确认配置是否存在。",
                    userid=userid
                )

        except Exception as e:
            logger.error(f"绑定默认通知用户失败：{e}", exc_info=True)
            self.send_msg(
                title="绑定失败",
                text=f"绑定过程中发生错误：{str(e)}",
                userid=userid
            )

    def send_msg(self, title: str, text: str, image: str = None, link: str = None,
                 buttons: list = None, userid: str = None,
                 original_message_id: str = None, original_chat_id: str = None,
                 card_title: str = None) -> bool:
        """
        发送文本消息
        """
        if self._mode == 'webhook':
            return self._send_webhook_msg(title, text, image, link, buttons)
        else:
            return self._send_sdk_msg(title, text, image, link, buttons, userid,
                                      original_message_id, original_chat_id, card_title)

    def _send_webhook_msg(self, title: str, text: str, image: str = None,
                          link: str = None, buttons: list = None) -> bool:
        """Webhook 模式发送消息"""
        try:
            content = {
                "title": title,
                "text": text
            }

            # 签名校验
            if self._secret:
                timestamp = str(int(time.time()))
                sign_str = f"{timestamp}\n{self._secret}"
                signature = base64.b64encode(
                    hmac.new(sign_str.encode('utf-8'), digestmod=hashlib.sha256).digest()
                ).decode('utf-8')
                content["sign"] = signature
                content["timestamp"] = timestamp

            body = {
                "msg_type": "interactive",
                "content": json_lib.dumps(content)
            }

            response = requests.post(self._webhook_url, json=body, timeout=10)
            result = response.json()

            if result.get('StatusCode') == 0 or result.get('code') == 0:
                logger.info(f"飞书 Webhook 消息发送成功：{title}")
                return True
            else:
                logger.error(f"飞书 Webhook 消息发送失败：{result}")
                return False

        except Exception as e:
            logger.error(f"发送飞书 Webhook 消息失败：{e}", exc_info=True)
            return False

    def _send_sdk_msg(self, title: str, text: str, image: str = None, link: str = None,
                      buttons: list = None, userid: str = None,
                      original_message_id: str = None, original_chat_id: str = None,
                      card_title: str = None) -> bool:
        """SDK 模式发送消息 - 使用 V2 卡片格式（按钮直接放在 elements 中）
        :param card_title: 卡片 header 标题，默认为 title
        """
        try:
            # 使用默认用户 ID
            if not userid:
                userid = self._default_user_id
                if not userid:
                    logger.error("未指定用户 ID 且未配置默认用户，消息无法发送")
                    return False

            # 构造 V2 卡片消息内容
            if buttons:
                # V2 交互式卡片消息
                text_content = text if text else ""

                # 构建卡片元素
                elements = [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**{title}**\n{text_content}" if text_content else f"**{title}**"
                        }
                    }
                ]

                # buttons 是 List[List[Dict]] 结构，按行处理
                # V2 中使用 column_set 实现按钮横向排列
                for button_row in buttons:
                    if isinstance(button_row, list):
                        columns = []
                        for btn in button_row:
                            columns.append({
                                "tag": "column",
                                "width": "auto",
                                "weight": 1,
                                "vertical_align": "top",
                                "elements": [
                                    {
                                        "tag": "button",
                                        "text": {
                                            "tag": "plain_text",
                                            "content": btn.get("text", btn.get("label", "操作"))
                                        },
                                        "type": "primary",
                                        "behaviors": [
                                            {
                                                "type": "callback",
                                                "value": {
                                                    "action": btn.get("value", btn.get("callback_data", "click"))
                                                }
                                            }
                                        ]
                                    }
                                ]
                            })

                        if columns:
                            elements.append({
                                "tag": "column_set",
                                "flex_mode": "flow",
                                "background_style": "default",
                                "columns": columns
                            })

                # V2 卡片格式 - 添加 schema 声明和 body 字段
                content = {
                    "schema": "2.0",  # V2 声明
                    "config": {
                        "wide_screen_mode": True,
                        "update_multi": True  # V2 默认共享卡片
                    },
                    "header": {
                        "template": "blue",
                        "title": {
                            "tag": "plain_text",
                            "content": card_title if card_title else title
                        }
                    },
                    "body": {  # V2 新增 body 字段
                        "elements": elements
                    }
                }
                msg_type = "interactive"
            else:
                # 普通文本消息（处理 text 为 None 的情况）
                text_content = text if text else ""
                content = {"text": f"**{title}**\n{text_content}" if text_content else title}
                msg_type = "text"

            # 判断是发送消息还是回复消息
            if original_message_id and original_chat_id:
                # 回复消息 - 使用 V2 API
                return self._reply_message_v2(original_message_id, original_chat_id, msg_type, content, userid)
            else:
                # 发送消息 - 使用 V2 API
                return self._create_message_v2(userid, msg_type, content)

        except Exception as e:
            logger.error(f"发送飞书 SDK 消息失败：{e}", exc_info=True)
            return False

    def _create_message(self, userid: str, msg_type: str, content: Dict) -> bool:
        """发送消息到飞书"""
        try:
            # 构建请求
            request: CreateMessageRequest = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(CreateMessageRequestBody.builder()
                              .receive_id(userid)
                              .msg_type(msg_type)
                              .content(json_lib.dumps(content))
                              .build()) \
                .build()

            # 创建客户端并发送请求
            from lark_oapi.client import Client
            client = Client.builder() \
                .app_id(self._app_id) \
                .app_secret(self._app_secret) \
                .log_level(LogLevel.DEBUG) \
                .build()

            response: CreateMessageResponse = client.im.v1.message.create(request)

            if response.success():
                logger.info(f"飞书 SDK 消息发送成功：{json_lib.dumps(content)}")
                return True
            else:
                logger.error(f"飞书 SDK 消息发送失败：{response.code}, {response.msg}")
                return False

        except Exception as e:
            logger.error(f"发送飞书消息失败：{e}", exc_info=True)
            return False

    def _reply_message(self, message_id: str, chat_id: str, msg_type: str, content: Dict, userid: str) -> bool:
        """回复消息到飞书"""
        try:
            # 构建请求
            request: ReplyMessageRequest = ReplyMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(ReplyMessageRequestBody.builder()
                              .msg_type(msg_type)
                              .content(json_lib.dumps(content))
                              .build()) \
                .build()

            # 创建客户端并发送请求
            from lark_oapi.client import Client
            client = Client.builder() \
                .app_id(self._app_id) \
                .app_secret(self._app_secret) \
                .log_level(LogLevel.DEBUG) \
                .build()

            response: ReplyMessageResponse = client.im.v1.message.reply(request)

            if response.success():
                logger.info(f"飞书 SDK 回复消息发送成功：{json_lib.dumps(content)}")
                return True
            else:
                logger.error(f"飞书 SDK 回复消息发送失败：{response.code}, {response.msg}")
                return False

        except Exception as e:
            logger.error(f"回复飞书消息失败：{e}", exc_info=True)
            return False

    def _create_message_v2(self, userid: str, msg_type: str, content: Dict) -> bool:
        """发送消息到飞书 - 使用 V1 API 发送 V2 格式卡片"""
        try:
            # 构建请求 - 使用 V1 API，但内容是 V2 格式
            request = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(CreateMessageRequestBody.builder()
                              .receive_id(userid)
                              .msg_type(msg_type)
                              .content(json_lib.dumps(content))
                              .build()) \
                .build()

            # 创建客户端并发送请求 - 使用 V1 API
            from lark_oapi.client import Client
            client = Client.builder() \
                .app_id(self._app_id) \
                .app_secret(self._app_secret) \
                .log_level(LogLevel.DEBUG) \
                .build()

            response = client.im.v1.message.create(request)

            if response.success():
                logger.info(f"飞书 V2 卡片消息发送成功：{json_lib.dumps(content)}")
                return True
            else:
                logger.error(f"飞书 V2 卡片消息发送失败：{response.code}, {response.msg}")
                return False

        except Exception as e:
            logger.error(f"发送飞书 V2 卡片消息失败：{e}", exc_info=True)
            return False

    def _reply_message_v2(self, message_id: str, chat_id: str, msg_type: str, content: Dict, userid: str) -> bool:
        """回复消息到飞书 - 使用 V1 API 发送 V2 格式卡片"""
        try:
            # 构建请求 - 使用 V1 API，但内容是 V2 格式
            request = ReplyMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(ReplyMessageRequestBody.builder()
                              .msg_type(msg_type)
                              .content(json_lib.dumps(content))
                              .build()) \
                .build()

            # 创建客户端并发送请求 - 使用 V1 API
            from lark_oapi.client import Client
            client = Client.builder() \
                .app_id(self._app_id) \
                .app_secret(self._app_secret) \
                .log_level(LogLevel.DEBUG) \
                .build()

            response = client.im.v1.message.reply(request)

            if response.success():
                logger.info(f"飞书 V2 卡片回复消息发送成功：{json_lib.dumps(content)}")
                return True
            else:
                logger.error(f"飞书 V2 卡片回复消息发送失败：{response.code}, {response.msg}")
                return False

        except Exception as e:
            logger.error(f"回复飞书 V2 卡片消息失败：{e}", exc_info=True)
            return False

    def send_medias_msg(self, title: str, medias: List[MediaInfo], userid: str = None,
                        buttons: list = None, original_message_id: str = None,
                        original_chat_id: str = None) -> bool:
        """发送媒体信息消息"""
        text = ""
        for media in medias:
            text += f"{media.title} ({media.year})\n"
            if media.vote_average:
                text += f"评分：{media.vote_average}\n"
            text += "\n"
        return self.send_msg(title, text, userid=userid, buttons=buttons,
                             original_message_id=original_message_id,
                             original_chat_id=original_chat_id,
                             card_title="媒体列表")

    def send_torrents_msg(self, title: str, torrents: List[Context], userid: str = None,
                          buttons: list = None, original_message_id: str = None,
                          original_chat_id: str = None) -> bool:
        """发送种子信息消息"""
        text = ""
        for i, torrent in enumerate(torrents, 1):
            torrent_info = torrent.torrent_info
            meta_info = torrent.meta_info
            # 将大小转换成 M 或 G 格式
            size_str = StringUtils.str_filesize(torrent_info.size) if torrent_info.size else "未知"
            # 获取视频规格信息
            resource_pix = meta_info.resource_pix if meta_info else None
            video_encode = meta_info.video_encode if meta_info else None
            # 构建规格标签
            specs = []
            if resource_pix:
                specs.append(resource_pix)
            if video_encode:
                specs.append(video_encode)
            specs_str = " ".join(specs) if specs else ""
            # 格式：序号。资源名称
            # 📦 大小  🔺 做种数  规格/站点
            text += f"**{i}. {torrent_info.title}**\n"
            line2 = f"📦 {size_str}  🔺 {torrent_info.seeders or 0}"
            if specs_str:
                line2 += f"  {specs_str}"
            elif torrent_info.description:
                line2 += f"  {torrent_info.description}"
            elif torrent_info.site_name:
                line2 += f"  {torrent_info.site_name}"
            text += line2 + "\n\n"
        return self.send_msg(title, text, userid=userid, buttons=buttons,
                             original_message_id=original_message_id,
                             original_chat_id=original_chat_id,
                             card_title="种子列表")

    def delete_msg(self, message_id: str, chat_id: str = None) -> bool:
        """删除消息"""
        try:
            from lark_oapi.client import Client
            from lark_oapi.api.im.v1 import DeleteMessageRequest, DeleteMessageRequestBuilder

            request: DeleteMessageRequest = DeleteMessageRequest.builder() \
                .message_id(message_id) \
                .build()

            client = Client.builder() \
                .app_id(self._app_id) \
                .app_secret(self._app_secret) \
                .log_level(LogLevel.DEBUG) \
                .build()

            response = client.im.v1.message.delete(request)

            if response.success():
                logger.info(f"飞书消息删除成功：{message_id}")
                return True
            else:
                logger.error(f"飞书消息删除失败：{response.code}, {response.msg}")
                return False

        except Exception as e:
            logger.error(f"删除飞书消息失败：{e}", exc_info=True)
            return False
