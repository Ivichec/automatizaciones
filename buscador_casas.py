#!/usr/bin/env python3
"""
buscador_casas.py — Buscador unificado de viviendas en portales inmobiliarios españoles.

Busca en Idealista (API oficial o scraping), Fotocasa (parseo __NEXT_DATA__),
Tecnocasa y Redpiso con filtros comunes.

Configuración:
  Variables de entorno o archivo .env:
    IDEALISTA_API_KEY    — API key de Idealista (developers.idealista.com)
    IDEALISTA_API_SECRET — API secret de Idealista
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# Playwright es opcional — solo se usa con --browser
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_DISPONIBLE = True
except ImportError:
    PLAYWRIGHT_DISPONIBLE = False

# ─── Cargar .env si existe ───

def load_dotenv():
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_dotenv()

# ─── Modelos ───

@dataclass
class Filtros:
    operacion: str = "venta"
    ubicacion: str = "madrid"
    precio_min: int = 0
    precio_max: int = 0
    habitaciones_min: int = 0
    habitaciones_max: int = 0
    metros_min: int = 0
    metros_max: int = 0
    pagina: int = 1
    paginas_max: int = 1   # páginas a recorrer en modo browser

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


# ─── Clase base ───

# Variable global para compartir la instancia del navegador
_browser_context = None

def get_browser_page(url, wait_selector=None, wait_seconds=3):
    """Navega a una URL con Playwright, hace scroll completo y devuelve el HTML renderizado."""
    global _browser_context
    if not PLAYWRIGHT_DISPONIBLE:
        print("  [!] Playwright no instalado. Ejecuta: pip install playwright && playwright install chromium")
        return None

    if _browser_context is None:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        _browser_context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
            viewport={"width": 1920, "height": 1080},
        )

    page = _browser_context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Aceptar cookies / cerrar popups comunes
        for selector in [
            "button#didomi-notice-agree-button",   # Didomi (Fotocasa)
            "button[data-testid='TcfAccept']",
            "button.sui-AtomButton--primary",
            "button:has-text('Aceptar')",
            "button:has-text('Aceptar todo')",
            "button:has-text('Aceptar y cerrar')",
            "button:has-text('Accept')",
        ]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    page.wait_for_timeout(500)
                    break
            except Exception:
                continue

        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=10000)
            except Exception:
                pass
        page.wait_for_timeout(wait_seconds * 1000)

        # Scroll progresivo hasta el final para cargar todos los ítems lazy
        prev_height = 0
        for _ in range(50):  # máx 50 scrolls
            altura_total = page.evaluate("document.body.scrollHeight")
            if altura_total <= prev_height:
                break
            prev_height = altura_total
            paso = 800
            pos = page.evaluate("window.pageYOffset") or 0
            target = min(pos + paso, altura_total)
            page.evaluate(f"window.scrollTo(0, {target})")
            page.wait_for_timeout(400)

        # Scroll final al fondo absoluto
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)

        html = page.content()
        return html
    except Exception as e:
        print(f"  [Browser] Error navegando a {url}: {e}")
        return None
    finally:
        page.close()


class PortalInmobiliario(ABC):
    NOMBRE = ""
    usar_browser = False
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }

    def _get(self, url, headers=None, params=None, allow_redirects=True):
        h = {**self.HEADERS, **(headers or {})}
        for intento in range(3):
            try:
                resp = requests.get(url, headers=h, params=params,
                                    timeout=15, allow_redirects=allow_redirects)
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 403:
                    print(f"  [{self.NOMBRE}] Bloqueado (403). Puede requerir API key o el portal rechaza scraping.")
                    return None
                if resp.status_code == 429:
                    wait = 2 ** (intento + 1)
                    print(f"  [{self.NOMBRE}] Rate limit, esperando {wait}s...")
                    time.sleep(wait)
                    continue
                if resp.status_code in (301, 302):
                    print(f"  [{self.NOMBRE}] Redireccion a: {resp.headers.get('Location', '?')}")
                    return None
                print(f"  [{self.NOMBRE}] HTTP {resp.status_code}")
                return None
            except requests.RequestException as e:
                print(f"  [{self.NOMBRE}] Error: {e}")
                if intento < 2:
                    time.sleep(2)
        return None

    def _post(self, url, headers=None, data=None, json_data=None):
        h = {**self.HEADERS, **(headers or {})}
        try:
            resp = requests.post(url, headers=h, data=data, json=json_data, timeout=15)
            if resp.status_code == 200:
                return resp
            print(f"  [{self.NOMBRE}] POST HTTP {resp.status_code}")
            return None
        except requests.RequestException as e:
            print(f"  [{self.NOMBRE}] Error POST: {e}")
            return None

    @abstractmethod
    def buscar(self, filtros: Filtros) -> list[Vivienda]:
        pass


# ─── Idealista (API oficial OAuth2 + fallback scraping) ───

class Idealista(PortalInmobiliario):
    NOMBRE = "Idealista"
    BASE = "https://www.idealista.com"
    API_BASE = "https://api.idealista.com"

    UBICACIONES = {
        "madrid": ("madrid-madrid", "0-EU-ES-28-07-001-079.html"),
        "barcelona": ("barcelona-barcelona", "0-EU-ES-08-07-001-019.html"),
        "valencia": ("valencia-valencia", "0-EU-ES-46-07-001-250.html"),
        "sevilla": ("sevilla-sevilla", "0-EU-ES-41-07-001-091.html"),
        "malaga": ("malaga-malaga", "0-EU-ES-29-07-001-067.html"),
        "zaragoza": ("zaragoza-zaragoza", "0-EU-ES-50-07-001-297.html"),
        "bilbao": ("bilbao-vizcaya", "0-EU-ES-48-07-001-020.html"),
        "alicante": ("alicante-alicante", "0-EU-ES-03-07-001-014.html"),
        "cordoba": ("cordoba-cordoba", "0-EU-ES-14-07-001-021.html"),
        "granada": ("granada-granada", "0-EU-ES-18-07-001-087.html"),
        "murcia": ("murcia-murcia", "0-EU-ES-30-07-001-030.html"),
        "palma": ("palma-de-mallorca-balears-illes", "0-EU-ES-07-07-001-040.html"),
        "valladolid": ("valladolid-valladolid", "0-EU-ES-47-07-001-186.html"),
        "santander": ("santander-cantabria", "0-EU-ES-39-07-001-075.html"),
        "pamplona": ("pamplona-navarra", "0-EU-ES-31-07-001-201.html"),
    }

    def _get_api_token(self):
        api_key = os.environ.get("IDEALISTA_API_KEY", "")
        api_secret = os.environ.get("IDEALISTA_API_SECRET", "")
        if not api_key or not api_secret:
            return None

        credentials = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        resp = self._post(
            f"{self.API_BASE}/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data="grant_type=client_credentials&scope=read",
        )
        if resp:
            token_data = resp.json()
            return token_data.get("access_token")
        return None

    def _buscar_api(self, filtros: Filtros, token: str) -> list[Vivienda]:
        loc_data = self.UBICACIONES.get(filtros.ubicacion.lower())
        if not loc_data:
            print(f"  [{self.NOMBRE}] API: ubicación '{filtros.ubicacion}' no mapeada, usando scraping.")
            return []

        location_id = loc_data[1]
        operation = "sale" if filtros.operacion == "venta" else "rent"

        params = {
            "operation": operation,
            "propertyType": "homes",
            "locationId": location_id,
            "maxItems": 50,
            "numPage": filtros.pagina,
            "language": "es",
            "country": "es",
        }
        if filtros.precio_min:
            params["minPrice"] = filtros.precio_min
        if filtros.precio_max:
            params["maxPrice"] = filtros.precio_max
        if filtros.habitaciones_min:
            params["bedrooms"] = filtros.habitaciones_min
        if filtros.metros_min:
            params["minSize"] = filtros.metros_min
        if filtros.metros_max:
            params["maxSize"] = filtros.metros_max

        url = f"{self.API_BASE}/3.5/es/search"
        print(f"  [{self.NOMBRE}] Buscando via API oficial...")
        resp = self._post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=urlencode(params),
        )
        if not resp:
            return []

        data = resp.json()
        resultados = []
        for elem in data.get("elementList", []):
            v = Vivienda(
                portal=self.NOMBRE,
                titulo=elem.get("suggestedTexts", {}).get("title", elem.get("description", "")[:80]),
                precio=f"{elem.get('price', '')} €",
                ubicacion=elem.get("address", ""),
                habitaciones=str(elem.get("rooms", "")),
                metros=f"{elem.get('size', '')} m²",
                url=elem.get("url", ""),
                descripcion=elem.get("description", "")[:150],
            )
            if not v.url.startswith("http"):
                v.url = self.BASE + "/" + v.url.lstrip("/")
            resultados.append(v)

        return resultados

    def _buscar_scraping(self, filtros: Filtros) -> list[Vivienda]:
        loc_data = self.UBICACIONES.get(filtros.ubicacion.lower())
        loc = loc_data[0] if loc_data else f"{filtros.ubicacion}-{filtros.ubicacion}"
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

        print(f"  [{self.NOMBRE}] Buscando via scraping: {url}")
        resp = self._get(url)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        resultados = []

        # Intentar extraer JSON embebido en el HTML (script con datos de listados)
        for script in soup.select("script"):
            txt = script.string or ""
            if "listingCards" in txt or "elementList" in txt:
                match = re.search(r'\{.*"elementList"\s*:\s*\[.*\].*\}', txt, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group())
                        for elem in data.get("elementList", []):
                            v = Vivienda(
                                portal=self.NOMBRE,
                                titulo=elem.get("title", ""),
                                precio=f"{elem.get('price', '')} €",
                                ubicacion=elem.get("address", ""),
                                habitaciones=str(elem.get("rooms", "")),
                                metros=f"{elem.get('size', '')} m²",
                                url=self.BASE + elem.get("url", ""),
                                descripcion=elem.get("description", "")[:150],
                            )
                            resultados.append(v)
                        return resultados
                    except json.JSONDecodeError:
                        pass

        # Fallback: parsear HTML directamente
        items = soup.select("article.item-multimedia-container, article[data-adid]")
        if not items:
            items = soup.select("div.item-info-container")

        for item in items:
            v = Vivienda(portal=self.NOMBRE)
            link = item.select_one("a.item-link")
            if link:
                v.titulo = link.get_text(strip=True)
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href
            precio_el = item.select_one("span.item-price")
            if precio_el:
                v.precio = precio_el.get_text(strip=True)
            detalles = item.select("span.item-detail")
            for d in detalles:
                txt = d.get_text(strip=True).lower()
                if "hab" in txt:
                    v.habitaciones = txt
                elif "m²" in txt or "m2" in txt:
                    v.metros = txt
            desc_el = item.select_one("p.item-description, div.item-description")
            if desc_el:
                v.descripcion = desc_el.get_text(strip=True)[:150]
            if v.titulo or v.precio:
                resultados.append(v)

        return resultados

    def buscar(self, filtros: Filtros) -> list[Vivienda]:
        # Intentar API oficial primero
        token = self._get_api_token()
        if token:
            resultados = self._buscar_api(filtros, token)
            if resultados:
                return resultados
            print(f"  [{self.NOMBRE}] API sin resultados, intentando scraping...")

        # Modo browser si está habilitado
        if self.usar_browser:
            return self._buscar_browser(filtros)

        return self._buscar_scraping(filtros)

    def _buscar_browser(self, filtros: Filtros) -> list[Vivienda]:
        loc_data = self.UBICACIONES.get(filtros.ubicacion.lower())
        loc = loc_data[0] if loc_data else f"{filtros.ubicacion}-{filtros.ubicacion}"
        op = "venta-viviendas" if filtros.operacion == "venta" else "alquiler-viviendas"
        url = f"{self.BASE}/{op}/{loc}/"

        params = []
        if filtros.precio_min:
            params.append(f"minPrice={filtros.precio_min}")
        if filtros.precio_max:
            params.append(f"maxPrice={filtros.precio_max}")
        if filtros.habitaciones_min:
            params.append(f"minRooms={filtros.habitaciones_min}")
        if filtros.metros_min:
            params.append(f"minSize={filtros.metros_min}")
        if filtros.pagina > 1:
            params.append(f"pagina={filtros.pagina}")
        if params:
            url += "?" + "&".join(params)

        print(f"  [{self.NOMBRE}] Usando navegador headless: {url}")
        html = get_browser_page(url, wait_selector="article.item-multimedia-container", wait_seconds=5)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        resultados = []

        items = soup.select("article.item-multimedia-container, article[data-adid]")
        for item in items:
            v = Vivienda(portal=self.NOMBRE)
            link = item.select_one("a.item-link")
            if link:
                v.titulo = link.get_text(strip=True)
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href
            precio_el = item.select_one("span.item-price")
            if precio_el:
                v.precio = precio_el.get_text(strip=True)
            detalles = item.select("span.item-detail")
            for d in detalles:
                txt = d.get_text(strip=True).lower()
                if "hab" in txt:
                    v.habitaciones = txt
                elif "m²" in txt or "m2" in txt:
                    v.metros = txt
            desc_el = item.select_one("p.item-description, div.item-description")
            if desc_el:
                v.descripcion = desc_el.get_text(strip=True)[:150]
            if v.titulo or v.precio:
                resultados.append(v)

        return resultados


# ─── Fotocasa (parseo de __NEXT_DATA__) ───

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

        # Paginación: /l para pág 1, /l/2 para pág 2, etc.
        if filtros.pagina > 1:
            url = f"{self.BASE}/es/{op}/viviendas/{loc}/todas-las-zonas/l/{filtros.pagina}"
        else:
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
        if params:
            url += "?" + urlencode(params)
        return url

    def _parse_next_data(self, soup: BeautifulSoup) -> list[Vivienda]:
        """Extrae datos del JSON __NEXT_DATA__ que Next.js inyecta en el HTML."""
        script = soup.select_one("script#__NEXT_DATA__")
        if not script or not script.string:
            return []

        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            return []

        resultados = []

        # Navegar la estructura de Next.js para encontrar listings
        props = data.get("props", {}).get("pageProps", {})

        # Buscar en varias ubicaciones posibles dentro del JSON
        listings = []
        for key in ("initialListings", "listings", "searchResults", "results"):
            if key in props:
                val = props[key]
                if isinstance(val, list):
                    listings = val
                elif isinstance(val, dict):
                    listings = val.get("results", val.get("items", val.get("elements", [])))
                if listings:
                    break

        # Buscar recursivamente si no se encontró en el primer nivel
        if not listings:
            listings = self._find_listings_recursive(props)

        for item in listings:
            if not isinstance(item, dict):
                continue
            v = Vivienda(portal=self.NOMBRE)

            # Diferentes estructuras posibles
            v.titulo = (item.get("title", "") or item.get("name", "")
                       or item.get("description", {}).get("title", "") if isinstance(item.get("description"), dict) else "")
            if not v.titulo and isinstance(item.get("description"), str):
                v.titulo = item["description"][:80]

            price = item.get("price", item.get("rawPrice", item.get("priceInfo", {})))
            if isinstance(price, dict):
                amount = price.get("amount", price.get("price", price.get("value", "")))
                v.precio = f"{amount} €" if amount else ""
            elif price:
                v.precio = f"{price} €"

            v.habitaciones = str(item.get("rooms", item.get("bedrooms", "")))
            size = item.get("surface", item.get("size", item.get("area", "")))
            v.metros = f"{size} m²" if size else ""

            v.ubicacion = item.get("address", item.get("location", item.get("zone", "")))
            if isinstance(v.ubicacion, dict):
                v.ubicacion = v.ubicacion.get("description", v.ubicacion.get("name", ""))

            detail_url = item.get("url", item.get("detail", {}).get("url", "") if isinstance(item.get("detail"), dict) else "")
            if detail_url:
                v.url = detail_url if detail_url.startswith("http") else self.BASE + detail_url

            desc = item.get("description", "")
            if isinstance(desc, str):
                v.descripcion = desc[:150]

            if v.titulo or v.precio:
                resultados.append(v)

        return resultados

    def _find_listings_recursive(self, obj, depth=0) -> list:
        """Busca arrays de listings dentro del JSON de forma recursiva."""
        if depth > 5:
            return []
        if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
            if any(k in obj[0] for k in ("price", "rooms", "surface", "priceInfo", "bedrooms")):
                return obj
        if isinstance(obj, dict):
            for val in obj.values():
                result = self._find_listings_recursive(val, depth + 1)
                if result:
                    return result
        return []

    def _parse_html(self, soup: BeautifulSoup) -> list[Vivienda]:
        """Fallback: parseo HTML directo."""
        resultados = []
        items = soup.select("article[class*='Card'], article[data-id]")
        if not items:
            items = soup.select("section.re-SearchResult article, div[class*='listing']")

        for item in items:
            v = Vivienda(portal=self.NOMBRE)
            link = item.select_one("a[href*='/es/']")
            if not link:
                link = item.select_one("a[href]")
            if link:
                v.titulo = link.get("title", "") or link.get_text(strip=True)
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href

            precio_el = item.select_one("[class*='Price'], [class*='price']")
            if precio_el:
                v.precio = precio_el.get_text(strip=True)

            for el in item.select("[class*='Feature'], [class*='feature'], li"):
                txt = el.get_text(strip=True).lower()
                if "hab" in txt or "dorm" in txt:
                    v.habitaciones = txt
                elif "m²" in txt or "m2" in txt:
                    v.metros = txt

            if v.titulo or v.precio:
                resultados.append(v)
        return resultados

    def buscar(self, filtros: Filtros) -> list[Vivienda]:
        if self.usar_browser:
            return self._buscar_browser(filtros)

        # Modo requests
        url = self._build_url(filtros)
        print(f"  [{self.NOMBRE}] Buscando en: {url}")
        resp = self._get(url)
        if not resp:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        resultados = self._parse_next_data(soup)
        if resultados:
            return resultados
        return self._parse_html(soup)

    def _build_url_pagina(self, filtros: Filtros, pagina: int) -> str:
        """Construye URL para una página específica."""
        f = Filtros(
            operacion=filtros.operacion, ubicacion=filtros.ubicacion,
            precio_min=filtros.precio_min, precio_max=filtros.precio_max,
            metros_min=filtros.metros_min, metros_max=filtros.metros_max,
            habitaciones_min=filtros.habitaciones_min, habitaciones_max=filtros.habitaciones_max,
            pagina=pagina,
        )
        return self._build_url(f)

    def _buscar_browser(self, filtros: Filtros) -> list[Vivienda]:
        """Abre la búsqueda en el browser, parsea y clicka 'Siguiente' para paginar."""
        global _browser_context
        if not PLAYWRIGHT_DISPONIBLE:
            print(f"  [{self.NOMBRE}] Playwright no instalado.")
            return []

        if _browser_context is None:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            _browser_context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="es-ES",
                viewport={"width": 1920, "height": 1080},
            )

        url = self._build_url(filtros)
        print(f"  [{self.NOMBRE}] Navegador headless: {url}")

        page = _browser_context.new_page()
        todos = []
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Aceptar cookies
            for selector in [
                "button#didomi-notice-agree-button",
                "button[data-testid='TcfAccept']",
                "button:has-text('Aceptar')",
                "button:has-text('Aceptar todo')",
                "button:has-text('Aceptar y cerrar')",
            ]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=1500):
                        btn.click()
                        page.wait_for_timeout(500)
                        break
                except Exception:
                    continue

            for pagina in range(1, filtros.paginas_max + 1):
                # Esperar a que carguen artículos
                try:
                    page.wait_for_selector("article", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(2000)

                # Scroll progresivo
                for _ in range(30):
                    prev = page.evaluate("document.body.scrollHeight")
                    page.evaluate("window.scrollBy(0, 800)")
                    page.wait_for_timeout(400)
                    nuevo = page.evaluate("document.body.scrollHeight")
                    if nuevo <= prev:
                        break
                page.wait_for_timeout(1000)

                # Parsear
                html = page.content()
                soup = BeautifulSoup(html, "lxml")
                resultados = self._parse_html_browser(soup)
                print(f"  [{self.NOMBRE}]   Pág {pagina}: {len(resultados)} vivienda(s)")

                if resultados:
                    todos.extend(resultados)

                if pagina >= filtros.paginas_max:
                    break

                # Debug: mostrar qué hay en la zona de paginación
                pag_info = page.evaluate("""() => {
                    const nav = document.querySelector('nav, [class*="aginat"], [class*="ager"]');
                    const links = document.querySelectorAll('a[href*="currentPage"], a[href*="/l/"], [class*="aginat"] a, nav a');
                    const buttons = document.querySelectorAll('[class*="aginat"] button, nav button');
                    return {
                        nav_html: nav ? nav.outerHTML.substring(0, 500) : 'NO NAV FOUND',
                        links: Array.from(links).slice(0, 10).map(a => ({text: a.textContent.trim(), href: a.href, classes: a.className})),
                        buttons: Array.from(buttons).slice(0, 5).map(b => ({text: b.textContent.trim(), classes: b.className})),
                        all_navs: Array.from(document.querySelectorAll('nav')).map(n => n.className),
                    };
                }""")
                print(f"  [{self.NOMBRE}]   Debug paginación: {json.dumps(pag_info, ensure_ascii=False, indent=2)[:600]}")

                # Intentar click por número de página
                pagina_siguiente = pagina + 1
                clicked = False

                # Método 1: buscar link con el número de página
                for sel in [
                    f"a:has-text('{pagina_siguiente}')",
                    f"[class*='aginat'] a:has-text('{pagina_siguiente}')",
                    f"nav a:has-text('{pagina_siguiente}')",
                    f"button:has-text('{pagina_siguiente}')",
                ]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=1500):
                            el.click()
                            clicked = True
                            break
                    except Exception:
                        continue

                # Método 2: buscar "Siguiente" / "Next" / ">"
                if not clicked:
                    for sel in [
                        "a[aria-label*='iguiente']",
                        "a[aria-label*='ext']",
                        "li.sui-MoleculePagination-item--next a",
                        "[class*='next'] a",
                        "[class*='Next'] a",
                        "a[rel='next']",
                        "a:has-text('Siguiente')",
                        "a:has-text('>')",
                        "button:has-text('Siguiente')",
                        "button:has-text('>')",
                    ]:
                        try:
                            el = page.locator(sel).first
                            if el.is_visible(timeout=1500):
                                el.click()
                                clicked = True
                                break
                        except Exception:
                            continue

                if not clicked:
                    print(f"  [{self.NOMBRE}]   No se encontró paginación, parando.")
                    break

                page.wait_for_timeout(3000)

        except Exception as e:
            print(f"  [{self.NOMBRE}] Error browser: {e}")
        finally:
            page.close()

        return todos

    def _parse_html_browser(self, soup: BeautifulSoup) -> list[Vivienda]:
        """Parseo HTML tras renderizado completo con browser — extrae campos limpios."""
        resultados = []
        urls_vistas = set()
        items = soup.select("article")

        for item in items:
            v = Vivienda(portal=self.NOMBRE)

            # URL — buscar cualquier enlace a detalle de vivienda
            link = None
            for a in item.select("a[href]"):
                href = a.get("href", "")
                if "/vivienda/" in href or "/inmueble/" in href or re.search(r'/\d{6,}/', href):
                    link = a
                    break
            if not link:
                link = item.select_one("a[href]")
            if link:
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href
                # Evitar duplicados
                if v.url in urls_vistas:
                    continue
                urls_vistas.add(v.url)

            # Extraer todo el texto del artículo
            full_text = item.get_text(" ", strip=True)

            # Descartar articles sin contenido útil (nav, ads, etc.)
            if len(full_text) < 20 or "€" not in full_text:
                continue

            # Precio
            m = re.search(r'([\d.,]+\s*€(?:/mes)?)', full_text)
            if m:
                v.precio = m.group(1)

            # Habitaciones
            m = re.search(r'(\d+)\s*habs?\.?', full_text, re.I)
            if m:
                v.habitaciones = f"{m.group(1)} habs"

            # Metros
            m = re.search(r'(\d+)\s*m[²2]', full_text)
            if m:
                v.metros = f"{m.group(1)} m²"

            # Título — tipo de vivienda
            m = re.search(r'((?:Piso|Estudio|Ático|Apartamento|Dúplex|Casa|Chalet|Loft)\S*(?:\s+con\s+\S+)?(?:\s+en\s+[^·€\d]{3,40})?)', full_text)
            if m:
                v.titulo = m.group(1).strip()[:80]
            elif link and link.get("title"):
                v.titulo = link["title"][:80]

            # Ubicación
            m = re.search(r'en\s+([^·€\d]{3,40}?,\s*[^·€\d]{3,30})', full_text)
            if m:
                v.ubicacion = m.group(1).strip()
            else:
                m = re.search(r'(?:en|,)\s+(\S+(?:\s+\S+){0,3})\s+Madrid', full_text)
                if m:
                    v.ubicacion = m.group(1).strip() + ", Madrid"

            if v.precio:
                resultados.append(v)

        return resultados


# ─── Tecnocasa ───

class Tecnocasa(PortalInmobiliario):
    NOMBRE = "Tecnocasa"
    BASE = "https://www.tecnocasa.es"

    REGIONES = {
        "madrid": "comunidad-de-madrid/madrid",
        "barcelona": "cataluna/barcelona/barcelona",
        "valencia": "comunidad-valenciana/valencia/valencia",
        "sevilla": "andalucia/sevilla/sevilla",
        "malaga": "andalucia/malaga/malaga",
        "zaragoza": "aragon/zaragoza/zaragoza",
        "bilbao": "pais-vasco/vizcaya/bilbao",
        "alicante": "comunidad-valenciana/alicante/alicante",
        "murcia": "region-de-murcia/murcia/murcia",
        "granada": "andalucia/granada/granada",
        "cordoba": "andalucia/cordoba/cordoba",
        "valladolid": "castilla-y-leon/valladolid/valladolid",
        "santander": "cantabria/cantabria/santander",
    }

    def _build_url(self, filtros: Filtros) -> str:
        loc = self.REGIONES.get(filtros.ubicacion.lower(), f"{filtros.ubicacion}/{filtros.ubicacion}")
        op = "venta" if filtros.operacion == "venta" else "alquiler"
        url = f"{self.BASE}/{op}/inmuebles/{loc}.html"

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

        # Buscar JSON-LD (schema.org) — muchas webs lo incluyen
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                ld = json.loads(script.string or "")
                items = []
                if isinstance(ld, list):
                    items = ld
                elif isinstance(ld, dict) and ld.get("@type") in ("ItemList", "SearchResultsPage"):
                    items = ld.get("itemListElement", [])
                elif isinstance(ld, dict) and ld.get("@type") in ("Apartment", "House", "Residence", "RealEstateListing"):
                    items = [ld]

                for item in items:
                    if isinstance(item, dict) and item.get("item"):
                        item = item["item"]
                    if not isinstance(item, dict):
                        continue
                    v = Vivienda(
                        portal=self.NOMBRE,
                        titulo=item.get("name", ""),
                        precio=item.get("offers", {}).get("price", "") if isinstance(item.get("offers"), dict) else "",
                        ubicacion=item.get("address", {}).get("streetAddress", "") if isinstance(item.get("address"), dict) else "",
                        url=item.get("url", ""),
                        descripcion=(item.get("description", "") or "")[:150],
                    )
                    if v.precio:
                        v.precio = f"{v.precio} €"
                    if v.titulo or v.precio:
                        resultados.append(v)
                if resultados:
                    return resultados
            except json.JSONDecodeError:
                continue

        # Parseo HTML
        items = soup.select("div[class*='annuncio'], div[class*='property'], div[class*='listing'], article")
        for item in items:
            v = Vivienda(portal=self.NOMBRE)
            link = item.select_one("a[href*='/venta/'], a[href*='/alquiler/']")
            if not link:
                link = item.select_one("a[href]")
            if link:
                v.titulo = link.get("title", "") or link.get_text(strip=True)[:80]
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href

            for el in item.select("span, div, p"):
                txt = el.get_text(strip=True)
                if re.search(r'[\d.,]+\s*€', txt) and not v.precio:
                    v.precio = txt
                elif re.search(r'\d+\s*hab', txt, re.I) and not v.habitaciones:
                    v.habitaciones = txt
                elif re.search(r'\d+\s*m[²2]', txt) and not v.metros:
                    v.metros = txt

            if v.titulo or v.precio:
                resultados.append(v)

        return resultados


# ─── Redpiso ───

class Redpiso(PortalInmobiliario):
    NOMBRE = "Redpiso"
    BASE = "https://www.redpiso.es"

    def _build_url(self, filtros: Filtros) -> str:
        loc = filtros.ubicacion.lower().replace(" ", "-")
        op = "venta-viviendas" if filtros.operacion == "venta" else "alquiler-viviendas"
        url = f"{self.BASE}/{op}/{loc}"

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

        # JSON-LD
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                ld = json.loads(script.string or "")
                items = []
                if isinstance(ld, list):
                    items = ld
                elif isinstance(ld, dict) and ld.get("@type") == "ItemList":
                    items = ld.get("itemListElement", [])
                elif isinstance(ld, dict) and ld.get("@type") in ("Apartment", "House", "RealEstateListing"):
                    items = [ld]

                for item in items:
                    if isinstance(item, dict) and item.get("item"):
                        item = item["item"]
                    if not isinstance(item, dict):
                        continue
                    v = Vivienda(
                        portal=self.NOMBRE,
                        titulo=item.get("name", ""),
                        precio=item.get("offers", {}).get("price", "") if isinstance(item.get("offers"), dict) else "",
                        ubicacion=item.get("address", {}).get("streetAddress", "") if isinstance(item.get("address"), dict) else "",
                        url=item.get("url", ""),
                        descripcion=(item.get("description", "") or "")[:150],
                    )
                    if v.precio:
                        v.precio = f"{v.precio} €"
                    if v.titulo or v.precio:
                        resultados.append(v)
                if resultados:
                    return resultados
            except json.JSONDecodeError:
                continue

        # HTML parsing
        items = soup.select("div[class*='property'], div[class*='inmueble'], article, div[class*='listing']")
        for item in items:
            v = Vivienda(portal=self.NOMBRE)
            link = item.select_one("a[href*='vivienda'], a[href*='piso']")
            if not link:
                link = item.select_one("a[href]")
            if link:
                v.titulo = link.get("title", "") or link.get_text(strip=True)[:80]
                href = link.get("href", "")
                v.url = href if href.startswith("http") else self.BASE + href

            for el in item.select("span, div, p"):
                txt = el.get_text(strip=True)
                if re.search(r'[\d.,]+\s*€', txt) and not v.precio:
                    v.precio = txt
                elif re.search(r'\d+\s*hab', txt, re.I) and not v.habitaciones:
                    v.habitaciones = txt
                elif re.search(r'\d+\s*m[²2]', txt) and not v.metros:
                    v.metros = txt

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

def buscar(filtros: Filtros, portales_activos: list[str] | None = None, usar_browser: bool = False) -> list[Vivienda]:
    if portales_activos is None:
        portales_activos = list(PORTALES.keys())

    if usar_browser and not PLAYWRIGHT_DISPONIBLE:
        print("  [!] Playwright no instalado. Instálalo con:")
        print("      pip install playwright && playwright install chromium")
        print("  [!] Continuando sin modo browser...\n")
        usar_browser = False

    todos = []
    for nombre in portales_activos:
        cls = PORTALES.get(nombre.lower())
        if not cls:
            print(f"  [!] Portal desconocido: {nombre}")
            continue
        portal = cls()
        portal.usar_browser = usar_browser
        try:
            resultados = portal.buscar(filtros)
            print(f"  [{portal.NOMBRE}] {len(resultados)} resultado(s) encontrado(s)")
            todos.extend(resultados)
        except Exception as e:
            print(f"  [{portal.NOMBRE}] Error: {e}")
        time.sleep(1)

    # Cerrar browser si se usó
    global _browser_context
    if _browser_context is not None:
        try:
            _browser_context.browser.close()
        except Exception:
            pass
        _browser_context = None

    return todos


# ─── Formateo de salida ───

def mostrar_tabla(viviendas: list[Vivienda]):
    if not viviendas:
        print("\n  No se encontraron resultados.")
        print("  Tip: Idealista requiere API key. Configura IDEALISTA_API_KEY y IDEALISTA_API_SECRET en .env")
        print("  Solicita acceso en: https://developers.idealista.com/access-request\n")
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
  %(prog)s -u madrid -o alquiler --precio-max 1800 --browser    # Usa navegador headless

Modo browser (recomendado para Fotocasa):
  pip install playwright && playwright install chromium
  Luego usa --browser para renderizar JavaScript y obtener todos los resultados.

Configuración API Idealista:
  Crea un archivo .env junto al script con:
    IDEALISTA_API_KEY=tu_api_key
    IDEALISTA_API_SECRET=tu_api_secret

  Solicita acceso en: https://developers.idealista.com/access-request
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
    parser.add_argument("--browser", action="store_true",
                        help="Usar navegador headless (Playwright) para cargar JavaScript. "
                             "Necesario para Fotocasa. Requiere: pip install playwright && playwright install chromium")
    parser.add_argument("--paginas", type=int, default=1,
                        help="Número de páginas a recorrer en modo --browser (default: 1, máx recomendado: 20)")

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
        paginas_max=args.pagina + args.paginas - 1,
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

    if args.browser:
        print("  Modo browser activado (Playwright headless)\n")

    resultados = buscar(filtros, portales_activos, usar_browser=args.browser)

    if args.json:
        mostrar_json(resultados)
    else:
        mostrar_tabla(resultados)


if __name__ == "__main__":
    main()
