# 展商目录浏览器读取器

启动本机虚拟目录，既支持读取首屏可见行，也支持通过真实无头浏览器对虚拟滚动目录做完整采集，并保存原始观察与可解释的采集进度。

## 运行

在 Python 3.11 或更新版本的 Linux 环境使用。原始内容保存在 `output/raw.sqlite3`，采集进度与发布台账保存在 `output/raw-progress.sqlite3`，删除 `output/` 可回到干净状态。全部示例使用本地合成数据，不包含账号凭据或真实企业资料。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 app.py demo                 # 原有单屏查看能力，保持不变
python3 app.py collect              # 全量目录采集（可中断、可续采）
python3 app.py progress             # 列出采集作业
python3 showcase.py                 # 真实浏览器端到端演示（节点复用/同名/插入/重启/挂起）
```

`app.py demo` 直接执行单屏读取路径并输出结构化摘要，不重放历史日志；正常执行不到 30 秒。`--output` 可以指定另一份原文库，进度库自动放在同目录、同名加 `-progress` 后缀的 SQLite 文件中。

## 全量采集命令

```sh
python3 app.py collect [--job-id ID] [--max-attempts 3] [--stop-after N]
                       [--url http://host:port] [--mutate]
python3 app.py progress [--job-id ID]
```

- 数据只来自浏览器实际渲染的目录 DOM 与详情弹层：不读取模拟站点内部数组，也不调用 `/seed`、`/advance` 数据接口。
- 每个逻辑行以「页面代际 `data-revision` + 逻辑行号 `data-row-index` + 记录主键 `data-reference`」定位。同名展商 `reference` 不同，视为不同主体；固定分组行在 `#viewport` 之外，不会被计入。
- 虚拟滚动会回收 DOM 节点，详情弹层关闭后原节点可能已代表另一条记录。采集器每次点击前重新滚动定位、以点击时刻按钮上的 `data-reference` 为准并核对弹层归属，不缓存旧节点。
- 滚动等待全部使用 `wait_for_function` 观察实际渲染状态，没有固定睡眠。
- **到尾判定需要同时满足**：滚动条到底、内容高度等于声明总行高、逻辑行 `0..N-1` 全覆盖、尾屏两次读取一致、代际与声明行数全程未变。仅一屏重复或滚动条到底不算完整。证据随结果中的 `end_evidence` 返回。
- **代际变化不拼接**：遍历中检测到代际变化，当前尝试立即作废（`aborted/revision-changed`），在 `--max-attempts` 上界内按新代际整代重采；预算耗尽则作业挂起（退出码 2），给出未完成原因，不产出名单。
- **可恢复续采**：`--stop-after N` 可模拟浏览器/进程在 N 个逻辑行后关闭（退出码 3），进度已落盘；用同一 `--job-id` 再次 `collect` 会重开浏览器并重新验证页面代际——代际一致则续采，已发布详情按 `reference` 台账跳过、不重复发布；代际已变则旧尝试标记 `abandoned/resume-revision-mismatch`，在预算内按新代际重采，绝不跨代际拼接。
- `--url` 指向外部已启动的目录页面；缺省时由命令自行启动本机 portal，`--mutate` 等价于页面参数 `?mutate=1`，在滚动若干次后模拟一次插入。
- 退出码：`0` 完成（含挂起摘要以外的正常结束）、`2` 挂起、`3` 中断待续采。

## 模块与数据

- `portal.py` 管理本机页面与目录代际，`fixtures/exhibitors.json` 提供虚构展商。
- `browser_source.py` 保留首屏读取入口 `first_screen`。
- `collector.py` 是浏览器全量采集器：滚动遍历、详情校验、代际守卫、到尾证据、有限重试与续采。
- `progress_store.py` 持久化作业、按代际隔离的尝试、行观察与发布台账；台账按 `(job_id, reference)` 去重，因此同名企业不会被合并，恢复后已发布详情不重复。
- `showcase.py` 用真实无头浏览器分段演示节点复用、同名主体、插入变更、中途重启与挂起原因。

`raw_store.py` 将原始字节按 SHA-256 保存，用来源和字节摘要区分观察。它只保存原文（首屏观察与每条详情弹层原文），不表达业务结果是否完整。同一内容重复观察保留一份对象并更新观察时间，SQLite 事务保证对象和观察同时提交。所有解析异常保持非零退出码，输入原件不改写。

## 环境

`requirements.txt` 登记精确依赖。环境准备阶段安装依赖，运行、构建及测试不执行下载。正式运行可以使用依赖准备后记录的镜像 ID；镜像和验证回执保存在项目外，不影响原始内容。

浏览器需要与 Playwright 1.56.0 配套的 Chromium。依赖环境中已预装浏览器及系统库；环境准备可执行 `python3 -m playwright install --with-deps chromium`，这一步不放在应用启动或测试里。`python3 portal.py` 会打印动态本机地址，页面参数 `?mutate=1` 会在若干次滚动后模拟一次插入。

需要固定监听地址时，运行 `python3 portal.py --host 127.0.0.1 --port 8765` 并访问打印的地址。页面和 `/seed`、`/advance`、`/portal.js` 由同一个 Python 服务提供，不能用静态文件服务器替代。按 Ctrl+C 停止服务。

提供 npm 的环境也可用 `npm start -- --host 127.0.0.1 --port 8765` 启动同一服务，`npm run test:browser` 执行真实无头浏览器的首屏、节点复用和来源变更测试，`npm run test:collector` 执行全量采集与进度测试，`npm run showcase` 运行端到端演示。`package-lock.json` 不包含任何 npm 依赖；npm 仅作为统一命令入口，不改变 Python API、演示或读取行为。完整测试包含固定端口启动入口的回归验证，仍使用上面的 unittest 命令。

也可以先用 `docker build -t exhibitor-reader .` 构建已锁定基础镜像和依赖的应用容器，再运行 `docker run --rm --network none exhibitor-reader`。测试命令为 `docker run --rm --network none exhibitor-reader python3 -m unittest discover -s tests -v`。构建阶段需要取镜像与依赖，运行阶段不下载内容。
