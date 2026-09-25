# LLM API Pricing

Machine-readable pricing for **200+ LLM APIs** — text, image, video, and audio
models — with a snapshot date and an updater script you can run yourself.

Every rate is published next to the upstream list price it is compared against,
and every row carries the URL it was read from. If a number here doesn't match
the page it cites, open an issue and it gets fixed.

<!-- BEGIN GENERATED: snapshot -->
> Last snapshot: **2026-09-26** · Currency: **USD**
> Models: **237** · Providers: **20** · With input and output rates: **133**
<!-- END GENERATED: snapshot -->

## What's in here

| File | Description |
|---|---|
| `data/pricing.json` | Full dataset — every model with input/output rates, the upstream list price, and source URLs |
| `data/pricing.csv` | Same data as CSV, for spreadsheets |
| `scripts/update_prices.py` | Re-reads the catalog and regenerates both files |
| `scripts/sources.yaml` | The list of pricing pages each row's list price is checked against |
| `requirements.txt` | Optional — see [How to regenerate this dataset](#how-to-regenerate-this-dataset) |

## Quick start

No dependencies for reading the data — it's plain JSON:

```bash
git clone https://github.com/gptprototeam-star/llm-api-pricing.git
cd llm-api-pricing

python3 -c "
import json
d = json.load(open('data/pricing.json'))
rows = [m for m in d['models']
        if m['input_per_mtok_usd'] is not None and m['output_per_mtok_usd'] is not None]
rows.sort(key=lambda m: m['input_per_mtok_usd'])
print(f\"{'model':<30} {'input':>8} {'output':>8}\")
for m in rows[:5]:
    print(f\"{m['id']:<30} {m['input_per_mtok_usd']:>8} {m['output_per_mtok_usd']:>8}\")
"
```

Output:

```
model                             input   output
doubao-seed-1-6-flash-250615     0.0182   0.1821
gpt-5-nano                        0.035     0.28
qwen-turbo                        0.045     0.18
gemini-2.0-flash                   0.06     0.24
gpt-4.1-nano                       0.07     0.28
```

Each row also carries the same rate before discount, so you can see the spread
without a second request:

```python
m = rows[0]
print(m["input_per_mtok_usd"], "vs list", m["official_input_per_mtok_usd"])
print(m["discount_percent"], "% off", "—", m["source_url"])
```

## Which model is cheapest per million tokens?

Ranked by blended cost at a 3:1 input:output ratio — the split most chat and
agent workloads land on. Full table in `data/pricing.csv`.

<!-- BEGIN GENERATED: cheapest -->
| Model | Provider | Input ($/1M) | Output ($/1M) | Blended ($/1M) |
|---|---|---|---|---|
| `doubao-seed-1-6-flash-250615` | bytedance | 0.0182 | 0.1821 | 0.05918 |
| `qwen-turbo` | alibaba | 0.045 | 0.18 | 0.07875 |
| `gpt-5-nano` | openai | 0.035 | 0.28 | 0.09625 |
| `gemini-2.0-flash` | google | 0.06 | 0.24 | 0.105 |
| `gpt-4.1-nano` | openai | 0.07 | 0.28 | 0.1225 |
| `doubao-1-5-pro-32k-250115` | bytedance | 0.0971 | 0.2428 | 0.1335 |
| `doubao-seed-1-6-250615` | bytedance | 0.0971 | 0.2428 | 0.1335 |
| `gpt-6-luna` | openai | 0.08 | 0.4 | 0.16 |
| `grok-4-1-fast-non-reasoning` | xai | 0.12 | 0.3 | 0.165 |
| `grok-4-1-fast-reasoning` | xai | 0.12 | 0.3 | 0.165 |
<!-- END GENERATED: cheapest -->

**How blended cost is calculated**

```python
blended = (input_per_mtok_usd * 3 + output_per_mtok_usd * 1) / 4
```

Change the ratio to match your own traffic before making a decision — the
cheapest model by list price isn't always the cheapest model for your workload.

## Data format

```json
{
  "snapshot_date": "2026-09-24",
  "currency": "USD",
  "unit": "per_1m_tokens",
  "models": [
    {
      "id": "claude-opus-5",
      "provider": "anthropic",
      "modalities": ["document", "image", "text", "text_output"],
      "context_window": 1000000,
      "input_per_mtok_usd": 4.5,
      "output_per_mtok_usd": 22.5,
      "official_input_per_mtok_usd": 5.0,
      "official_output_per_mtok_usd": 25.0,
      "cache_write_per_mtok_usd": 5.625,
      "cache_read_per_mtok_usd": 0.45,
      "discount_percent": 10,
      "source_url": "https://gptproto.com/model/claude/claude-opus-5",
      "official_price_source_url": "https://www.anthropic.com/pricing"
    }
  ]
}
```

Fields, for a token-billed model:

| Field | Meaning |
|---|---|
| `id` | Model id as you pass it to the API |
| `provider` | Normalised vendor name |
| `modalities` | Inputs and outputs, e.g. `text_output`, `image`, `video_output` |
| `context_window` | Tokens, or `null` when the vendor doesn't publish one |
| `input_per_mtok_usd` / `output_per_mtok_usd` | Published rate, per 1M tokens |
| `official_*_per_mtok_usd` | The same rate before discount, as billed upstream |
| `cache_write_per_mtok_usd` / `cache_read_per_mtok_usd` | Cache rates, where published |
| `discount_percent` | Smallest `% off` shown on the page; `null` when none is shown |
| `source_url` | The page this row was read from |
| `official_price_source_url` | The vendor's own rate card |

Image, video and audio models are billed per second, per image or per run
rather than per token, and are priced per resolution. For those, the scalar
`input_per_mtok_usd` / `output_per_mtok_usd` stay `null` and the tiers are kept
verbatim instead:

```json
{
  "id": "viduq3-pro",
  "per_second_usd": 0.04,
  "media_rates": [
    { "label": "540p Resolution",  "unit": "per_second", "price_usd": 0.04,  "official_price_usd": 0.05,  "discount_percent": 20 },
    { "label": "720p Resolution",  "unit": "per_second", "price_usd": 0.1,   "official_price_usd": 0.125, "discount_percent": 20 },
    { "label": "1080p Resolution", "unit": "per_second", "price_usd": 0.12,  "official_price_usd": 0.15,  "discount_percent": 20 }
  ]
}
```

**Do not compare a `per_second` rate against a per-token rate, or a 540p rate
against a 1080p rate.** `media_rates[].unit` and `media_rates[].label` exist so
that a comparison can be made at the same unit and the same tier, or not at all.

## How to regenerate this dataset

```bash
pip install -r requirements.txt
python3 scripts/update_prices.py --out data/
```

The script reads `scripts/sources.yaml`, walks the catalog, and rewrites
`data/pricing.json` and `data/pricing.csv`. It also refreshes the two generated
blocks in this README, so the headline numbers cannot drift away from the data.

`requirements.txt` only installs PyYAML, which is used to read `sources.yaml`.
Without it the script still runs, on the standard library alone, with built-in
defaults — it just won't pick up your edits to `sources.yaml`.

```bash
# verify without writing
python3 scripts/update_prices.py --dry-run

# a quick sanity check on five models
python3 scripts/update_prices.py --limit 5 --verbose --dry-run
```

It does not silently overwrite on a failed fetch. A page that is unreachable,
or whose panel no longer parses, is reported under `skipped` and its previous
values are carried over, so a broken selector degrades the dataset instead of
corrupting it. No rate is ever estimated or interpolated: missing means missing.

## Caveats

- **Rates change without notice.** Always confirm against the provider's own
  pricing page before making a billing decision. This dataset is a starting
  point for comparison, not a billing source of truth.
- **Blended cost is an estimate.** It assumes a fixed input:output ratio. Real
  cost depends on prompt length, caching, and retries.
- **The discount is a published rate, not a negotiated one.** It is the rate
  shown on the public catalog page at snapshot time, and it can change without
  a commit here. Nothing in this repo reads a private price list.
- **Batched, cached, and provisioned-throughput rates are out of scope** for
  now, except where a vendor publishes a cache rate on the same panel.
- **Some vendors publish no comparable rate.** Midjourney is subscription-only
  and TypeSafeAI has no public rate card; those rows carry `null` rather than a
  guess. See the `note` field in `scripts/sources.yaml`.

## Contributing

Spotted a rate that's out of date? Open an issue with the model ID and a link
to the current pricing page. Data corrections get merged fast.

## License

MIT — code and data.

---

Maintained by [@gptprototeam-star](https://github.com/gptprototeam-star).
I run [gptproto](https://gptproto.com/?s=gh_llm_api_pricing) — 200+ AI models behind
one OpenAI-compatible endpoint.
