# M16 私有版本登记

## 结论

M16 已形成可在个人电脑上运行的只读实时观察候选版本：Massive REST 轮询、默认 15 分钟刷新、本地 SSE、独立实时温度计页面和 Asia/Shanghai 展示时区均已接通。

本登记的状态是 **`BASELINE_PROMOTION_REQUIRED`**。这表示候选版本已经通过当前治理 Trusted baseline 下的本地与 GitHub Actions 验证，但还没有被提升为新的 Trusted baseline，也不是 Product baseline。旧版本不被覆盖。

机器可读登记见 [`M16_PRIVATE_RELEASE_MANIFEST.json`](M16_PRIVATE_RELEASE_MANIFEST.json)。

## 版本与回滚

| 项目 | 值 |
|---|---|
| 候选分支 | `codex/m16-realtime-observation` |
| 实现提交 | `09078f9ce06b9903f9b3c7bc59334a034b41c0d5` |
| 私有候选 ref | `m16-private-release-candidate`（解析到最终登记提交） |
| M16 验证 Trusted ref | `verification-baseline-v3.26` |
| 回滚 ref | `verification-baseline-v3.26` |
| M15 保留 ref | `verification-baseline-v3.21` |
| 发布范围 | 私有个人使用、只读行情观察 |

回滚时停止 M16 进程，切回 `verification-baseline-v3.26`；不删除候选分支、配置或外部 Evidence。M15/v3.21 与 `frontend/demo/` 等原有页面继续保持独立。

## 运行边界

- 数据源为 Massive，只使用市场数据接口，不使用账户、订单或券商接口。
- 配置文件是 [`configs/realtime/massive.json`](../../configs/realtime/massive.json)；默认 `refresh_interval_seconds` 为 `900`，需要调整刷新频率时只修改该版本化非秘密配置并重新验证。
- API key 只从当前进程的 `MASSIVE_API_KEY` 环境变量读取，不写入配置、URL、HTML、日志或 Git。
- 页面和 SSE 只绑定 `127.0.0.1`；不绑定 `0.0.0.0`，不做公网部署。
- 页面使用 `Asia/Shanghai`（UTC+8）显示；市场交易时段按 `America/New_York` 解释。
- 盘中观察是临时、可失效的 `PROVISIONAL` 数据，不能改变既有确认状态、目标权重或 paper ledger。
- `MASSIVE_API_KEY` 未设置、未授权、标的不存在、限流、过期、时间异常、缺字段或解析失败时，页面必须显示数据质量问题，不把旧值伪装成有效确认值。

PowerShell 启动示例：

```powershell
$env:MASSIVE_API_KEY = "<只在本机进程环境设置>"
python -m src.realtime --config configs/realtime/massive.json --host 127.0.0.1 --port 8766
```

然后打开 `http://127.0.0.1:8766/`。停止终端中的 Python 进程即可停止轮询和页面服务。

## 验证 Evidence

本地受控验证：

- `18/18` acceptance gates 通过；开发测试 `137`，独立测试 `11`，合计 `162`；失败 `0`，跳过 `0`。
- Run ID：`verify-20260818T031104Z-347d68c3ea24`。
- Evidence：`../m16.4-local-evidence-09078f9/evidence.json`。

GitHub Actions 独立验证：

- Run ID：`32094664145`；`18/18` acceptance gates 通过；开发测试 `137`，独立测试 `11`，合计 `162`；失败 `0`，跳过 `0`。
- Evidence：`../m16.4-ci-evidence-32094664145/qqq-verification-evidence-09078f9ce06b9903f9b3c7bc59334a034b41c0d5/evidence.json`。

上述路径指向工作区旁的 Evidence archive，而不是仓库内的临时生成物。哈希、候选提交和 Trusted ref 均记录在机器可读 manifest 中。

## 明确不包含的能力

M16 不会修改 M04–M07 的确认逻辑，不把盘中数据直接变成交易信号，不新增真实交易、券商下单、转账、自动调仓、公共部署、多用户权限或生产级高可用。

如果后续要把实时观察与确认状态、paper ledger 或新的策略版本连接，必须另开任务、另建版本、另跑回测/回放和验证，不能在本登记上直接追加。
