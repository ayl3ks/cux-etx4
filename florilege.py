"""Le Florilège : lit les flux RSS listés dans sources.json et fabrique
une page recto verso A4 (florilege.pdf) avec titres, chapôs et QR codes.

Dépendance : reportlab (pip install reportlab). Le reste est en Python standard.
Usage : python florilege.py [sources.json] [sortie.pdf]
"""
import json
import re
import sys
import html
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

SOURCES = sys.argv[1] if len(sys.argv) > 1 else "sources.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "site/florilege.pdf"

# ------------------------------------------------------------------ polices
FONT_DIR = "/usr/share/fonts/truetype/dejavu/"
for name, f in [("Serif", "DejaVuSerif.ttf"), ("Serif-B", "DejaVuSerif-Bold.ttf"),
                ("Sans", "DejaVuSans.ttf"), ("Sans-B", "DejaVuSans-Bold.ttf"),
                ("Sans-C", "DejaVuSansCondensed.ttf")]:
    pdfmetrics.registerFont(TTFont(name, FONT_DIR + f))

W, H = A4
M = 14 * mm
CW = W - 2 * M
GAP = 6 * mm
COL = (CW - GAP) / 2
BOTTOM = M + 10
GREY = colors.HexColor("#666666")

title_st = ParagraphStyle("t", fontName="Serif-B", fontSize=10.5, leading=13)
big_st = ParagraphStyle("b", fontName="Serif-B", fontSize=15, leading=18)
chapo_st = ParagraphStyle("c", fontName="Serif", fontSize=8.6, leading=11,
                          textColor=colors.HexColor("#333333"))

# ------------------------------------------------------------------ lecture des flux
UA = "Mozilla/5.0 (Florilege personnel; lecture RSS quotidienne)"


def clean(text, limit=None):
    """Retire le HTML, décode les entités, coupe proprement."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "…"
    return text


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        root = ET.fromstring(r.read())
    items = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = {}
        for child in node:
            ctag = child.tag.split("}")[-1]
            if ctag == "link" and child.get("href"):
                get.setdefault("link", child.get("href"))
            elif child.text and ctag not in get:
                get[ctag] = child.text
        titre = clean(get.get("title"))
        lien = (get.get("link") or get.get("guid") or "").strip()
        if titre and lien.startswith("http"):
            items.append({
                "titre": titre,
                "lien": lien,
                "chapo": clean(get.get("description") or get.get("summary"), 140),
            })
    return items


def load_all(cfg):
    data, report = {}, []
    for src, info in cfg["sources"].items():
        data[src] = {}
        for rubrique, url in info["flux"].items():
            try:
                items = fetch(url)
                data[src][rubrique] = items
                report.append(f"OK    {src:<11} {rubrique:<14} {len(items):>3} articles")
            except Exception as e:  # un flux en panne ne doit pas tout bloquer
                data[src][rubrique] = []
                report.append(f"ECHEC {src:<11} {rubrique:<14} {type(e).__name__}: {e}"[:120])
    print("\n".join(report))
    return data


# ------------------------------------------------------------------ sujet du jour
STOP = set("""avec dans pour sans sous entre selon apres avant depuis contre vers chez
plus moins tres trop tout tous toute toutes cette celui celle ceux elles leur leurs
nous vous sont etre avoir fait faire font peut doit veut quand comme mais donc alors
encore aussi deja ainsi notre votre quel quelle quels quelles dont faut face mois
annee jour jours semaine premier premiere nouveau nouvelle nouveaux selon pourquoi
comment video direct live entretien tribune analyse""".split())


def words(t):
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return {w for w in re.findall(r"[a-z]+", t) if len(w) >= 4 and w not in STOP}


def sujet_du_jour(une):
    """Cherche un même sujet traité en tête par Le Monde, Libération et Le Figaro."""
    lm, lib, fig = (une.get(s, [])[:15] for s in ("Le Monde", "Libération", "Le Figaro"))
    # Les mots présents dans trop de titres (« gouvernement », « France »…) ne
    # prouvent pas qu'on parle du même sujet : on les ignore.
    df = {}
    for it in lm + lib + fig:
        for w in words(it["titre"]):
            df[w] = df.get(w, 0) + 1
    common = {w for w, n in df.items() if n > 6}
    best, best_score = None, 0
    for a in lm:
        wa = words(a["titre"]) - common
        mates = []
        for src, pool in (("Libération", lib), ("Le Figaro", fig)):
            scored = [(len(wa & words(b["titre"])), b) for b in pool]
            scored = [s for s in scored if s[0] >= 2]
            if scored:
                sc, b = max(scored, key=lambda x: x[0])
                mates.append((sc, src, b))
        score = sum(m[0] for m in mates) + (2 if len(mates) == 2 else 0)
        if mates and score > best_score:
            best, best_score = (a, [(m[1], m[2]) for m in mates]), score
    return best


# ------------------------------------------------------------------ dessin
def qr(c, url, x, y, size):
    w = QrCodeWidget(url)
    b = w.getBounds()
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(w)
    renderPDF.draw(d, c, x, y)


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Page:
    def __init__(self, cfg):
        self.cfg = cfg

    def color(self, src):
        return colors.HexColor(self.cfg["sources"][src]["couleur"])

    def badge(self, c, x, y, src):
        c.setFont("Sans-B", 7)
        tw = c.stringWidth(src.upper(), "Sans-B", 7)
        c.setFillColor(self.color(src))
        c.rect(x, y - 2, tw + 6, 10, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.drawString(x + 3, y, src.upper())
        acces = self.cfg["sources"][src]["acces"]
        if acces in ("abonne", "bloque"):
            lab = "ABONNÉ ✓" if acces == "abonne" else "RÉSERVÉ ✕"
            x2 = x + tw + 10
            lw = c.stringWidth(lab, "Sans-B", 7)
            c.setStrokeColor(colors.black)
            c.setLineWidth(0.6)
            c.setFillColor(colors.black if acces == "bloque" else colors.white)
            c.rect(x2, y - 2, lw + 6, 10, stroke=1, fill=1)
            c.setFillColor(colors.white if acces == "bloque" else colors.black)
            c.drawString(x2 + 3, y, lab)

    def measure(self, it, w, big=False, qsize=17 * mm):
        tw = w - qsize - 4 * mm
        p = Paragraph(esc(it["titre"]), big_st if big else title_st)
        _, ph = p.wrap(tw, 400)
        q = Paragraph(esc(it["chapo"]), chapo_st)
        _, qh = q.wrap(tw, 400)
        h = max(17 + ph + qh, 12 + qsize) + 5 * mm
        return h, p, ph, q, qh

    def item(self, c, x, y, w, it, big=False, qsize=17 * mm):
        h, p, ph, q, qh = self.measure(it, w, big, qsize)
        self.badge(c, x, y - 8, it["src"])
        p.drawOn(c, x, y - 14 - ph)
        if it["chapo"]:
            q.drawOn(c, x, y - 17 - ph - qh)
        qr(c, it["lien"], x + w - qsize, y - 12 - qsize, qsize)
        return y - h

    def columns(self, c, items, y, bottom):
        """Remplit deux colonnes ; renvoie (y_fin, articles qui n'ont pas tenu)."""
        ys = [y, y]
        xs = [M, M + COL + GAP]
        rest = list(items)
        while rest:
            i = 0 if ys[0] >= ys[1] else 1
            h = self.measure(rest[0], COL)[0]
            if ys[i] - h < bottom:
                break
            ys[i] = self.item(c, xs[i], ys[i], COL, rest.pop(0))
        if min(ys) < y:
            c.setStrokeColor(colors.HexColor("#BBBBBB"))
            c.setLineWidth(0.4)
            c.line(M + COL + GAP / 2, y, M + COL + GAP / 2, min(ys) + 4 * mm)
        return min(ys), rest


def rule(c, y, label):
    c.setStrokeColor(colors.black)
    c.setLineWidth(1.4)
    c.line(M, y, W - M, y)
    c.setFont("Sans-B", 8.5)
    c.setFillColor(colors.black)
    c.drawString(M, y - 11, label.upper())
    return y - 18


JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


def header(c, page, now):
    c.setFillColor(colors.black)
    c.setFont("Serif-B", 26)
    c.drawString(M, H - M - 20, "Le Florilège")
    c.setFont("Sans", 8.5)
    c.drawRightString(W - M, H - M - 8, f"{JOURS[now.weekday()]} {now.day} {MOIS[now.month - 1]} {now.year}")
    c.drawRightString(W - M, H - M - 20, f"Page {page}/2 · édité à {now:%H:%M}")
    c.setLineWidth(2)
    c.line(M, H - M - 28, W - M, H - M - 28)
    c.setLineWidth(0.5)
    c.line(M, H - M - 31, W - M, H - M - 31)
    return H - M - 40


def footer(c, cfg):
    c.setFont("Sans-C", 6.8)
    x, y = M, M - 4
    for s, info in cfg["sources"].items():
        c.setFillColor(colors.HexColor(info["couleur"]))
        c.rect(x, y, 6, 6, stroke=0, fill=1)
        c.setFillColor(GREY)
        c.drawString(x + 8, y + 0.5, s)
        x += c.stringWidth(s, "Sans-C", 6.8) + 16
    c.drawRightString(W - M, y + 0.5, "ABONNÉ ✓ = inclus dans ton abonnement · RÉSERVÉ ✕ = payant, non abonné")


# ------------------------------------------------------------------ assemblage
def pick(data, src, rubrique, used, n):
    out = []
    for it in data.get(src, {}).get(rubrique, []):
        if it["lien"] in used or it["titre"] in used:
            continue
        used.update((it["lien"], it["titre"]))
        out.append(dict(it, src=src))
        if len(out) == n:
            break
    return out


def interleave(*lists):
    out = []
    for i in range(max((len(l) for l in lists), default=0)):
        out += [l[i] for l in lists if i < len(l)]
    return out


def main():
    cfg = json.load(open(SOURCES, encoding="utf-8"))
    data = load_all(cfg)
    now = datetime.now(ZoneInfo("Europe/Brussels"))
    used = set()

    une = {s: data.get(s, {}).get("une", []) for s in ("Le Monde", "Libération", "Le Figaro")}
    sdj = sujet_du_jour(une)

    import os
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    c = canvas.Canvas(OUT, pagesize=A4)
    c.setTitle("Le Florilège")
    pg = Page(cfg)

    # ---------- RECTO
    y = header(c, 1, now)
    if sdj:
        main_it, mates = sdj
        used.update((main_it["lien"], main_it["titre"]))
        y = rule(c, y, "Le sujet du jour · vu par plusieurs titres")
        y = pg.item(c, M, y, CW, dict(main_it, src="Le Monde"), big=True, qsize=22 * mm)
        mates_items = []
        for src, b in mates:
            used.update((b["lien"], b["titre"]))
            mates_items.append(dict(b, src=src))
        y, _ = pg.columns(c, mates_items, y, BOTTOM)

    inter = interleave(pick(data, "Le Monde", "international", used, 3),
                       pick(data, "Le Figaro", "international", used, 2))
    france = interleave(*(pick(data, s, "une", used, 4) for s in ("Le Monde", "Libération", "Le Figaro")))

    # on garde de la place pour l'international en bas du recto
    inter_h = max(pg.measure(i, COL)[0] for i in inter[:2]) + 18 if inter else 0
    y = rule(c, y, "France · à la une")
    y, overflow = pg.columns(c, france, y, BOTTOM + inter_h)
    if inter:
        y = rule(c, y, "International")
        y, inter_rest = pg.columns(c, inter, y, BOTTOM)
        overflow += inter_rest
    footer(c, cfg)
    c.showPage()

    # ---------- VERSO
    mid = H / 2 - 4 * mm
    y = header(c, 2, now)
    idees = pick(data, "Le Monde", "idees", used, 2)
    suite = [o for o in overflow if o["lien"] not in {i["lien"] for i in idees}]
    top = interleave(idees, suite)
    if top:
        y = rule(c, y, "Idées et suite")
        y, _ = pg.columns(c, top, y, mid + 40 * mm)

    enq = pick(data, "Mediapart", "une", used, 1)
    if enq:
        h = pg.measure(enq[0], CW - 6 * mm)[0] + 9 * mm
        c.setFillColor(colors.HexColor("#EEEEEE"))
        c.rect(M, y - h, CW, h, stroke=0, fill=1)
        c.setFont("Sans-B", 8.5)
        c.setFillColor(colors.black)
        c.drawString(M + 3 * mm, y - 5 * mm, "ENQUÊTES · MEDIAPART")
        pg.item(c, M + 3 * mm, y - 7 * mm, CW - 6 * mm, enq[0])
        y -= h + 5 * mm

    y = min(y, mid)
    c.setStrokeColor(colors.black)
    c.setLineWidth(2)
    c.line(M, y, W - M, y)
    c.setFillColor(colors.black)
    c.setFont("Serif-B", 16)
    c.drawString(M, y - 18, "Belgique")
    be = interleave(pick(data, "RTBF", "une", used, 4), pick(data, "Le Soir", "une", used, 4))
    pg.columns(c, be, y - 26, BOTTOM)
    footer(c, cfg)
    c.showPage()
    c.save()
    print(f"PDF écrit : {OUT}")


if __name__ == "__main__":
    main()
