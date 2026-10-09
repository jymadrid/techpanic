"""HTTP 基础设施：会话、超时、重试、退避、流式下载与进度。

设计依据（全部为实测值）：

* optbbs 的 QVIX 宽表约 914 KB，实测单次耗时在 1.7 秒到 123 秒之间剧烈抖动，
  并有多次读超时。因此 QVIX 走**流式下载 + 字节进度 + 长读超时**，
  而不是 akshare 的 \u0060pd.read_csv(url)\u0060（同进程实测 8.28s vs requests 2.09s）。
* 指数接口（新浪）实测 0.07~0.84 秒，稳定，用短超时 + 快速重试。
* 退避带 ±30% 抖动，避免多线程同时重试造成脉冲。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from ..errors import NetworkError

ProgressFn = Callable[[int, int | None, float], None]

# 允许的「实收字节 / 声明字节」比例下限。低于它就认为响应被截断。
MIN_COMPLETE_RATIO = 0.999


def _check_truncated(content: bytes, total: int | None, label: str) -> None:
    """检测响应是否被截断。

    为什么必须做：QVIX 上游是第三方小站，实测会出现连接中途断掉、
    只返回一部分的情况。如果不比对字节数，**截断的 CSV 会被静默接受**，
    于是最新几天的数据悄悄消失，而程序还会把「下载不全」报告成
    「上游未发布，滞后 N 个交易日」—— 把我们的故障说成上游的行为。

    只比对 Content-Length：没有该响应头时无从判断，只能放行
    （上游常见 chunked 编码，此时不应误报）。
    """
    if not total:
        return
    ratio = len(content) / total
    if ratio < MIN_COMPLETE_RATIO:
        raise NetworkError(
            f"响应被截断：{label} 只收到 {len(content)} 字节，"
            f"上游声明 {total} 字节（{ratio:.1%}）。已丢弃这次响应并重试。"
        )


@dataclass
class FetchResponse:
    content: bytes
    status_code: int
    elapsed: float
    url: str

    def text(self, encoding: str = "utf-8") -> str:
        return self.content.decode(encoding, errors="replace")


class HttpClient:
    """带重试与退避的 HTTP 客户端。"""

    def __init__(
        self,
        *,
        timeout_connect: float = 5.0,
        timeout_read: float = 15.0,
        retries: int = 3,
        backoff: tuple[float, ...] = (0.5, 1.5, 4.0),
        jitter: float = 0.30,
        proxy: str | None = None,
        user_agent: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.timeout = (timeout_connect, timeout_read)
        self.retries = max(1, retries)
        self.backoff = backoff or (0.5, 1.5, 4.0)
        self.jitter = jitter
        self.session = requests.Session()
        if user_agent:
            self.session.headers["User-Agent"] = user_agent
        if headers:
            self.session.headers.update(headers)
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

    # ------------------------------------------------------------ 内部
    def _sleep(self, attempt: int) -> None:
        base = self.backoff[min(attempt, len(self.backoff) - 1)]
        time.sleep(base * (1.0 + random.uniform(-self.jitter, self.jitter)))

    def _attempts(self, retries: int):
        for i in range(retries):
            if i:
                self._sleep(i - 1)
            yield i

    # ------------------------------------------------------------ 公开
    def get_bytes(
        self,
        url: str,
        *,
        retries: int | None = None,
        timeout: tuple[float, float] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        progress: ProgressFn | None = None,
        progress_label: str = "",
        total_budget: float | None = None,
    ) -> FetchResponse:
        """下载到内存（支持流式进度）。失败按退避重试，最后抛 NetworkError。

        total_budget：本次调用的**总**时间上限（秒）。上游长尾严重时（实测 QVIX
        单次 1.7s~123s），没有总预算就会出现「3 次重试 × 120s = 6 分钟以上」，
        用户会以为程序卡死。超预算立即停止重试。
        """
        n = self.retries if retries is None else max(1, retries)
        base_to = timeout or self.timeout
        last: Exception | None = None
        budget_started = time.time()

        for _ in self._attempts(n):
            remaining = None
            if total_budget is not None:
                remaining = total_budget - (time.time() - budget_started)
                if remaining <= 1.0:
                    raise NetworkError(
                        f"下载超时（已用 {time.time() - budget_started:.0f} 秒，"
                        f"超过 {total_budget:.0f} 秒预算）：{progress_label or url}"
                    )
            # 每次尝试的读超时不得超过剩余预算，避免「两次 150 秒」突破总预算。
            # 注意不能加 max(..., 5.0) 之类的下限：剩余预算不足 5 秒时那样会
            # 反过来突破总预算（实测 3 秒预算跑出 5 秒）。
            read_to = base_to[1] if remaining is None else min(base_to[1], remaining)
            to = (min(base_to[0], read_to), max(read_to, 0.1))
            started = time.time()
            try:
                resp = self.session.get(
                    url, timeout=to, headers=headers, params=params, stream=progress is not None
                )
                # 5xx 与 429/408 都是**可重试**的上游故障。
                # 4xx 以前被当作成功返回，于是 403/429 限流时既不重试也不报错，
                # 调用方拿到一个空 body 还以为是正常响应。
                if resp.status_code >= 500 or resp.status_code in (408, 429):
                    raise NetworkError(f"上游返回 {resp.status_code}")
                if resp.status_code >= 400:
                    raise NetworkError(
                        f"上游返回 {resp.status_code}（该状态码通常不可通过重试解决）"
                    )

                total = int(resp.headers.get("Content-Length") or 0) or None
                if progress is None:
                    content = resp.content
                    _check_truncated(content, total, progress_label or url)
                    return FetchResponse(content, resp.status_code, time.time() - started, url)

                chunks: list[bytes] = []
                got = 0
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    got += len(chunk)
                    progress(got, total, time.time() - started)
                content = b"".join(chunks)
                _check_truncated(content, total, progress_label or url)
                return FetchResponse(content, resp.status_code, time.time() - started, url)
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise NetworkError(f"下载失败（已重试 {n} 次）：{progress_label or url}\n   最后错误：{last}")

    def get_text(
        self,
        url: str,
        *,
        encoding: str = "utf-8",
        **kwargs: Any,
    ) -> str:
        return self.get_bytes(url, **kwargs).text(encoding)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
