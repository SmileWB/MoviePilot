import json
from typing import Optional, Union, List, Tuple, Any, Dict

from app.core.context import MediaInfo, Context
from app.db.systemconfig_oper import SystemConfigOper
from app.log import logger
from app.modules import _ModuleBase, _MessageBase
from app.modules.feishu.feishu import Feishu
from app.schemas import MessageChannel, CommingMessage, Notification
from app.schemas.types import ModuleType


class FeishuModule(_ModuleBase, _MessageBase[Feishu]):

    def init_module(self) -> None:
        """
        初始化模块
        """
        super().init_service(service_name="feishu", service_type=Feishu)
        self._channel = MessageChannel.Feishu

    @staticmethod
    def get_name() -> str:
        return "feishu"

    @staticmethod
    def get_type() -> ModuleType:
        """
        获取模块类型
        """
        return ModuleType.Notification

    @staticmethod
    def get_subtype() -> MessageChannel:
        """
        获取模块子类型
        """
        return MessageChannel.Feishu

    @staticmethod
    def get_priority() -> int:
        """
        获取模块优先级，数字越小优先级越高
        """
        return 5

    def stop(self):
        """
        停止模块
        """
        # 停止所有实例的长连接
        for name, instance in self.get_instances().items():
            if hasattr(instance, 'stop_long_connection'):
                instance.stop_long_connection()

    def test(self) -> Optional[Tuple[bool, str]]:
        """
        测试模块连接性
        """
        if not self.get_instances():
            return None
        for name, client in self.get_instances().items():
            state = client.get_state()
            if not state:
                return False, f"飞书机器人 {name} 未就绪"
        return True, ""

    def get_ws_status(self, source: str) -> Optional[Dict[str, Any]]:
        """
        获取长连接状态
        :param source: 配置名称
        :return: 状态信息
        """
        client: Feishu = self.get_instance(source)
        if not client:
            return None
        return client.get_ws_status()

    def reconnect_ws(self, source: str) -> bool:
        """
        重连长连接
        :param source: 配置名称
        :return: 是否成功
        """
        client: Feishu = self.get_instance(source)
        if not client:
            return False
        return client.reconnect_ws()

    def init_setting(self) -> Tuple[str, Union[str, bool]]:
        pass

    def message_parser(self, source: str, body: Any, form: Any, args: Any) -> Optional[CommingMessage]:
        """
        解析消息内容
        :param source: 消息来源
        :param body: 请求体
        :param form: 表单
        :param args: 参数
        :return: 渠道、消息体
        """
        # 获取服务配置
        client_config = self.get_config(source)
        if not client_config:
            return None

        try:
            msg_json: dict = json.loads(body)
        except Exception as err:
            logger.debug(f"解析飞书消息失败：{str(err)}")
            return None

        if not msg_json:
            return None

        # 飞书机器人消息格式（事件订阅）
        # {
        #     "schema": "2.0",
        #     "header": {
        #         "msg_type": "interactive_card",
        #         "event_type": "im.message.receive_v1",
        #         ...
        #     },
        #     "event": {
        #         "sender": {
        #             "sender_type": "user",
        #             "sender_id": {
        #                 "union_id": "xxx",
        #                 "user_id": "xxx"
        #             }
        #         },
        #         "message": {
        #             "message_id": "xxx",
        #             "chat_id": "xxx",
        #             "content": "{\"text\":\"hello\"}"
        #         }
        #     }
        # }

        header = msg_json.get("header", {})
        event = msg_json.get("event", {})

        # 处理事件订阅消息
        if header.get("event_type") == "im.message.receive_v1":
            sender = event.get("sender", {})
            message = event.get("message", {})

            # 获取用户的 open_id
            sender_id = sender.get("sender_id", {})
            userid = sender_id.get("open_id") or sender_id.get("union_id") or sender_id.get("user_id")
            username = sender.get("name", "")
            text = message.get("content", "")

            # 解析消息内容
            try:
                content_data = json.loads(text)
                text = content_data.get("text", "")
            except:
                pass

            # 获取原消息信息
            message_id = message.get("message_id")
            chat_id = message.get("chat_id")

            logger.info(f"收到来自 {client_config.name} 的飞书消息：userid={userid}, username={username}, text={text}")

            # 检查是否是绑定默认通知用户的命令
            if text and text.strip() == "绑定默认通知用户":
                self._bind_default_user(source=source, userid=userid, username=username)

            return CommingMessage(
                channel=MessageChannel.Feishu,
                source=client_config.name,
                userid=userid,
                username=username,
                text=text,
                message_id=message_id,
                chat_id=chat_id
            )

        # 处理按钮回调（事件订阅模式）
        if header.get("event_type") == "interactive_card.action":
            event_data = event.get("event", {})
            sender = event_data.get("sender", {})
            action = event_data.get("action", {})

            userid = sender.get("sender_id", {}).get("union_id") or sender.get("sender_id", {}).get("user_id")
            username = sender.get("name", "")
            callback_data = action.get("value", "")
            message = event_data.get("message", {})

            # 使用 CALLBACK 前缀标识按钮回调
            text = f"CALLBACK:{callback_data}"
            message_id = message.get("message_id")
            chat_id = message.get("chat_id")

            logger.info(f"收到来自 {client_config.name} 的飞书按钮回调：userid={userid}, callback_data={callback_data}")

            return CommingMessage(
                channel=MessageChannel.Feishu,
                source=client_config.name,
                userid=userid,
                username=username,
                text=text,
                is_callback=True,
                callback_data=callback_data,
                message_id=message_id,
                chat_id=chat_id
            )

        return None

    def _bind_default_user(self, source: str, userid: str, username: str) -> None:
        """
        绑定默认通知用户
        :param source: 消息来源（配置名称）
        :param userid: 用户 ID（open_id）
        :param username: 用户名
        """
        if not userid:
            logger.warn("无法获取用户 ID，绑定失败")
            return

        # 获取当前配置
        client_config = self.get_config(source)
        if not client_config:
            logger.warn(f"未找到配置：{source}")
            return

        try:
            # 获取所有通知配置
            notifications = SystemConfigOper().get('Notifications') or []

            # 找到对应的飞书配置并更新
            updated = False
            for conf in notifications:
                if conf.get('name') == source and conf.get('type') == 'feishu':
                    # 更新默认用户 ID
                    conf['config']['FEISHU_DEFAULT_USER_ID'] = userid
                    updated = True
                    logger.info(f"已将 {username}（{userid}）绑定为飞书配置 {source} 的默认通知用户")
                    break

            if updated:
                # 保存配置
                SystemConfigOper().set('Notifications', notifications)
                # 发送确认消息
                client: Feishu = self.get_instance(source)
                if client:
                    client.send_msg(
                        title="绑定成功",
                        text=f"您好 {username}，您已成功绑定为默认通知用户！\n后续通知消息将发送给您。",
                        userid=userid
                    )
            else:
                logger.warn(f"未找到对应的飞书配置：{source}")

        except Exception as e:
            logger.error(f"绑定默认通知用户失败：{e}", exc_info=True)

    def post_message(self, message: Notification, **kwargs) -> None:
        """
        发送消息
        :param message: 消息
        """
        for conf in self.get_configs().values():
            if not self.check_message(message, conf.name):
                continue

            # 获取用户 ID
            userid = message.userid
            targets = message.targets
            if not userid and targets is not None:
                userid = targets.get('feishu_userid')

            # 如果是测试消息或没有指定 userid，使用配置中的默认用户 ID
            if not userid:
                # 从配置中获取默认的 open_id
                default_userid = conf.config.get('FEISHU_DEFAULT_USER_ID')
                if default_userid:
                    userid = default_userid
                    logger.info(f"使用配置的默认用户 ID: {userid}")
                else:
                    logger.warn("未指定飞书用户 ID 且未配置默认用户，消息无法发送（SDK 模式需要指定 open_id）")
                    logger.warn("请在飞书配置中设置「默认通知用户 Open ID」，可通过飞书 API 获取用户的 open_id")
                    return

            client: Feishu = self.get_instance(conf.name)
            if client:
                result = client.send_msg(
                    title=message.title,
                    text=message.text,
                    image=message.image,
                    link=message.link,
                    userid=userid,
                    buttons=message.buttons,
                    original_message_id=message.original_message_id,
                    original_chat_id=message.original_chat_id
                )
                if result:
                    logger.info(f"飞书消息发送成功：{message.title}")
                else:
                    logger.error(f"飞书消息发送失败：{message.title}")

    def post_medias_message(self, message: Notification, medias: List[MediaInfo]) -> None:
        """
        发送媒体信息选择列表
        :param message: 消息体
        :param medias: 媒体信息
        """
        for conf in self.get_configs().values():
            if not self.check_message(message, conf.name):
                continue

            client: Feishu = self.get_instance(conf.name)
            if client:
                client.send_medias_msg(
                    title=message.title,
                    medias=medias,
                    userid=message.userid,
                    buttons=message.buttons,
                    original_message_id=message.original_message_id,
                    original_chat_id=message.original_chat_id
                )

    def post_torrents_message(self, message: Notification, torrents: List[Context]) -> None:
        """
        发送种子信息选择列表
        :param message: 消息体
        :param torrents: 种子信息
        """
        for conf in self.get_configs().values():
            if not self.check_message(message, conf.name):
                continue

            client: Feishu = self.get_instance(conf.name)
            if client:
                client.send_torrents_msg(
                    title=message.title,
                    torrents=torrents,
                    userid=message.userid,
                    buttons=message.buttons,
                    original_message_id=message.original_message_id,
                    original_chat_id=message.original_chat_id
                )

    def delete_message(self, channel: MessageChannel, source: str,
                       message_id: str, chat_id: Optional[str] = None) -> bool:
        """
        删除消息
        :param channel: 消息渠道
        :param source: 指定的消息源
        :param message_id: 消息 ID
        :param chat_id: 聊天 ID
        :return: 删除是否成功
        """
        success = False
        for conf in self.get_configs().values():
            if channel != self._channel:
                break
            if source != conf.name:
                continue

            client: Feishu = self.get_instance(conf.name)
            if client:
                result = client.delete_msg(message_id=message_id, chat_id=chat_id)
                if result:
                    success = True
        return success
