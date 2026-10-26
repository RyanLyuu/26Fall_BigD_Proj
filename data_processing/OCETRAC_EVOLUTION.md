# 基于 Ocetrac 的全球复合事件演化识别

## 本次实现

处理链路为：统一网格及深度 → 原生时间片异常判断 → 双／三要素逻辑交集 → Ocetrac 对象构建及追踪 → 统一事件摘要、逐片范围及谱系。

- 历史输入使用月均温度、氧和 pH，每个月独立判断异常。
- 近期输入使用日均温度、氧和 pH，每天独立判断异常。
- 默认持续条件为 **1 个时间片**，不要求连续五天，不在追踪前排除停留时间短的移动异常。
- 月均与日均可在同一次追踪中交接，事件保留统一编号或明确的父子关联。
- 单要素入口与复合入口均调用真实安装的 `ocetrac==0.1.7`。

这是一套可运行的离线计算模块。前端、服务器 API、全球数据自动下载和更新任务不包含在本次改动中。

## 使用哪个 Ocetrac 算法

| 参数 | 上游核心 | 连接规则 |
|---|---|---|
| `--tracking-mode surface` | `SurfTrack.tracker.label_3d` | 每个深度独立，在时间—纬度—经度中按 26 邻域连通 |
| `--tracking-mode volume` | `DeepTrack.build_3d_objects` + `track_objects_with_splitting` | 每片构建三维水体，通过相邻时间片空间／体积重叠追踪 |
| `--tracking-mode auto`（默认） | 单深度选 SurfTrack，多深度选 DeepTrack | 根据深度层数决定 |

SurfTrack 对应 Scannell 等（2024）的论文；DeepTrack 是上游后续新增的三维扩展，不能称为原论文的三维原样复现。

适配层不执行上游默认的形态学填孔，不采用默认删除小面积对象的百分位筛选，以免填出不存在的复合异常或删除小区域测试对象。`--min-object-cells` 显式控制每时间片对象的最小体素数，默认 1。

DeepTrack 使用 8 邻域水平连接、面相邻垂向连接。时间连接的 containment 为 `max(交集/前片对象, 交集/后片对象)`；有多深度时同时使用 CMEMS 网格面积及近似层厚构造的体积权重。`--containment-threshold` 仅控制 volume 模式，默认 0，比较采用上游的严格大于。

## 安装与快速验证

需要 Python 3.10 或更新版本。在项目根目录运行：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r data_processing/requirements.txt
.venv/bin/python -B -m data_processing.demo_evolution
```

当前工作区已创建 `.venv` 并安装依赖，可直接执行最后一条。

演示构造一份**全球粗网格合成数据**：跨日期变更线的三维异常水体，包含 2024 年 6、7 月两个月均时间片，以及 8 月 1—7 日七个日均时间片。它不是实际海洋事件，也不是实际全球 CMEMS 数据。

预期得到 3 个单要素事件和 4 个复合事件（三种双要素＋一种三要素）；每个连续事件共有 9 个原生时间片，其时间支持区间覆盖 68 天。月均仍是两片，不会展开成 61 个日观测。结果写入 `data/processed/ocetrac_evolution_demo/`。

## 真实数据输入

输入需已通过预处理，使用四个一维坐标 `time/depth/latitude/longitude`，变量为 `thetao/o2/ph`，单位依次为 `degrees_C/mmol m-3/1`。月均和日均分别准备，再交给演化入口；不要把六个不同频率、不同年代的源文件合成一条伪连续日序列。

四个数据产品均支持全球海洋。复合分析可使用 BGC 的 0.25°网格，温度降到共同网格；不同产品的原生深度层也需统一。预处理支持通过 `--depths` 选择实际研究水层，默认仍为原项目的 1—50 m，不能把默认值理解为全水深。

下面的输入路径是待准备的标准化数据路径：

```bash
.venv/bin/python -B -m data_processing.evolution \
  --historical-input data/prepared/historical_monthly.nc \
  --recent-input data/prepared/recent_daily.nc \
  --historical-baseline data/prepared/monthly_reference.nc \
  --recent-baseline data/prepared/daily_reference.nc \
  --threshold-mode seasonal_percentile \
  --tracking-mode auto \
  --output data/processed/global_evolution
```

历史月均温度对应 GLORYS12，月均氧和 pH 对应多年 BGC；近期日均温度对应物理分析预报，日均氧和 pH 对应 BGC 分析预报。

温度高于 P90、氧及 pH 低于 P10。当前季节阈值按日历月分组，但月均和日均参考样本**分别计算**，不会把月均与日均样本混在一起求分位数，也不会用月均参考数据冒充日均参考。`--baseline-start/--baseline-end` 可限制参考时段。没有单独参考文件时使用各尺度本次输入，质量报告会提示；短样本适合流程验证，不能替代研究基准。

跨产品均值／方差偏差尚未校正，应利用重叠期验证交接处结果，不能把时间覆盖连续解释为数值完全同质。

## 月／日交接规则

1. 默认选择最后一个历史月结束后的月初作为交接点。例如历史到 2026 年 8 月，则日均段从 9 月 1 日开始。
2. 日均来源可能可回溯数年，但交接点之前的日均不重复计入。可用 `--cutover 2026-09-01` 指定交接点，必须是 UTC 月初。
3. 每片保留 `time_start/time_end/time_scale`；时间区间为 UTC `[start, end)`。月均标签规范到月初，覆盖整个日历月。
4. 相邻区间首尾相接，才允许 Ocetrac 跨片连接；是否同一事件仍取决于空间连接规则。
5. 日均缺日、月均缺月补为未知；历史末端与近期起点之间存在的空档也会切断追踪。数组相邻不等于实际时间相邻。
6. 混合模式固定使用逐片异常，不允许把“5 个混合时间片”当作五天。日均／月均单独分析仍可显式开启旧的持续时间筛选。

前端只显示最近 30 天时，后台仍要保留交接点到当前日期的完整连接段。月均片不提供月内逐日发生、消亡或连续性的证据；事件摘要保留尺度精度，不伪造日级信息。

## 输出

| 文件 | 内容 |
|---|---|
| `masks.nc` | 阈值、有效／异常掩膜、单要素与复合事件编号、原生时间区间及逐片来源 |
| `events.json` | 复合事件摘要、完整 `time_span`、轨迹、父子事件编号和变量指标；单变量模式输出单要素摘要 |
| `single_events.json` | 参与复合匹配的单要素事件 |
| `events.geojson` | 整个事件的水平足迹并集，保留孔洞 |
| `tracks.geojson` | 单要素及复合事件每时间片的边界、质心、面积和体积 |
| `lineage.json` | 对象节点间的 continuation／split／merge／split_merge 边，包含跨尺度、跨事件编号关联 |
| `quality.json` | 时间空档、交接点、来源、参数、Ocetrac 版本及统计支持 |

`source_event_ids` 是复合事件的单要素贡献关系；`parent_event_ids/child_event_ids` 与 `lineage.json` 表示追踪关系。DeepTrack 的分裂分支可继承同一事件编号；合并时继承较小原始编号，适配层另外保留所有符合连接条件的父子边，避免丢失另一来源。

`duration_steps` 是原生片数，`time_span.slice_counts` 分别记录月片和日片；`span_days` 是时间支持区间跨度，不能视作月内每日都观测到异常的天数。

保留原有最大瞬时面积／体积和按片求和字段；新增 `integrated_volume_km3_days`、`time_weighted_mean_excess`、`summed_voxel_excess_days` 使用实际区间天数加权。月均片对应的积分仍是月均近似。层厚由深度中心推断，未提供真实海底边界，因此体积为近似值；单层体积为 `null`。

混合输入的 LMF 按月均／日均分开输出，不把一个月和一天当作同等采样机会混成一个概率。LMF 仍是区域统计，不能解释为生态损害。

## 已有近期样本的运行

```bash
.venv/bin/python -B -m data_processing.compound_detection \
  --input data/processed/compound_recent_input.nc \
  --variables thetao o2 ph --frequency daily \
  --threshold-mode absolute \
  --threshold thetao=28 --threshold o2=205 --threshold ph=8.03 \
  --output data/processed/ocetrac_recent_sample
```

该输入为 2026-09-28—29 的实际小区域数据。以上阈值仅为流程验证配置，输出是候选对象，不是已通过科学验证的极端事件结论；50 m 缺测仍保持未知。

## 验证与当前边界

```bash
.venv/bin/python -B -m unittest discover -s data_processing/tests -v
```

测试使用真实 Ocetrac，覆盖月／日交接、闰年、缺测中断、移动对象、垂向连接、全球日期变更线、区域边界、分裂合并、独立参考阈值和输出回读。

全球完整经度网格自动启用周期连接，纬度边界不连接；GeoJSON 在 ±180°裁切，质心经度采用圆统计。区域网格不会误接东西边界。

当前计算仍将一个分析立方体载入内存，尚未实现全球全水深数十年的分布式计算、分块间对象归并、跨批次稳定编号和在线增量追踪。输入支持全球坐标不代表已下载、计算了全球全量数据。生产部署还需完成数据更新流水线、容量规划、分块计算和前端接口；不能把各空间块独立编号后直接拼接成全球事件。

## 上游与许可证

- 源码及 MIT 许可：[ocetrac/ocetrac](https://github.com/ocetrac/ocetrac)、[LICENSE.txt](https://github.com/ocetrac/ocetrac/blob/main/LICENSE.txt)。本项目通过依赖调用，没有复制上游源码；再分发依赖时需保留许可声明。
- 原论文：[Scannell et al. (2024), Spatiotemporal Evolution of Marine Heatwaves Globally](https://doi.org/10.1175/JTECH-D-23-0126.1)。
- 软件引用：[Ocetrac software](https://doi.org/10.5281/zenodo.5102928)。月／日交接及显式谱系为本项目适配，不作为原论文原生能力宣传。
