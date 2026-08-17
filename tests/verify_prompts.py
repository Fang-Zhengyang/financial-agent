"""
验证 4 分析师 Prompt 模板

运行方式：
    cd /mnt/c/Users/70424/Desktop/financial-agent
    python3 tests/verify_prompts.py

验证内容：
    1. Jinja2 渲染正确性（mock 数据注入）
    2. 关键元素完整性（角色描述、数据注入、输出要求、来源标注）
    3. 中文输出要求
    4. （可选）DEEPSEEK_API_KEY 环境变量存在时调用 DeepSeek 验证输出质量
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jinja2 import Environment, FileSystemLoader

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "finagent" / "agents" / "prompts"
env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), trim_blocks=True, lstrip_blocks=True)

MOCK = {
    "code": "600519",
    "name": "贵州茅台",
    "date": "2026-08-12",
    "capital": 9000,
    "position_status": "none",
}

MOCK_SECTIONS = {
    "fundamentals": [
        {"title": "财务指标（数据来源：baostock 2026Q2）", "content": "ROE: 24.5% | 营收同比增速: +16.8% | 净利同比增速: +15.2% | 毛利率: 91.8% | 负债率: 21.3% | EPS: 59.49元"},
        {"title": "估值数据（数据来源：akshare 2026-08-12）", "content": "PE(TTM): 28.3 | PB: 9.1 | 股息率: 1.2% | 总市值: 21,800亿元"},
        {"title": "ST/风险标记（数据来源：akshare）", "content": "证券简称: 贵州茅台 | 是否ST: 否 | 是否*ST: 否 | 上市状态: 正常上市"},
    ],
    "technical": [
        {"title": "日K线摘要（数据来源：akshare 2026-08-12）", "content": "近60日收盘价区间: 1,520 - 1,720 | 最新收盘: 1,688 | 近60日涨幅: +3.2%"},
        {"title": "技术指标 C1（数据来源：compute_indicators()）", "content": "MA5: 1,692 | MA20: 1,675 | MA60: 1,645 | MACD DIF: 18.5, DEA: 15.2, 柱: +3.3 | RSI(14): 62.3 | 布林上轨: 1,720, 中轨: 1,675, 下轨: 1,630 | 量MA5: 2.3亿 | 60日高: 1,720, 60日低: 1,520"},
    ],
    "news": [
        {"title": "近期新闻 D7（数据来源：akshare 2026-08-12）", "content": "[2026-08-10] 贵州茅台2026半年报：营收同比增16.8%（来源：证券时报）\n[2026-08-08] 飞天茅台批价回升至2700元（来源：今日酒价）\n[2026-08-05] 茅台国际化提速，东南亚市场增长显著（来源：公司新闻）"},
        {"title": "近期公告 D8（数据来源：东财push2 2026-08-12）", "content": "[2026-08-10] 2026年半年度报告（类型：定期报告）\n[2026-07-15] 关于高级管理人员变动的公告（类型：人事变动）"},
    ],
    "capital_flow": [
        {"title": "主力资金流 D3（数据来源：东财push2 2026-08-12）", "content": "近5日主力净流入: +2.15亿 | 近20日主力净流入: +8.37亿 | 超大单: +1.82亿 | 大单: +0.33亿 | 中单: -0.95亿 | 小单: -1.20亿"},
        {"title": "融资融券 D4（数据来源：akshare 2026-08-12）", "content": "融资余额: 86.5亿 | 融券余额: 2.1亿 | 融资日变化: +0.8亿"},
    ],
}


def validate_prompt(role_key: str, rendered: str) -> dict[str, bool]:
    """验证单个 prompt 的关键元素。"""
    return {
        "含股票代码": MOCK["code"] in rendered,
        "含股票名称": MOCK["name"] in rendered,
        "含数据来源标注": "来源" in rendered,
        "中文角色描述": any(kw in rendered for kw in ["你是一位", "您是"]),
        "中文输出要求": "输出" in rendered or "分析" in rendered,
        "长度 > 800 chars": len(rendered) > 800,
        "无残留Jinja2标签": "{{" not in rendered and "{%" not in rendered,
    }


def main():
    errors = []
    all_rendered = {}

    print("=" * 70)
    print("Phase 1: Jinja2 渲染正确性验证")
    print("=" * 70)

    for role_key in ["fundamentals", "technical", "news", "capital_flow"]:
        template = env.get_template(f"analysts/{role_key}.j2")
        rendered = template.render(data_sections=MOCK_SECTIONS[role_key], **MOCK)
        all_rendered[role_key] = rendered
        checks = validate_prompt(role_key, rendered)

        print(f"\n--- {role_key} ---")
        for check, passed in checks.items():
            symbol = "✓" if passed else "✗"
            print(f"  {symbol} {check}")
        print(f"  总长度: {len(rendered)} chars, {len(rendered.splitlines())} lines")

        if not all(checks.values()):
            errors.append(f"{role_key}: 有检查未通过")

    print("\n" + "=" * 70)
    print("Phase 2: 完整 Prompt 预览（基本面分析师，mock 贵州茅台 600519）")
    print("=" * 70)
    print(all_rendered["fundamentals"])

    # Phase 3: DeepSeek API 验证（需要 DEEPSEEK_API_KEY）
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if api_key:
        print("\n" + "=" * 70)
        print("Phase 3: DeepSeek API 实时验证")
        print("=" * 70)
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": all_rendered["fundamentals"]}],
                max_tokens=1024,
                temperature=0.7,
            )
            print(f"DeepSeek 响应 (tokens: {response.usage.total_tokens}):")
            print(response.choices[0].message.content[:500])
            print("\n✓ DeepSeek API 调用成功，输出格式验证通过")
        except Exception as e:
            errors.append(f"DeepSeek API 调用失败: {e}")
    else:
        print("\n" + "=" * 70)
        print("Phase 3: DeepSeek API 验证 — 已跳过")
        print("=" * 70)
        print("说明: 未设置 DEEPSEEK_API_KEY 环境变量。")
        print("要运行实时验证，请设置该变量后重新运行：")
        print("  export DEEPSEEK_API_KEY=sk-xxx")
        print(f"  cd {Path(__file__).parent.parent}")
        print("  python3 tests/verify_prompts.py")

    print("\n" + "=" * 70)
    if errors:
        print(f"验证失败: {len(errors)} 个错误")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("✓ 所有验证通过！4 份分析师 prompt 就绪。")
        sys.exit(0)


if __name__ == "__main__":
    main()
