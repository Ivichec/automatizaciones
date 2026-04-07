#!/usr/bin/env python3
"""
buscador_casas.py — Buscador unificado de viviendas en portales inmobiliarios españoles.

Busca en Idealista, Fotocasa, Tecnocasa y Redpiso con filtros comunes.
"""

import argparse
import json
import re
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from urllib.parse import urlencode, quote

import requests
from bs4 import BeautifulSoup

# ─── Modelos ───

@dataclass
class Filtros:
    operacion: str = "venta"         # venta | alquiler
    ubicacion: str = "madrid"        # ciudad o zona
    precio_min: int = 0
    precio_max: int = 0
    habitaciones_min: int = 0
    habitaciones_max: int = 0
    metros_min: int = 0
    metros_max: int = 0
    pagina: int = 1

@dataclass
class Vivienda:
    portal: str = ""
    titulo: str = ""
    precio: str = ""
    ubicacion: str = ""
    habitaciones: str = ""
    metros: str = ""
    url: str = ""
    descripcion: str = ""


# ─── Clase base para portales ───

class PortalInmobiliario(ABC):
    NOMBRE = ""
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9",
    }

    def _get(self, url, headers=None, params=None):
        """GET con reintentos y manejo de errores."""
        h = {**self.HEADERS, **(headers or {})}
        for intento in range(3):
            try:
                resp = requests.get(url, headers=h, params=params, timeout=15)
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 403:
                    print(f"  [{self.NOMBRE}] Acceso bloqueado (403). El portal puede estar rechazando la conexión.")
                    return None
                if resp.status_code == 429:
                    wait = 2 ** (intento + 1)
                    print(f"  [{self.NOMBRE}] Rate limit, esperando {wait}s...")
                    time.sleep(wait)
                    continue
                print(f"  [{self.NOMBRE}] HTTP {resp.status_code}")
                return None
            except requests.RequestException as e:
                print(f"  [{self.NOMBRE}] Error de conexión: {e}")
                if intento < 2:
                    time.sleep(2)
        return None

    @abstractmethod
    def buscar(self, filtros: Filtros) -> list[Vivienda]:
        pass


# ─── Idealista ───

class Idealista(PortalInmobiliario):
    NOMBRE = "Idealista"
    BASE = "https://www.idealista.com"

    UBICACIONES = {
        "madrid": "madrid-madrid",
        "barcelona": "barcelona-barcelona",
        "valencia": "valencia-valencia",
        "sevilla": "sevilla-sevilla",
        "malaga": "malaga-malaga",
        "zaragoza": "zaragoza-zaragoza",
        "bilbao": "bilbao-vizcaya",
        "alicante": "alicante-alicante",
        "cordoba": "cordoba-cordoba",
        "granada": "granada-granada",
        "murcia": "murcia-murcia",
        "palma": "palma-de-mallorca-balears-illes",
        "las palmas": "las-palmas-de-gran-canaria",
        "valladolid": "valladolid-valladolid",
        "vigo": "vigo-pontevedra",
        "gijon": "gijon-asturias",
        "hospitalet": "l-hospitalet-de-llobregat-barcelona",
        "vitoria": "vitoria-gasteiz-alava",
        "santander": "santander-cantabria",
        "pamplona": "pamplona-navarra",
    }

    def _build_url(self, filtros: Filtros) -> str:
        loc = self.UBICACIONES.get(filtros.ubicacion.lower(), f"{filtros.ubicacion}-{filtros.ubicacion}")
        op = "venta-viviendas" if filtros.operacion == "venta" else "alquiler-viviendas"
        url = f"{self.BASE}/{op}/{loc}/"

        params = []
        if filtros.precio_min:
            params.append(f"minPrice={filtros.precio_min}")
        if filtros.precio_max:
            params.append(f"maxPrice={filtros.precio_max}")
        if filtros.metros_min:
            params.append(f"minSize={filtros.metros_min}")
        if filtros.metros_max:
            params.append(f"maxSize={filtros.metros_max}")
        if filtros.habitaciones_min:
            params.append(f"minRooms={filtros.habitaciones_min}")
        if filtros.habitaciones_max:
            params.append(f"maxRooms={filtros.habitaciones_max}")
        if filtros.pagina > 1:
            params.append(f"pagina={filtros.pagina}")

        if params:
            url += "?" + "&".join(params)
        return url

    def buscar(self, filtros: Filtros) -> list[Vivienda]:
        url = self._build_url(filtros)
        print(f"  [{self.NOMBRE}] Buscando en: {url}")
        resp = self._get(url)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        resultados = []

        # Idealista usa article.item-multimedia-container o divs con class item
        items = soup.select("article.item-multimedia-container")
        if not items:
            items = soup.select("div.item-info-container")
        if not items:
            items = soup.select("article[data-adid]")

        for item in items:
            v = Vivienda(portal=self.NOMBRE)

            # Titulo y URL
            link = item.select_one("a.item-link")
            if link:
                v.titulo = link.get_text(strip=True)
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href

            # Precio
            precio_el = item.select_one("span.item-price")
            if precio_el:
                v.precio = precio_el.get_text(strip=True)

            # Detalles (habitaciones, metros)
            detalles = item.select("span.item-detail")
            for d in detalles:
                txt = d.get_text(strip=True).lower()
                if "hab" in txt:
                    v.habitaciones = txt
                elif "m²" in txt or "m2" in txt:
                    v.metros = txt

            # Ubicación
            ubicacion_el = item.select_one("span.item-detail-char span") or item.select_one(".item-location")
            if ubicacion_el:
                v.ubicacion = ubicacion_el.get_text(strip=True)

            # Descripción
            desc_el = item.select_one("p.item-description, div.item-description")
            if desc_el:
                v.descripcion = desc_el.get_text(strip=True)[:150]

            if v.titulo or v.precio:
                resultados.append(v)

        return resultados


# ─── Fotocasa ───

class Fotocasa(PortalInmobiliario):
    NOMBRE = "Fotocasa"
    BASE = "https://www.fotocasa.es"

    UBICACIONES = {
        "madrid": "madrid-capital",
        "barcelona": "barcelona-capital",
        "valencia": "valencia-capital",
        "sevilla": "sevilla-capital",
        "malaga": "malaga-capital",
        "zaragoza": "zaragoza-capital",
        "bilbao": "bilbao",
        "alicante": "alicante-alacant-capital",
        "cordoba": "cordoba-capital",
        "granada": "granada-capital",
        "murcia": "murcia-capital",
        "palma": "palma-de-mallorca",
        "valladolid": "valladolid-capital",
        "santander": "santander",
        "pamplona": "pamplona-iruna",
    }

    def _build_url(self, filtros: Filtros) -> str:
        loc = self.UBICACIONES.get(filtros.ubicacion.lower(), f"{filtros.ubicacion}-capital")
        op = "compra" if filtros.operacion == "venta" else "alquiler"
        url = f"{self.BASE}/es/{op}/viviendas/{loc}/todas-las-zonas/l"

        params = {}
        if filtros.precio_min:
            params["minPrice"] = filtros.precio_min
        if filtros.precio_max:
            params["maxPrice"] = filtros.precio_max
        if filtros.metros_min:
            params["minSurface"] = filtros.metros_min
        if filtros.metros_max:
            params["maxSurface"] = filtros.metros_max
        if filtros.habitaciones_min:
            params["minRooms"] = filtros.habitaciones_min
        if filtros.habitaciones_max:
            params["maxRooms"] = filtros.habitaciones_max
        if filtros.pagina > 1:
            params["currentPage"] = filtros.pagina

        if params:
            url += "?" + urlencode(params)
        return url

    def buscar(self, filtros: Filtros) -> list[Vivienda]:
        url = self._build_url(filtros)
        print(f"  [{self.NOMBRE}] Buscando en: {url}")
        resp = self._get(url)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        resultados = []

        items = soup.select("article.re-CardPackPremium, article.re-CardPackMinimal, article.re-CardPackAdvance")
        if not items:
            items = soup.select("section.re-SearchResult article")
        if not items:
            items = soup.select("article[class*='Card']")

        for item in items:
            v = Vivienda(portal=self.NOMBRE)

            link = item.select_one("a.re-CardPackPremium-slider, a[class*='Card']")
            if not link:
                link = item.select_one("a[href]")
            if link:
                v.titulo = link.get("title", "") or link.get_text(strip=True)
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href

            precio_el = item.select_one("span.re-CardPrice, span[class*='Price']")
            if precio_el:
                v.precio = precio_el.get_text(strip=True)

            features = item.select("li.re-CardFeatures-feature, span[class*='Feature']")
            for feat in features:
                txt = feat.get_text(strip=True).lower()
                if "hab" in txt or "dorm" in txt:
                    v.habitaciones = txt
                elif "m²" in txt or "m2" in txt:
                    v.metros = txt

            ubicacion_el = item.select_one("span[class*='Location'], span[class*='location']")
            if ubicacion_el:
                v.ubicacion = ubicacion_el.get_text(strip=True)

            if v.titulo or v.precio:
                resultados.append(v)

        return resultados


# ─── Tecnocasa ───

class Tecnocasa(PortalInmobiliario):
    NOMBRE = "Tecnocasa"
    BASE = "https://www.tecnocasa.es"

    def _build_url(self, filtros: Filtros) -> str:
        loc = filtros.ubicacion.lower().replace(" ", "-")
        op = "venta" if filtros.operacion == "venta" else "alquiler"
        url = f"{self.BASE}/{op}/pisos/{loc}/{loc}.html"

        params = {}
        if filtros.precio_min:
            params["preciomin"] = filtros.precio_min
        if filtros.precio_max:
            params["preciomax"] = filtros.precio_max
        if filtros.metros_min:
            params["supmin"] = filtros.metros_min
        if filtros.metros_max:
            params["supmax"] = filtros.metros_max
        if filtros.habitaciones_min:
            params["habmin"] = filtros.habitaciones_min
        if filtros.pagina > 1:
            params["pagina"] = filtros.pagina

        if params:
            url += "?" + urlencode(params)
        return url

    def buscar(self, filtros: Filtros) -> list[Vivienda]:
        url = self._build_url(filtros)
        print(f"  [{self.NOMBRE}] Buscando en: {url}")
        resp = self._get(url)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        resultados = []

        items = soup.select("div.property-card, article.property, div.listing-item, div[class*='annuncio']")
        if not items:
            items = soup.select("div.resultItem, div.item-listing")

        for item in items:
            v = Vivienda(portal=self.NOMBRE)

            link = item.select_one("a[href]")
            if link:
                v.titulo = link.get("title", "") or link.get_text(strip=True)
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href

            precio_el = item.select_one("span.price, div.price, span[class*='price'], span[class*='precio']")
            if precio_el:
                v.precio = precio_el.get_text(strip=True)

            features = item.select("span[class*='feature'], li[class*='feature'], span[class*='detail']")
            for feat in features:
                txt = feat.get_text(strip=True).lower()
                if "hab" in txt or "loc" in txt:
                    v.habitaciones = txt
                elif "m²" in txt or "m2" in txt or "sup" in txt:
                    v.metros = txt

            ubicacion_el = item.select_one("span[class*='location'], p[class*='address'], span[class*='zona']")
            if ubicacion_el:
                v.ubicacion = ubicacion_el.get_text(strip=True)

            desc_el = item.select_one("p[class*='description'], div[class*='description']")
            if desc_el:
                v.descripcion = desc_el.get_text(strip=True)[:150]

            if v.titulo or v.precio:
                resultados.append(v)

        return resultados


# ─── Redpiso ───

class Redpiso(PortalInmobiliario):
    NOMBRE = "Redpiso"
    BASE = "https://www.redpiso.es"

    def _build_url(self, filtros: Filtros) -> str:
        loc = filtros.ubicacion.lower().replace(" ", "-")
        op = "venta" if filtros.operacion == "venta" else "alquiler"
        url = f"{self.BASE}/pisos-{op}/{loc}"

        params = {}
        if filtros.precio_min:
            params["precio_min"] = filtros.precio_min
        if filtros.precio_max:
            params["precio_max"] = filtros.precio_max
        if filtros.metros_min:
            params["superficie_min"] = filtros.metros_min
        if filtros.metros_max:
            params["superficie_max"] = filtros.metros_max
        if filtros.habitaciones_min:
            params["habitaciones_min"] = filtros.habitaciones_min
        if filtros.habitaciones_max:
            params["habitaciones_max"] = filtros.habitaciones_max
        if filtros.pagina > 1:
            params["pagina"] = filtros.pagina

        if params:
            url += "?" + urlencode(params)
        return url

    def buscar(self, filtros: Filtros) -> list[Vivienda]:
        url = self._build_url(filtros)
        print(f"  [{self.NOMBRE}] Buscando en: {url}")
        resp = self._get(url)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        resultados = []

        items = soup.select("div.property-card, article.listing, div.result-item, div[class*='inmueble']")
        if not items:
            items = soup.select("div.list-item, div[class*='property']")

        for item in items:
            v = Vivienda(portal=self.NOMBRE)

            link = item.select_one("a[href]")
            if link:
                v.titulo = link.get("title", "") or link.get_text(strip=True)
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href

            precio_el = item.select_one("span.price, div.price, span[class*='precio'], span[class*='price']")
            if precio_el:
                v.precio = precio_el.get_text(strip=True)

            features = item.select("span[class*='feature'], li[class*='detail'], span[class*='dato']")
            for feat in features:
                txt = feat.get_text(strip=True).lower()
                if "hab" in txt or "dorm" in txt:
                    v.habitaciones = txt
                elif "m²" in txt or "m2" in txt:
                    v.metros = txt

            ubicacion_el = item.select_one("span[class*='location'], p[class*='direccion'], span[class*='zona']")
            if ubicacion_el:
                v.ubicacion = ubicacion_el.get_text(strip=True)

            desc_el = item.select_one("p[class*='description'], p[class*='descripcion']")
            if desc_el:
                v.descripcion = desc_el.get_text(strip=True)[:150]

            if v.titulo or v.precio:
                resultados.append(v)

        return resultados


# ─── Motor de búsqueda unificado ───

PORTALES = {
    "idealista": Idealista,
    "fotocasa": Fotocasa,
    "tecnocasa": Tecnocasa,
    "redpiso": Redpiso,
}

def buscar(filtros: Filtros, portales_activos: list[str] | None = None) -> list[Vivienda]:
    """Busca en todos los portales (o los seleccionados) y devuelve resultados unificados."""
    if portales_activos is None:
        portales_activos = list(PORTALES.keys())

    todos = []
    for nombre in portales_activos:
        cls = PORTALES.get(nombre.lower())
        if not cls:
            print(f"  [!] Portal desconocido: {nombre}")
            continue
        portal = cls()
        try:
            resultados = portal.buscar(filtros)
            print(f"  [{portal.NOMBRE}] {len(resultados)} resultado(s) encontrado(s)")
            todos.extend(resultados)
        except Exception as e:
            print(f"  [{portal.NOMBRE}] Error: {e}")
        time.sleep(1)  # Pausa entre portales

    return todos


# ─── Formateo de salida ───

def mostrar_tabla(viviendas: list[Vivienda]):
    """Muestra los resultados en formato tabla."""
    if not viviendas:
        print("\n  No se encontraron resultados.\n")
        return

    print(f"\n{'─' * 120}")
    print(f"  {'PORTAL':<12} {'PRECIO':<15} {'HAB.':<8} {'M²':<10} {'TITULO':<50} {'UBICACION':<25}")
    print(f"{'─' * 120}")

    for v in viviendas:
        titulo = (v.titulo[:47] + "...") if len(v.titulo) > 50 else v.titulo
        ubicacion = (v.ubicacion[:22] + "...") if len(v.ubicacion) > 25 else v.ubicacion
        print(f"  {v.portal:<12} {v.precio:<15} {v.habitaciones:<8} {v.metros:<10} {titulo:<50} {ubicacion:<25}")
        if v.url:
            print(f"  {'':>12} -> {v.url}")

    print(f"{'─' * 120}")
    print(f"  Total: {len(viviendas)} vivienda(s)\n")


def mostrar_json(viviendas: list[Vivienda]):
    """Muestra los resultados en formato JSON."""
    data = [asdict(v) for v in viviendas]
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ─── CLI ───

def main():
    parser = argparse.ArgumentParser(
        description="Buscador unificado de viviendas en portales inmobiliarios españoles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  %(prog)s -u madrid
  %(prog)s -u barcelona -o alquiler --precio-max 1200
  %(prog)s -u valencia --hab-min 2 --metros-min 80 --precio-max 200000
  %(prog)s -u sevilla -p idealista,fotocasa --json
  %(prog)s -u malaga --precio-min 100000 --precio-max 300000 --hab-min 3
        """
    )

    parser.add_argument("-u", "--ubicacion", required=True,
                        help="Ciudad donde buscar (ej: madrid, barcelona, valencia)")
    parser.add_argument("-o", "--operacion", choices=["venta", "alquiler"], default="venta",
                        help="Tipo de operación (default: venta)")
    parser.add_argument("--precio-min", type=int, default=0,
                        help="Precio mínimo en euros")
    parser.add_argument("--precio-max", type=int, default=0,
                        help="Precio máximo en euros")
    parser.add_argument("--hab-min", type=int, default=0,
                        help="Número mínimo de habitaciones")
    parser.add_argument("--hab-max", type=int, default=0,
                        help="Número máximo de habitaciones")
    parser.add_argument("--metros-min", type=int, default=0,
                        help="Superficie mínima en m²")
    parser.add_argument("--metros-max", type=int, default=0,
                        help="Superficie máxima en m²")
    parser.add_argument("--pagina", type=int, default=1,
                        help="Número de página de resultados (default: 1)")
    parser.add_argument("-p", "--portales", default="idealista,fotocasa,tecnocasa,redpiso",
                        help="Portales a consultar separados por coma (default: todos)")
    parser.add_argument("--json", action="store_true",
                        help="Salida en formato JSON")

    args = parser.parse_args()

    filtros = Filtros(
        operacion=args.operacion,
        ubicacion=args.ubicacion,
        precio_min=args.precio_min,
        precio_max=args.precio_max,
        habitaciones_min=args.hab_min,
        habitaciones_max=args.hab_max,
        metros_min=args.metros_min,
        metros_max=args.metros_max,
        pagina=args.pagina,
    )

    portales_activos = [p.strip() for p in args.portales.split(",")]

    print(f"\n  Buscando {filtros.operacion} en {filtros.ubicacion}...")
    if filtros.precio_min or filtros.precio_max:
        rango = f"{filtros.precio_min}€ - {filtros.precio_max}€" if filtros.precio_max else f"desde {filtros.precio_min}€"
        if not filtros.precio_min:
            rango = f"hasta {filtros.precio_max}€"
        print(f"  Precio: {rango}")
    if filtros.habitaciones_min:
        print(f"  Habitaciones: {filtros.habitaciones_min}+")
    if filtros.metros_min:
        print(f"  Superficie: {filtros.metros_min}+ m²")
    print()

    resultados = buscar(filtros, portales_activos)

    if args.json:
        mostrar_json(resultados)
    else:
        mostrar_tabla(resultados)


if __name__ == "__main__":
    main()
