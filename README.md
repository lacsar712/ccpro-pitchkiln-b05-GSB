# PitchKiln-01 · 灶台值守看板

Django 5 + PostgreSQL：灶台瓦片看板 + 右侧抽屉探针时间线，无 Vue/React SPA。

## 技术栈

- Django 5、PostgreSQL
- Session 登录
- HTMX：局部刷新灶台网格与抽屉
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4710** |
| Postgres | **6110**（容器内 5432） |

数据库账号：`pitchkiln` / `pitchkiln` / 库名 `pitchkiln`

## 快速启动

```bash
cd PitchKiln/PitchKiln-01
docker compose up --build -d
```

浏览器打开：http://localhost:4710

演示账号：

- `admin` / `123456`（超级用户）
- `worker` / `123456`（普通用户）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 6110，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6110
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4710
```

## 业务模型

1. **ResinLot（来脂批）**：`lotCode`、`originPlace`、`arrivalKg`、`receivedAt`
2. **FireHearth（灶台）**：`lane`、`tag`（唯一）、`resinGrade`、相位 `cold|charging|ramping|holding|drawing`、灶允许窗 `cookWindowStart` / `cookWindowEnd`（每日允许开灶时段，关窗时刻早于开窗时刻表示跨午夜）
3. **CookRun（熬制值守）**：归属灶台与来脂批、`openedAt`、`closedAt`（可空）、`targetSoftPointC`
4. **SoftPointProbe（软化点探针）**：归属值守、`sampledAt`、`softPointC`、`samplerName`

## 业务规则

### 出胶探针前置

将灶台相位切到 `drawing`（出胶）时，进行中（最晚开灶）的 CookRun 必须至少有一条 SoftPointProbe 的 `softPointC ≤ 95`。

### 开灶三重联锁

开灶（**新建值守**与**修改开灶时刻**走同一个校验入口 `validate_run_opening`，模型 `CookRun.clean()` 统一拦截，表单 / 视图 / admin 无法绕过其中任一条）：

1. **灶允许窗**：开灶时刻按本地钟点必须落在该灶 `[cookWindowStart, cookWindowEnd)` 半开区间内；关窗时刻早于开窗时刻时为跨午夜窗（如 18:00–06:00），起止相同视为全天开窗。
2. **相位联锁**：**冷灶禁止新建值守**；只有装料 / 升温 / 保温 / 出胶四个相位可开新值守。冷灶须先手动把相位切到装料，再开灶（开灶不再自动拨相位）。
3. **同灶未收灶值守顺序**（见下「何谓乱序」）。

已收灶（`closedAt` 非空）的历史值守不参与窗以外的顺序联锁。出胶灶仍可随时补探针；补探针不改变任何顺序，但改开灶时刻与新建受完全相同的约束。收灶时若灶上仍有其它未收灶值守，相位保持不变；最后一条值守收完才回冷灶。

### 何谓乱序

同灶的未收灶值守存在一条公认先后：**按 `openedAt` 升序排列的结果**。按灶过滤出未收灶值守后，这个排序必须始终唯一、可复算对齐（因此开灶时刻并列也被禁止）。乱序指任何一次写入会改变该先后：

- **新建乱序**：同灶已存在开灶更晚的未收灶值守时，新值守的开灶时刻晚于（或并列于）当前最晚者。合法的新建只能插在最晚值守之前，越早越好。
- **更新乱序**：把一条值守的开灶时刻挪到任意一条**其它**未收灶值守的另一侧——既包括把更早的值守挪过更晚者（跑到它之后），也包括把更晚的值守挪回更早者之前（越过它）。更新只允许在相邻两条值守夹出的时间槽内移动；与任何值守并列同样拒绝。

只挡新建而放行更新乱序、或冷灶仍能开灶，均视为联锁失效。

逻辑在 `apps/kiln/services/floor_rules.py`，由模型 `clean()`、表单与相位切换入口共同调用。

## 界面

- 首页：**灶台值守看板** — 左侧班次条 + 按过道排布的灶台瓦片；点瓦片打开右侧抽屉（值守、探针时间线、改相位 / 登记探针 / 开灶）
- 次页：**来脂批** — 卡片时间线，非宽表 CRUD

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有灶台则只保证账号存在。样例地名仅用「松脂坳 / 桐油坑」系。

## 目录结构

```
PitchKiln-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/
  apps/kiln/          # 模型、视图、floor_rules、种子
  templates/floor/    # 值守看板 + 抽屉
  templates/resin/    # 来脂批时间线
  static/css/         # 值守台 ops-console 样式
```
