# CMEMS 统一预处理模块

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
