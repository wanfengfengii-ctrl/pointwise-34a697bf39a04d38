# 清汀计量日结仓

厂区班组按表计日键报送日用量。接入员确认原报和更正后，报送员可以从固定厂区范围签发指定历史修订。监管值班员在源仓关闭后安装全量件，并核对确认修订、原始报送行和分厂总量。

## 运行

需要 Python 3.11 和标准库中的 SQLite。在项目根目录运行控制台物化的标准回归入口 `python3 -B check_project.py`。直接运行完整 unittest 套件可用 `python3 -B -m unittest discover -s tests -t .`。运行 `python3 -B demo.py` 演示正常业务。测试和演示使用独立临时库。

样例日为 2026 年 6 月 12 日。首批登记东厂 x=5、y=7、z=0 和西厂 w=4。首批重投仍取得修订 1。随后将 x 改为东厂 9 升，形成修订 2。将 x 移到西厂并改为 5 升，同时撤销 w，形成修订 3。报送员登记东厂流，签发修订 3，领取原件后关闭源仓。监管镜像安装后读到 y 和 z，日报为东厂 2 笔、7 升。晚班重开镜像并重投原件，重新核对确认号和日报。`samples/` 提供逐修订输入、冻结的全量件和独立手算日报。

## 接入与历史

- `facts.ledger.open_source(path, *, source_id=None)` 打开源仓 SQLite 连接。初次打开生成持久身份，也可以显式指定。重开沿用原身份，显式指定的异身份会被拒绝。调用方关闭连接。各写操作管理自己的事务。
- `intake.batches.accept(db, request_id, entries, *, before_commit=None)` 接受一组字典。条目形如 `{"meter":"x","day":"2026-06-12","before":null,"after":{"plant":"east","liters":5}}`，其中示例使用 JSON 表示，Python 中以 `None` 分别表示缺失的旧像或新像。日期必须是实有的 `YYYY-MM-DD`，标识保留原值且须包含非空白字符。值采用整数升数，布尔和浮点无效。每批日键唯一，条目顺序不影响重投身份。
- 首次请求用同一批前状态核对所有完整旧像，通过后提交当前事实、修订历史和回执。返回 `Receipt(request_id, revision)`。同号同内容领取首次回执，同号异内容和旧像冲突整批拒绝。显式空批和合法同值批形成新修订。
- `facts.selection.as_of(db, revision)` 返回按 `(meter, day)` 排序的 `(DayKey, Measurement)` 元组。修订 0 为空，未知修订抛出 `KeyError`。撤销和重登按各自确认修订生效。

## 签发与安装

- `shipment.streams.register_stream(db, stream_id, plants)` 以非空厂区集合登记固定流，返回 `StreamBinding(source_id, stream_id, plants)`。同号同集合返回原绑定，同号异集合抛出 `ValueError`。
- `shipment.export.issue_full(db, publication_id, stream_id, revision, *, before_commit=None)` 返回 `PublishedFull(receipt, blob)`。回执含发布号、绑定、修订和行数。发布号绑定首次签发参数，修订 0 可签发空件。`shipment.catalog.get_full(db, publication_id)` 领取原存储字节。未知流、修订或发布号抛出 `KeyError`。
- V1 件是 UTF-8 JSON。信封含 `version`、`kind`、`body` 和 `sha256`。`body` 是正文原字节的 Base64，摘要核对传输完整性。正文含完整固定流绑定、发布号、修订及全量行。签发使用确定性编码，接收校验摘要后核对正文，并按日键规范化排序。
- `mirror.ledger.open_mirror(path)` 打开独立镜像，由调用方关闭。`mirror.receive.load_full(db, blob, *, before_commit=None)` 返回确认修订。首份合法来件绑定流，更高修订整体替换明细。同修订同内容重投返回已有确认号，允许另一发布号指向同一内容。坏件、异绑定、同修订异内容及较低修订抛出 `ValueError`。
- `mirror.readback.read_mirror(db, stream_id)` 一次读事务取 `MirrorSnapshot(binding, revision, rows)`。未安装的流抛出 `KeyError`。已安装空件返回确认号与空行。

## 增量正文格式与交接

`shipment.export.export_increment(db, stream_id, low, high)` 签发 `version=2`、`kind="increment"` 的信封，`body`、`sha256` 与 V1 相同。正文为规范化 JSON：

```json
{"binding":{"plants":["east"],"source_id":"...","stream_id":"east-daily"},
 "changes":[{"after":{"liters":9,"plant":"east"},"day":"2026-06-12",
             "meter":"x","revision":2}],
 "high":3,"low":1}
```

每个 `changes` 元素表示：日键在修订 `revision` 生效后，其厂区范围内的报送像从上一确认修订的值变为 `after`；`after` 为 `null` 表示迁出报送范围或撤销。元素按 `(meter, day, revision)` 排序，同一日键可在区间内出现多次。只记录区间内报送像**曾变化**的日键：同值重报、两侧均不在厂区范围、仅范围外厂区更正都不产生记录；`(low, high]` 内没有任何变化时 `changes` 为空数组，仍确认覆盖到 `high`。签发不使用随机量，源仓追加更正后再导出同区间返回原字节。

`mirror.receive.apply_increment(db, blob, *, before_commit=None)` 先校验信封、摘要、完整流绑定、`0 <= low < high`、每个记录的修订落在 `(low, high]`、厂区在范围内、按日键与修订严格排序且无重复，再按件内 `stream_id` 查找本地绑定：

- 未初始化（含从未装过全量件）→ `ValueError`，与修订进度无关。
- 本地确认修订 `F < low` → 覆盖缺口 `ValueError`；`low <= F < high` → 从本地明细重放 `revision > F` 的记录并确认到 `high`；`F >= high` → 校验通过后返回 `F`，明细与确认号不动。
- 坏信封、异流绑定、超出区间的记录、布尔或浮点升数、非法日期、乱序或重复记录，即使 `high` 已经过去也一律 `ValueError`。

安装与全量件一样在单个写事务内同时写明细和确认号；`before_commit` 抛出时整体回滚，重开读到最后成功修订。`mirror.readback.read_mirror`、`report.reconcile.daily_report` 和 `shipment.export.issue_full` 的 V1 行为保持不变。

## 核账和失败恢复

`report.reconcile.daily_report(db, stream_id)` 返回 `(revision, totals)`，其中每个 `PlantTotal` 含厂区、笔数及总升数。零升计笔，空厂区省略，厂区按标识排序，升数采用精确整数求和。`reconcile_stream` 保留同一快照的排序原行供 `report.render.render_reconciliation` 生成 JSON 核账输出。读回或核对失败会抛出异常。

`before_commit` 可在首次接纳、首次签发或实际安装写入后、提交前抛出异常，以验证回滚。异常向调用方传播，重开后可以从最后一次成功提交继续。运行入口遇到业务异常或断言失败时使用非零退出码。
