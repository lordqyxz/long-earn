# 论文本地参考（Research Papers）

本目录是 Long Earn **架构与门控决策的文献锚点**：PDF（若已下载）+ 机制映射笔记。
运行时行为以源码与 ADR 为准；本目录不替代 ADR。

## 索引

| 主题 | 决策 | 映射笔记 | 本地 PDF / 外链 |
|------|------|----------|-----------------|
| Think-on-Graph | [ADR-018](../../adr/018-think-on-graph-research-agent.md) | [tog-mechanism-mapping.md](tog-mechanism-mapping.md) | `2307.07697-*.pdf` / `2407.10805-*.pdf` |
| 统计验证门控 | [ADR-022](../../adr/022-statistical-validation-gates-and-evolution-staging.md) | [statistical-gates-mapping.md](statistical-gates-mapping.md) | 见下表外链（PDF 按需补齐） |
| 回测准确性 | [ADR-013](../../adr/013-backtest-accuracy-principles.md) | [../backtest-engine-correctness-proof.md](../backtest-engine-correctness-proof.md) | ADR-013 文末参考 |

调研综述（正确性 vs 质量、与 factor-qc 对照）：
[../2026-08-31-statistical-validation-gates.md](../2026-08-31-statistical-validation-gates.md)

---

## A. Think-on-Graph

| 文件 | 论文 | 用途 |
|------|------|------|
| `2307.07697-think-on-graph.pdf` | Think-on-Graph (ICLR 2024) | **主范式**：LLM ⊗ KG，beam explore + prune |
| `2407.10805-think-on-graph-2.pdf` | Think-on-Graph 2.0 | 图检索 ↔ 文档上下文交替 |
| `tog-mechanism-mapping.md` | — | 论文机制 → 本仓库模块 |

补齐 PDF：

- https://arxiv.org/pdf/2307.07697
- https://arxiv.org/pdf/2407.10805

**明确不落地（本轮）**：Think-on-Graph 3.0（MACER 四角色）— 等 ResearchAgent 正反馈闭环稳定后再评估。

```bibtex
@inproceedings{sun2024thinkongraph,
  title={Think-on-Graph: Deep and Responsible Reasoning of Large Language Model on Knowledge Graph},
  author={Sun, Jiashuo and Xu, Chengjin and Tang, Lumingyuan and Wang, Saizhuo and Lin, Chen and Gong, Yeyun and Ni, Lionel and Shum, Heung-Yeung and Guo, Jian},
  booktitle={ICLR},
  year={2024}
}

@inproceedings{ma2025tog2,
  title={Think-on-Graph 2.0: Deep and Faithful Large Language Model Reasoning with Knowledge-guided Retrieval Augmented Generation},
  author={Ma, Shengjie and Xu, Chengjin and Jiang, Xuhui and Li, Muzhi and Qu, Huaren and Yang, Cehao and Mao, Jiaxin and Guo, Jian},
  booktitle={ICLR},
  year={2025}
}
```

---

## B. 统计验证门控（Statistical Validation）

不在仓库内 vendoring 第三方实现；对照开源闸门
[foolproof-labs/factor-qc](https://github.com/foolproof-labs/factor-qc)（MIT）与下列原论文。

| 符号 | 论文 | SSRN / DOI | 本仓库角色 |
|------|------|------------|------------|
| DSR | Bailey & López de Prado (2014), *The Deflated Sharpe Ratio* | [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) | 诊断门 `DeflatedSharpeGate` |
| PSR / MinTRL | Bailey & López de Prado (2012), *The Sharpe Ratio Efficient Frontier* | [SSRN 1821643](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1821643) | 诊断 `evaluate_mintrl` |
| PBO / CSCV | Bailey et al. (2017), *The Probability of Backtest Overfitting* | [eScholarship](https://escholarship.org/uc/item/4w1110bb) / [SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) | 诊断 `evaluate_returns_matrix` |
| Haircut / 多重检验 | Harvey & Liu；Harvey, Liu & Zhu (2016) RFS | [SSRN 2345489](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2345489) / [DOI](https://doi.org/10.1093/rfs/hhv059) | 诊断 `evaluate_haircut_sharpe` |
| CPCV / purge | López de Prado, *Advances in Financial Machine Learning* Ch.7/12 | 图书 | WF `TimeSeriesSplit.gap`（embargo 简化） |
| OOS 预测力 | Wiecki et al. (Quantopian), *All That Glitters Is Not Gold* | 公开讲义/博客 | ADR-022 背景：IS 夏普对 OOS 几乎无预测力 |
| LLM 时代过拟合 | Mobarekeh & López de Prado (2024) | [SSRN 4778909](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4778909) | 诚实 trial 计数不可省 |

```bibtex
@article{bailey2014dsr,
  title={The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality},
  author={Bailey, David H. and L{\'o}pez de Prado, Marcos},
  journal={Journal of Portfolio Management},
  volume={40},
  number={5},
  year={2014}
}

@article{bailey2017pbo,
  title={The Probability of Backtest Overfitting},
  author={Bailey, David H. and Borwein, Jonathan M. and L{\'o}pez de Prado, Marcos and Zhu, Qiji Jim},
  journal={Journal of Computational Finance},
  year={2017}
}

@article{harvey2016rfs,
  title={...and the Cross-Section of Expected Returns},
  author={Harvey, Campbell R. and Liu, Yan and Zhu, Heqing},
  journal={Review of Financial Studies},
  year={2016}
}
```

按需下载 PDF 到本目录时命名建议：`2460551-deflated-sharpe.pdf`、`2326253-pbo.pdf`（文件名带 SSRN id）。

---

## 维护约定

1. **新增主题**：先写 ADR 或调研笔记，再在本 README 加一行索引；映射笔记与 ADR 交叉链接。
2. **不提交**未授权版权 PDF；开源实现以链接引用为主，避免整文件 vendoring。
3. **冲突时**：AGENTS.md + ADR > 本目录笔记 > 外部博客。
