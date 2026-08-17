"""Unified DataProvider interface for all data-source adapters.

Every adapter (akshare / eastmoney push2 / baostock) implements this ABC.
Return None when the source does not support a given data type so the
fallback chain can try the next adapter.
"""

from abc import ABC, abstractmethod
from typing import Optional

from finagent.data.schemas import (
    AnnouncementData,
    CapitalFlow,
    FinancialIndicators,
    KlineData,
    MarginTrading,
    NewsData,
    RealTimeQuote,
    STRiskData,
    TradeCalendar,
    ValuationData,
)


class DataProvider(ABC):
    """Unified data provider interface.  All adapters MUST implement this."""

    # Each method returns Optional[Pydantic Model].
    # Return None = this source does not have this data → fallback chain continues.

    @abstractmethod
    def get_kline(
        self,
        code: str,
        period: str = "day",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[KlineData]:
        """日K线（前复权）。对应 Spec D1。"""
        ...

    @abstractmethod
    def get_realtime_quote(self, code: str) -> Optional[RealTimeQuote]:
        """实时行情快照（现价/涨跌停价/量比）。对应 Spec D2。"""
        ...

    @abstractmethod
    def get_capital_flow(self, code: str) -> Optional[CapitalFlow]:
        """主力资金流（近5/10/20日净流入，超大单/大单/中单/小单）。
        对应 Spec D3。"""
        ...

    @abstractmethod
    def get_margin_trading(self, code: str) -> Optional[MarginTrading]:
        """融资融券（融资余额/融券余额/融资买入额/融券卖出量）。
        对应 Spec D4。"""
        ...

    @abstractmethod
    def get_financials(self, code: str) -> Optional[FinancialIndicators]:
        """财务指标（ROE / 营收净利同比 / 毛利率 / 负债率 / EPS）。
        对应 Spec D5。"""
        ...

    @abstractmethod
    def get_valuation(self, code: str) -> Optional[ValuationData]:
        """估值数据（PE / PB / 股息率 / 总市值）。对应 Spec D6。"""
        ...

    @abstractmethod
    def get_news(self, code: str, limit: int = 20) -> Optional[NewsData]:
        """新闻（标题/发布时间/来源/正文摘要）。对应 Spec D7。"""
        ...

    @abstractmethod
    def get_announcements(
        self, code: str, limit: int = 20
    ) -> Optional[AnnouncementData]:
        """公告（标题/日期/类型）。对应 Spec D8。"""
        ...

    @abstractmethod
    def get_st_risk(self, code: str) -> Optional[STRiskData]:
        """ST / 风险标记（证券简称、上市状态）。对应 Spec D9。"""
        ...

    @abstractmethod
    def get_trade_calendar(
        self, year: Optional[int] = None,
    ) -> Optional[TradeCalendar]:
        """交易日历。对应 Spec D10。"""
        ...

    # ── 扩展数据种类（阶段Ⅱ新增，默认 None → 走降级链）───────────
    #
    # 这 5 类数据为「可选」数据面，仅 akshare 适配器实现；其余适配器
    # 继承此处的默认实现（返回 None），表示「该源不支持此数据」，
    # 降级链会继续尝试下一个源，不会因缺方法而崩溃。

    def get_lhb(self, code: str):
        """D11 龙虎榜（近 30 日个股上榜记录）。默认 None。"""
        return None

    def get_jiejin(self, code: str):
        """D12 限售解禁（未来 3 个月解禁计划）。默认 None。"""
        return None

    def get_holder(self, code: str):
        """D13 股东户数（最新 + 环比变化）。默认 None。"""
        return None

    def get_north(self, code: str):
        """D14 北向资金（近 10 日沪深港通持股变化）。默认 None。"""
        return None

    def get_pe_percentile(self, code: str):
        """D15 行业 PE 分位（估值相对位置）。默认 None。"""
        return None

    def get_dazong(self, code: str):
        """D16 大宗交易（近 30 日个股大宗交易明细）。默认 None。"""
        return None

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源名称（如 'akshare'），用于日志和降级链。"""
        ...
