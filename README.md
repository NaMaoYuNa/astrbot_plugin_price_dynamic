# price_dynamic — DeepSeek 峰谷动态调度

AstrBot 插件：根据 DeepSeek 官方峰谷定价时段，自动切换群聊主动回复概率与上下文压缩触发轮数，在高峰时段压低 token 开销、低谷时段放开体验。

## 原理

DeepSeek 错峰定价（北京时间）：

- **高峰**：周一至周五 9:00–12:00、14:00–18:00（标准价）
- **低谷**：其余时段（含周末、节假日全天，约半价）

插件每 `check_interval_seconds` 秒检查一次当前时段，状态切换时通过 AstrBot 面板 API 写入：

| 配置项 | 高峰 | 低谷 |
|---|---|---|
| `group_chat_plus` → `after_reply_probability` | `peak_probability`（默认 0.05） | `valley_probability`（默认 0.35） |
| `agent_runner.config.compression.max_turns` | `peak_max_turns`（默认 10） | `valley_max_turns`（默认 20，-1 = 不压缩） |

状态未变化时不写配置（避免无意义的热重载）；写入失败下个周期自动重试。

## 安装

1. 将本目录打包为 zip，在 AstrBot 面板 → 插件 → 上传安装；
2. 插件配置中打开 `enabled` 总开关（**默认关闭**）。

## 依赖

- AstrBot ≥ 4.29（低版本未测试）
- 可选：配合 [astrbot_plugin_group_chat_plus](https://github.com/NickCharlie/astrbot_plugin_group_chat_plus) 使用主动回复概率调度；没有装该插件时概率写入会报错，但 max_turns 调度不受影响
- Python 依赖：`aiohttp`、`PyJWT`（均为 AstrBot 自带依赖）

## 配置项

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | false | 总开关 |
| `check_interval_seconds` | int | 60 | 时段检查间隔 |
| `peak_probability` | float | 0.05 | 高峰主动回复概率 |
| `valley_probability` | float | 0.35 | 低谷主动回复概率 |
| `peak_max_turns` | int | 10 | 高峰压缩触发轮数 |
| `valley_max_turns` | int | 20 | 低谷压缩触发轮数，-1 不压缩 |

## 实现要点

- 通过面板 `jwt_secret` 自签发 JWT 调用本地面板 API，无需额外凭据；
- 模块级全局任务锁，避免上传/热重载导致后台循环重复启动；
- 法定节假日未做精确判断，统一按低谷处理（符合官方"其余时段为空闲"的计费规则）。
