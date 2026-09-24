#!/usr/bin/env python3
"""
update_prices.py — regenerate data/pricing.json and data/pricing.csv.

The dataset is built from GPTProto's public model catalog. Each model page
publishes, for every billing row, both the upstream ("official price") rate and
the GPTProto rate, so a single fetch yields a comparison baseline instead of a
bare number. Every row keeps the URL it was read from.

Design rules (do not relax these):

* A fetch that fails, or a page whose price panel cannot be parsed, is
  *reported and skipped* — never written as a null. If the model already exists
  in the output file, its previous (last known good) values are carried over.
  A broken selector therefore degrades the dataset; it never corrupts it.
* No number is ever estimated or interpolated. Missing means missing.

Usage
-----
    python3 scripts/update_prices.py --out data/
    python3 scripts/update_prices.py --dry-run
    python3 scripts/update_prices.py --limit 5 --verbose
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

try:  # optional: only needed to read scripts/sources.yaml
    import yaml
except ImportError:  # pragma: no cover - fallback keeps the updater runnable
    yaml = None

SITEMAP_URL = "https://gptproto.com/sitemap-models.xml"
CATALOG_URL = "https://gptproto.com/model"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
SOURCES_PATH = os.path.join(SCRIPT_DIR, "sources.yaml")
USER_AGENT = (
    "llm-api-pricing-dataset/1.0 (+https://github.com/gptprototeam-star/llm-api-pricing)"
)

# Canonical English model page: /model/<vendor>/<model>
MODEL_URL_RE = re.compile(r"^https://gptproto\.com/model/([^/]+)/([^/]+)$")
MONEY_RE = re.compile(r"^\$([0-9]+(?:\.[0-9]+)?)$")
OFF_RE = re.compile(r"^([0-9]+)%\s*off$")
UNIT_RE = re.compile(r"^ /\s*(.+?)$")
TOKENS_RE = re.compile(r"^([0-9][0-9,]*)\s+tokens?$")
SEOTEXT_RE = re.compile(r'data-seo-text="([^"]*)"')
DESC_RE = re.compile(r'<meta name="description" content="([^"]*)"')
TAG_RE = re.compile(r"<[^>]+>")
MODALITY_IN_RE = re.compile(r"Input:\s*(Text|Image|Document|Audio|Video)")
MODALITY_OUT_RE = re.compile(r"Output:\s*(Text|Image|Video|Audio)")

# The panel writes the unit as free text (" / 1M", " / s", " / Per Time"), so
# normalise it before anything downstream reasons about comparability.
UNIT_ALIASES = {
    "1m": "per_1m_tokens",
    "1m tokens": "per_1m_tokens",
    "1 million tokens": "per_1m_tokens",
    "s": "per_second",
    "second": "per_second",
    "per time": "per_run",
    "run": "per_run",
    "request": "per_request",
    "image": "per_image",
    "video": "per_video",
    "megapixel": "per_megapixel",
    "minute": "per_minute",
}

# Billing-row labels are matched case-insensitively against these keywords;
# the mapping itself lives in assign_slots().

PROVIDER_ALIASES = {
    "openai": "openai",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "google": "google",
    "gemini": "google",
    "grok": "xai",
    "xai": "xai",
    "qwen": "alibaba",
    "alibaba": "alibaba",
    "deepseek": "deepseek",
    "moonshotai": "moonshotai",
    "moonshot": "moonshotai",
    "z-ai": "z-ai",
    "minimax": "minimax",
    "bytedance": "bytedance",
    "vidu": "vidu",
    "kling": "kling",
    "hunyuan": "tencent",
    "higgsfield": "higgsfield",
    "tripo3d": "tripo3d",
    "gptproto": "gptproto",
    "kwaipilot": "kwaipilot",
    "typesafeai": "typesafeai",
    "midjourney": "midjourney",
    "suno": "suno",
    "runway": "runway",
    "ideogram": "ideogram",
    "meta": "meta",
    "mistral": "mistral",
    "nvidia": "nvidia",
    "perplexity": "perplexity",
    "recraft": "recraft",
    "flux": "black-forest-labs",
    "black-forest-labs": "black-forest-labs",
    "stability": "stability-ai",
    "lightricks": "lightricks",
    "topaz": "topaz",
    "bria": "bria",
    "elevenlabs": "elevenlabs",
    "openrouter": "openrouter",
}


def log(verbose: bool, message: str) -> None:
    if verbose:
        print(message, file=sys.stderr)


def load_sources(path: str = SOURCES_PATH) -> dict:
    """Read scripts/sources.yaml, falling back to built-in defaults."""
    if not os.path.exists(path):
        return {"dataset": {}, "vendors": {}}
    if yaml is None:
        log(
            True,
            "PyYAML is not installed — ignoring sources.yaml "
            "(pip install -r requirements.txt to enable it)",
        )
        return {"dataset": {}, "vendors": {}}
    with open(path, encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return {
        "dataset": loaded.get("dataset") or {},
        "vendors": loaded.get("vendors") or {},
    }


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #


def fetch(url: str, timeout: int = 30, retries: int = 3) -> str | None:
    """GET a URL with retries. Returns None if every attempt fails."""
    last_error: str | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    last_error = f"HTTP {response.status}"
                else:
                    return response.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code in (404, 410):
                break  # delisted — retrying will not help
        except Exception as exc:  # noqa: BLE001 - network errors are expected
            last_error = type(exc).__name__
        if attempt < retries:
            time.sleep(1.5 * attempt)
    log(True, f"  ! fetch failed ({last_error}): {url}")
    return None


def discover_models(sitemap_xml: str) -> list[tuple[str, str, str]]:
    """Return (vendor_slug, model_id, url) for canonical English model pages."""
    found: list[tuple[str, str, str]] = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap_xml):
        match = MODEL_URL_RE.match(loc)
        if match:
            found.append((match.group(1), match.group(2), loc))
    deduped: dict[str, tuple[str, str, str]] = {}
    for vendor, model, url in found:
        deduped[url] = (vendor, model, url)
    return [deduped[key] for key in sorted(deduped)]


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #


def normalise_unit(raw: str) -> str:
    key = raw.strip().lower()
    if key in UNIT_ALIASES:
        return UNIT_ALIASES[key]
    return "per_" + re.sub(r"[^a-z0-9]+", "_", key).strip("_")


def parse_rates(seo_values: list[str]) -> list[dict]:
    """
    Turn the pricing panel into billing rows.

    The panel is emitted as a flat sequence of labelled values. A combined row
    reads

        Input / Output | 20% off | $1.6 |  / 1M | $2 | official price
                       |           $8   |  / 1M | $10    | official price

    that is: a label, then one or more `<price> " / <unit>" [<official> "official
    price"]` pairs. Only the first pair of a group carries the "<n>% off"
    marker; later pairs inherit it. Media rows are priced in other units
    (" / s", " / Per Time") and sometimes carry no official price at all.

    Anything that does not match this shape is ignored rather than guessed at.
    """
    rows: list[dict] = []
    label: str | None = None
    discount: int | None = None
    index = 0
    while index < len(seo_values):
        value = seo_values[index]

        off = OFF_RE.match(value)
        if off:
            discount = int(off.group(1))
            index += 1
            continue

        money = MONEY_RE.match(value)
        unit_match = (
            UNIT_RE.match(seo_values[index + 1]) if index + 1 < len(seo_values) else None
        )
        if money and unit_match:
            official: float | None = None
            consumed = 2
            if (
                index + 3 < len(seo_values)
                and MONEY_RE.match(seo_values[index + 2])
                and seo_values[index + 3] == "official price"
            ):
                official = float(MONEY_RE.match(seo_values[index + 2]).group(1))
                consumed = 4
            rows.append(
                {
                    "label": label,
                    "discount_percent": discount,
                    "price_usd": float(money.group(1)),
                    "official_price_usd": official,
                    "unit": normalise_unit(unit_match.group(1)),
                    "unit_raw": unit_match.group(1).strip(),
                    "slot": None,
                }
            )
            index += consumed
            continue

        if value != "official price" and not UNIT_RE.match(value):
            label = value.strip()
            discount = None
        index += 1
    return rows


def assign_slots(rows: list[dict]) -> list[dict]:
    """Label every billing row with the field it corresponds to."""
    groups: list[tuple[str, list[dict]]] = []
    for row in rows:
        key = (row["label"] or "").strip().lower()
        if groups and groups[-1][0] == key:
            groups[-1][1].append(row)
        else:
            groups.append((key, [row]))

    for key, group in groups:
        # Anything not billed per token is a media-rate row; resolution and
        # duration live in its label, which media_rates keeps verbatim.
        if all(row["unit"] != "per_1m_tokens" for row in group):
            for row in group:
                row["slot"] = "media"
            continue

        mentions_input = "input" in key
        mentions_output = "output" in key
        if mentions_input and mentions_output:
            # Combined "input / output" row: first pair is input, second output.
            for position, row in enumerate(group):
                row["slot"] = {0: "input", 1: "output"}.get(position)
        elif "cache write" in key or "cache creation" in key:
            for row in group:
                row["slot"] = "cache_write"
        elif "cache read" in key or "cached input" in key or "cache hit" in key:
            for row in group:
                row["slot"] = "cache_read"
        elif mentions_input:
            for row in group:
                row["slot"] = "input"
        elif mentions_output:
            for row in group:
                row["slot"] = "output"
    return rows


def parse_model_page(
    html: str,
    vendor_slug: str,
    model_id: str,
    url: str,
    vendor_meta: dict | None = None,
) -> dict | None:
    seo_values = SEOTEXT_RE.findall(html)
    rates = assign_slots(parse_rates(seo_values))
    if not rates:
        return None

    context_window = None
    for value in seo_values:
        tokens = TOKENS_RE.match(value)
        if tokens:
            digits = int(tokens.group(1).replace(",", ""))
            if digits >= 1000:
                context_window = max(context_window or 0, digits)

    # Scope the modality chips to their own block so that the chips of
    # "Related Models" further down the page are not picked up.
    start = html.find("Modalities")
    end = html.find("Related Models", start + 1) if start != -1 else -1
    block = html[start:end] if start != -1 and end > start else ""
    block_text = re.sub(r"\s+", " ", TAG_RE.sub(" ", block))
    modalities = sorted(
        {m.lower() for m in MODALITY_IN_RE.findall(block_text)}
        | {f"{m.lower()}_output" for m in MODALITY_OUT_RE.findall(block_text)}
    )

    description_match = DESC_RE.search(html)
    description = description_match.group(1) if description_match else ""

    def first(slot: str) -> dict | None:
        for row in rates:
            if row["slot"] == slot:
                return row
        return None

    input_row = first("input")
    output_row = first("output")
    cache_write_row = first("cache_write")
    cache_read_row = first("cache_read")

    media_rows = [row for row in rates if row["slot"] == "media"]

    def first_media(unit: str) -> dict | None:
        for row in media_rows:
            if row["unit"] == unit:
                return row
        return None

    per_second_row = first_media("per_second")
    per_image_row = first_media("per_image")
    per_run_row = first_media("per_run")

    discounts = [
        row["discount_percent"]
        for row in rates
        if row["discount_percent"] is not None
    ]
    provider = PROVIDER_ALIASES.get(vendor_slug, vendor_slug)
    vendor_meta = vendor_meta or {}

    return {
        "id": model_id,
        "provider": provider,
        "provider_name": vendor_meta.get("name") or provider,
        "vendor_slug": vendor_slug,
        "modalities": modalities,
        "context_window": context_window,
        "input_per_mtok_usd": input_row["price_usd"] if input_row else None,
        "output_per_mtok_usd": output_row["price_usd"] if output_row else None,
        "official_input_per_mtok_usd": (
            input_row["official_price_usd"] if input_row else None
        ),
        "official_output_per_mtok_usd": (
            output_row["official_price_usd"] if output_row else None
        ),
        "cache_write_per_mtok_usd": (
            cache_write_row["price_usd"] if cache_write_row else None
        ),
        "cache_read_per_mtok_usd": (
            cache_read_row["price_usd"] if cache_read_row else None
        ),
        # Media rows are priced per resolution / duration, so a single scalar
        # cannot stand for the model. These two fields carry the entry rate
        # only; media_rates holds every tier verbatim.
        "per_second_usd": per_second_row["price_usd"] if per_second_row else None,
        "per_image_usd": per_image_row["price_usd"] if per_image_row else None,
        "per_run_usd": per_run_row["price_usd"] if per_run_row else None,
        "media_rates": [
            {
                "label": row["label"],
                "unit": row["unit"],
                "price_usd": row["price_usd"],
                "official_price_usd": row["official_price_usd"],
                "discount_percent": row["discount_percent"],
            }
            for row in media_rows
        ],
        "discount_percent": min(discounts) if discounts else None,
        "billing_rows": rates,
        "description": description.strip(),
        "source_url": url,
        "source_url_kind": "catalog_page",
        "official_price_source_url": vendor_meta.get("pricing_url"),
    }


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #


CSV_COLUMNS = [
    "id",
    "provider",
    "modalities",
    "context_window",
    "input_per_mtok_usd",
    "output_per_mtok_usd",
    "official_input_per_mtok_usd",
    "official_output_per_mtok_usd",
    "cache_write_per_mtok_usd",
    "cache_read_per_mtok_usd",
    "per_second_usd",
    "per_image_usd",
    "per_run_usd",
    "discount_percent",
    "media_rates",
    "source_url",
    "official_price_source_url",
]


def write_outputs(out_dir: str, payload: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "pricing.json")
    csv_path = os.path.join(out_dir, "pricing.csv")

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=False)
        handle.write("\n")

    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for model in payload["models"]:
            row = dict(model)
            row["modalities"] = "|".join(model.get("modalities") or [])
            row["media_rates"] = json.dumps(model.get("media_rates") or [], ensure_ascii=False)
            writer.writerow(row)


def load_previous(path: str) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            previous = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}
    return {model["id"]: model for model in previous.get("models", [])}


# --------------------------------------------------------------------------- #
# README blocks
#
# The numbers that appear in README.md are generated, not typed. Each block is
# delimited by a pair of markers so a refresh can rewrite it in place, which
# keeps the headline figures from rotting as the snapshot moves.
# --------------------------------------------------------------------------- #

BLENDED_INPUT_WEIGHT = 3
BLENDED_OUTPUT_WEIGHT = 1


def blended_usd(model: dict) -> float | None:
    """Blended cost per 1M tokens at a 3:1 input:output ratio."""
    price_in = model.get("input_per_mtok_usd")
    price_out = model.get("output_per_mtok_usd")
    if price_in is None or price_out is None:
        return None
    total_weight = BLENDED_INPUT_WEIGHT + BLENDED_OUTPUT_WEIGHT
    return (price_in * BLENDED_INPUT_WEIGHT + price_out * BLENDED_OUTPUT_WEIGHT) / total_weight


def render_snapshot_block(payload: dict) -> str:
    models = payload["models"]
    providers = {model["provider"] for model in models}
    priced = [m for m in models if blended_usd(m) is not None]
    return (
        f"> Last snapshot: **{payload['snapshot_date']}** · Currency: **{payload['currency']}**\n"
        f"> Models: **{len(models)}** · Providers: **{len(providers)}** · "
        f"With input and output rates: **{len(priced)}**\n"
    )


def render_cheapest_block(payload: dict, limit: int = 10) -> str:
    rows = [
        (blended_usd(model), model)
        for model in payload["models"]
        # Text-billed rows only: media rows mix per-image and per-token units.
        if blended_usd(model) is not None
        and model.get("provider") != "gptproto"
        and "text" in (model.get("modalities") or [])
    ]
    rows.sort(key=lambda pair: pair[0])
    lines = [
        f"| Model | Provider | Input ($/1M) | Output ($/1M) | Blended ($/1M) |",
        "|---|---|---|---|---|",
    ]
    for blended, model in rows[:limit]:
        lines.append(
            f"| `{model['id']}` | {model['provider']} | "
            f"{model['input_per_mtok_usd']:.4g} | {model['output_per_mtok_usd']:.4g} | "
            f"{blended:.4g} |"
        )
    return "\n".join(lines) + "\n"


README_RENDERERS = {
    "snapshot": render_snapshot_block,
    "cheapest": render_cheapest_block,
}


def update_readme(path: str, payload: dict, verbose: bool = False) -> bool:
    """Rewrite every generated block in README.md. Returns True if it changed."""
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    updated = text
    for name, renderer in README_RENDERERS.items():
        begin = f"<!-- BEGIN GENERATED: {name} -->"
        end = f"<!-- END GENERATED: {name} -->"
        pattern = re.compile(
            re.escape(begin) + r".*?" + re.escape(end), re.DOTALL
        )
        if not pattern.search(updated):
            log(verbose, f"README block '{name}' not found — skipped")
            continue
        replacement = "\n".join([begin, renderer(payload).rstrip("\n"), end])
        updated = pattern.sub(lambda _match, body=replacement: body, updated)

    if updated == text:
        return False
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(updated)
    return True


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/", help="output directory (default: data/)")
    parser.add_argument("--dry-run", action="store_true", help="verify without writing")
    parser.add_argument("--limit", type=int, default=0, help="only process N models")
    parser.add_argument("--delay", type=float, default=0.3, help="seconds between requests")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--readme", default=os.path.join(REPO_ROOT, "README.md"))
    parser.add_argument(
        "--no-readme", action="store_true", help="do not refresh the generated README blocks"
    )
    parser.add_argument(
        "--readme-only",
        metavar="JSON",
        nargs="?",
        const="",
        help="skip fetching; refresh the README blocks from an existing pricing.json",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.readme_only is not None:
        payload_path = args.readme_only or os.path.join(args.out, "pricing.json")
        try:
            with open(payload_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            print(f"FATAL: cannot read {payload_path}: {exc}", file=sys.stderr)
            return 2
        if update_readme(args.readme, payload, args.verbose):
            print(f"refreshed generated blocks in {args.readme}", file=sys.stderr)
        else:
            print("README is already up to date", file=sys.stderr)
        return 0

    sources = load_sources()
    sitemap_url = sources["dataset"].get("sitemap_url") or SITEMAP_URL
    catalog_url = sources["dataset"].get("catalog_url") or CATALOG_URL
    vendors = sources["vendors"]

    sitemap = fetch(sitemap_url, timeout=args.timeout)
    if sitemap is None:
        print("FATAL: could not read the model sitemap", file=sys.stderr)
        return 2

    targets = discover_models(sitemap)
    if args.limit:
        targets = targets[: args.limit]
    print(f"discovered {len(targets)} canonical model pages", file=sys.stderr)

    previous = load_previous(os.path.join(args.out, "pricing.json"))
    models: list[dict] = []
    fetched = 0
    skipped: list[dict] = []

    for number, (vendor, model_id, url) in enumerate(targets, start=1):
        html = fetch(url, timeout=args.timeout)
        record = (
            parse_model_page(html, vendor, model_id, url, vendors.get(vendor))
            if html is not None
            else None
        )

        if record is None:
            carried = previous.get(model_id)
            reason = "unreachable" if html is None else "no pricing panel"
            if carried is not None:
                models.append(carried)
                reason += " (kept previous values)"
            skipped.append({"id": model_id, "url": url, "reason": reason})
            log(args.verbose, f"[{number}/{len(targets)}] SKIP {model_id} — {reason}")
        else:
            models.append(record)
            fetched += 1
            log(
                args.verbose,
                f"[{number}/{len(targets)}] ok   {model_id} "
                f"({len(record['billing_rows'])} rows)",
            )

        time.sleep(args.delay)

    models.sort(key=lambda item: (item["provider"], item["id"]))
    payload = {
        "snapshot_date": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "currency": "USD",
        "unit": "per_1m_tokens",
        "source": catalog_url,
        "source_kind": (
            "Published rates read from GPTProto's public model catalog, where the "
            "upstream list price and the GPTProto rate appear side by side. "
            "List prices are cross-checked against scripts/sources.yaml."
        ),
        "counts": {
            "total": len(models),
            "fetched": fetched,
            "carried_over": len(models) - fetched,
            "skipped": len(skipped),
        },
        "models": models,
        "skipped": skipped,
    }

    print(
        f"fetched {fetched}/{len(targets)} models · "
        f"carried over {len(models) - fetched} · skipped {len(skipped)}",
        file=sys.stderr,
    )

    if args.dry_run:
        print("dry run — nothing written", file=sys.stderr)
        for item in skipped:
            print(f"  skipped: {item['id']} ({item['reason']})", file=sys.stderr)
        return 0

    write_outputs(args.out, payload)
    print(f"wrote {args.out}pricing.json and {args.out}pricing.csv", file=sys.stderr)

    if not args.no_readme:
        if update_readme(args.readme, payload, args.verbose):
            print(f"refreshed generated blocks in {args.readme}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
