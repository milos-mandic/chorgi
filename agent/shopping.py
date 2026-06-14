"""Shopping list — manually-added product links with a current price, stored as locked JSON.

Parallel to agent/watchlist.py but tuned for products: each item carries a
structured price (amount + currency) and an auto-assigned category instead of
duration/rating. Data lives in skills/shopping/workspace/shopping.json so a future
skill CLI and the dashboard API share one store (same arrangement as watchlist).

On a manual add we best-effort enrich from the link:
  - title + image + description via Open Graph / JSON-LD (reusing watchlist's fetcher)
  - price (amount + currency) from JSON-LD `offers` or og:price/itemprop meta
  - category + tags via a single Haiku call
"""

import html.parser
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SHOPPING_FILE = Path(__file__).parent.parent / "skills" / "shopping" / "workspace" / "shopping.json"

# Used when a price is saved without a currency and the page exposes none.
DEFAULT_CURRENCY = "EUR"

sys.path.insert(0, str(Path(__file__).parent.parent / "skills"))
import _shared  # noqa: E402

# Reuse watchlist's browser-like HTML fetch, JSON-LD walker, and host-label helper
# so scraping behaviour stays identical to the Watch hub (no duplicate UA/parse code).
from agent.watchlist import source_of, _fetch, _walk_ld  # noqa: E402,F401
from agent.api_client import call_haiku_sync  # noqa: E402


# ---------------------------------------------------------------------------
# Metadata enrichment (stdlib scraping + one Haiku call, no API keys for scraping)
# ---------------------------------------------------------------------------

class _ProductMetaParser(html.parser.HTMLParser):
    """Pull <title>, og:title/og:image/og:description, price meta, and ld+json blocks."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.og_title = ""
        self.og_image = ""
        self.description = ""
        self.price = ""
        self.currency = ""
        self.ld_blocks: list[str] = []
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_ld = False
        self._ld_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = dict(attrs)
            name = (a.get("name") or "").lower()
            prop = (a.get("property") or "").lower()
            itemprop = (a.get("itemprop") or "").lower()
            content = a.get("content") or ""
            if prop == "og:title" and not self.og_title:
                self.og_title = content
            elif prop == "og:image" and not self.og_image:
                self.og_image = content
            elif (name == "description" or prop == "og:description") and not self.description:
                self.description = content
            elif prop in ("og:price:amount", "product:price:amount") and not self.price:
                self.price = content
            elif prop in ("og:price:currency", "product:price:currency") and not self.currency:
                self.currency = content
            elif itemprop == "price" and not self.price:
                self.price = content
            elif itemprop == "pricecurrency" and not self.currency:
                self.currency = content
        elif tag == "script":
            if (dict(attrs).get("type") or "").lower() == "application/ld+json":
                self._in_ld = True
                self._ld_parts = []

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join(self._title_parts).strip()
        elif tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld_blocks.append("".join(self._ld_parts))

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)
        if self._in_ld:
            self._ld_parts.append(data)


def _ld_price(blocks: list[str]) -> tuple[str, str]:
    """Best-effort (amount, currency) from any JSON-LD `offers` block. '' if absent."""
    for raw in blocks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for obj in _walk_ld(data):
            if not isinstance(obj, dict):
                continue
            price = obj.get("price")
            if price is None:
                price = obj.get("lowPrice")
            if price is not None:
                return str(price), str(obj.get("priceCurrency") or "")
    return "", ""


def fetch_product_meta(url: str) -> dict:
    """Return {title, image, description, price, currency} via stdlib scraping.

    Price prefers JSON-LD `offers`, then og:price / itemprop meta. All best-effort:
    fields stay '' when a site blocks scraping or renders price only via JS.
    """
    result = {"url": url, "title": "", "image": "", "description": "",
              "price": "", "currency": ""}
    try:
        text = _fetch(url)
        parser = _ProductMetaParser()
        parser.feed(text)
        result["title"] = (parser.og_title or parser.title)[:200]
        result["image"] = parser.og_image
        result["description"] = (parser.description or "")[:500]
        amount, currency = _ld_price(parser.ld_blocks)
        if not amount and parser.price:
            amount, currency = parser.price, parser.currency
        result["price"] = amount
        result["currency"] = currency or parser.currency
    except Exception as e:
        logger.debug("Failed to fetch product metadata for %s: %s", url, e)
    return result


_CATEGORY_SYSTEM = (
    "You categorize shopping items. Given a product's title, description and price, "
    "reply with ONLY a compact JSON object and nothing else: "
    '{"category": "<one short category, e.g. Electronics, Home, Kitchen, Clothing, '
    'Books, Grocery, Tools, Beauty, Toys, Sports, Other>", '
    '"tags": ["<up to 3 short lowercase keyword tags>"]}'
)


def _parse_json(raw: str):
    """Parse a JSON object from a model reply, tolerating code fences / surrounding text."""
    for candidate in (raw, ):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            pass
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def categorize(title: str, description: str = "", price: str = "") -> dict:
    """Ask Haiku for {category, tags}. Returns {} on any failure (best-effort)."""
    if not (title or "").strip():
        return {}
    user = f"Title: {title}\nDescription: {description}\nPrice: {price}"
    try:
        raw, _ = call_haiku_sync(
            system=_CATEGORY_SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=120,
        )
    except Exception as e:
        logger.warning("Shopping categorize failed: %s", e)
        return {}
    data = _parse_json(raw)
    if not isinstance(data, dict):
        return {}
    out = {}
    cat = str(data.get("category") or "").strip()
    if cat:
        out["category"] = cat[:40]
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    tags = [str(t).strip().lower() for t in tags if str(t).strip()][:3]
    if tags:
        out["tags"] = tags
    return out


def parse_amount(value) -> float | None:
    """Parse a price amount from UI input or a scraped string; junk → None.

    Tolerates currency symbols and thousands separators, e.g. '$1,299.00'.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def add_from_url(
    url: str,
    *,
    title: str = "",
    amount: float | None = None,
    currency: str = "",
    notes: str = "",
    tags: list[str] | None = None,
    image: str = "",
) -> dict:
    """Enrich a product link and store it. Returns the stored item.

    Shared by the dashboard API and the skill CLI so the enrichment policy lives
    in one place: caller-supplied values always win; everything left blank is
    auto-filled — title/image/price by scraping, category + tags by Haiku.
    """
    meta = {}
    try:
        meta = fetch_product_meta(url)
    except Exception as e:
        logger.warning("Shopping enrichment failed for %s: %s", url, e)

    if amount is not None:
        cur = currency or DEFAULT_CURRENCY
    else:
        amount = parse_amount(meta.get("price"))
        cur = currency or meta.get("currency") or DEFAULT_CURRENCY

    title = (title or meta.get("title") or "").strip()

    cat = {}
    try:
        cat = categorize(title, meta.get("description", ""),
                         "" if amount is None else str(amount))
    except Exception as e:
        logger.warning("Shopping categorize failed for %s: %s", url, e)

    add_item(
        url,
        title=title,
        image=(image or meta.get("image") or "").strip(),
        amount=amount,
        currency=(cur or DEFAULT_CURRENCY).strip() or DEFAULT_CURRENCY,
        notes=(notes or "").strip(),
        tags=(tags or cat.get("tags", [])),
        category=cat.get("category", ""),
        source=source_of(url),
    )
    for it in load_items():
        if it["url"] == url:
            return it
    return {"url": url}


# ---------------------------------------------------------------------------
# Storage (locked JSON, shared with the skill CLI + dashboard API)
# ---------------------------------------------------------------------------

def load_items() -> list[dict]:
    """Return all shopping items as a flat list (newest first by convention)."""
    data = _shared.load_json(SHOPPING_FILE, [], tolerant=True)
    if isinstance(data, dict) and isinstance(data.get("shopping"), list):
        return data["shopping"]
    return data if isinstance(data, list) else []


def save_items(items: list[dict]) -> None:
    _shared.save_json(SHOPPING_FILE, items)


def add_item(
    url: str,
    *,
    title: str = "",
    image: str = "",
    amount: float | None = None,
    currency: str = DEFAULT_CURRENCY,
    notes: str = "",
    tags: list[str] | None = None,
    category: str = "",
    source: str = "",
) -> int:
    """Insert or merge a shopping item. Returns count of un-bought items."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _shared.file_lock(SHOPPING_FILE):
        items = load_items()
        for it in items:
            if it["url"] == url:
                if title:
                    it["title"] = title
                if image:
                    it["image"] = image
                if amount is not None:
                    it["amount"] = amount
                    it["price_checked_at"] = now
                if currency:
                    it["currency"] = currency
                if notes:
                    it["notes"] = notes
                if tags:
                    it["tags"] = tags
                if category:
                    it["category"] = category
                break
        else:
            items.insert(0, {
                "url": url,
                "title": title,
                "image": image,
                "amount": amount,
                "currency": currency or DEFAULT_CURRENCY,
                "notes": notes,
                "tags": tags or [],
                "category": category,
                "source": source or source_of(url),
                "saved_at": now,
                "bought": False,
                "price_checked_at": now if amount is not None else "",
            })
        save_items(items)
    return sum(1 for it in items if not it.get("bought"))


def set_bought(url: str, bought: bool) -> bool:
    """Toggle the bought flag for one item. Returns True if found."""
    with _shared.file_lock(SHOPPING_FILE):
        items = load_items()
        for it in items:
            if it["url"] == url:
                it["bought"] = bool(bought)
                save_items(items)
                return True
    return False


def set_price(url: str, amount: float | None, currency: str = "") -> bool:
    """Update an item's price (amount + optional currency). Returns True if found.

    Stamps price_checked_at so the future weekly updater and the UI can show
    when the price was last refreshed.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _shared.file_lock(SHOPPING_FILE):
        items = load_items()
        for it in items:
            if it["url"] == url:
                it["amount"] = amount
                if currency:
                    it["currency"] = currency
                it["price_checked_at"] = now if amount is not None else ""
                save_items(items)
                return True
    return False
