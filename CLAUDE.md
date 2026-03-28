# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MoviePilot - 智能影视媒体库管理工具，基于 NAStool 部分代码重新设计。

**技术栈**:
- 后端：FastAPI + Python 3.12
- 前端：Vue 3 + Vite + vuetify
- 数据库：SQLite (默认) / PostgreSQL

**相关仓库**:
- 主项目：[MoviePilot](https://github.com/jxxghp/MoviePilot)
- 前端：[MoviePilot-Frontend](https://github.com/jxxghp/MoviePilot-Frontend)
- 资源项目：[MoviePilot-Resources](https://github.com/jxxghp/MoviePilot-Resources)
- 插件项目：[MoviePilot-Plugins](https://github.com/jxxghp/MoviePilot-Plugins)

## Development Setup

### Requirements
- Python 3.12
- Node.js v20.12.1+

### Backend Setup
```bash
cd MoviePilot
pip install -r requirements.txt
python -m app.main  # 后端服务，默认端口 3001
```

API 文档：http://localhost:3001/docs

### Frontend Setup
```bash
cd MoviePilot-Frontend
yarn  # 安装依赖
yarn dev  # 前端开发服务器，默认端口 5173
```

前端通过 Vite proxy 将 `/api/v1` 请求转发到后端。

### Resource Files
克隆资源项目并将对应平台的库文件复制到 `app/helper` 目录：
```bash
git clone https://github.com/jxxghp/MoviePilot-Resources
# 复制 .so/.pyd/.bin 文件到 app/helper/
```

## Architecture

### Backend Structure

```
app/
├── api/              # API 层（RESTful + MCP）
│   ├── endpoints/    # 各功能模块路由
│   └── apiv1.py      # API v1 路由注册
├── chain/            # 业务链路层（核心业务逻辑）
├── core/             # 核心配置和模块
│   ├── config.py     # 配置管理
│   └── modules/      # 核心模块
├── db/               # 数据库模型和操作
├── helper/           # 辅助工具类
├── modules/          # 功能模块（站点、媒体服务器等）
├── plugins/          # 插件目录（运行时动态加载）
├── schemas/          # Pydantic 数据模型
├── startup/          # 启动初始化模块
└── utils/            # 工具函数
```

### Key Components

**配置系统** (`app/core/config.py`):
- 使用 Pydantic Settings 管理配置
- 支持从环境变量和 `.env` 文件读取
- 配置文件位于 `config/app.env`
- 主要配置项：
  - `PORT=3001` - 后端监听端口
  - `NGINX_PORT=3000` - 前端端口
  - `DB_TYPE` - 数据库类型 (sqlite/postgresql)
  - `API_TOKEN` - API 认证密钥

**生命周期管理** (`app/startup/lifespan.py`):
- FastAPI lifespan 管理启动/关闭事件
- 初始化顺序：routers → modules → plugins → scheduler → monitor → commands → workflows

**模块系统**:
- `modules_initializer.py` - 初始化站点、媒体服务器、下载器等模块
- `plugins_initializer.py` - 动态加载插件
- `scheduler_initializer.py` - APScheduler 定时任务管理
- `monitor_initializer.py` - 文件监控服务

**API 端点** (`app/api/endpoints/`):
- `mcp.py` - MCP (Model Context Protocol) AI 接口
- `plugin.py` - 插件管理
- `subscribe.py` - 订阅管理
- `download.py` - 下载管理
- `transfer.py` - 文件转移
- `site.py` - 站点管理
- `tmdb.py`/`douban.py` - 媒体元数据

### Frontend Structure

```
src/
├── @core/           # 核心组件和工具
├── @layouts/        # 布局组件
├── api/             # API 客户端
├── components/      # Vue 组件
├── composables/     # 组合式 API
├── pages/           # 页面组件
├── router/          # 路由配置
├── stores/          # Pinia 状态管理
└── views/           # 视图组件
```

## Commands

### Backend
```bash
# 启动服务
python -m app.main

# 开发模式（自动重载）
# 设置 config/app.env: DEV=true
python -m app.main
```

### Frontend
```bash
# 开发服务器
yarn dev

# 构建生产版本
yarn build

# 预览构建结果
yarn preview

# 代码检查
yarn lint
```

## MCP (Model Context Protocol)

MoviePilot 实现了标准 MCP 协议，允许 AI 智能体调用工具：

**端点**: `/api/v1/mcp`
**认证**: Header `X-API-KEY` 或 Query `apikey`

**支持方法**:
- `tools/list` - 获取可用工具列表
- `tools/call` - 调用工具

**主要工具**:
- `add_subscribe` - 添加媒体订阅
- `search_torrents` - 搜索种子
- `query_library_exists` - 查询媒体库是否存在
- `run_workflow` - 执行工作流
- `send_message` - 发送消息

## Database Migrations

使用 Alembic 管理数据库迁移：
```bash
# 生成新迁移
alembic revision --autogenerate -m "description"

# 应用迁移
alembic upgrade head
```

迁移文件位于 `database/versions/`

## Plugin Development

插件位于 `app/plugins/` 目录，每个插件是一个独立 Python 模块。

**插件基类**: 继承 `PluginBase`
**插件配置**: 使用 `PluginConfigModel`

参考：https://wiki.movie-pilot.org/zh/plugindev

## Environment Variables

关键环境变量（在 `config/app.env` 中配置）:
- `API_TOKEN` - API 认证密钥
- `SUPERUSER` / `SUPERUSER_PASSWORD` - 管理员账号
- `DB_TYPE` - 数据库类型
- `DB_POSTGRESQL_*` - PostgreSQL 连接配置
- `CACHE_BACKEND_TYPE` - 缓存类型 (cachetools/redis)
- `PROXY_HOST` - 代理服务器地址

## Frontend Code Locations

**通知渠道配置界面**:
- `MoviePilot-Frontend/src/views/setting/AccountSettingNotification.vue` - 通知设置页面，添加新渠道的菜单项在此文件的 VMenu 中
- `MoviePilot-Frontend/src/components/cards/NotificationChannelCard.vue` - 通知渠道卡片组件，包含：
  - `notificationTypeNames` 字典 - 定义各渠道类型名称
  - `getIcon` 计算属性 - 定义各渠道图标映射
  - 各渠道配置表单 - 按 `type` 区分不同配置字段
  - 测试发送消息功能 - 测试按钮和对话框

**添加新通知渠道时需修改**:
1. 后端 `app/schemas/types.py` - MessageChannel 枚举添加新渠道
2. 后端 `app/schemas/message.py` - NotificationSwitch 添加开关、ChannelCapabilityManager 添加能力配置
3. 后端 `app/modules/{channel}/` - 创建渠道模块
4. 前端 `AccountSettingNotification.vue` - VMenu 添加渠道选项
5. 前端 `NotificationChannelCard.vue` - 添加类型名称、图标、配置表单

## Test Notification API

测试通知发送 API 端点：`POST /api/v1/system/notification/test`

**请求参数**:
```json
{
  "channel": "通知渠道名称",
  "title": "消息标题",
  "content": "消息内容"
}
```

**前端实现**:
- 在通知渠道配置卡片中点击「测试发送」按钮
- 输入测试消息标题和内容
- 点击「发送」即可测试该通知渠道是否正常工作

## Feishu Bot Configuration Guide

### 如何配置飞书机器人

#### 方式一：Webhook 模式（简单，推荐）

1. **创建飞书机器人**
   - 打开飞书，进入要添加机器人的群聊
   - 点击右上角「...」或「设置」
   - 选择「群机器人」→「添加机器人」
   - 选择「自定义机器人」
   - 填写机器人名称，点击「添加」

2. **获取 Webhook URL**
   - 创建成功后，复制 Webhook 地址
   - 格式：`https://open.feishu.cn/open-apis/bot/v2/hook/xxx`

3. **配置签名密钥（可选，推荐开启）**
   - 在机器人设置页面，开启「签名校验」
   - 复制签名密钥（SECRET）
   - 用于消息加签验证，提高安全性

4. **在 MoviePilot 中配置**
   - 进入「设置」→「通知设置」
   - 点击「+」添加通知渠道
   - 选择「飞书」
   - 填写：
     - **名称**：自定义，如「飞书通知」
     - **飞书机器人 Webhook URL**：复制的 Webhook 地址
     - **签名密钥**：复制的 SECRET（可选但推荐）
     - **App ID / App Secret**：Webhook 模式无需填写

#### 方式二：事件订阅模式（高级，支持双向交互）

1. **创建飞书应用**
   - 访问 [飞书开放平台](https://open.feishu.cn/)
   - 登录企业飞书账号
   - 进入「应用开发」→「企业内部开发」
   - 创建新应用，填写应用名称

2. **获取 App ID 和 App Secret**
   - 在应用详情页「凭证与基础信息」
   - 复制 App ID 和 App Secret

3. **配置事件订阅**
   - 进入「事件订阅」页面
   - 开启事件订阅功能
   - 配置请求地址（需要外网可访问）：
     ```
     https://your-domain.com/api/v1/message/?source={name}
     ```
   - 订阅事件：
     - `im.message.receive_v1` - 接收消息
     - `im.message.reply_v1` - 接收回复

4. **配置机器人能力**
   - 进入「机器人」页面
   - 配置机器人头像、名称
   - 开启「单聊」、「群聊」模式

5. **在 MoviePilot 中配置**
   - 填写：
     - **App ID**：应用的 App ID
     - **App Secret**：应用的 App Secret
     - **Webhook URL**：可选，用于备用发送

#### 方式三：长连接模式（推荐，使用官方 SDK）

飞书开放平台提供官方 Python SDK 支持长连接（WebSocket）方式接收事件，无需配置公网回调地址。

**项目已集成官方 lark-oapi SDK，自动安装依赖。**

1. **创建飞书应用**
   - 访问 [飞书开放平台](https://open.feishu.cn/)
   - 登录企业飞书账号
   - 进入「应用开发」→「企业内部开发」
   - 创建新应用，填写应用名称

2. **获取 App ID 和 App Secret**
   - 在应用详情页「凭证与基础信息」
   - 复制 App ID 和 App Secret

3. **配置事件订阅**
   - 进入「事件订阅」页面
   - 订阅事件：
     - `im.message.receive_v1` - 接收消息
     - `interactive_card.action` - 按钮回调
   - **无需配置回调地址，官方 SDK 自动建立长连接**

4. **配置机器人能力**
   - 进入「机器人」页面
   - 配置机器人头像、名称
   - 开启「单聊」模式

5. **在 MoviePilot 中配置**
   - 进入「设置」→「通知设置」
   - 点击「+」添加通知渠道，选择「飞书」
   - 选择「SDK 模式（推荐）」
   - 填写：
     - **名称**：自定义，如「飞书单聊机器人」
     - **App ID**：应用的 App ID（必填）
     - **App Secret**：应用的 App Secret（必填）
     - **默认通知用户 Open ID**：用户的 open_id（必填，用于测试消息和主动通知）
     - **飞书长连接 WebSocket URL**：无需配置，官方 SDK 自动获取

**SDK 模式特点**：
- 使用官方 lark-oapi SDK，自动建立长连接
- 无需公网域名，无需配置回调地址
- 自动鉴权，事件推送为明文数据，无需解密和验签
- 支持自动重连，连接失败后自动重试
- 需要配置接收者的 `open_id` 才能发送主动消息
- 推荐用于个人使用的单聊场景

**如何获取用户的 open_id**：
1. 方法一：发送消息自动获取（推荐）
   - 用户给机器人发送任意消息
   - 系统自动记录用户的 open_id
   - 发送「绑定默认通知用户」命令可设为默认通知用户
2. 方法二：通过飞书 API 查询
   - 调用 `GET https://open.feishu.cn/open-apis/contact/v3/users/me` 接口
   - 使用用户的 access_token 访问，返回数据中包含 `data.user.open_id`
3. 方法三：通过飞书后台查询
   - 进入企业通讯录，查看用户详情中的 open_id

**SDK 使用参考**：
- 官方 SDK GitHub：https://github.com/larksuite/oapi-sdk-python
- SDK Demo 示例：https://github.com/larksuite/oapi-sdk-python-demo
- 服务端 API 文档：https://open.feishu.cn/document/server-docs/server-side-sdk
- 长连接文档：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/event-callback
- 发送消息 API：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create

**SDK 代码示例**（官方推荐方式）：
```python
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.ws.client import Client as WsClient
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.core.enum import LogLevel

# 创建事件处理器
event_handler = EventDispatcherHandler.builder("", "").build()

# 注册事件处理器
def do_im_message_receive_v1(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    print(f"收到消息：{data}")

event_handler.register_p2_im_message_receive_v1(do_im_message_receive_v1)

# 创建长连接客户端
cli = WsClient(
    app_id="APP_ID",
    app_secret="APP_SECRET",
    event_handler=event_handler,
    log_level=LogLevel.INFO,
    auto_reconnect=True
)

# 启动长连接（阻塞主线程）
cli.start()
```

**项目中的实现**：
- `app/modules/feishu/feishu.py` - 飞书 SDK 实现
- 使用 `lark_oapi.ws.Client` 自动建立长连接
- 支持 `im.message.receive_v1` 和 `interactive_card.action` 事件
- 自动重连机制，连接断开后自动恢复

### 飞书机器人功能

- **通知发送**：支持文本、图片、链接、按钮
- **媒体列表**：发送电影/电视剧搜索结果
- **种子列表**：发送下载资源选择
- **按钮回调**：支持交互式操作（需要长连接模式）
- **测试发送**：在配置对话框中点击「测试发送」按钮即可测试

### 注意事项

- Webhook 模式不支持消息删除和编辑
- 事件订阅模式（HTTP Push）需要外网可访问的域名
- 长连接模式仅支持单聊机器人，无需公网域名
- 长连接模式需要安装 `lark-oapi` 依赖，项目 requirements.in 已自动包含

### 安装依赖

长连接模式需要安装官方 SDK 依赖，项目 `requirements.in` 已自动包含：

```bash
# 手动安装（如需要）
pip install lark-oapi -U
```

依赖说明：
- `requirements.in` 已添加 `lark-oapi>=1.5.0`
- 模块导入时自动检查 SDK 可用性，未安装时仅长连接功能不可用
- Webhook 模式不受影响，无需安装 SDK

- 飞书机器人消息发送频率限制：100 条/秒
- 建议在测试群先测试配置

### SDK 实现参考

飞书 SDK 模式实现参考官方源码：
- 官方 SDK GitHub：https://github.com/larksuite/oapi-sdk-python
- 官方 SDK 文档：https://open.feishu.cn/document/server-docs/server-side-sdk/python--sdk

**核心 API 端点**：
- Token 获取：`POST /open-apis/auth/v3/tenant_access_token/internal`
- 发送消息：`POST /open-apis/im/v1/messages`
- 回复消息：`POST /open-apis/im/v1/messages/{message_id}/reply`
- 删除消息：`DELETE /open-apis/im/v1/messages/{message_id}`
- 认证方式：`Authorization: Bearer {tenant_access_token}`

当前实现完全基于官方 lark-oapi SDK，核心逻辑与官方文档一致。
