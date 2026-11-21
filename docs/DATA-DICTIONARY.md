# 数据定义

票号与原始编号均为字符串，保留前导零。称重支持 kg、公斤与 t，统一为 kg，单价为 CNY/kg。
毛重与皮重按票号和版本配对，品类、单价、扣杂率与更正目标必须一致。计价重量=(毛重−皮重)×(1−扣杂率)，先量化到0.001kg，再乘单价，金额四舍五入到0.01CNY。
每票第一版不填写 replaces。后续版本只能替代当前生效版，分支或缺基版整票阻断结算。已支付额为各版本中最大累计已付值，待付=最终应付−累计已付；负待付表示待收回。
同编号同原值仅计一次；同编号不同原值全部隔离。空文件、全空行和仅表头分别报错。原始输入计数包括空行及重复表头，对账会分别解释。

## 校准调整

校准证据随批次输入保存（`calibration.json` 或 `POST /api/runs` 的 `calibration`/`reweighs` 参数），参与批次摘要。

- `calibration.scale` 秤台，`calibration.id` 校准记录号，`calibration.calibrated_at` 校准时间。
- `calibration.factor` 乘性校准系数，>1 表示校准前读数偏轻需上调，<1 表示偏重需下调。双次称重的恒定加性偏差在毛重−皮重时抵消，不改变净重，故用比例系数。
- `calibration.tickets` 受影响票号清单，是整段修正的适用范围。
- `reweighs[]` 现场复秤证据：`ticket`、`version`、`net_kg`。同票同版本出现不同净重即冲突。

调整结果字段：`baseline`（RE_WEIGH / CALIBRATION_FACTOR / ORIGINAL / UNRESOLVED）、`net_kg`（原净重）、`adjusted_net_kg`、`billable_kg`、`original_amount`、`adjusted_amount`、`adjustment`、`due`、`decision`（可付款/待复秤）。待复秤票的调整与调整后金额为空，不按 0 计入。

发布与恢复：`POST /api/runs/{run_id}/calibration/publish`，`POST /api/runs/{run_id}/calibration/restore/{pub_id}`，查询 `GET /api/runs/{run_id}/calibrations`。发布版本按原始数据来源串联，同来源同校准记录只调整一次，旧版本保留为已撤回。

归档封存保存逐版证据、隔离原因和运行摘要，非支付执行系统。有任意待复秤票时归档状态为“待处理”。数据使用虚构编号，不连接实际支付渠道。
