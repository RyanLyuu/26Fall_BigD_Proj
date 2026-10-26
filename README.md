To initialyze: npm run dev

## 后端事件识别

后端已接入 Ocetrac，支持逐月／逐日单要素、双要素和三要素异常，以及历史月均到近期日均的事件追踪。默认不要求连续五天。

```bash
.venv/bin/python -B -m data_processing.demo_evolution
.venv/bin/python -B -m unittest discover -s data_processing/tests -v
```

第一条命令生成明确标记的全球粗网格演示数据及追踪结果。安装、真实数据输入、输出结构和当前计算规模限制见 [Ocetrac 演化模块说明](data_processing/OCETRAC_EVOLUTION.md)。
