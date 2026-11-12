# 展商目录浏览器读取器

启动本机虚拟目录、读取首屏可见行并保存原始观察。

## 运行

在 Python 3.11 或更新版本的 Linux 环境使用。原始内容保存在 `output/raw.sqlite3`，删除 `output/` 可回到干净状态。全部示例使用本地合成数据，不包含账号凭据或真实企业资料。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 app.py demo
```

`app.py demo` 直接执行当前读取路径并输出结构化摘要，不重放历史日志；正常执行不到 30 秒。`--output` 可以指定另一份原文库。

## 模块与数据

portal.py 管理本机页面与目录代际，fixtures/exhibitors.json 提供虚构展商。

`raw_store.py` 将原始字节按 SHA-256 保存，用来源和字节摘要区分观察。它只保存原文，不表达业务结果是否完整。同一内容重复观察保留一份对象并更新观察时间，SQLite 事务保证对象和观察同时提交。所有解析异常保持非零退出码，输入原件不改写。

## 环境

`requirements.txt` 登记精确依赖。环境准备阶段安装依赖，运行、构建及测试不执行下载。正式运行可以使用依赖准备后记录的镜像 ID；镜像和验证回执保存在项目外，不影响原始内容。

浏览器需要与 Playwright 1.56.0 配套的 Chromium。依赖环境中已预装浏览器及系统库；环境准备可执行 `python3 -m playwright install --with-deps chromium`，这一步不放在应用启动或测试里。`python3 portal.py` 会打印动态本机地址，页面参数 `?mutate=1` 会在若干次滚动后模拟一次插入。

也可以先用 `docker build -t exhibitor-reader .` 构建已锁定基础镜像和依赖的应用容器，再运行 `docker run --rm --network none exhibitor-reader`。测试命令为 `docker run --rm --network none exhibitor-reader python3 -m unittest discover -s tests -v`。构建阶段需要取镜像与依赖，运行阶段不下载内容。
