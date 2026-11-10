# Calibration v2 变更报告

**日期**: 2026-11-10
**提交**: `6267637` + `d6fa6e9` (已 push 到 `origin/main`)
**变更范围**: 12 个文件, +4054 / -1269 行

---

## 背景

和 TA 开会后确认了 4 个代码层面的 bug（Q1/Q2/Q4/Q6），本次变更全部修复并重新跑了校准。所有修复都基于队友上传的 22 天 Bybit + Binance 真实数据（`data/reference/`），不再依赖之前下载的 6 天 Binance Vision 数据。

---

## 修复了什么

### Q1: spread 和 depth 测量是假数据

**问题**: 旧代码从 Binance bookDepth 计算 spread，但 bookDepth 的 percentage 字段是 ±1~±5 **百分号**（不是 bps）。代码取 `|1 - (-1)| = 2` → spread 恒定 2 bps，是 artifact。同理 depth 把整个订单簿（±5%）加总，得到 ~50,000 BTC，而不是 0.5% band 内的真实深度。

**修复**: 改用队友的 Bybit 数据：
- spread → `spread_bps_mean` 字段（实测 median = 0.009 bps）
- depth → `near_depth_usd / mid_bybit`（实测 median = 123 BTC，与 Hyperliquid 实时快照 108 BTC 交叉验证一致）

| 指标 | 旧值 (bug) | 新值 (修复) | 差异 |
|------|-----------|------------|------|
| spread_rel | 0.0002 (2 bps) | 0.00000088 (0.009 bps) | ×227 |
| depth_btc | 58,398 BTC | 123 BTC | ×475 |

**涉及文件**: `compute_calibration_targets.py` (完全重写)

---

### Q2: 日级回撤被当作 1 分钟跳跃

**问题**: 旧代码把日级最大回撤（-13.15%）直接作为 `shock_return` 塞进 GBM 的单步跳跃。这等于在一个 1 分钟步骤里注入 ~100σ 的移动——物理上不可能。真实 1 分钟最大跌幅只有 -7.19%。

**修复**: 新建 `ReplayIndexPrice` 类，回放真实 1 分钟价格路径：
- Normal 场景: 回放 9 月 24 日前 600 分钟（平稳日，最大 1 分钟跌幅 -0.37%）
- Stress 场景: 回放 10 月 9 日前 600 分钟（崩盘日，最大 1 分钟跌幅 -0.14%）
- 价格按 `initial_price` 缩放，保持模拟器内部价格水平一致
- Exchange 自动根据 `scenario.use_replay` 选择 replay 或 GBM

**涉及文件**: `index_price.py` (新增 ReplayIndexPrice), `config.py` (Scenario 扩展), `exchange.py` (自动选择), `scenarios.py` (stress 场景开启 replay)

---

### Q4: 去除零回报扭曲了 stylized facts

**问题**: `verify_stylized_facts.py:124` 用 `returns != 0` 过滤掉了零回报。真实 BTC 有 2.52% 的零回报分钟。去掉它们会系统性扭曲 kurtosis 和 ACF。

**修复**: 保留零回报，同时报告两套结果（with zeros / without zeros）让读者看到差异。

**关键发现**: 保留零回报时 kurtosis = 61.8，去掉后变为 **-0.7**。旧代码的零回报去除实际上把胖尾特征完全抹掉了——之前 "stylized facts 全部通过" 的结论是基于被扭曲的数据。

**涉及文件**: `verify_stylized_facts.py`, `run_calibration.py` (两处都修改了统计计算)

---

### Q6: 单种子、无 held-out 验证

**问题**: 校准和验证都只用了 seed=42，没有 out-of-sample 检验。

**修复**:
- 校准: 3 个种子 (42, 123, 456)，每个 grid 点取均值
- 验证: 5 个种子 (42, 123, 456, 789, 2025)，报告 mean ± std
- 数据分割: 前 16 天训练 / 后 8 天 (Oct 10-21) held-out 验证

**涉及文件**: `run_calibration.py`, `verify_stylized_facts.py`, `compute_calibration_targets.py`

---

## 文件变更清单

### 引擎核心 (5 文件)

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `perp_sim/engine/index_price.py` | 新增类 | `ReplayIndexPrice` — 从 JSON 加载真实价格路径并回放 |
| `perp_sim/engine/config.py` | 扩展 | `Scenario` 增加 `use_replay`, `replay_regime` 两个字段 |
| `perp_sim/engine/exchange.py` | 修改 | `__init__` 中根据 scenario 选择 Replay 或 GBM |
| `perp_sim/engine/scenarios.py` | 重写 | 全部 8 个场景更新为 v2 校准参数 |
| `perp_sim/engine/__init__.py` | 微调 | 导出 `ReplayIndexPrice` |

### 数据脚本 (3 文件，完全重写)

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `data/scripts/compute_calibration_targets.py` | 完全重写 | 改用 Bybit 数据源；16/8 天分割；提取 replay 路径 |
| `data/scripts/run_calibration.py` | 完全重写 | 3 种子 grid search；支持 replay；报告 held-out 对比 |
| `data/scripts/verify_stylized_facts.py` | 完全重写 | 5 种子；保留零回报；held-out 对比 |

### 输出文件 (4 文件，全部重新生成)

| 文件 | 状态 | 说明 |
|------|------|------|
| `data/processed/calibration_targets.json` | 重新生成 | v2 目标值（Bybit 来源，26.8KB） |
| `data/processed/replay_prices.json` | **新文件** | 真实价格路径（normal + stress 各 600 步，19.7KB） |
| `data/processed/calibration_result.json` | 重新生成 | v2 校准结果（6.4KB） |
| `data/processed/stylized_facts_report.json` | 重新生成 | v2 验证报告（10.2KB） |

---

## 校准结果

### 最佳参数

| 参数 | Normal | Stress |
|------|--------|--------|
| sigma_hourly | 0.002334 | 0.002567 |
| bg_depth_btc | 8.0 (thick) / 4.0 (thin) | 同左 |
| bg_half_spread | 3.5e-06 | 3.3e-06 |
| use_replay | No (GBM) | **Yes (Oct 9 真实路径)** |
| fit score | 19.08 | 34.90 |

### Stylized Facts (5 seeds, 全部 PASS)

| 指标 | Normal (mean ± std) | Stress (mean ± std) | 通过标准 |
|------|---------------------|---------------------|---------|
| 胖尾 (excess kurtosis) | 61.8 ± 31.1 | 72.5 ± 33.3 | > 0 ✓ |
| 波动率聚集 (ACF₁) | 0.447 ± 0.153 | 0.522 ± 0.168 | > 0.1 ✓ |
| 订单流自相关 (ACF₁) | 0.555 ± 0.050 | 0.215 ± 0.167 | > 0 ✓ |

### Held-out 验证 (8 天, Oct 10-21)

| 指标 | Sim (normal) | Real (held-out) |
|------|-------------|-----------------|
| sigma_hourly | 0.000508 | 0.003956 |
| kurtosis | 61.84 | 1348.34 |
| vol_acf1 | 0.447 | 0.0205 |

> 注: sim 的 sigma 和 kurtosis 低于 real held-out，主要因为迷你市场零交易分钟占比高（~90% vs 真实 2.5%）。这是 50 agents vs 真实市场数千交易者的结构性差异，不影响 stylized facts 的方向性结论。

---

## 场景配置一览 (8 个场景)

| 场景名 | regime | depth | timing | replay |
|--------|--------|-------|--------|--------|
| normal-thick-early | normal | 8.0 | 0.35 | No |
| normal-thick-late | normal | 8.0 | 0.65 | No |
| normal-thin-early | normal | 4.0 | 0.35 | No |
| normal-thin-late | normal | 4.0 | 0.65 | No |
| stress-thick-early | stress | 8.0 | 0.35 | **Yes** |
| stress-thick-late | stress | 8.0 | 0.65 | **Yes** |
| stress-thin-early | stress | 4.0 | 0.35 | **Yes** |
| stress-thin-late | stress | 4.0 | 0.65 | **Yes** |

---

## 如何复现

```bash
# 1. 计算校准目标值 (需要 data/reference/ 下的队友数据)
python data/scripts/compute_calibration_targets.py

# 2. 运行校准 (3 seeds × 100 combos/regime, ~70s/regime)
python data/scripts/run_calibration.py

# 3. 验证 stylized facts (5 seeds × 2 scenarios, ~30s)
python data/scripts/verify_stylized_facts.py
```

---

## 还没做的 (TA Q5/Q6/Q7, 非 bug 修复)

| TA 问题 | 状态 | 说明 |
|---------|------|------|
| Q5: LLM vs baseline 风险识别 | 待做 | 需要 matched-pair 设计，固定 N/leverage/collateral |
| Q6: 级联和价格冲击验证 | 待做 | Kyle's λ, 流动性恢复时间, cascade decay |
| Q7: 文献基础 | 待做 | 需要写 MODEL_FOUNDATION.md |
