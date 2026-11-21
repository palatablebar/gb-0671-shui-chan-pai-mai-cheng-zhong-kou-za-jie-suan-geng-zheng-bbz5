# 东港水产拍卖结算

给水产市场结算员处理双次称重、扣杂计价、结算更正与应付复核，并在秤台校准后形成可付款调整。

八个模块：称重采信、货品换算、毛皮配对、扣杂竞价、更正裁决、校准调整、应付对账、结算归档。每个导航页均可重跑同一批数据，隔离行保留编号、原值、行号和原因码。

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

浏览器访问 http://localhost:18240 。默认载入现场脏批，包含53条逻辑输入、重复表头、缺列、空值、重复冲突、缺皮重、毛重小于皮重等问题。切换“核验清批”或“旧编码批”后重跑。进入“更正裁决”核对票00001：原应付1168.50CNY，更正后1285.35CNY，已付1168.50CNY，仅待付116.85CNY。

## 校准调整

3号秤下午校准发现偏差后，进入“校准调整”（`/calibration`）选择“校准影响批”。该批复用核验清批的称重数据，另带 `sample_data/calibration.json` 的校准记录与复秤证据。

- 有现场复秤的票按复秤净重重算，称重基准 `RE_WEIGH`。
- 无复秤但票在校准记录覆盖范围内的，按校准系数整段修正，基准 `CALIBRATION_FACTOR`。
- 校准记录未覆盖、复秤证据互相冲突、复秤版本与生效版本不符、复秤与校准推算差异过大的票，一律列入“待复秤”，不进入可付款结果；调整额留空，不当作 0 改价。
- 页面同时展示采用的称重基准、原/调整后应付、调整额与仍待复秤票。

发布：`POST /api/runs/{run_id}/calibration/publish` 把可付款调整写成发布版本；同一原始数据来源、同一校准记录只发布一次，重跑复用原版本，不重复累计调整额。新校准形成 v2 并把 v1 标为已撤回；`POST /api/runs/{run_id}/calibration/restore/{pub_id}` 恢复旧版。发布在单事务内完成，中断即整体回滚，旧版继续生效；发布只写发布记录，原始称重与成交证据保持只读。查询 `GET /api/runs/{run_id}/calibrations`。

有任意待复秤票时，结算归档状态为“待处理”。

API：各模块 `POST /api/<导航英文名>`；完整运行 `POST /api/runs`，JSON参数可用 `dataset` 或 `csv_text`，校准证据可用 `calibration`/`reweighs`。查询历史 `GET /api/runs`，复核 `POST /api/runs/{run_id}/review`，传入当前 `revision`。封存下载只有 `GET /api/export/{run_id}` 一个入口。

同输入和规则摘要生成相同批号，不新增重复记录；复核版本改变后旧版本提交返回409。SQLite保存在命名卷。可使用新卷名称重建演示数据，原卷保留。

定义见 `docs/DATA-DICTIONARY.md`，模块与测试映射见 `docs/DEPTH.md`。源码在 `app/`，业务样例在 `sample_data/`，测试在 `tests/`。不连接实际秤台和支付渠道。

采信页支持上传或粘贴CSV。完整源字节与参数随批次持久化，跨页通过run_id读取同一结果。`POST /api/runs/{run_id}/rerun` 读取该批不可变输入快照重新计算，规则未变时批号不变。切换另一示例则创建或复用另一批次。
