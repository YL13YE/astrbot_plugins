import asyncio
import random
import json
import os
from time import sleep
from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import At
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.permission import PermissionTypeFilter

# 情感映射表
emotions_dict = {
    "开心": [2, 74, 109, 272, 295, 305, 318, 319, 324, 339],
    "得意": [4, 16, 28, 29, 99, 101, 178, 269, 270, 277, 283, 299, 307, 336, 426],
    "害羞": [6, 20, 21],
    "难过": [5, 34, 35, 36, 37, 173, 264, 265, 267, 425],
    "纠结": [106, 176, 262, 263, 270],
    "生气": [11, 26, 31, 105],
    "惊讶": [3, 325],
    "疑惑": [32, 268],
    "恳求": [111, 353],
    "可怕": [1, 286],
    "尴尬": [100, 306, 342, 344, 347],
    "无语": [46, 97, 181, 271, 281, 284, 287, 312, 352, 357, 427],
    "恶心": [19, 59, 323],
    "无聊": [8, 25, 285, 293],
}

# 完整表情列表（从qqemotionreply导入）
complete_emoji_list = [
    # 系统表情（type=1，ID为数字，存储为整数）
    4, 5, 8, 9, 10, 12, 14, 16, 21, 23, 24, 25, 26, 27, 28, 29, 30, 32, 33, 34,
    38, 39, 41, 42, 43, 49, 53, 60, 63, 66, 74, 75, 76, 78, 79, 85, 89, 96, 97,
    98, 99, 100, 101, 102, 103, 104, 106, 109, 111, 116, 118, 120, 122, 123, 124,
    125, 129, 144, 147, 171, 173, 174, 175, 176, 179, 180, 181, 182, 183, 201,
    203, 212, 214, 219, 222, 227, 232, 240, 243, 246, 262, 264, 265, 266, 267,
    268, 269, 270, 271, 272, 273, 277, 278, 281, 282, 284, 285, 287, 289, 290,
    293, 294, 297, 298, 299, 305, 306, 307, 314, 315, 318, 319, 320, 322, 324, 326,
    # emoji表情（type=2，ID为文档中明确的数字编号，存储为字符串）
    '9728', '9749', '9786', '10024', '10060', '10068', '127801', '127817', '127822',
    '127827', '127836', '127838', '127847', '127866', '127867', '127881', '128027',
    '128046', '128051', '128053', '128074', '128076', '128077', '128079', '128089',
    '128102', '128104', '128147', '128157', '128164', '128166', '128168', '128170',
    '128235', '128293', '128513', '128514', '128516', '128522', '128524', '128527',
    '128530', '128531', '128532', '128536', '128538', '128540', '128541', '128557',
    '128560', '128563'
]

@register(
    "astrbot_plugin_emoji_like",
    "Zhalslar",
    "调用LLM判断消息的情感，智能地给消息贴QQ表情",
    "1.0.1",
    "https://github.com/Zhalslar/astrbot_plugin_emoji_like",
)
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # 情感映射表
        self.emotions_dict: dict[str, list[int]] = emotions_dict
        # 可用情感关键字
        self.emotion_keywords: list[str] = list(self.emotions_dict.keys())
        # 对普通消息进行情感分析的概率
        self.normal_analysis_prob: float = config.get("normal_analysis_prob", 0.01)
        # 对@消息进行情感分析的概率
        self.at_analysis_prob: float = config.get("at_analysis_prob", 0.1)
        # 管理员 id 列表
        self.admin_id = self.context.get_config().admins_id
        self.config = config  # 保存引用，便于后续写回
        
        # 特殊 id 列表（全局持久化）
        self.special_id_list: set = set()
        self._load_special_id_list()
        # 只对列表用户生效模式
        self.only_list_mode: bool = config.get("only_list_mode", True)
        
        # fill功能配置
        self.default_emoji_num = config.get("default_emoji_num", 20)
        self.time_interval = config.get("time_interval", 0.2)
        # 完整表情列表
        self.complete_emoji_list = complete_emoji_list
        
        # CD 时间配置
        self.boom_cd = config.get("boom_cd", 60)  # 默认冷却时间为 60 秒
        self.last_usage = {}  # 存储每个用户上次使用指令的时间

    def _get_data_file_path(self):
        """获取数据文件路径"""
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(plugin_dir, "special_id_list.json")
    
    def _load_special_id_list(self):
        """从文件加载特殊id列表"""
        try:
            file_path = self._get_data_file_path()
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.special_id_list = set(data.get("special_id_list", []))
                    logger.info(f"已从文件加载特殊id列表: {self.special_id_list}")
            else:
                # 如果文件不存在，尝试从config读取（兼容旧版本）
                config_list = self.config.get("special_id_list", [])
                self.special_id_list = set(config_list)
                logger.info(f"从config加载特殊id列表: {self.special_id_list}")
                # 保存到文件以便后续使用
                self._save_special_id_list()
            
            # 确保配置对象中的列表与内存中的保持同步
            self.config["special_id_list"] = list(self.special_id_list)
        except Exception as e:
            logger.error(f"加载特殊id列表失败: {e}")
            self.special_id_list = set()
    
    def _save_special_id_list(self):
        """保存特殊id列表到文件"""
        try:
            file_path = self._get_data_file_path()
            data = {"special_id_list": list(self.special_id_list)}
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # 同时更新config中的列表，确保前端管理页面显示正确
            self.config["special_id_list"] = list(self.special_id_list)
            logger.info(f"已保存特殊id列表到文件和配置: {self.special_id_list}")
        except Exception as e:
            logger.error(f"保存特殊id列表失败: {e}")


    @filter.command("fill", alias={'贴'})
    async def fill_emoji(self, event: AiocqhttpMessageEvent, emojiNum: int = -1):
        """/fill 数量 - 随机贴表情，功能与qqemotionreply相同"""
        #yield event.plain_result(f"检测到fill command:{emojiNum}")
        # 如果用户未输入参数，使用默认值
        if emojiNum == -1:
            emojiNum = self.default_emoji_num
        
        # 获取回复消息ID，参考 replyMessage 的实现
        reply_text = next(
            (msg.text for msg in event.message_obj.message if msg.type == "Reply"), None  # type: ignore
        )
        if not reply_text:
            yield event.plain_result("请回复一条消息")
            return
        message_id = next(
            (msg.id for msg in event.message_obj.message if msg.type == "Reply"), None  # type: ignore
        )
        if not message_id:
            yield event.plain_result("无法获取回复消息ID")
            return

        # 获取发送者ID
        sender_id = str(event.message_obj.sender.user_id)
        
        # 如果发送者不是管理员，将其添加到特殊ID列表
        if sender_id not in self.admin_id:
            self.special_id_list.add(sender_id)
            self._save_special_id_list()  # 保存到文件和配置
            yield event.plain_result(f"扣1给你贴")
            await asyncio.sleep(5)
            yield event.plain_result(f"已将用户 {sender_id} 添加到贴猴列表")
            return
        
        # 限制表情数量上限为20
        if emojiNum > 20:
            emojiNum = 20
            return
        
        # 随机选择表情并发送
        try:
            rand_emoji_list = random.sample(self.complete_emoji_list, min(emojiNum, len(self.complete_emoji_list)))
            for emoji_id in rand_emoji_list:
                try:
                    await event.bot.set_msg_emoji_like(
                        message_id=message_id, emoji_id=emoji_id, set=True
                    )
                    await asyncio.sleep(self.time_interval)
                except Exception as e:
                    logger.error(f"贴表情失败: {e}")
            
        except Exception as e:
            logger.error(f"贴表情过程中出错: {e}")
        
        event.stop_event()

    @filter.command("爆破猴", alias={'boom'})
    async def boom_emoji(self, event: AiocqhttpMessageEvent, Num: int = 0):
        #yield event.plain_result(f"检测到爆破 command:{Num}")
        
        # 获取发送者ID
        sender_id = str(event.message_obj.sender.user_id)
        now = asyncio.get_event_loop().time()
        
        # 检查CD时间
        if sender_id in self.last_usage and (now - self.last_usage[sender_id]) < self.boom_cd:
            remaining_time = self.boom_cd - (now - self.last_usage[sender_id])
            yield event.plain_result(f"爆破猴冷却中，请等待 {remaining_time:.1f} 秒后重试。")
            return
        
        # 获取回复消息ID，参考 replyMessage 的实现
        reply_text = next(
            (msg.text for msg in event.message_obj.message if msg.type == "Reply"), None  # type: ignore
        )
        if not reply_text:
            yield event.plain_result("请回复一条消息")
            return
        message_id = next(
            (msg.id for msg in event.message_obj.message if msg.type == "Reply"), None  # type: ignore
        )
        if not message_id:
            yield event.plain_result("无法获取回复消息ID")
            return


        # 如果发送者不是管理员，将其添加到特殊ID列表
        if sender_id not in self.admin_id:
            if Num > 10:
                Num = 10  # 最多爆破10次
            
            # 添加用户到特殊列表
        '''self.special_id_list.add(sender_id)
            self._save_special_id_list()  # 保存到文件和配置
            yield event.plain_result(f"扣1给你贴")
            await asyncio.sleep(5)
            yield event.plain_result(f"已将用户 {sender_id} 添加到贴猴列表")
            return'''        
        # 记录使用时间
        self.last_usage[sender_id] = now        
        try:
            logger.info(f"开始爆破，次数: {Num}")
            for time in range(0, Num):
                try:
                    await event.bot.set_msg_emoji_like(
                        message_id=message_id, emoji_id='128053', set=True
                    )
                    await asyncio.sleep(self.time_interval)
                    await event.bot.set_msg_emoji_like(
                        message_id=message_id, emoji_id='128053', set=False
                    )
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"贴表情失败: {e}")
            logger.info(f"爆破完成，共 {Num} 次")
        except Exception as e:
            logger.error(f"爆破猴过程中出错: {e}")
        
        event.stop_event()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("add_list")
    async def add_list(self, event: AiocqhttpMessageEvent, id_str: str):
        """/add_list xxx，将 xxx 加入特殊 id 列表"""
        self.special_id_list.add(id_str)
        self._save_special_id_list()  # 保存到文件和配置
        yield event.plain_result(f"已将 {id_str} 加入贴猴列表。")
        event.stop_event()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("show_list")
    async def show_list(self, event: AiocqhttpMessageEvent):
        yield event.plain_result(f"贴猴列表：{self.special_id_list} ")
        event.stop_event()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("delete_list")
    async def delete_list(self, event: AiocqhttpMessageEvent, id_str: str = None):
        """/delete_list xxx 删除指定id，/delete_list ALL 清空列表"""
        if id_str is None:
            yield event.plain_result("请指定要删除的id，或用 ALL 清空列表。")
        elif id_str == "ALL":
            self.special_id_list.clear()
            self._save_special_id_list()  # 保存到文件和配置
            yield event.plain_result("已清空贴猴列表。")
        else:
            # 转换为字符串格式并去除空白字符确保匹配
            id_str = str(id_str).strip()
            # 查找匹配项（不区分类型）
            found = False
            for item in self.special_id_list:
                if str(item).strip() == id_str:
                    self.special_id_list.remove(item)
                    found = True
                    break
            
            if found:
                self._save_special_id_list()  # 保存到文件和配置
                yield event.plain_result(f"已删除 {id_str}。当前贴猴列表：{self.special_id_list}")
            else:
                yield event.plain_result(f"{id_str} 不在贴猴列表中。当前贴猴列表：{self.special_id_list}")
        event.stop_event()

    @filter.command("emoji_help", alias={'贴表情帮助', '表情帮助'})
    async def show_help(self, event: AiocqhttpMessageEvent):
        """显示帮助信息"""
        help_text = """贴表情插件使用方法:
1. /fill [数量] - 随机贴表情 (别名: /贴)
2. /爆破猴 [次数] - 重复贴表情并取消，产生爆破效果
3. /add_list [ID] - 将用户添加到特殊列表 (管理员)
4. /delete_list [ID] - 从特殊列表删除用户 (管理员)
5. /show_list - 显示特殊列表 (管理员)
6. /set_boom_cd [秒] - 设置爆破猴CD时间 (管理员)
7. /emoji_help - 显示本帮助 (别名: /贴表情帮助, /表情帮助)

注意: 
- 使用 /fill 或 /贴 命令的非管理员用户会自动被添加到特殊列表
- 爆破猴命令有CD时间限制，默认为30秒
"""
        yield event.plain_result(help_text)
        event.stop_event()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_message(self, event: AiocqhttpMessageEvent):
        """
        监听群消息，并进行情感分析
        """
        user_id = str(event.get_sender_id())
        emoji_id = '128053'
        if user_id in self.admin_id:
            return
        #yield event.plain_result("非admin")
        # 新增：只对列表用户生效模式
        if self.only_list_mode:
            #yield event.plain_result("进入到user_only mode")
            if user_id not in self.special_id_list:
                #yield event.plain_result("非list user")
                return
            # 只对列表用户，概率贴表情
            temp=random.random()
            if  temp > self.at_analysis_prob:
                #yield event.plain_result(f"本次不贴，随机数为{temp}>{self.at_analysis_prob}")
                return
            if  temp < 0.1:
                emoji_id = 66
            elif temp < 0.2:
                emoji_id = '10068'
            
            message_id = event.message_obj.message_id
            try:
                await event.bot.set_msg_emoji_like(
                    message_id=message_id, emoji_id=emoji_id, set=True
                )
               # yield event.plain_result("已贴")
            except Exception as e:
                logger.warning(f"设置表情失败: {e}")
            event.stop_event()
            return
        # 非只对列表用户模式，保持原有逻辑
        if user_id in self.special_id_list:
            if random.random() > self.at_analysis_prob:
                return
            message_id = event.message_obj.message_id
            try:
                await event.bot.set_msg_emoji_like(
                    message_id=message_id, emoji_id='128053', set=True
                )
            except Exception as e:
                logger.warning(f"设置表情失败: {e}")
            event.stop_event()
            return
        chain = event.get_messages()
        if not chain:
            return
        if isinstance(chain[0], At):
            if random.random() > self.at_analysis_prob:
                return
        else:
            if random.random() > self.normal_analysis_prob:
                return
        text = event.get_message_str()
        if not text:
            return

        emotion = await self.judge_emotion(text)
        message_id = event.message_obj.message_id

        for keyword in self.emotion_keywords:
            if keyword in emotion:
                emoji_id = random.choice(self.emotions_dict[keyword])
                try:
                    await event.bot.set_msg_emoji_like(
                        message_id=message_id, emoji_id=emoji_id, set=True
                    )
                except Exception as e:
                    logger.warning(f"设置表情失败: {e}")
                break
        if not isinstance(chain[0], At):
            event.stop_event()

    async def judge_emotion(self, text: str):
        """让LLM判断语句的情感"""

        system_prompt = f"你是一个情感分析专家，请根据给定的文本判断其情感倾向，并给出相应的一个最符合的情感标签，可选标签有：{self.emotion_keywords}"

        try:
            llm_response = await self.context.get_using_provider().text_chat(
                prompt="这是要分析的文本：" + text,
                system_prompt=system_prompt,
                image_urls=[],
                func_tool=self.context.get_llm_tool_manager(),
            )

            return llm_response.completion_text.strip()
        except Exception as e:
            logger.error(f"情感分析失败: {e}")
            return "其他"

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("set_boom_cd")
    async def set_boom_cd(self, event: AiocqhttpMessageEvent, cd: int):
        """设置爆破猴的CD时间"""
        if cd <= 0:
            yield event.plain_result("CD时间必须大于0秒")
            return
        
        self.boom_cd = cd
        # 更新配置
        self.config["boom_cd"] = cd
        yield event.plain_result(f"爆破猴CD时间已设置为 {cd} 秒")
        event.stop_event()


