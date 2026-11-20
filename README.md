# 栖木印刷配对试验簿

同批纸只测部分工艺组合，试印报告给出带来源的工艺差值区间。应用登记报告、冻结一版数据与目标工艺对，由 Python worker 推断两道工艺的差值范围并给出最少剔除方案。原生 HTML/CSS/JavaScript 多模块工作席，所有计算由 Python 完成。

## 模拟业务
栖木研究协作组，12名分析师与业务人员，日常手工抄写 `paired-press-202611-1.csv` 与试印报告。当前痛点：区间来源不可追溯、互相矛盾的区间手工剔除易错、结果与复核割裂。本应用接管数据登记、方法执行和输入冻结；下游负责人形成复测/预算方案，观察员获取复核后的CSV。

核心业务实体：PressStudy / TrialReport / PairedRun / Review。源码 model/schema/service/controller/route 与前端 api/store/pages 完成链路，不依赖浏览器本地假数据。

## 功能与方法
- 试印批次、试印报告录入/编辑/删除；报告必须带来源编号、两道工艺和差值区间 [下界, 上界]；重名来源与非法数值明确拒绝。修改时携带版本号，版本不一致即拒绝覆盖。
- 每份报告给出 ΔE(工艺B) − ΔE(工艺A) 的区间，等价于两条差分约束。全部报告相容当且仅当约束图无负环；目标工艺对的最紧上界是两点间最短路，最紧下界是反向最短路取负。
- 只接受“剔除报告数量最少且剩余区间相容”的方案。结果页给出覆盖**全部最优方案**的最紧上下界，以及下界、上界两端各自的剔除清单；相关系数 `schemes` 列出全部最少剔除方案及各自区间。任一最优方案中目标工艺对断开即整体报无界。
- 每次推断最多 20 份报告，第 21 份拒绝排队；满载求解硬上限 30 秒。
- 每次运行冻结当前一版全部报告（含版本号）和目标工艺对，并生成 SHA256。排队中的并发改录不影响已排队任务；输入修改不会改写历史结果，旧结果照常可查。
- 输入校验失败（空批次、缺来源、下界>上界、非有限数、超 20 份等）在发布前拒绝，任务不进入队列。
- 负责人复核不可覆盖；审计记录登录、数据改动、排队、复核与导出；240请求/分钟限流、全局可恢复错误。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18242 ，API http://localhost:19242/docs 。数据库映射 17742。无需Node构建。可以从中文和带空格目录启动。清理 `docker compose down -v --remove-orphans` 会删除本题演示数据卷。

|角色|账号|密码|权限与典型动作|
|---|---|---|---|
|admin 负责人|admin@demo.local|Research2026!|全部读写、批准复核、下载|
|analyst 分析师|analyst@demo.local|Research2026!|数据修改、创建任务、读取结果及下载；不能复核|
|viewer 观察员|viewer@demo.local|Research2026!|只读；仅可导出已复核结果|

## 一天的工作剧本
1. 09:00 admin登录，查“栖木·米白纸套印”。
2. 09:10 analyst核对试印报告的来源编号与差值区间。
3. 09:15 来源录入页录入 `原工艺色差 ΔE` 对应的工艺对与区间，必要时编辑（带版本号）。
4. 09:20 分析执行页选择目标工艺对并命名本轮研究。
5. 09:21 冻结输入并排队，worker实算，结果页自动刷新。
6. 09:25 阅读覆盖全部方案的最紧上下界、两端剔除清单、全部最少剔除方案与SHA256；发现失败先修数据重跑，旧结果仍可查。
7. 09:35 admin打开业务复核，写明限制与下一步动作，归档后不可覆盖。
8. 09:40 viewer读取复核意见并下载CSV，在操作留痕页核对下载事件。

## 技术与目录
|部分|实现|
|---|---|
|前端|原生ES modules、HTML/CSS、Nginx1.27，无前端框架|
|API|Python3.11、FastAPI0.115、SQLAlchemy2、JWT、PBKDF2密码|
|计算|纯Python标准库（`decimal` 精确区间运算），无 NumPy/SciPy 依赖|
|数据库|PostgreSQL16|
|任务|Redis7持久队列，Python独立worker，心跳healthcheck|

`frontend/src/`分api、stores、types、components/common、hooks、pages、router、utils、constants；`backend/src/`分config、models、schemas、services、controllers、routes、middlewares、utils、constants；`backend/scripts/seed_demo.py`自然键插入、重复不覆盖修改；`backend/migrations/README.md`说明schema版本。

环境变量 `.env.example`：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO。开发时在本地venv安装backend/requirements.txt，设置DATABASE_URL、REDIS_URL、JWT_SECRET再运行uvicorn；前端开发经Nginx代理 `/api/` 到不带前缀的API。

共享枚举：角色admin/analyst/viewer和队列queued/running/completed/failed；后端`src/constants/enums.py`、认证与worker使用相同值；前端`src/types/enums.js`、状态与角色界面使用相同值。Google Fonts：Noto Serif SC + IBM Plex Mono；断网使用serif/monospace回退。

## 算法输入、失败态与复现
输入为选定批次当前一版全部报告（来源、工艺A、工艺B、上/下界、版本）和目标工艺对。参数经后端严格白名单验证（本方法无可调参数）。空批次、缺来源、工艺相同、下界大于上界、非有限数、超过 20 份均拒绝；拒绝即不排队。固定输入 checksum 相同可复现。

独立 oracle：小样本穷举全部剔除子集逐一核对；存在“删不同报告会得出不同边界”的小样本（见 `backend/tests/test_algorithm.py`）；目标工艺对断开即无界。

已知边界：小型本地研究应用，使用合成数据，不含企业SSO与外发邮件；结果由人工复核，不自动执行业务决策；单worker恢复queued/running任务，数据库与Redis间未采用分布式事务。以 `backend/tests/` 记录实际验证结果（`test_algorithm.py` 为纯标准库验算，`test_domain_flow.py` 需本地安装 `fakeredis`/`httpx`）。

MIT License。
