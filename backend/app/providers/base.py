from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SellerCandidate:
    """发现阶段产出的候选卖家。"""

    storefront_url: str | None
    brand: str | None = None
    seller_id: str | None = None
    source_query: str | None = None
    raw: dict[str, Any] | None = field(default=None, repr=False)


class DataSourceProvider(ABC):
    """数据源 provider 抽象：隔离具体抓取实现（Apify / httpx / 第三方 API）。

    每个 Actor 单独一个 adapter，schema 变更只改 adapter，不污染上层。
    """

    @abstractmethod
    async def discover_sellers(self, category: str, market: str) -> list[SellerCandidate]:
        """按品类+市场发现候选卖家。"""
