from __future__ import annotations

import asyncio
import datetime

import aiohttp
import jwt

from astrbot.api.star import Context, Star

_GLOBAL_TASK: asyncio.Task | None = None
_GCP_PLUGIN_ID = "astrbot_plugin_group_chat_plus"


def _is_peak(now: datetime.datetime) -> bool:
    """DeepSeek 高峰：周一至周五 9:00-11:59 与 14:00-17:59（北京时间）。

    服务器时区已设为 Asia/Shanghai，直接使用本地时间。
    法定节假日未做精确判断，按低谷处理（符合官方“其余时段为空闲”）。
    """
    if now.weekday() >= 5:  # 周六、周日
        return False
    h, m = now.hour, now.minute
    minutes = h * 60 + m
    return (9 * 60 <= minutes < 12 * 60) or (14 * 60 <= minutes < 18 * 60)


class PriceDynamic(Star):
    name = "price_dynamic"
    author = "local"
    desc = "根据 DeepSeek 峰谷定价动态调整主动回复概率与压缩轮数"
    version = "1.0.0"

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self._last_state: str | None = None

    async def initialize(self) -> None:
        global _GLOBAL_TASK
        if _GLOBAL_TASK is not None and not _GLOBAL_TASK.done():
            self.logger.info("price_dynamic loop already running, skip.")
            return
        self.logger.info("price_dynamic loaded, starting loop.")
        _GLOBAL_TASK = asyncio.create_task(self._loop())

    async def terminate(self) -> None:
        global _GLOBAL_TASK
        if _GLOBAL_TASK is not None:
            _GLOBAL_TASK.cancel()
            _GLOBAL_TASK = None

    # ---------------- 内部工具 ----------------

    def _cfg(self, key: str, default):
        return self.config.get(key, default)

    def _jwt_secret(self) -> str:
        ab = self.context.get_config()
        return ab["dashboard"]["jwt_secret"]

    def _dashboard_base(self) -> str:
        ab = self.context.get_config()
        port = ab["dashboard"]["port"]
        return f"http://127.0.0.1:{port}"

    def _make_token(self) -> str:
        payload = {
            "username": self.context.get_config()["dashboard"]["username"],
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=7),
        }
        return jwt.encode(payload, self._jwt_secret(), algorithm="HS256")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._make_token()}",
            "Content-Type": "application/json",
        }

    # ---------------- 主循环 ----------------

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa
                self.logger.exception("price_dynamic tick error: %s", e)
            interval = int(self._cfg("check_interval_seconds", 60))
            await asyncio.sleep(max(5, interval))

    async def _tick(self) -> None:
        if not self._cfg("enabled", False):
            if self._last_state != "disabled":
                self.logger.info("price_dynamic disabled, no action.")
                self._last_state = "disabled"
            return

        now = datetime.datetime.now()
        peak = _is_peak(now)
        state = "peak" if peak else "valley"
        target_prob = self._cfg("peak_probability", 0.05) if peak else self._cfg("valley_probability", 0.35)
        target_turns = self._cfg("peak_max_turns", 10) if peak else self._cfg("valley_max_turns", 20)

        if self._last_state == state:
            return

        self.logger.info(
            "price_dynamic period=%s -> apply prob=%.3f max_turns=%s",
            state, target_prob, target_turns,
        )
        try:
            await self._apply_gcp_probability(target_prob)
            await self._apply_max_turns(target_turns)
            self._last_state = state
            self.logger.info("price_dynamic applied %s successfully.", state)
        except Exception as e:  # noqa
            self.logger.exception("price_dynamic apply failed: %s", e)
            # 不更新 last_state，下个周期重试

    # ---------------- 实际写入 ----------------

    async def _apply_gcp_probability(self, prob: float) -> None:
        base = self._dashboard_base()
        headers = self._headers()
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{base}/api/v1/plugins/config",
                params={"plugin_id": _GCP_PLUGIN_ID},
                headers=headers,
            ) as r:
                r.raise_for_status()
                data = await r.json()
            cfg = data.get("data", {}).get("config")
            if cfg is None:
                # 部分版本直接返回 config
                cfg = data.get("data") if isinstance(data.get("data"), dict) else data
            cfg["after_reply_probability"] = prob
            async with s.put(
                f"{base}/api/v1/plugins/config",
                json={"plugin_id": _GCP_PLUGIN_ID, "config": cfg},
                headers=headers,
            ) as r:
                r.raise_for_status()
                self.logger.info("gcp after_reply_probability -> %.3f", prob)

    async def _apply_max_turns(self, max_turns: int) -> None:
        base = self._dashboard_base()
        headers = self._headers()
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{base}/api/v1/config-profiles/default",
                headers=headers,
            ) as r:
                r.raise_for_status()
                data = await r.json()
            cfg = data["data"]["config"]
            cfg["agent_runner"]["config"]["compression"]["max_turns"] = max_turns
            async with s.put(
                f"{base}/api/v1/config-profiles/default",
                json=cfg,
                headers=headers,
            ) as r:
                r.raise_for_status()
                self.logger.info("compression.max_turns -> %s", max_turns)
