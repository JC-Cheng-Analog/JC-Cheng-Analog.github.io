#!/usr/bin/env python3
"""Maintain SEO metadata, sitemap.xml, and robots.txt from seo-config.json.

Uses only Python's standard library. Safe defaults:
- Only pages listed in seo-config.json are modified.
- Existing page content and styles are preserved.
- SEO tags are wrapped in managed markers for repeatable updates.
- Sitemap contains only entries with sitemap=true and indexable robots rules.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

SEO_START = "<!-- SEO:START (managed by update-seo.py) -->"
SEO_END = "<!-- SEO:END (managed by update-seo.py) -->"


@dataclass(frozen=True)
class SiteConfig:
    name: str
    alternate_names: list[str]
    base_url: str
    default_language: str
    default_og_locale: str
    author_name: str
    default_social_image: str | None
    robots: str


def load_config(path: Path) -> tuple[SiteConfig, list[dict[str, Any]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"找不到設定檔：{path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"seo-config.json 格式錯誤：{exc}") from exc

    site_raw = raw.get("site", {})
    required = ["name", "base_url", "default_language", "author_name", "robots"]
    missing = [key for key in required if not site_raw.get(key)]
    if missing:
        raise SystemExit("site 設定缺少：" + ", ".join(missing))

    base_url = site_raw["base_url"].rstrip("/") + "/"
    site = SiteConfig(
        name=site_raw["name"],
        alternate_names=list(site_raw.get("alternate_names", [])),
        base_url=base_url,
        default_language=site_raw["default_language"],
        default_og_locale=site_raw.get("default_og_locale", site_raw["default_language"].replace("-", "_")),
        author_name=site_raw["author_name"],
        default_social_image=site_raw.get("default_social_image"),
        robots=site_raw["robots"],
    )
    pages = raw.get("pages", [])
    if not isinstance(pages, list) or not pages:
        raise SystemExit("pages 必須是非空白陣列")
    return site, pages


def absolute_url(site: SiteConfig, page: dict[str, Any]) -> str:
    path = str(page.get("path", "")).lstrip("/")
    return urljoin(site.base_url, path)


def esc_attr(value: str) -> str:
    return html.escape(value, quote=True)


def json_script(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return f'<script id="seo-structured-data" type="application/ld+json">\n{payload}\n</script>'


def breadcrumb_schema(site: SiteConfig, page: dict[str, Any]) -> dict[str, Any] | None:
    crumbs = page.get("breadcrumbs")
    if not crumbs:
        return None
    items = []
    for position, crumb in enumerate(crumbs, start=1):
        item = {
            "@type": "ListItem",
            "position": position,
            "name": crumb["name"],
        }
        path = str(crumb.get("path", ""))
        item["item"] = urljoin(site.base_url, path.lstrip("/"))
        items.append(item)
    return {
        "@type": "BreadcrumbList",
        "@id": absolute_url(site, page) + "#breadcrumb",
        "itemListElement": items,
    }


def structured_data(site: SiteConfig, page: dict[str, Any]) -> dict[str, Any]:
    url = absolute_url(site, page)
    language = page.get("language", site.default_language)
    og_locale = page.get("og_locale", site.default_og_locale)
    page_type = page.get("page_type", "website")
    image = page.get("image") or site.default_social_image

    graph: list[dict[str, Any]] = []
    if page_type == "website" and not str(page.get("path", "")).strip("/"):
        website: dict[str, Any] = {
            "@type": "WebSite",
            "@id": site.base_url + "#website",
            "url": site.base_url,
            "name": site.name,
            "inLanguage": language,
        }
        if site.alternate_names:
            website["alternateName"] = site.alternate_names
        graph.append(website)
    elif page_type == "article":
        article: dict[str, Any] = {
            "@type": "Article",
            "@id": url + "#article",
            "mainEntityOfPage": {"@type": "WebPage", "@id": url},
            "headline": page["title"].split(" | ")[0],
            "description": page["description"],
            "url": url,
            "inLanguage": language,
            "author": {"@type": "Person", "name": site.author_name},
        }
        if page.get("lastmod"):
            article["dateModified"] = page["lastmod"]
        if page.get("date_published"):
            article["datePublished"] = page["date_published"]
        if image:
            article["image"] = image
        graph.append(article)
    elif page_type == "webapp":
        app: dict[str, Any] = {
            "@type": "WebApplication",
            "@id": url + "#webapp",
            "name": page["title"].split(" | ")[0],
            "description": page["description"],
            "url": url,
            "applicationCategory": page.get("application_category", "EducationalApplication"),
            "operatingSystem": "Any",
            "inLanguage": language,
            "isAccessibleForFree": True,
        }
        if image:
            app["image"] = image
        graph.append(app)
    else:
        graph.append({
            "@type": "WebPage",
            "@id": url + "#webpage",
            "url": url,
            "name": page["title"],
            "description": page["description"],
            "inLanguage": language,
        })

    crumbs = breadcrumb_schema(site, page)
    if crumbs:
        graph.append(crumbs)

    return {"@context": "https://schema.org", "@graph": graph}


def build_seo_block(site: SiteConfig, page: dict[str, Any]) -> str:
    title = page["title"]
    description = page["description"]
    url = absolute_url(site, page)
    language = page.get("language", site.default_language)
    og_locale = page.get("og_locale", site.default_og_locale)
    page_type = page.get("page_type", "website")
    og_type = "article" if page_type == "article" else "website"
    image = page.get("image") or site.default_social_image
    robots = page.get("robots", site.robots)

    lines = [
        SEO_START,
        f"<title>{html.escape(title)}</title>",
        f'<meta name="description" content="{esc_attr(description)}" />',
        f'<meta name="author" content="{esc_attr(site.author_name)}" />',
        f'<meta name="robots" content="{esc_attr(robots)}" />',
        f'<link rel="canonical" href="{esc_attr(url)}" />',
        "",
        f'<meta property="og:type" content="{og_type}" />',
        f'<meta property="og:site_name" content="{esc_attr(site.name)}" />',
        f'<meta property="og:title" content="{esc_attr(title)}" />',
        f'<meta property="og:description" content="{esc_attr(description)}" />',
        f'<meta property="og:url" content="{esc_attr(url)}" />',
        f'<meta property="og:locale" content="{esc_attr(og_locale)}" />',
    ]
    if image:
        lines.extend([
            f'<meta property="og:image" content="{esc_attr(image)}" />',
            f'<meta property="og:image:alt" content="{esc_attr(page.get("image_alt", title))}" />',
        ])

    lines.extend([
        "",
        '<meta name="twitter:card" content="summary" />',
        f'<meta name="twitter:title" content="{esc_attr(title)}" />',
        f'<meta name="twitter:description" content="{esc_attr(description)}" />',
    ])
    if image:
        lines.append(f'<meta name="twitter:image" content="{esc_attr(image)}" />')

    lines.extend([
        "",
        json_script(structured_data(site, page)),
        SEO_END,
    ])
    return "\n  ".join(lines)


def strip_legacy_seo(head: str) -> str:
    patterns = [
        r"\s*<title\b[^>]*>.*?</title>\s*",
        r"\s*<meta\b(?=[^>]*\bname\s*=\s*['\"](?:description|robots|author)['\"])[^>]*>\s*",
        r"\s*<link\b(?=[^>]*\brel\s*=\s*['\"]canonical['\"])[^>]*>\s*",
        r"\s*<meta\b(?=[^>]*\bproperty\s*=\s*['\"]og:[^'\"]+['\"])[^>]*>\s*",
        r"\s*<meta\b(?=[^>]*\bname\s*=\s*['\"]twitter:[^'\"]+['\"])[^>]*>\s*",
        r"\s*<script\b(?=[^>]*\bid\s*=\s*['\"]seo-structured-data['\"])[^>]*>.*?</script>\s*",
    ]
    result = head
    for pattern in patterns:
        result = re.sub(pattern, "\n", result, flags=re.IGNORECASE | re.DOTALL)
    return result


def update_html(text: str, block: str) -> tuple[str, str]:
    managed = re.compile(
        re.escape(SEO_START) + r".*?" + re.escape(SEO_END),
        flags=re.DOTALL,
    )
    if managed.search(text):
        return managed.sub(block, text, count=1), "updated"

    head_match = re.search(r"<head\b[^>]*>(.*?)</head>", text, flags=re.IGNORECASE | re.DOTALL)
    if not head_match:
        raise ValueError("找不到 <head>...</head>")

    original_inner = head_match.group(1)
    cleaned = strip_legacy_seo(original_inner)
    insert_match = re.search(r"\n\s*(?=<style\b|<link\b[^>]*rel=['\"]stylesheet['\"]|</?head\b)", cleaned, flags=re.IGNORECASE)
    if insert_match:
        pos = insert_match.start()
        new_inner = cleaned[:pos].rstrip() + "\n\n  " + block + "\n" + cleaned[pos:].lstrip("\n")
    else:
        new_inner = cleaned.rstrip() + "\n\n  " + block + "\n"

    start, end = head_match.span(1)
    return text[:start] + new_inner + text[end:], "bootstrapped"


def is_noindex(page: dict[str, Any], site: SiteConfig) -> bool:
    robots = str(page.get("robots", site.robots)).lower()
    return bool(re.search(r"(?:^|[,\s])noindex(?:$|[,\s])", robots))


def build_sitemap(site: SiteConfig, pages: list[dict[str, Any]]) -> str:
    ET.register_namespace("", "http://www.sitemaps.org/schemas/sitemap/0.9")
    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    urlset = ET.Element(f"{{{ns}}}urlset")
    seen: set[str] = set()
    for page in pages:
        if not page.get("sitemap", True) or is_noindex(page, site):
            continue
        url = absolute_url(site, page)
        if url in seen:
            raise ValueError(f"sitemap URL 重複：{url}")
        seen.add(url)
        url_node = ET.SubElement(urlset, f"{{{ns}}}url")
        ET.SubElement(url_node, f"{{{ns}}}loc").text = url
        if page.get("lastmod"):
            ET.SubElement(url_node, f"{{{ns}}}lastmod").text = page["lastmod"]
    ET.indent(urlset, space="  ")
    xml = ET.tostring(urlset, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml + "\n"


def build_robots(site: SiteConfig) -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {urljoin(site.base_url, 'sitemap.xml')}\n"


def validate_pages(site: SiteConfig, pages: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    files: set[str] = set()
    urls: set[str] = set()
    titles: dict[str, str] = {}
    descriptions: dict[str, str] = {}

    for index, page in enumerate(pages, start=1):
        label = page.get("file", f"pages[{index}]")
        for key in ("file", "path", "title", "description"):
            if key not in page:
                errors.append(f"{label}: 缺少 {key}")
        if errors and "file" not in page:
            continue
        filename = str(page.get("file", ""))
        url = absolute_url(site, page)
        if filename in files:
            errors.append(f"檔名重複：{filename}")
        files.add(filename)
        if url in urls:
            errors.append(f"Canonical URL 重複：{url}")
        urls.add(url)

        title = str(page.get("title", "")).strip()
        description = str(page.get("description", "")).strip()
        if title in titles:
            errors.append(f"Title 重複：{title}（{titles[title]}、{filename}）")
        titles[title] = filename
        if description in descriptions:
            errors.append(f"Description 重複：{filename} 與 {descriptions[description]}")
        descriptions[description] = filename
        if not (20 <= len(title) <= 75):
            errors.append(f"{filename}: title 長度 {len(title)}，建議約 20–75 字元")
        if not (70 <= len(description) <= 180):
            errors.append(f"{filename}: description 長度 {len(description)}，建議約 70–180 字元")
        if page.get("lastmod") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", str(page["lastmod"])):
            errors.append(f"{filename}: lastmod 格式錯誤")
    return errors


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        href = values.get("href")
        if href:
            self.links.append(href)


def audit_internal_links(root: Path, site: SiteConfig, pages: list[dict[str, Any]]) -> list[str]:
    sitemap_urls = {
        absolute_url(site, page)
        for page in pages
        if page.get("sitemap", True) and not is_noindex(page, site)
    }
    warnings: list[str] = []
    checked: set[Path] = set()
    for page in pages:
        file_path = root / str(page.get("file", ""))
        if not file_path.exists() or file_path in checked:
            continue
        checked.add(file_path)
        collector = LinkCollector()
        collector.feed(file_path.read_text(encoding="utf-8", errors="replace"))
        for href in collector.links:
            href = href.split("#", 1)[0].split("?", 1)[0]
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            parsed = urlparse(href)
            if parsed.scheme and parsed.netloc:
                if parsed.netloc != urlparse(site.base_url).netloc:
                    continue
                target_url = href
            elif href.endswith(".html") or href == "index.html":
                target_url = urljoin(absolute_url(site, page), href)
            else:
                continue
            if target_url.endswith("/index.html"):
                target_url = target_url[:-10]
            if target_url not in sitemap_urls:
                warnings.append(f"{file_path.name} 連到 sitemap 外頁面：{target_url}")
    return sorted(set(warnings))


def main() -> int:
    parser = argparse.ArgumentParser(description="更新 JC Cheng Analog SEO metadata 與 sitemap")
    parser.add_argument("--root", default=".", help="網站根目錄，預設目前資料夾")
    parser.add_argument("--config", default="seo-config.json", help="設定檔名稱")
    parser.add_argument("--check", action="store_true", help="只檢查，不寫入")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    config_path = root / args.config
    site, pages = load_config(config_path)

    errors = validate_pages(site, pages)
    if errors:
        print("[ERROR] 設定檢查失敗：")
        for error in errors:
            print(" -", error)
        return 1

    changes: list[tuple[Path, str, str]] = []
    for page in pages:
        if not page.get("manage_head", True):
            continue
        path = root / page["file"]
        if not path.exists():
            print(f"[ERROR] 找不到要管理的 HTML：{path.name}")
            return 1
        original = path.read_text(encoding="utf-8")
        try:
            updated, mode = update_html(original, build_seo_block(site, page))
        except ValueError as exc:
            print(f"[ERROR] {path.name}: {exc}")
            return 1
        if updated != original:
            changes.append((path, updated, mode))

    sitemap_path = root / "sitemap.xml"
    sitemap_text = build_sitemap(site, pages)
    current_sitemap = sitemap_path.read_text(encoding="utf-8") if sitemap_path.exists() else ""
    if current_sitemap != sitemap_text:
        changes.append((sitemap_path, sitemap_text, "generated"))

    robots_path = root / "robots.txt"
    robots_text = build_robots(site)
    current_robots = robots_path.read_text(encoding="utf-8") if robots_path.exists() else ""
    if current_robots != robots_text:
        changes.append((robots_path, robots_text, "generated"))

    warnings = audit_internal_links(root, site, pages)

    if args.check:
        if changes:
            print(f"[CHECK] 有 {len(changes)} 個檔案需要更新：")
            for path, _, mode in changes:
                print(f" - {path.name} ({mode})")
        else:
            print("[CHECK] SEO 檔案已同步。")
    else:
        for path, content, mode in changes:
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"[OK] {path.name}: {mode}")
        if not changes:
            print("[OK] SEO 檔案已是最新狀態。")

    if warnings:
        print("[WARNING] 內部連結檢查：")
        for warning in warnings:
            print(" -", warning)
        print("  這些頁面不會自動加入 sitemap；確認要公開索引後，再加入 seo-config.json。")

    print(f"[INFO] sitemap 收錄 {sum(1 for p in pages if p.get('sitemap', True) and not is_noindex(p, site))} 個 URL。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
