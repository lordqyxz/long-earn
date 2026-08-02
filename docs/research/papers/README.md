# Think-on-Graph 论文本地参考

本目录存放 ADR-018 架构翻转的论文依据。

## 文件清单

| 文件 | 论文 | 用途 |
|------|------|------|
| `2307.07697-think-on-graph.pdf` | Think-on-Graph (ICLR 2024) | **主范式**：LLM ⊗ KG，beam explore + prune |
| `2407.10805-think-on-graph-2.pdf` | Think-on-Graph 2.0 | 图检索 ↔ 文档上下文交替（Substance + 经验文本） |
| `tog-mechanism-mapping.md` | — | 论文机制 → 本仓库模块映射 |

若 PDF 因网络未就绪，可从 arXiv 手动补齐：

- https://arxiv.org/pdf/2307.07697
- https://arxiv.org/pdf/2407.10805

## 明确不落地（本轮）

**Think-on-Graph 3.0**（MACER / Constructor·Retriever·Reflector·Responser）复杂度过高，等 ResearchAgent Spike 证明飞轮后再评估。

## 引用

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
