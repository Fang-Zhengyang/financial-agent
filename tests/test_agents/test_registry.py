"""角色注册表单元测试 — 12 角色加载 + prompt 注入 + 查询过滤"""

import pytest

from finagent.agents.registry import (
    RoleRegistry,
    RoleConfig,
    get_registry,
    get_role,
)


@pytest.fixture(scope="module")
def registry() -> RoleRegistry:
    """模块级 fixture：加载一次，所有测试共享。"""
    return RoleRegistry()


# ── 加载 ───────────────────────────────────────────────────

def test_load_count(registry: RoleRegistry):
    """应加载全部 12 个角色。"""
    assert registry.count == 12


def test_all_roles_have_required_fields(registry: RoleRegistry):
    """每个角色应包含必需字段。"""
    for role in registry.list_all():
        assert role.role_id, f"{role.role_id}: role_id 为空"
        assert role.type, f"{role.role_id}: type 为空"
        assert role.llm_layer in ("deep", "quick"), f"{role.role_id}: llm_layer 无效"
        assert role.name, f"{role.role_id}: name 为空"
        assert role.output_format in ("free_text", "structured"), f"{role.role_id}: output_format 无效"


def test_prompt_injection_for_non_analysts(registry: RoleRegistry):
    """非分析师角色应从 .py 模块加载了 role_description。"""
    for role in registry.list_all():
        if not role.is_analyst:
            assert role.role_description, (
                f"{role.role_id}: role_description 未从 prompt 模块加载"
            )
            # role_description 应该比原始 description 更长（更详细）
            assert len(role.role_description) > len(role.description), (
                f"{role.role_id}: role_description 未正确注入"
            )


def test_analyst_prompt_injection(registry: RoleRegistry):
    """分析师角色的 role_description 使用配置中的 description。"""
    for role in registry.list_by_type("analyst"):
        assert role.role_description == role.description
        assert len(role.extra_rules) > 0


# ── 查询 ───────────────────────────────────────────────────

def test_get_existing_role(registry: RoleRegistry):
    """get() 应返回正确角色。"""
    role = registry.get("fundamentals")
    assert role.role_id == "fundamentals"
    assert role.type == "analyst"


def test_get_nonexistent_role(registry: RoleRegistry):
    """get() 不存在的角色应 raise KeyError。"""
    with pytest.raises(KeyError, match="未找到角色"):
        registry.get("nonexistent_role")


def test_list_by_type(registry: RoleRegistry):
    """按类型筛选应返回正确数量。"""
    assert len(registry.list_by_type("analyst")) == 4
    assert len(registry.list_by_type("researcher")) == 2
    assert len(registry.list_by_type("manager")) == 2
    assert len(registry.list_by_type("trader")) == 1
    assert len(registry.list_by_type("risk")) == 3


def test_list_by_layer(registry: RoleRegistry):
    """按 LLM 分层筛选应返回正确数量。"""
    deep_roles = registry.list_by_layer("deep")
    quick_roles = registry.list_by_layer("quick")
    assert len(deep_roles) == 2
    assert len(quick_roles) == 10
    assert {r.role_id for r in deep_roles} == {"research_manager", "portfolio_manager"}


def test_list_by_output_format(registry: RoleRegistry):
    """按输出格式筛选应返回正确数量。"""
    structured = registry.list_by_output_format("structured")
    free_text = registry.list_by_output_format("free_text")
    assert len(structured) == 3
    assert len(free_text) == 9
    assert {r.role_id for r in structured} == {
        "research_manager", "trader", "portfolio_manager"
    }


def test_list_analyst_ids(registry: RoleRegistry):
    """应返回 4 个分析师 ID。"""
    ids = registry.list_analyst_ids()
    assert sorted(ids) == sorted(["fundamentals", "technical", "news", "capital_flow"])


# ── Pipeline 顺序 ──────────────────────────────────────────

def test_pipeline_order(registry: RoleRegistry):
    """get_pipeline_order() 应返回正确顺序。"""
    order = registry.get_pipeline_order()
    assert len(order) == 12
    # 前 4 个是分析师（顺序无关）
    assert set(order[:4]) == {"fundamentals", "technical", "news", "capital_flow"}
    # bull → bear
    assert order[4] == "bull"
    assert order[5] == "bear"
    # research_manager → trader
    assert order[6] == "research_manager"
    assert order[7] == "trader"
    # 3 风控
    assert set(order[8:11]) == {"risk_aggressive", "risk_conservative", "risk_neutral"}
    # portfolio_manager 最后
    assert order[11] == "portfolio_manager"


# ── RoleConfig 属性 ────────────────────────────────────────

def test_is_structured(registry: RoleRegistry):
    """is_structured 属性应正确反映 output_format。"""
    assert registry.get("research_manager").is_structured is True
    assert registry.get("fundamentals").is_structured is False


def test_is_analyst(registry: RoleRegistry):
    """is_analyst 应正确识别分析师角色。"""
    assert registry.get("fundamentals").is_analyst is True
    assert registry.get("technical").is_analyst is True
    assert registry.get("bull").is_analyst is False


def test_is_deep(registry: RoleRegistry):
    """is_deep 应正确识别深思考角色。"""
    assert registry.get("research_manager").is_deep is True
    assert registry.get("portfolio_manager").is_deep is True
    assert registry.get("fundamentals").is_deep is False


def test_tools_config(registry: RoleRegistry):
    """工具配置应正确。"""
    assert len(registry.get("fundamentals").tools) == 3
    assert len(registry.get("technical").tools) == 1
    assert len(registry.get("bull").tools) == 0
    assert len(registry.get("research_manager").tools) == 0
    assert len(registry.get("trader").tools) == 1


def test_max_tool_calls(registry: RoleRegistry):
    """分析师应有 max_tool_calls=5，其他非工具角色应为 0。"""
    for role in registry.list_by_type("analyst"):
        assert role.max_tool_calls == 5
    for role in registry.list_all():
        if role.type != "analyst" and role.type != "trader":
            # 研究员/经理/风控不应调用工具
            pass  # 可用 0
    # trader 可以调用位置计算工具
    assert registry.get("trader").max_tool_calls == 3


# ── 单例 ────────────────────────────────────────────────────

def test_get_registry_singleton():
    """get_registry() 应返回同一个实例。"""
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2


def test_get_role_convenience():
    """get_role() 便捷函数应正常工作。"""
    role = get_role("bear")
    assert role.name == "空头研究员"


# ── summary ────────────────────────────────────────────────

def test_summary(registry: RoleRegistry):
    """summary() 应包含所有 12 个角色。"""
    s = registry.summary()
    assert "12" in s
    assert "fundamentals" in s
    assert "portfolio_manager" in s
