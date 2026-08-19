import os
import re
import json
import hashlib
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SITEMAP_URL = "https://www.pokemoncenter.com/sitemaps/pages.xml"
STATE_FILE = Path("state.json")

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
if DISCORD_WEBHOOK:
    requests.post(DISCORD_WEBHOOK, json={"content": "🧪 **Pokémon Center Drop Radar — TEST SUCCESSFUL**\nGitHub → Radar → Discord → iPhone is working."}, timeout=15).raise_for_status()
WATCH_TERMS = [
    "elite trainer box",
    "etb",
    "ultra-premium collection",
    "ultra premium collection",
    "upc",
    "booster bundle",
    "booster box",
    "booster display",
    "pokemon tcg",
    "prismatic evolutions",
    "ascended heroes",
    "30th",
]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 PokemonCenterDropRadar/1.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
})


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass

    return {
        "known_urls": [],
        "products": {},
        "baseline_complete": False,
        "queue_active": False,
    }


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, sort_keys=True)
    )


def send_discord(title, message, url=None):
    print(title)
    print(message)

    if not DISCORD_WEBHOOK:
        print("No Discord webhook configured.")
        return

    text = f"**{title}**\n{message}"

    if url:
        text += f"\n<{url}>"

    requests.post(
        DISCORD_WEBHOOK,
        json={"content": text[:1900]},
        timeout=15,
    ).raise_for_status()


def extract_urls(xml):
    return set(
        re.findall(
            r"<loc>\s*(.*?)\s*</loc>",
            xml,
            flags=re.IGNORECASE,
        )
    )


def matches_watch_terms(text):
    text = text.lower()
    return any(term in text for term in WATCH_TERMS)


def detect_queue(response):
    blob = (response.url + "\n" + response.text[:250000]).lower()

    queue_terms = [
        "queue-it",
        "queueittoken",
        "virtual queue",
        "waiting room",
        "you are now in line",
        "you are in line",
    ]

    return any(term in blob for term in queue_terms)


def analyze_page(html):
    soup = BeautifulSoup(html, "html.parser")

    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )

    metadata = []

    for tag in soup.find_all("meta"):
        content = tag.get("content")
        if content:
            metadata.append(content)

    for tag in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"},
    ):
        if tag.string:
            metadata.append(tag.string)

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    visible = " ".join(soup.stripped_strings)

    combined = " ".join(
        [title, *metadata, visible]
    )

    combined = re.sub(
        r"\s+",
        " ",
        combined,
    ).strip()

    signature = hashlib.sha256(
        combined.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()

    lower = combined.lower()

    buy_terms = [
        "add to cart",
        "add to basket",
        "preorder",
        "pre-order",
    ]

    out_terms = [
        "out of stock",
        "sold out",
        "currently unavailable",
    ]

    buy_signal = any(
        term in lower
        for term in buy_terms
    )

    out_of_stock = any(
        term in lower
        for term in out_terms
    )

    return {
        "title": title,
        "text": combined,
        "signature": signature,
        "buy": buy_signal,
        "oos": out_of_stock,
    }


def main():
    state = load_state()

    known_urls = set(
        state.get("known_urls", [])
    )

    print("Starting Pokémon Center scan...")

    try:
        sitemap = session.get(
            SITEMAP_URL,
            timeout=20,
        )

        queue_now = detect_queue(sitemap)

        if sitemap.status_code == 200:
            urls = extract_urls(
                sitemap.text
            )

            new_urls = urls - known_urls

            if state["baseline_complete"]:
                for url in sorted(new_urls):
                    if matches_watch_terms(url):
                        send_discord(
                            "🚨 EARLY POKÉMON CENTER SIGNAL",
                            (
                                "A new matching public Pokémon "
                                "Center product URL appeared.\n\n"
                                "Confidence: MEDIUM\n"
                                "This may indicate a product "
                                "is being staged."
                            ),
                            url,
                        )

            known_urls.update(urls)

            if not state["baseline_complete"]:
                print(
                    "Initial baseline created. "
                    "Future new URLs will alert."
                )
                state["baseline_complete"] = True

        else:
            print(
                f"Sitemap returned "
                f"HTTP {sitemap.status_code}"
            )

        old_queue = state.get(
            "queue_active",
            False,
        )

        if queue_now and not old_queue:
            send_discord(
                "🟡 POKÉMON CENTER QUEUE SIGNAL",
                (
                    "A public waiting-room or queue "
                    "signal was detected.\n\n"
                    "Confidence: LOW by itself.\n"
                    "Watch for additional product signals."
                ),
            )

        state["queue_active"] = queue_now

    except Exception as exc:
        print(
            f"Sitemap check failed: {exc}"
        )

    product_urls = [
        url
        for url in known_urls
        if "/product/" in url.lower()
        and matches_watch_terms(url)
    ]

    product_urls = product_urls[-50:]

    for url in product_urls:
        try:
            response = session.get(
                url,
                timeout=20,
            )

            if response.status_code != 200:
                continue

            page = analyze_page(
                response.text
            )

            if not matches_watch_terms(
                page["text"] + " " + url
            ):
                continue

            old = state["products"].get(
                url,
                {},
            )

            changed = (
                old.get("signature")
                and old.get("signature")
                != page["signature"]
            )

            buy_appeared = (
                page["buy"]
                and not old.get("buy", False)
            )

            stock_returned = (
                old.get("oos", False)
                and not page["oos"]
            )

            if buy_appeared or stock_returned:
                send_discord(
                    "🔥 POKÉMON CENTER PRODUCT MAY BE LIVE",
                    (
                        f"{page['title']}\n\n"
                        "Confidence: VERY HIGH\n"
                        "A preorder/Add-to-Cart or "
                        "inventory-state signal appeared."
                    ),
                    url,
                )

            elif changed:
                send_discord(
                    "⚡ POKÉMON CENTER STAGING CHANGE",
                    (
                        f"{page['title']}\n\n"
                        "Confidence: MEDIUM\n"
                        "A watched public product page "
                        "changed since the last scan."
                    ),
                    url,
                )

            state["products"][url] = {
                "signature": page["signature"],
                "buy": page["buy"],
                "oos": page["oos"],
                "title": page["title"],
            }

        except Exception as exc:
            print(
                f"Product check failed "
                f"for {url}: {exc}"
            )

    state["known_urls"] = sorted(
        known_urls
    )[-20000:]

    save_state(state)

    print("Scan complete.")


if __name__ == "__main__":
    main()
