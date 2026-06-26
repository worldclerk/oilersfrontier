#!/usr/bin/env python3
"""
update_news.py — Oilers Frontier News Updater
Fetches oil & gas news from RSS feeds (stdlib only) and regenerates
article HTML pages + index.html with the latest articles.

No third-party packages required. Runs: Python 3.8+
"""

import urllib.request
import xml.etree.ElementTree as ET
import json
import os
import re
import html
import datetime
import sys
import time

# ── Configuration ───────────────────────────────────────────────────────────
FEEDS = [
    {
        "name": "OilPrice.com",
        "url": "https://oilprice.com/rss/main",
        "default_category": "markets",
    },
    {
        "name": "Offshore Technology",
        "url": "https://www.offshore-technology.com/feed/",
        "default_category": "upstream",
    },
    {
        "name": "Rigzone Latest",
        "url": "https://www.rigzone.com/news/rss/rigzone_latest.aspx",
        "default_category": "production",
    },
]

MAX_ARTICLES = 24          # total to keep in articles.json
GRID_ARTICLES = 9          # shown on homepage grid
FETCH_TIMEOUT = 15         # seconds per feed
USER_AGENT = (
    "Mozilla/5.0 (compatible; OilersFrontierBot/1.0; "
    "+https://oilersfrontier.com)"
)

# Category keyword mapping
CATEGORY_KEYWORDS = {
    "exploration":  ["explor", "wildcat", "discovery", "seismic", "drilling", "prospect", "reserve"],
    "production":   ["produc", "output", "barrel", "bpd", "operator", "well", "completion", "rig"],
    "markets":      ["price", "crude", "brent", "wti", "futures", "opec", "market", "supply", "demand"],
    "pipeline":     ["pipeline", "transport", "midstream", "transit", "tariff", "ferc", "approval"],
    "lng":          ["lng", "liquefied", "natural gas", "regasif", "terminal", "export", "import"],
    "offshore":     ["offshore", "deepwater", "gulf", "platform", "fpso", "subsea", "arctic"],
    "technology":   ["technolog", "ai", "digital", "automation", "software", "sensor", "data"],
    "regulations":  ["regulat", "policy", "sec", "epa", "law", "permit", "sanction", "tax"],
}

TAG_STYLES = {
    "exploration": "tag-exploration",
    "production":  "tag-production",
    "markets":     "tag-markets",
    "pipeline":    "tag-pipeline",
    "lng":         "tag-lng",
    "offshore":    "tag-offshore",
    "technology":  "tag-technology",
    "regulations": "tag-regulations",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Helpers ──────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Turn a headline into a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[''']", "", text)
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text[:80].strip("-")


def detect_category(title: str, description: str, default: str) -> str:
    """Guess category from keywords in title/description."""
    combined = (title + " " + description).lower()
    scores = {cat: 0 for cat in CATEGORY_KEYWORDS}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                scores[cat] += 1
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else default


def estimate_read_time(text: str) -> int:
    """Estimate reading time in minutes (200 wpm average)."""
    words = len(re.findall(r"\w+", text))
    return max(1, round(words / 200))


def clean_html_text(raw: str) -> str:
    """Strip HTML tags and decode entities."""
    raw = re.sub(r"<[^>]+>", " ", raw or "")
    raw = html.unescape(raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def parse_date(date_str: str) -> datetime.datetime:
    """Try common RSS date formats; fall back to now."""
    if not date_str:
        return datetime.datetime.utcnow()
    fmts = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in fmts:
        try:
            return datetime.datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            pass
    return datetime.datetime.utcnow()


def format_date_display(dt: datetime.datetime) -> str:
    return dt.strftime("%B %-d, %Y") if sys.platform != "win32" else dt.strftime("%B %d, %Y").replace(" 0", " ")


def format_iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── RSS Fetching ─────────────────────────────────────────────────────────────

def fetch_feed(feed_cfg: dict) -> list:
    """Fetch one RSS feed and return a list of article dicts."""
    url = feed_cfg["url"]
    default_category = feed_cfg["default_category"]
    articles = []

    print(f"  → Fetching {feed_cfg['name']} ({url})")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw_xml = resp.read()
    except Exception as exc:
        print(f"    ✗ Failed to fetch {url}: {exc}")
        return []

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        print(f"    ✗ XML parse error for {url}: {exc}")
        return []

    # Handle both RSS 2.0 and Atom feeds
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "content": "http://purl.org/rss/1.0/modules/content/",
        "media": "http://search.yahoo.com/mrss/",
    }

    # RSS 2.0
    items = root.findall(".//item")
    # Atom fallback
    if not items:
        items = root.findall(".//atom:entry", ns)

    for item in items:
        def get(tag, fallback=""):
            el = item.find(tag)
            if el is None:
                el = item.find(f"atom:{tag}", ns)
            if el is not None:
                return (el.text or "").strip()
            return fallback

        title = clean_html_text(get("title"))
        link  = get("link")
        # Atom link is an attribute
        if not link:
            link_el = item.find("atom:link", ns)
            if link_el is not None:
                link = link_el.get("href", "")

        description_raw = (
            get("description")
            or get("content:encoded", "")
            or get("summary", "")
        )
        description = clean_html_text(description_raw)[:600]
        pub_date_str = get("pubDate") or get("published") or get("updated")
        pub_dt = parse_date(pub_date_str)

        if not title or not link:
            continue

        slug = slugify(title)
        category = detect_category(title, description, default_category)
        read_time = estimate_read_time(description)

        articles.append({
            "title":       title,
            "link":        link,
            "description": description,
            "pub_date":    format_iso(pub_dt),
            "pub_display": format_date_display(pub_dt),
            "category":    category,
            "slug":        slug,
            "read_time":   read_time,
            "source":      feed_cfg["name"],
            "filename":    f"article-{slug}.html",
        })

    print(f"    ✓ Got {len(articles)} items from {feed_cfg['name']}")
    return articles


def fetch_all_feeds() -> list:
    """Fetch all configured feeds and deduplicate by slug."""
    all_articles = []
    seen_slugs = set()

    for feed_cfg in FEEDS:
        articles = fetch_feed(feed_cfg)
        for a in articles:
            if a["slug"] not in seen_slugs:
                all_articles.append(a)
                seen_slugs.add(a["slug"])
        # Be polite between feeds
        time.sleep(1)

    # Sort by date descending
    all_articles.sort(key=lambda a: a["pub_date"], reverse=True)
    return all_articles[:MAX_ARTICLES]


# ── HTML Components ───────────────────────────────────────────────────────────

HEADER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="{meta_description}">
  <meta property="og:title" content="{title} | Oilers Frontier">
  <meta property="og:type" content="article">
  <meta property="og:url" content="https://oilersfrontier.com/{filename}">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="https://oilersfrontier.com/{filename}">
  <title>{title} | Oilers Frontier</title>
  <link rel="stylesheet" href="style.css">
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='4' fill='%231a2e1a'/><line x1='16' y1='4' x2='16' y2='20' stroke='%23d4a017' stroke-width='2'/><polygon points='16,4 20,10 12,10' fill='%23d4a017'/><rect x='12' y='10' width='8' height='10' fill='%232f5230'/><rect x='10' y='20' width='12' height='3' fill='%23243d24'/></svg>">
</head>
<body>
"""

NAVBAR_HTML = """\
  <div class="ticker-bar">
    <div class="container ticker-inner">
      <div class="ticker-label">Breaking</div>
      <div style="overflow:hidden;flex:1;">
        <div class="ticker-track">
          <span class="ticker-item">Brent Crude holds above $82 as OPEC+ maintains production discipline</span>
          <span class="ticker-item">EIA reports US crude inventories draw of 3.2M barrels</span>
          <span class="ticker-item">Permian Basin operators report record Q2 output exceeding 6.2M bpd</span>
          <span class="ticker-item">Brent Crude holds above $82 as OPEC+ maintains production discipline</span>
          <span class="ticker-item">EIA reports US crude inventories draw of 3.2M barrels</span>
          <span class="ticker-item">Permian Basin operators report record Q2 output exceeding 6.2M bpd</span>
        </div>
      </div>
    </div>
  </div>
  <header class="site-header">
    <div class="header-inner">
      <a href="/" class="logo-wrap">
        <svg class="logo-icon" viewBox="0 0 46 46" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect width="46" height="46" rx="6" fill="#1a2e1a"/>
          <line x1="23" y1="5" x2="23" y2="33" stroke="#d4a017" stroke-width="2.5" stroke-linecap="round"/>
          <line x1="17" y1="10" x2="29" y2="10" stroke="#d4a017" stroke-width="1.8" stroke-linecap="round"/>
          <polygon points="23,5 29,14 17,14" fill="#2f5230" stroke="#d4a017" stroke-width="1.5"/>
          <line x1="17" y1="14" x2="23" y2="24" stroke="#d4a017" stroke-width="1.5" opacity="0.8"/>
          <line x1="29" y1="14" x2="23" y2="24" stroke="#d4a017" stroke-width="1.5" opacity="0.8"/>
          <line x1="14" y1="24" x2="32" y2="24" stroke="#d4a017" stroke-width="1.8"/>
          <line x1="23" y1="24" x2="14" y2="36" stroke="#d4a017" stroke-width="2"/>
          <line x1="23" y1="24" x2="32" y2="36" stroke="#d4a017" stroke-width="2"/>
          <rect x="11" y="36" width="24" height="4" rx="1" fill="#d4a017" opacity="0.9"/>
          <rect x="20" y="40" width="6" height="4" rx="1" fill="#2f5230" stroke="#d4a017" stroke-width="0.8"/>
        </svg>
        <div class="logo-text">
          <span class="logo-name">Oilers Frontier</span>
          <span class="logo-tagline">Oil &amp; Gas Intelligence</span>
        </div>
      </a>
      <nav class="main-nav">
        <a href="/">Home</a>
        <a href="/#news-grid" onclick="filterCat('exploration');return false;">Exploration</a>
        <a href="/#news-grid" onclick="filterCat('production');return false;">Production</a>
        <a href="/#news-grid" onclick="filterCat('markets');return false;">Markets</a>
        <a href="/#news-grid" onclick="filterCat('pipeline');return false;">Pipeline</a>
        <a href="/#about">About</a>
        <a href="leadership-changes.html">Leadership</a>
      </nav>
      <div class="header-actions">
        <a href="/#newsletter" class="btn-subscribe">Subscribe</a>
      </div>
    </div>
  </header>
"""

FOOTER_HTML = """\
  <footer class="site-footer">
    <div class="container">
      <div class="footer-grid">
        <div class="footer-about">
          <a href="/" class="logo-wrap" style="margin-bottom:16px;display:flex;">
            <div class="logo-text">
              <span class="logo-name">Oilers Frontier</span>
              <span class="logo-tagline">Oil &amp; Gas Intelligence</span>
            </div>
          </a>
          <p>Independent oil and gas news covering exploration, production, markets, pipelines, and energy policy.</p>
        </div>
        <div class="footer-col">
          <h4>Coverage</h4>
          <ul class="footer-links">
            <li><a href="/#exploration">Exploration</a></li>
            <li><a href="/#production">Production</a></li>
            <li><a href="/#markets">Markets</a></li>
            <li><a href="/#pipeline">Pipelines</a></li>
            <li><a href="/#lng">LNG</a></li>
          </ul>
        </div>
        <div class="footer-col">
          <h4>Company</h4>
          <ul class="footer-links">
            <li><a href="/#about">About</a></li>
            <li><a href="/#newsletter">Newsletter</a></li>
            <li><a href="/">Home</a></li>
          </ul>
        </div>
        <div class="footer-col">
          <h4>Data Sources</h4>
          <ul class="footer-links">
            <li><a href="https://www.eia.gov" target="_blank" rel="noopener">EIA</a></li>
            <li><a href="https://oilprice.com" target="_blank" rel="noopener">OilPrice.com</a></li>
            <li><a href="https://www.rigzone.com" target="_blank" rel="noopener">Rigzone</a></li>
          </ul>
        </div>
      </div>
    </div>
    <div class="footer-bottom">
      <span>&copy; {year} Oilers Frontier. All rights reserved.</span>
      <div class="footer-bottom-links">
        <a href="#">Privacy Policy</a>
        <a href="#">Terms of Use</a>
      </div>
    </div>
  </footer>
</body>
</html>
"""


def build_article_card_html(article: dict) -> str:
    """Build a single news card HTML block for the homepage grid."""
    tag_class = TAG_STYLES.get(article["category"], "tag-markets")
    cat_label = article["category"].title()
    title_esc = html.escape(article["title"])
    excerpt = article["description"][:180] + ("…" if len(article["description"]) > 180 else "")
    excerpt_esc = html.escape(excerpt)

    return f"""\
            <article class="news-card" data-category="{article['category']}">
              <div class="news-card-thumb">
                <div class="news-card-thumb-placeholder">
                  <svg viewBox="0 0 48 48" fill="none" stroke="#d4a017" stroke-width="1.5" aria-hidden="true">
                    <line x1="24" y1="6" x2="24" y2="32"/>
                    <polygon points="24,6 30,16 18,16" fill="#2f5230" stroke="#d4a017"/>
                    <rect x="18" y="16" width="12" height="16" fill="#243d24" stroke="#d4a017"/>
                    <rect x="14" y="32" width="20" height="4" fill="#1a2e1a" stroke="#d4a017"/>
                  </svg>
                </div>
              </div>
              <div class="news-card-body">
                <span class="tag {tag_class}">{cat_label}</span>
                <h2><a href="{article['filename']}">{title_esc}</a></h2>
                <p class="excerpt">{excerpt_esc}</p>
                <div class="news-card-footer">
                  <span class="date">{article['pub_display']}</span>
                  <span class="read-time">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="8" cy="8" r="6"/><polyline points="8,4 8,8 11,10"/></svg>
                    {article['read_time']} min read
                  </span>
                </div>
              </div>
            </article>"""


def build_article_page(article: dict) -> str:
    """Build a full standalone article HTML page."""
    tag_class = TAG_STYLES.get(article["category"], "tag-markets")
    cat_label = article["category"].title()
    title_esc = html.escape(article["title"])
    desc_esc = html.escape(article["description"])
    year = datetime.datetime.utcnow().year
    meta_desc = article["description"][:155].replace('"', "'")

    paragraphs = ""
    # Split description into paragraphs if it's long enough
    sentences = re.split(r"(?<=[.!?])\s+", article["description"])
    chunk = []
    for sentence in sentences:
        chunk.append(sentence)
        if len(" ".join(chunk)) > 200:
            paragraphs += f"<p>{html.escape(' '.join(chunk))}</p>\n    "
            chunk = []
    if chunk:
        paragraphs += f"<p>{html.escape(' '.join(chunk))}</p>"

    header = HEADER_HTML.format(
        meta_description=html.escape(meta_desc),
        title=title_esc,
        filename=article["filename"],
    )
    footer = FOOTER_HTML.format(year=year)

    return f"""{header}{NAVBAR_HTML}
  <div class="article-header">
    <div class="article-header-inner">
      <span class="tag {tag_class}">{cat_label}</span>
      <h1>{title_esc}</h1>
      <div class="article-meta">
        <span class="author">{html.escape(article['source'])}</span>
        <span class="divider">|</span>
        <span>{article['pub_display']}</span>
        <span class="divider">·</span>
        <span>{article['read_time']} min read</span>
      </div>
    </div>
  </div>

  <main>
    <div class="article-body-wrap">
      <div class="article-body">
        {paragraphs}
        <p style="margin-top:24px;">
          <a href="{html.escape(article['link'])}" target="_blank" rel="noopener noreferrer"
             style="display:inline-flex;align-items:center;gap:8px;background:var(--green-mid);
                    border:1px solid var(--card-border);border-radius:var(--radius-sm);
                    padding:10px 18px;color:var(--gold);font-family:var(--font-head);
                    font-size:0.82rem;font-weight:600;letter-spacing:1px;text-transform:uppercase;">
            Read Full Story at {html.escape(article['source'])} →
          </a>
        </p>
      </div>
    </div>
  </main>
{footer}"""


# ── Index Regeneration ────────────────────────────────────────────────────────

def build_index_html(articles: list, timestamp: str) -> str:
    """Regenerate index.html with latest articles from live feeds."""
    grid_articles = articles[:GRID_ARTICLES]

    # Build article cards
    cards_html = "\n".join(build_article_card_html(a) for a in grid_articles)

    # Featured article (first one)
    featured = grid_articles[0] if grid_articles else None

    # Build sidebar breaking news list from articles 10-15
    sidebar_articles = articles[9:14] if len(articles) > 9 else articles[:5]
    sidebar_html = ""
    for a in sidebar_articles:
        tag_class = TAG_STYLES.get(a["category"], "tag-markets")
        cat_label = a["category"].title()
        title_esc = html.escape(a["title"])
        sidebar_html += f"""\
              <li class="breaking-item">
                <h4><a href="{a['filename']}">{title_esc}</a></h4>
                <p class="meta"><span class="tag {tag_class}" style="font-size:0.6rem;">{cat_label}</span> &nbsp;{a['pub_display']}</p>
              </li>
"""

    featured_html = ""
    if featured:
        tag_class = TAG_STYLES.get(featured["category"], "tag-markets")
        cat_label = featured["category"].title()
        excerpt = featured["description"][:300] + ("…" if len(featured["description"]) > 300 else "")
        featured_html = f"""\
            <span class="tag {tag_class}">{cat_label}</span>
            <h1><a href="{featured['filename']}" style="color:inherit;text-decoration:none;">{html.escape(featured['title'])}</a></h1>
            <p class="excerpt">{html.escape(excerpt)}</p>
            <div class="article-meta">
              <span class="author">{html.escape(featured['source'])}</span>
              <span class="divider">|</span>
              <span>{featured['pub_display']}</span>
              <span class="divider">·</span>
              <span>{featured['read_time']} min read</span>
            </div>"""

    year = datetime.datetime.utcnow().year

    # Read the static index.html, replace the news-grid and featured content
    index_path = os.path.join(SCRIPT_DIR, "index.html")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print("  ✗ index.html not found — skipping index regeneration")
        return ""

    # Replace news grid contents
    grid_pattern = re.compile(
        r'(<div class="news-grid"[^>]*id="news-grid"[^>]*>)(.*?)(</div><!-- /news-grid -->)',
        re.DOTALL,
    )
    new_content = grid_pattern.sub(
        r'\1\n' + cards_html + r'\n          \3',
        content,
    )

    # Update last-updated comment
    updated_comment = f"<!-- Last updated by update_news.py at {timestamp} UTC -->"
    if "<!-- Last updated" in new_content:
        new_content = re.sub(r"<!-- Last updated.*?-->", updated_comment, new_content)
    else:
        new_content = new_content.replace("</head>", f"  {updated_comment}\n</head>", 1)

    return new_content


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n🛢  Oilers Frontier — News Update {timestamp} UTC")
    print("=" * 60)

    # Fetch articles
    print("\n[1/4] Fetching RSS feeds…")
    articles = fetch_all_feeds()
    print(f"  Total unique articles collected: {len(articles)}")

    if not articles:
        print("  ⚠ No articles fetched — keeping existing content.")
        return

    # Save articles.json manifest
    print("\n[2/4] Saving articles.json manifest…")
    manifest_path = os.path.join(SCRIPT_DIR, "articles.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "updated": timestamp,
                "count": len(articles),
                "articles": articles,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"  ✓ Saved {manifest_path}")

    # Generate individual article pages
    print("\n[3/4] Generating article pages…")
    generated = 0
    for article in articles:
        page_html = build_article_page(article)
        page_path = os.path.join(SCRIPT_DIR, article["filename"])
        with open(page_path, "w", encoding="utf-8") as f:
            f.write(page_html)
        generated += 1

    print(f"  ✓ Generated {generated} article HTML files")

    # Regenerate index.html
    print("\n[4/4] Regenerating index.html…")
    new_index = build_index_html(articles, timestamp)
    if new_index:
        index_path = os.path.join(SCRIPT_DIR, "index.html")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(new_index)
        print(f"  ✓ index.html updated with {min(GRID_ARTICLES, len(articles))} articles")
    else:
        print("  ⚠ index.html regeneration skipped")

    # Generate sitemap.xml + robots.txt
    print("\n[5/5] Generating sitemap.xml and robots.txt…")
    generate_sitemap(articles)
    generate_robots()

    print("\n✅ Update complete!")
    print(f"   Articles: {len(articles)}")
    print(f"   Pages:    {generated}")
    print(f"   Updated:  {timestamp} UTC\n")


def generate_sitemap(articles: list) -> None:
    """Generate sitemap.xml for all pages."""
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    urls = [
        f'  <url><loc>https://oilersfrontier.com/</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>',
        f'  <url><loc>https://oilersfrontier.com/leadership-changes.html</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.8</priority></url>',
    ]
    for article in articles:
        pub = article.get("pub_date", today)[:10]
        urls.append(
            f'  <url><loc>https://oilersfrontier.com/{article["filename"]}</loc>'
            f'<lastmod>{pub}</lastmod><changefreq>never</changefreq><priority>0.8</priority></url>'
        )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n</urlset>\n"
    )
    sitemap_path = os.path.join(SCRIPT_DIR, "sitemap.xml")
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  ✓ Generated sitemap.xml ({len(urls)} URLs)")


def generate_robots() -> None:
    """Generate robots.txt allowing all crawlers."""
    robots_path = os.path.join(SCRIPT_DIR, "robots.txt")
    with open(robots_path, "w", encoding="utf-8") as f:
        f.write("User-agent: *\nAllow: /\n\nSitemap: https://oilersfrontier.com/sitemap.xml\n")
    print("  ✓ Generated robots.txt")


if __name__ == "__main__":
    main()
