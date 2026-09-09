#!/usr/bin/env python3
"""Static site generator for awesome-rclone.com.

Reads README.md (the single source of entries) and .pages/content.json (page
copy) and writes a prerendered hub page plus one page per major section, so
crawlers get the full content on the first fetch. Standard library only.

Usage: python3 build.py [output-dir]   (default: _site)
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES_DIR = ROOT / ".pages"
README = ROOT / "README.md"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "_site").resolve()

CONTENT = json.loads((PAGES_DIR / "content.json").read_text(encoding="utf-8"))
SITE = CONTENT["site"]
SITE_URL = SITE["url"].rstrip("/")
REPO_BLOB = "https://github.com/awesome-rclone/awesome-rclone/blob/main/"

ACCENTS = ["#ff7f50", "#0c9b8a", "#f0b44d", "#2f5a89", "#d65f7f", "#2d7a58"]
HUB_PREVIEW = 4
SKIP = {"Contents"}


# --------------------------------------------------------------------------- markdown
# The parser mirrors the old client-side one so section anchors (which Google
# already attributes impressions to) keep their ids.

def slugify(value: str) -> str:
    value = value.lower().replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def clean(text: str) -> str:
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!&])", r"\1", text).strip()


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def absolute(href: str) -> str:
    # README-relative links (contributing.md) must not 404 on the site
    if href.startswith(("http://", "https://", "#", "mailto:")):
        return href
    return REPO_BLOB + href.lstrip("./")


def inline(markdown: str) -> str:
    tokens: list[str] = []

    def stash(fragment: str) -> str:
        tokens.append(fragment)
        return f"@@T{len(tokens) - 1}@@"

    text = clean(markdown)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        lambda m: stash(f'<img class="inline-badge" src="{esc(m[2])}" alt="{esc(m[1])}" loading="lazy">'),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: stash(
            f'<a href="{esc(absolute(m[2]))}" target="_blank" rel="noopener noreferrer">{esc(m[1])}</a>'
        ),
        text,
    )
    text = re.sub(r"`([^`]+)`", lambda m: stash(f"<code>{esc(m[1])}</code>"), text)
    text = esc(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    return re.sub(r"@@T(\d+)@@", lambda m: tokens[int(m[1])], text)


def plain(markdown: str) -> str:
    text = clean(markdown)
    # badges (GitHub stars) are decoration, not description
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r" \1 ", text)
    text = re.sub(r"`([^`]+)`", r" \1 ", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r" \1 ", text)
    text = re.sub(r"\*([^*]+)\*", r" \1 ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Item:
    kind: str  # "project" | "note"
    description: str
    title: str = ""
    href: str = ""
    slug: str = ""
    url: str = ""  # internal page + anchor, assigned once pages are known


@dataclass
class Subsection:
    title: str
    slug: str
    paragraphs: list[str] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)


@dataclass
class Section:
    title: str
    slug: str
    paragraphs: list[str] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    subsections: list[Subsection] = field(default_factory=list)

    def all_items(self) -> list[Item]:
        return self.items + [item for sub in self.subsections for item in sub.items]

    def projects(self) -> list[Item]:
        return [item for item in self.all_items() if item.kind == "project"]


@dataclass
class Readme:
    title: str = "Awesome Rclone"
    tagline: str = ""
    intro: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)


def parse_bullet(text: str) -> Item:
    match = re.match(r"^\[([^\]]+)\]\(([^)]+)\)\s*-\s*(.+)$", text)
    if match:
        return Item("project", match[3], title=clean(match[1]), href=match[2])
    return Item("note", text)


def parse_readme(text: str) -> Readme:
    model = Readme()
    section: Section | None = None
    subsection: Subsection | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        paragraph = " ".join(buffer).strip()
        buffer = []
        if not paragraph:
            return
        target = subsection or section
        if target:
            target.paragraphs.append(paragraph)
        else:
            model.intro.append(paragraph)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "---":
            flush()
            continue
        if line.startswith("# "):
            flush()
            model.title = clean(re.sub(r"\s*\[!\[[^\]]*\]\([^)]+\)\]\([^)]+\)\s*$", "", line[2:]))
            continue
        if line.startswith("> "):
            flush()
            model.tagline = line[2:].strip()
            continue
        if line.startswith("## "):
            flush()
            title = clean(line[3:])
            section = Section(title, slugify(title))
            subsection = None
            model.sections.append(section)
            continue
        if line.startswith("### "):
            flush()
            if section is None:
                continue
            title = clean(line[4:])
            subsection = Subsection(title, slugify(f"{section.title}-{title}"))
            section.subsections.append(subsection)
            continue
        if line.startswith("- "):
            flush()
            if section is None or section.title in SKIP:
                continue
            (subsection or section).items.append(parse_bullet(line[2:].strip()))
            continue
        buffer.append(line)
    flush()

    model.sections = [s for s in model.sections if s.title not in SKIP]
    return model


# --------------------------------------------------------------------------- data

def readme_updated() -> datetime:
    try:
        stamp = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", str(README)],
            capture_output=True, text=True, check=True, cwd=ROOT,
        ).stdout.strip()
        if stamp:
            return datetime.fromisoformat(stamp)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    return datetime.now(timezone.utc)


UPDATED = readme_updated()
UPDATED_ISO = UPDATED.date().isoformat()
UPDATED_HUMAN = f"{UPDATED.day} {UPDATED.strftime('%B %Y')}"

readme = parse_readme(README.read_text(encoding="utf-8"))
SECTIONS_BY_TITLE = {section.title: section for section in readme.sections}

PAGES = CONTENT["pages"]
for page in PAGES:
    missing = [t for t in page["sections"] if t not in SECTIONS_BY_TITLE]
    if missing:
        sys.exit(f"content.json page '{page['slug']}' names unknown README sections: {missing}")
PAGE_OF_SECTION = {title: page for page in PAGES for title in page["sections"]}

# stable, unique anchors per page; hub-only sections anchor on the hub
for page in PAGES:
    seen: set[str] = set()
    for title in page["sections"]:
        for item in SECTIONS_BY_TITLE[title].projects():
            base = slugify(item.title) or "project"
            slug, n = base, 2
            while slug in seen:
                slug, n = f"{base}-{n}", n + 1
            seen.add(slug)
            item.slug = slug
            item.url = f"/{page['slug']}/#{slug}"
hub_seen: set[str] = set()
for section in readme.sections:
    if section.title in PAGE_OF_SECTION:
        continue
    for item in section.projects():
        base = slugify(item.title) or "project"
        slug, n = base, 2
        while slug in hub_seen:
            slug, n = f"{base}-{n}", n + 1
        hub_seen.add(slug)
        item.slug = slug
        item.url = f"/#{slug}"

TOTAL_PROJECTS = sum(len(s.projects()) for s in readme.sections if s.title != "Contributing")


# --------------------------------------------------------------------------- rendering

def paragraphs(items: list[str], css: str) -> str:
    return "".join(f'<p class="{css}">{inline(p)}</p>' for p in items)


def card(item: Item, level: int, with_id: bool = True) -> str:
    # hub previews repeat cards that live on a section page: no id there, so
    # the anchor stays unique to the page that owns the entry
    id_attr = f' id="{esc(item.slug)}"' if with_id else ""
    return (
        f'<article class="entry-card"{id_attr}>'
        f'<div class="entry-topline"><h{level} class="entry-title">'
        f'<a class="entry-link" href="{esc(absolute(item.href))}" target="_blank" rel="noopener noreferrer">{esc(item.title)}</a>'
        f"</h{level}></div>"
        f'<p class="entry-description">{inline(item.description)}</p>'
        f"</article>"
    )


def notes(items: list[Item]) -> str:
    if not items:
        return ""
    return '<div class="note-list">' + "".join(
        f'<div class="note-item"><div>{inline(item.description)}</div></div>' for item in items
    ) + "</div>"


def cards(items: list[Item], level: int) -> str:
    projects = [i for i in items if i.kind == "project"]
    grid = f'<div class="card-grid">{"".join(card(i, level) for i in projects)}</div>' if projects else ""
    return grid + notes([i for i in items if i.kind == "note"])


def shell_open(slug: str, index: int, heading: str, copy: str = "") -> str:
    accent = ACCENTS[index % len(ACCENTS)]
    return (
        f'<section class="section-shell" id="{esc(slug)}" style="--accent: {accent}">'
        f'<div class="section-header"><div class="section-kicker">{index + 1:02d}</div>'
        f'<div><h2 class="section-title">{heading}</h2>{copy}</div></div>'
    )


def render_section_full(section: Section, index: int) -> str:
    out = shell_open(section.slug, index, esc(section.title), paragraphs(section.paragraphs, "section-copy"))
    out += cards(section.items, 3)
    for sub in section.subsections:
        out += (
            f'<section class="subsection-shell" id="{esc(sub.slug)}">'
            f'<h3 class="subsection-title">{esc(sub.title)}</h3>'
            f'{paragraphs(sub.paragraphs, "subsection-copy")}{cards(sub.items, 4)}</section>'
        )
    return out + "</section>"


def render_subsections_promoted(section: Section, start: int) -> str:
    # single-section pages: the page title is the h1, so subsections become h2 blocks
    out = paragraphs(section.paragraphs, "section-copy")
    if section.items:
        out += shell_open(section.slug, start, esc(section.title)) + cards(section.items, 3) + "</section>"
        start += 1
    for i, sub in enumerate(section.subsections):
        out += shell_open(sub.slug, start + i, esc(sub.title), paragraphs(sub.paragraphs, "section-copy"))
        out += cards(sub.items, 3) + "</section>"
    return out


def render_section_preview(section: Section, index: int, page: dict) -> str:
    projects = section.projects()
    heading = f'<a href="/{page["slug"]}/">{esc(section.title)}</a>'
    out = shell_open(section.slug, index, heading, f'<p class="section-copy">{esc(page["teaser"])}</p>')
    out += f'<div class="card-grid">{"".join(card(i, 3, with_id=False) for i in projects[:HUB_PREVIEW])}</div>'
    out += f'<a class="see-all" href="/{page["slug"]}/">See all {len(projects)} projects</a></section>'
    return out


def search_box() -> str:
    return (
        '<div class="hero-tools"><input id="search-input" class="search-input" type="search" '
        'placeholder="Search projects, platforms or keywords" aria-label="Search projects"></div>'
    )


def hero_meta(count: int) -> str:
    return (
        f'<p class="hero-meta"><span>{count} projects</span>'
        f'<span>Updated <time datetime="{UPDATED_ISO}">{UPDATED_HUMAN}</time></span></p>'
    )


def toc_link(href: str, label: str, sub: bool = False, current: bool = False) -> str:
    css = "toc-link toc-sub" if sub else "toc-link"
    aria = ' aria-current="page"' if current else ""
    return f'<a class="{css}" href="{esc(href)}"{aria}>{esc(label)}</a>'


def layout_shell(aside: str, main: str) -> str:
    return (
        f'<div class="layout"><aside class="panel toc-panel">{aside}</aside>'
        f'<div><div id="search-results" class="search-results" hidden></div>'
        f'<main class="content" id="content">{main}</main></div></div>'
    )


def breadcrumbs(trail: list[tuple[str, str | None]]) -> str:
    items = "".join(
        f'<li><a href="{esc(href)}">{esc(name)}</a></li>' if href else f"<li>{esc(name)}</li>"
        for name, href in trail
    )
    return f'<nav class="breadcrumbs" aria-label="Breadcrumb"><ol>{items}</ol></nav>'


def breadcrumb_ld(trail: list[tuple[str, str | None]]) -> dict:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, **({"item": SITE_URL + href} if href else {})}
            for i, (name, href) in enumerate(trail)
        ],
    }


LAYOUT = (PAGES_DIR / "layout.html").read_text(encoding="utf-8")


def render_page(*, title: str, description: str, path: str, body: str, jsonld: object,
                og_title: str | None = None, robots: str = "index, follow") -> str:
    fills = {
        "title": esc(title),
        "description": esc(description),
        "canonical": esc(SITE_URL + path),
        "robots": robots,
        "og_title": esc(og_title or title),
        "jsonld": json.dumps(jsonld, ensure_ascii=False).replace("<", "\\u003c"),
        "body": body,
        "updated_human": esc(UPDATED_HUMAN),
    }
    out = LAYOUT
    for key, value in fills.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def build_hub() -> str:
    intro = paragraphs(readme.intro, "intro") + paragraphs(SITE["intro"], "intro")
    hero = (
        '<header class="hero"><section class="hero-card">'
        '<a class="awesome-badge" href="https://awesome.re" target="_blank" rel="noopener noreferrer">'
        '<img src="https://awesome.re/badge.svg" alt="Awesome badge" width="80" height="28"></a>'
        f"<h1>{esc(readme.title)}</h1>"
        f'<p class="tagline">{inline(readme.tagline)}</p>{intro}{hero_meta(TOTAL_PROJECTS)}{search_box()}'
        "</section></header>"
    )

    aside = '<h2 class="toc-title">Contents</h2><nav class="toc">'
    aside += '<div class="toc-group-title">Section pages</div>'
    for page in PAGES:
        aside += toc_link(f'/{page["slug"]}/', SECTIONS_BY_TITLE[page["sections"][0]].title if len(page["sections"]) == 1 else page["title"].split(":")[0])
    aside += '<div class="toc-group-title">Also on this page</div>'
    aside += toc_link("#what-is-rclone", SITE["what_is_rclone"]["heading"])
    for section in readme.sections:
        if section.title not in PAGE_OF_SECTION:
            aside += toc_link(f"#{section.slug}", section.title)
    aside += "</nav>"

    what = SITE["what_is_rclone"]
    main = (
        f'<section class="section-shell" id="what-is-rclone" style="--accent: {ACCENTS[1]}">'
        f'<div class="section-header"><div class="section-kicker">rclone</div>'
        f'<div><h2 class="section-title">{esc(what["heading"])}</h2>{paragraphs(what["paragraphs"], "section-copy")}</div></div>'
        "</section>"
    )
    rendered: set[str] = set()
    index = 0
    for section in readme.sections:
        page = PAGE_OF_SECTION.get(section.title)
        if page:
            if page["slug"] in rendered:
                continue  # multi-section pages get one preview block
            rendered.add(page["slug"])
            merged = Section(page["title"].split(":")[0], page["slug"])
            for t in page["sections"]:
                merged.items += SECTIONS_BY_TITLE[t].all_items()
            main += render_section_preview(merged, index, page)
        else:
            main += render_section_full(section, index)
        index += 1

    jsonld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "@id": SITE_URL + "/#website", "name": SITE["name"], "url": SITE_URL + "/",
             "description": SITE["description"], "inLanguage": "en"},
            {"@type": "CollectionPage", "url": SITE_URL + "/", "name": SITE["title"],
             "description": SITE["description"], "isPartOf": {"@id": SITE_URL + "/#website"},
             "dateModified": UPDATED_ISO,
             "mainEntity": {"@type": "ItemList", "name": SITE["name"], "numberOfItems": len(PAGES),
                            "itemListElement": [
                                {"@type": "ListItem", "position": i + 1, "name": p["title"], "url": f'{SITE_URL}/{p["slug"]}/'}
                                for i, p in enumerate(PAGES)]}},
        ],
    }
    return render_page(title=SITE["title"], description=SITE["description"], path="/",
                       body=hero + layout_shell(aside, main), jsonld=jsonld, og_title=SITE["name"])


def build_section_page(page: dict, position: int) -> str:
    sections = [SECTIONS_BY_TITLE[t] for t in page["sections"]]
    projects = [i for s in sections for i in s.projects()]
    trail = [("Home", "/"), (page["title"].split(":")[0], None)]

    hero = (
        '<header class="hero"><section class="hero-card">'
        f"{breadcrumbs(trail)}<h1>{esc(page['title'])}</h1>"
        f'<p class="tagline">{esc(page["teaser"])}</p>{paragraphs(page["intro"], "intro")}'
        f"{hero_meta(len(projects))}{search_box()}</section></header>"
    )

    aside = '<h2 class="toc-title">Contents</h2><nav class="toc"><div class="toc-group-title">On this page</div>'
    if len(sections) == 1:
        section = sections[0]
        if section.items:
            aside += toc_link(f"#{section.slug}", section.title)
        for sub in section.subsections:
            aside += toc_link(f"#{sub.slug}", sub.title)
        main = render_subsections_promoted(section, 0)
    else:
        main = ""
        for i, section in enumerate(sections):
            aside += toc_link(f"#{section.slug}", section.title)
            for sub in section.subsections:
                aside += toc_link(f"#{sub.slug}", sub.title, sub=True)
            main += render_section_full(section, i)
    aside += '<div class="toc-group-title">Other sections</div>' + toc_link("/", "Overview")
    for other in PAGES:
        aside += toc_link(f'/{other["slug"]}/', other["title"].split(":")[0], current=other is page)
    aside += "</nav>"

    prev_page = PAGES[position - 1] if position > 0 else None
    next_page = PAGES[position + 1] if position + 1 < len(PAGES) else None
    nav = '<nav class="page-nav" aria-label="Section pages">'
    nav += (f'<a class="prev" href="/{prev_page["slug"]}/">&larr; {esc(prev_page["title"].split(":")[0])}</a>'
            if prev_page else '<a class="prev" href="/">&larr; Overview</a>')
    if next_page:
        nav += f'<a class="next" href="/{next_page["slug"]}/">{esc(next_page["title"].split(":")[0])} &rarr;</a>'
    nav += "</nav>"

    path = f'/{page["slug"]}/'
    jsonld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "CollectionPage", "url": SITE_URL + path, "name": page["title"],
             "description": page["description"], "dateModified": UPDATED_ISO,
             "isPartOf": {"@type": "WebSite", "@id": SITE_URL + "/#website", "name": SITE["name"], "url": SITE_URL + "/"},
             "mainEntity": {"@type": "ItemList", "name": page["title"], "numberOfItems": len(projects),
                            "itemListElement": [
                                {"@type": "ListItem", "position": i + 1, "name": item.title,
                                 "url": absolute(item.href), "description": plain(item.description)}
                                for i, item in enumerate(projects)]}},
            breadcrumb_ld(trail),
        ],
    }
    return render_page(title=page["title"], description=page["description"], path=path,
                       body=hero + layout_shell(aside, main) + nav, jsonld=jsonld)


def build_404() -> str:
    body = (
        '<header class="hero"><section class="hero-card"><h1>Page not found</h1>'
        '<p class="tagline">There is nothing at this address.</p>'
        '<p class="intro"><a href="/">Back to the overview</a></p></section></header>'
    )
    return render_page(title="Page not found", description="This page does not exist.", path="/",
                       body=body, jsonld={}, robots="noindex")


def build_sitemap() -> str:
    urls = ["/"] + [f'/{p["slug"]}/' for p in PAGES]
    entries = "".join(f"<url><loc>{SITE_URL}{u}</loc><lastmod>{UPDATED_ISO}</lastmod></url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>\n'


def build_search_index() -> list[dict]:
    records = []
    for section in readme.sections:
        if section.title == "Contributing":
            continue
        groups = [(section.items, "")] + [(sub.items, sub.title) for sub in section.subsections]
        for items, sub_title in groups:
            for item in items:
                if item.kind != "project":
                    continue
                records.append({
                    "title": item.title, "description": plain(item.description),
                    "section": section.title, "subsection": sub_title,
                    "href": absolute(item.href), "url": item.url,
                })
    return records


# --------------------------------------------------------------------------- write

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

(OUT / "index.html").write_text(build_hub(), encoding="utf-8")
for i, page in enumerate(PAGES):
    folder = OUT / page["slug"]
    folder.mkdir()
    (folder / "index.html").write_text(build_section_page(page, i), encoding="utf-8")
(OUT / "404.html").write_text(build_404(), encoding="utf-8")
(OUT / "sitemap.xml").write_text(build_sitemap(), encoding="utf-8")
(OUT / "search-index.json").write_text(json.dumps(build_search_index(), ensure_ascii=False), encoding="utf-8")
for name in ("styles.css", "search.js", "robots.txt"):
    shutil.copy(PAGES_DIR / name, OUT / name)
if (ROOT / "CNAME").exists():
    shutil.copy(ROOT / "CNAME", OUT / "CNAME")

print(f"built {1 + len(PAGES)} pages, {TOTAL_PROJECTS} projects, updated {UPDATED_ISO} -> {OUT}")
