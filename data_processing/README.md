# CMEMS 统一预处理模块

当前事件识别已迁移到 Ocetrac，默认逐时间片判断异常，不要求连续五天。历史月均／近期日均交接入口、隔离环境安装及可运行演示见 [Ocetrac 演化模块](OCETRAC_EVOLUTION.md)。预处理应分别准备月均、日均数据，再交给演化入口。

`cmems_preprocess.py` 将 GLORYS12、CMEMS 多年 BGC、物理分析预报和 BGC 分析预报的 NetCDF 子集整理到一个共同数据立方体。输出变量为：

- `thetao`：海水位温，保留 `degrees_C`；
- `o2`：溶解氧，保留产品原始 `mmol m-3`；
- `ph`：总标度 pH，保留无量纲单位。

模块会完成：

1. 坐标别名统一为 `time/depth/latitude/longitude`；
2. 经度转到 `[-180, 180)` 并排序；
3. 显式屏蔽 `_FillValue`、`missing_value`、`valid_min` 和 `valid_max`；
4. 将所有变量重采样到目标水平网格，默认 0.25°；
5. 按实际深度坐标插值到 `1, 5, 10, 20, 30, 40, 50 m`；
6. 不在海底以下外推，超出有效深度的值保留为 NaN；
7. 同变量多源数据按时间合并，重复时间默认保留命令行中先出现的文件；
8. 输出 NetCDF 和质量报告 JSON。

## 运行

在已经安装 `xarray`、`numpy`、`scipy` 和 NetCDF 引擎的环境中运行。Copernicus Marine Toolbox 环境通常已包含前两项，但线性插值仍需要额外安装 `scipy`：

```bash
pip install xarray numpy scipy h5netcdf
```

然后运行：

```bash
python -m data_processing.cmems_preprocess \
  --input data/samples/glorys12_thetao_20240813.nc \
         data/samples/bgc_my_o2_20240813.nc \
         data/samples/bgc_my_o2_ph_202408.nc \
         data/samples/phy_anfc_thetao_20260928.nc \
         data/samples/bgc_anfc_o2_20260928.nc \
         data/samples/bgc_anfc_ph_20260928.nc \
  --bbox 122 123 27 28 \
  --output data/processed/cmems_common.nc
```

也可以使用更严格的最近邻重采样：

```bash
python -m data_processing.cmems_preprocess ... --regrid-method nearest
```

`--depths 1 5 10 20 30 40 50` 可以替换统一深度层。默认表层使用 1 m，因为不同样本的最浅原生层约为 0.49 m 和 0.51 m；这样插值有上下包围层。垂向外推始终关闭，超出实际水深范围的值会保留为缺测。重复时间默认保留命令行中较早的输入，可用 `--duplicate-policy last` 改为保留较后的输入。

如果目标需要 50 m，下载请求的最大深度建议至少设为 60 m。样本请求到 50 m 时，原生最深层只有约 47.2 m，因此模块会把 50 m 标成缺测；质量报告会列出这类无覆盖深度。

## 重要说明

- GLORYS12 的 0.083° 网格会降到 BGC 使用的 0.25° 目标网格，避免把 BGC 数据人为插值成更高分辨率。
- 多年 BGC 的 pH 通常来自月均文件，和日均 `o2` 合并后会保留各自实际时间点；模块不会把月均 pH 复制成日均值。
- 模块只做结构和坐标统一，不做气候态、P90/P10 阈值或复合事件识别。
- `o2` 单位仍为 `mmol m-3`，如要换成 `μmol kg-1`，需要使用海水密度另行转换。
- 单要素事件识别使用 `data_processing/event_detection.py`；它会检查日/月频率，不会把月均 pH 扩展成日均。

## 单要素事件识别

阶段一先识别单变量事件，输出阈值场、连续时间掩膜、事件指标和 GeoJSON：

```bash
python -m data_processing.event_detection \
  --input path/to/bio_daily.nc path/to/car_daily.nc \
  --variables o2 ph \
  --frequency daily \
  --threshold-mode absolute \
  --threshold o2=200 --threshold ph=7.95 \
  --output data/processed/events_daily
```

默认也支持季节百分位阈值：温度使用 P90，氧和 pH 使用 P10：

```bash
python -m data_processing.event_detection \
  --input path/to/daily_cube.nc \
  --variables thetao o2 ph \
  --frequency daily \
  --threshold-mode seasonal_percentile \
  --high-percentile 0.90 --low-percentile 0.10 \
  --output data/processed/events_percentile
```

输出文件包括：

- `masks.nc`：变量事件掩膜和时间对齐后的阈值；
- `events.json`：事件起止时间、持续时间、面积、体积、深度范围、质心和强度；
- `events.geojson`：供 MapLibre 或 GIS 使用的事件对象；
- `quality.json`：频率检查、有效率、阈值配置和跳过原因。

历史多年 BGC 的月均 pH 应使用 `--frequency monthly` 单独分析；使用 `--frequency daily` 时模块会跳过该变量并在质量报告中说明原因。

`event_detection.py` 保留阶段一的单要素入口。复合识别使用新增的 `compound_detection.py`，复用其阈值和持续时间逻辑，支持双要素/三要素同时型与持续叠加型事件、区域 LMF 和探索性的时间区块统计检验。

## 复合事件识别

先安装 Python 依赖：

```bash
python -m pip install -r data_processing/requirements.txt
```

当前已有数据可直接运行低氧＋高酸度的流程验证：

```bash
python -m data_processing.compound_detection \
  --input classroom/09_CMEMS分析预报BGC/cmems_bgc_nrt_bio_20240101_20240107_118-124E_24-30N_0-50m.nc \
          classroom/09_CMEMS分析预报BGC/cmems_bgc_nrt_car_20240101_20240107_118-124E_24-30N_0-50m.nc \
  --variables o2 ph --frequency daily \
  --threshold-mode absolute --threshold o2=245 --threshold ph=8.12 \
  --single-min-duration 3 --min-duration 3 \
  --output data/processed/compound_bgc_daily_sample
```

这些阈值用于流程验证，不作为通用生态极端标准。相同网格的多个文件可以直接合并；不同网格必须先使用预处理模块。默认单要素和复合持续条件均为 1 个时间片，再通过 Ocetrac 追踪；显式指定 `--min-duration 5` 才采用旧的逐位置五步筛选。

针对已经统一的三变量数据立方体，可使用研究基准期和百分位阈值：

```bash
python -m data_processing.compound_detection \
  --input path/to/aligned_daily_cube.nc \
  --variables thetao o2 ph --frequency daily \
  --threshold-mode seasonal_percentile \
  --baseline-start 1993-01-01 --baseline-end 2022-12-31 \
  --output data/processed/compound_events
```

这里的路径和基准期是示例，需要对应实际数据共同覆盖范围。日均输入必须包含连续时间段，缺失日期会补为缺测并中断持续条件；月均 pH 不能作为日均输入使用。

输出为 `masks.nc`、`events.json`、`single_events.json`、`events.geojson`、`tracks.geojson`、`lineage.json`、`quality.json`。`*_valid_mask=0` 表示未知，不能把对应 `*_event_mask=0` 解释为正常。GeoJSON 是事件实际格点范围的并集；逐片边界及谱系另行输出。

详细参数、数据语义、LMF 口径与限制见 [复合识别说明](COMPOUND_DETECTION.md)。逐片追踪和显式谱系已实现；滞后/级联匹配、全球分布式计算和前端真实计算接入尚未实现。

验证命令：

```bash
python -m unittest discover -s data_processing/tests -v
```
