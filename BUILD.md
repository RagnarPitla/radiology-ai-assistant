# RadHarness Build Specification

The picked and tested stack for running RadHarness fully locally. Prepared by
Rbuild.ai. This document is internal (closed IP) and stays local.

## 1. Local model, picked and tested

Recommendation from the model research and the on-device benchmark.

### Primary reasoning model
- **Qwen3-32B** (Apache 2.0) on a 64GB+ machine, or **Qwen3-14B** on 32GB.
- Rationale: permissive license for commercial and clinical-adjacent use, a
  thinking mode that suits impression drafting and structured reports, native
  on Ollama and MLX.
- Tested default that runs today on 32GB: **gpt-oss:20b** (Apache 2.0), which
  benchmarked at 33 to 41 generation tokens per second on an M1 Max.

### Medical text and image model
- **MedGemma 27B** multimodal (Google HAI-DEF terms) on 64GB+, or **MedGemma 4B**
  on 32GB. Trained on chest X-rays and clinical text. HAI-DEF permits building
  healthcare applications. Assistant and drafting use is in scope. Clinical
  deployment still requires validation and local regulatory approval.

### Embeddings for RAG
- **nomic-embed-text**. If it is not installed, RadHarness falls back to a local
  deterministic embedding, so knowledge search always works offline.

### Memory tier mapping (Q4 quantization)
| Unified memory | Reasoning | Medical / vision |
| --- | --- | --- |
| 32 GB | Qwen3-14B or gpt-oss:20b | MedGemma 4B |
| 64 GB | Qwen3-32B | MedGemma 27B |
| 128 GB | Qwen3-72B or Llama 3.3-70B | MedGemma 27B plus a summarizer |
| 256 GB+ | gpt-oss:120b | multiple models in parallel |

### Benchmark summary (Apple M1 Max, 32GB, warm runs)
| Task | Model | Gen tok/s | Latency | Quality (1-5) |
| --- | --- | --- | --- | --- |
| Report drafting | gpt-oss:20b | 33.3 | 11.7 s | 5 |
| Triage classify | gpt-oss:20b | 32.9 | 25.2 s | 3 |
| RAG answer | gpt-oss:20b | 41.0 | 3.6 s | 5 |
| Report drafting | qwen2.5:14b | 12.9 | 15.1 s | 5 |

Finding: gpt-oss:20b runs 100% on the GPU at a clinically usable speed. Triage
classification undercalled one case, which is why RadHarness wraps triage in
deterministic rules plus mandatory radiologist verification. Use qwen3 or a
larger model on a Mac Studio for higher-stakes reasoning.

## 2. Hardware, the appliance

Current Mac Studio (Early 2025 lineup, US pricing July 2026). Unified memory is
the key constraint, memory bandwidth drives tokens per second.

| Build | Config | Memory / GPU / Bandwidth | Price | Runs |
| --- | --- | --- | --- | --- |
| Good | M4 Max | 64GB / 40-core / 546 GB/s | $3,799 | 32B + vision |
| Better (recommended) | M4 Max | 128GB / 40-core / 546 GB/s | ~$5,099 | 70B + vision + summarizer |
| Best | M3 Ultra | 96GB / 80-core / 819 GB/s | $7,299 | 70B, ~50% faster |

Recommended pick: **Mac Studio M4 Max, 128GB, 2TB, about $5,099.** Verify current
pricing at apple.com. The 2026 DRAM shortage removed the 256GB and 512GB M3 Ultra
options, so plan around 96GB to 128GB for new purchases.

## 3. Setup

```bash
# 1. Install the local runtime
#    Ollama: https://ollama.com

# 2. Pull the recommended models for your memory tier
./scripts/setup-models.sh            # auto-detects memory
#    or: ./scripts/setup-models.sh --tier 64

# 3. Point the app at your chosen model (optional, defaults to gpt-oss:20b)
export RADHARNESS_CHAT_MODEL=qwen3:32b
export RADHARNESS_EMBED_MODEL=nomic-embed-text

# 4. Run
./run.sh                             # http://127.0.0.1:8000
```

## 4. Privacy posture
- Inference stays on a loopback endpoint. Offline mode refuses any non-local
  model endpoint in code.
- All state is local SQLite plus local files under data/, gitignored.
- Not a medical device. Every AI output is verified by a qualified radiologist.

Contact: Rbuild.ai, ragnar@rbuild.ai
