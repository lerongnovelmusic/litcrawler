# 第三方数据与许可说明 / Third-Party Notices

本程序代码以 MIT 许可发布（见 `LICENSE`）。仓库中还包含下列第三方内容，
它们各自遵循原有许可，感谢原作者。

## 1. 影响因子数据 `litcrawler/resources/impact_factor_jcr.tsv.gz`

* 来源：[lcpmgh/Journal](https://github.com/lcpmgh/Journal)（MIT License，© 2025 Liang Chen）
* 处理：由 `tools/build_if_bundle.py` 从该仓库的 `journal_jcr_2025.csv` 中提取
  2022–2025 年度、含 JIF 的记录，保留 期刊名 / ISSN / JIF / 分区 / 学科 / 收录库 等字段。
* 说明：原始指标版权归 Clarivate（Journal Citation Reports）所有，
  本项目仅作个人检索筛选的参考实现，**不得用于商业用途**。
  如需权威指标，请使用 JCR 官方导出数据并通过程序内的「导入影响因子数据」功能载入。

## 2. 文献元数据

检索结果来自各平台的公开 API，版权归各平台及原作者所有：

* [OpenAlex](https://openalex.org) — CC0
* [Crossref](https://www.crossref.org) — 元数据开放使用
* [Europe PMC](https://europepmc.org) — 开放获取元数据
* [arXiv](https://arxiv.org) — 元数据开放，论文版权归作者
* [DOAJ](https://doaj.org) — 开放获取期刊目录
* [PubMed](https://pubmed.ncbi.nlm.nih.gov) — NCBI E-utilities 使用条款

本程序只下载**开放获取（OA）**内容的 PDF，不绕过任何付费墙。
使用时请遵守各站点的服务条款与访问频率限制。
