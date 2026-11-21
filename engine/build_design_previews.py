"""Build five isolated first-view proposals with the same real site directory.

These pages do not change the selected production masthead or embed forecast
numbers. Existing brand images are reused; no new stock or generated imagery.
"""
from html import escape
from pathlib import Path

ROOT = Path(__file__).parent
DEST = ROOT.parent / "docs" / "design" / "first-view"
CTA = '<a class="fv-cta" href="../../#forecast">ハンマー予報を見る</a>'
TITLE = '<h1>鮫暦<span>AI</span></h1>'
LOCATION = '<p class="fv-location">神子元島の海況とハンマー予報</p>'
TAGLINE = '<p class="fv-tagline">潮を読み、鮫に会う。</p>'
VARIANTS = [
    ("scroll", "浮世絵の帯", "浮世絵全体を一枚の絵として見せ、縦の題字を添える。元の世界観を最も生かす案。",
     f'<section class="fv-hero fv-scroll"><figure class="fv-scroll-art"><img src="/hammerhead/img/hero_ukiyoe.png" alt="ハンマーの群れと波を描いた、鮫暦AIの浮世絵" width="1376" height="768"><figcaption>神子元の海を、次に潜る日の手がかりに。</figcaption></figure><div class="fv-copy">{TITLE}{TAGLINE}{LOCATION}{CTA}</div></section>'),
    ("blue", "水面の余白", "青い水中写真と大きな題字。余白の下に群れが広がる、開放的な入口。",
     f'<section class="fv-hero fv-blue"><img class="fv-photo" src="/hammerhead/img/hero_school.jpg" alt="青い海に広がるハンマーヘッドシャークの群れ" width="2000" height="1337"><div class="fv-copy">{LOCATION}{TITLE}{TAGLINE}{CTA}</div><p class="fv-caption">海況を確かめ、次のダイビングを考える。</p></section>'),
    ("island", "島から潜る", "灯台と水平線を主役に、海上から水中へ。初めて訪れる人にも神子元という場所が伝わる案。",
     f'<section class="fv-hero fv-island"><div class="fv-island-main"><img class="fv-photo" src="/hammerhead/img/island.jpg" alt="神子元島の灯台と海" width="1600" height="1200"><div class="fv-copy">{LOCATION}{TITLE}<p class="fv-tagline">この島の潮を読み、<br>鮫に会う。</p></div></div><div class="fv-island-strip"><span>海の上から、海の中へ。</span><a href="../../#forecast">ハンマー予報を見る</a></div></section>'),
    ("book", "暦の扉", "家紋と題字を静かに据え、隣に浮世絵を広げる。和の暦を開くような落ち着いた構成。",
     f'<section class="fv-hero fv-book"><div class="fv-copy"><img class="fv-emblem" src="/hammerhead/img/kamon.png" alt="鮫暦の家紋" width="96" height="96">{LOCATION}{TITLE}{TAGLINE}<p class="fv-description">月の満ち欠け、潮の移ろい。<br>次に潜る日を、ここから。</p>{CTA}</div><div class="fv-book-art"><img src="/hammerhead/img/hero_ukiyoe.png" alt="藍と金で描かれたハンマーの群れと波" width="1376" height="768"></div></section>'),
    ("river", "群れの中へ", "題字と短いコピーを写真から分離し、群れの写真を大きく見せる。水中の臨場感を優先する案。",
     f'<section class="fv-hero fv-river"><div class="fv-copy">{TITLE}<div>{LOCATION}<p class="fv-tagline">潮を読み、<br>鮫に会う。</p></div></div><div class="fv-river-art"><img class="fv-photo" src="/hammerhead/img/hero_school.jpg" alt="水中を泳ぐハンマーヘッドシャークの群れ" width="2000" height="1337">{CTA}<p>次の一本を、海から考える。</p></div></section>'),
]


def document(title, body, styles, body_class=""):
    return f'''<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{escape(title)} — 鮫暦AI デザイン案</title>
<link rel="icon" href="../../img/kamon.png"><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;600;700&family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap" rel="stylesheet">
<style>{styles}</style></head><body class="{body_class}">{body}</body></html>'''


def build_previews():
    DEST.mkdir(parents=True, exist_ok=True)
    css = (ROOT / "home.css").read_text(encoding="utf-8") + "\n" + (ROOT / "first_view_options.css").read_text(encoding="utf-8")
    css = css.replace("/hammerhead/img/", "../../img/")
    sections = (ROOT / "home_sections.html").read_text(encoding="utf-8").replace("/hammerhead/img/", "../../img/")
    for old, new in [('href="#forecast"', 'href="../../#forecast"'), ('href="map/"', 'href="../../map/"'), ('href="aim/"', 'href="../../aim/"'), ('href="history/"', 'href="../../history/"')]:
        sections = sections.replace(old, new)
    cards = []
    for index, (slug, title, description, hero) in enumerate(VARIANTS, 1):
        hero = hero.replace("/hammerhead/img/", "../../img/")
        bar = f'<header class="fv-review-bar"><a href="./">5案の比較に戻る</a><span>案{index} · {escape(title)} / デザイン候補</span><a href="../../">公開サイト</a></header>'
        footer = '<footer class="fv-footer">ファーストビューのデザイン候補です。サイトマップと島・生態の紹介は全案共通です。</footer>'
        page = document(title, bar + '<main class="home-intro">' + hero + sections + '</main>' + footer, css)
        (DEST / f"{slug}.html").write_text(page, encoding="utf-8")
        (DEST / f"{slug}-thumb.html").write_text(document(title, hero, css, "fv-embed"), encoding="utf-8")
        recommend = ' is-recommended' if index == 1 else ''
        cards.append(f'<article class="fv-choice{recommend}"><div class="fv-thumb"><iframe src="{slug}-thumb.html" title="案{index} {escape(title)}の縮小プレビュー" tabindex="-1" aria-hidden="true" loading="eager"></iframe></div><h2>{index}. {escape(title)}</h2><p>{escape(description)}</p><a href="{slug}.html">この案を実際のサイズで見る</a></article>')
    gallery = '''<main class="fv-gallery"><header><h1>鮫暦AI、五つの入口。</h1><p>元の浮世絵・家紋・写真を使い、ファーストビューの構図を変えた5案です。各案を開くと、その下に共通のサイトマップと、神子元島・アカシュモクザメの紹介が続きます。</p></header><div class="fv-gallery-grid">''' + "".join(cards) + '''</div><div class="fv-gallery-note"><p>おすすめは「浮世絵の帯」。元のイラストを生かし、題字と絵の両方を読みやすく見せます。5案ともPC・スマートフォンに対応しています。</p><p>Claudeとの設計検討に、<a href="https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md" target="_blank" rel="noopener noreferrer">Anthropic公式 Frontend Design</a>の方針を取り入れました。新しい予報数値や素材は作っていません。</p><p><a href="../../">公開サイトに戻る</a> / <a href="../../aim/">月相と潮位曲線のカレンダーを見る</a></p></div></main>'''
    (DEST / "index.html").write_text(document("ファーストビュー5案", gallery, css), encoding="utf-8")
    print(f"Built {len(VARIANTS)} first-view proposals and comparison page")


if __name__ == "__main__":
    build_previews()
