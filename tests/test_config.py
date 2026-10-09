"""配置加载契约：**dataclass 字段默认值必须是唯一的默认值来源**。

本项目踩过两次同源的坑：
  * `[network]` 段曾把 `timeout_qvix` 写死成 45 秒，导致 QVIX 反复超时；
  * `[index]` / `[output]` / `[ui]` 段同样把权重 0.40/0.35/0.25、`ema_span=3`、
    `"csv","json"`、`"utf-8-sig"` 等字面量写死。

坑的形态是一样的：用户没有配置文件时，这些硬编码**就是**实际生效的默认值，
于是修改 dataclass 字段默认值毫无效果 —— 被 `load_config` 静默覆盖回旧值。
症状是"改了默认值却不生效"，而且没有任何报错，极难发现。

## 为什么测试要用"替身类"而不是 monkeypatch.setattr

`@dataclass` 会在**装饰的那一刻**把字段默认值固化进生成的 `__init__` 签名里。
之后无论怎么改 `__dataclass_fields__[name].default`，
`IndexConfig()` 仍然返回旧值（实测：改 Field.default 后构造出来的还是 0.4）。
所以必须造一个**重新声明了哨兵默认值**的继承类，替换掉模块里的名字，
再走一遍 `load_config` —— 这才是唯一能真正验证"回退值取自 dataclass"的测法。
"""

from __future__ import annotations

import dataclasses

import pytest

from techpanic import config as cfg_mod


def _shadow(cls, **overrides):
    """造一个字段默认值被替换成哨兵的子类（保留原方法，并跟随 frozen）。"""
    annotations = {f.name: f.type for f in dataclasses.fields(cls) if f.init}
    namespace: dict = {"__annotations__": annotations}
    for f in dataclasses.fields(cls):
        if f.init:
            namespace[f.name] = dataclasses.field(default=overrides.get(f.name, f.default))
    params = {"frozen": True} if cls.__dataclass_params__.frozen else {}
    return dataclasses.dataclass(type("Shadow" + cls.__name__, (cls,), namespace), **params)


# 说明：权重那三项不能单独替换 —— IndexConfig.xcheck() 要求三者之和为 1.0，
# 单独改一个会先被 xcheck 拦下（这本身是好事）。权重单独用下一条用例测。
CASES = [
    ("IndexConfig", "ema_span", 999),
    ("IndexConfig", "rv_window", 999),
    ("IndexConfig", "rv_short", 999),
    ("IndexConfig", "annualization", 999),
    ("IndexConfig", "z_min_periods", 999),
    ("IndexConfig", "level_anchor", "full_sample"),
    ("OutputConfig", "encoding", "SENTINEL"),
    ("OutputConfig", "json_schema", "SENTINEL"),
    ("UIConfig", "lang", "SENTINEL"),
    ("UIConfig", "color", "SENTINEL"),
    ("NetworkConfig", "retries", 999),
    ("NetworkConfig", "timeout_qvix", 999.0),
    ("NetworkConfig", "cache_ttl_hours", 999.0),
]


@pytest.mark.parametrize(("cls_name", "field", "sentinel"), CASES)
def test_load_config_falls_back_to_dataclass_default(
    monkeypatch, tmp_path, cls_name, field, sentinel
):
    """没有配置文件时，`load_config` 必须回退到 dataclass 的**当前**默认值。

    把该字段的默认值换成哨兵后，如果 `load_config` 返回哨兵，
    说明它确实从 dataclass 取默认值；如果返回旧的字面量（例如 3 / 0.40），
    说明又有人把默认值写死进了 `load_config`。
    """
    real_cls = getattr(cfg_mod, cls_name)
    monkeypatch.setattr(cfg_mod, cls_name, _shadow(real_cls, **{field: sentinel}))

    cfg = cfg_mod.load_config(data_dir=tmp_path, config_path=None)
    section = {
        "IndexConfig": cfg.index,
        "OutputConfig": cfg.output,
        "UIConfig": cfg.ui,
        "NetworkConfig": cfg.network,
    }[cls_name]
    assert getattr(section, field) == sentinel, (
        f"{cls_name}.{field} 的回退值没有取自 dataclass —— "
        "load_config 里又出现硬编码默认值了"
    )


def test_frozen_anchors_fall_back_to_dataclass_default(monkeypatch, tmp_path):
    """`frozen_anchors` 的四个回退值同样要走 dataclass。"""
    monkeypatch.setattr(
        cfg_mod,
        "IndexConfig",
        _shadow(cfg_mod.IndexConfig, frozen_anchors=(11.0, 22.0, 33.0, 44.0)),
    )
    cfg = cfg_mod.load_config(data_dir=tmp_path, config_path=None)
    assert cfg.index.frozen_anchors == (11.0, 22.0, 33.0, 44.0)


def test_toml_values_still_win_over_defaults(tmp_path):
    """有配置文件时，TOML 里的值必须优先于默认值（别把修复改过头）。"""
    (tmp_path / "config.toml").write_text(
        "[index]\nema_span = 11\nrv_window = 44\n", encoding="utf-8"
    )
    cfg = cfg_mod.load_config(data_dir=tmp_path, config_path=None)
    assert cfg.index.ema_span == 11
    assert cfg.index.rv_window == 44


def test_output_and_ui_toml_values_still_win(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[output]\nencoding = \"utf-16\"\njson_schema = \"v2\"\n"
        "[ui]\nlang = \"en-GB\"\nprogress = false\n",
        encoding="utf-8",
    )
    cfg = cfg_mod.load_config(data_dir=tmp_path, config_path=None)
    assert cfg.output.encoding == "utf-16"
    assert cfg.output.json_schema == "v2"
    assert cfg.ui.lang == "en-GB"
    assert cfg.ui.progress is False


def test_weight_sentinel_zero_really_propagates(monkeypatch, tmp_path):
    """权重哨兵必须真的传到 IndexConfig；若三条被写死就抓得住。"""
    monkeypatch.setattr(
        cfg_mod,
        "IndexConfig",
        _shadow(cfg_mod.IndexConfig, weight_s=0.5, weight_a=0.3, weight_f=0.2),
    )
    cfg = cfg_mod.load_config(data_dir=tmp_path, config_path=None)
    assert (cfg.index.weight_s, cfg.index.weight_a, cfg.index.weight_f) == (0.5, 0.3, 0.2)
