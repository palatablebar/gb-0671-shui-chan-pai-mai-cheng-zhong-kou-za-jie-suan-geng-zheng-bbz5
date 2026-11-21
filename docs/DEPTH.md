# 深度登记 水产拍卖结算

规模：中型。使用者：水产批发市场结算员。核心规则：先毛皮配对，再扣杂计价，按更正链选择唯一生效版，只支付差额。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 称重采信 | 查看并执行称重采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 货品换算 | 查看并执行货品换算 | `POST /api/conversion` | `app/modules/conversion/rules.py` | `tests/test_conversion.py` |
| M3 毛皮配对 | 查看并执行毛皮配对 | `POST /api/pairing` | `app/modules/pairing/rules.py` | `tests/test_pairing.py` |
| M4 扣杂竞价 | 查看并执行扣杂竞价 | `POST /api/auction` | `app/modules/auction/rules.py` | `tests/test_auction.py` |
| M5 更正裁决 | 查看并执行更正裁决 | `POST /api/correction` | `app/modules/correction/rules.py` | `tests/test_correction.py` |
| M6 校准调整 | 复核校准影响票并发布可付款调整 | `POST /api/calibration` | `app/modules/calibration/rules.py` | `tests/test_calibration.py` |
| M7 应付对账 | 查看并执行应付对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M8 结算归档 | 查看并执行结算归档 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码中文票据 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_encoding` |
| TRAP-02 | BOM表头 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_real` |
| TRAP-04 | 分段表头 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_repeated_header_real` |
| TRAP-05 | 缺列记录 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bad_columns_real` |
| TRAP-07 | 空值哨兵 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/conversion/rules.py` | `tests/test_conversion.py::test_sentinel_real` |
| TRAP-08 | 前导零票号 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_leading_zero_real` |
| TRAP-14 | 重复编号不同原值 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_duplicate_conflict_real` |
| TRAP-18 | 三类空态 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_empty_states` |
| TRAP-15 | 吨与千克混合 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/conversion/rules.py` | `tests/test_conversion.py::test_tonnes_counterexample` |
| TRAP-06 | 全角千分位 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/conversion/rules.py` | `tests/test_conversion.py::test_fullwidth_thousands` |
| TRAP-10 | 金额半分舍入 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/auction/rules.py` | `tests/test_auction.py::test_half_cent_rounding` |
| TRAP-26 | 陈旧更正分支 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/correction/rules.py` | `tests/test_correction.py::test_stale_branch` |
| TRAP-31 | 校准偏差按整段统一套用 | 未复秤票被错误改价 | 复秤优先，其次按校准系数修正，未覆盖或证据冲突保留待处理 | `app/modules/calibration/rules.py` | `tests/test_calibration.py::test_payable_excludes_uncovered` |
| TRAP-32 | 同一校准重复发布/重跑 | 调整额重复累计 | 来源+校准记录唯一，重跑复用原发布 | `app/publish.py` | `tests/test_calibration.py::test_publish_idempotent` |
| TRAP-33 | 发布中途失败 | 旧版被覆盖或证据被删 | 单事务写发布表，失败回滚旧版继续生效；原始快照只读 | `app/publish.py` | `tests/test_calibration.py::test_interrupted_publish_keeps_old_version` |
| TRAP-34 | 复秤与校准证据冲突 | 任选其一静默计价 | 差异超容差即列待复秤，不并入可付款 | `app/modules/calibration/rules.py` | `tests/test_calibration.py::test_pending_conflicting_reweigh` |

## 校准调整规则

在更正裁决后、应付对账前执行。称重基准：`RE_WEIGH`（现场复秤，优先）、`CALIBRATION_FACTOR`（校准记录覆盖范围内的乘性比例修正）、`ORIGINAL`（该批无任何校准证据，不改价）。校准未覆盖、复秤冲突、复秤版本不符、复秤与校准模型差异超容差、修正后净重为负的票一律进入待复秤，不进入可付款结果，调整额留空而非 0。

发布按原始数据来源分组形成 v1/v2 版本链：同来源+同校准记录只发布一次；新版本把旧版本标为已撤回但不删除；发布失败整体回滚，原始称重与成交证据不受影响。恢复旧版只切状态，不重算。

## 对账规则

输入=采纳原始行+隔离行+空行+同值重复行+重复表头。计价版本总额与生效票总额分别展示，避免把更正前后重复求和。最终应付=已付+待付，负待付为退款。

## 已知不覆盖

无称重设备实时接入，采用现场导出文件。无支付执行，只生成可复核结算证据。
