# Automatizaciones

Coleccion de scripts para automatizar tareas comunes con repositorios Git.

---

## auto_commit.sh

Script que detecta cambios locales en un repositorio Git, genera un mensaje de commit inteligente basado en el codigo modificado y lo pushea a GitHub. Incluye un modo automatico que revisa el repositorio cada N minutos.

### Requisitos

- Bash 4.0+
- Git instalado y configurado
- Un repositorio Git inicializado con un remote `origin`

### Instalacion

```bash
# Clonar el repositorio
git clone https://github.com/Ivichec/automatizaciones.git
cd automatizaciones

# Dar permisos de ejecucion
chmod +x auto_commit.sh

# (Opcional) Crear un enlace simbolico para usarlo desde cualquier lugar
sudo ln -s "$(pwd)/auto_commit.sh" /usr/local/bin/auto-commit
```

### Uso

```bash
./auto_commit.sh [opciones]
```

### Opciones

| Flag | Descripcion |
|------|-------------|
| `-r, --repo <ruta>` | Ruta al repositorio Git (por defecto: directorio actual) |
| `-m, --message <msg>` | Mensaje de commit personalizado (por defecto: generado automaticamente) |
| `-a, --auto` | Activar modo auto-commit (ejecuta en bucle) |
| `-i, --interval <min>` | Intervalo en minutos para el modo auto (por defecto: 5) |
| `-b, --branch <rama>` | Rama contra la que comparar y pushear (por defecto: la rama actual) |
| `-p, --push` | Pushear a origin despues de cada commit |
| `-h, --help` | Mostrar la ayuda |

### Ejemplos

```bash
# Commit unico en el directorio actual
./auto_commit.sh

# Especificar la ruta del repositorio
./auto_commit.sh -r /ruta/a/mi/proyecto

# Commit + push
./auto_commit.sh -p

# Commit con mensaje personalizado + push
./auto_commit.sh -m "feat: nueva funcionalidad de login" -p

# Modo auto-commit cada 10 minutos con push
./auto_commit.sh -a -i 10 -p

# Auto-commit en un repo especifico, rama develop, cada 2 minutos
./auto_commit.sh -r /home/user/mi-app -b develop -a -i 2 -p
```

### Mensajes de commit inteligentes

Cuando no se especifica `-m`, el script analiza el diff y genera un mensaje automatico con el formato:

```
<accion>: <archivos> (<contexto>) [+lineas/-lineas]
```

**Acciones detectadas:**

| Accion | Cuando se usa |
|--------|---------------|
| `add` | Solo se agregan archivos nuevos |
| `update` | Se modifican archivos existentes |
| `fix` | El diff contiene palabras como `fix`, `bug`, `error`, `patch` |
| `refactor` | El diff contiene `refactor`, `cleanup`, `simplify` |
| `remove` | Solo se eliminan archivos |
| `rename` | Solo se renombran archivos |

**Contexto detectado:**

- Nombres de funciones y clases modificadas
- `config` si se tocan archivos de configuracion (.json, .yml, Dockerfile, etc.)
- `tests` si se modifican archivos de tests
- `docs` si se tocan archivos de documentacion (.md, README, etc.)
- `dependencias` si se agregan imports o requires

**Ejemplos de mensajes generados:**

```
add: auth.py (login, register) [+85/-0]
fix: utils.js (parseDate) [+3/-1]
update: src/ (4 archivos) (config, dependencias) [+22/-8]
remove: tests/ (3 archivos) (tests) [+0/-120]
refactor: UserService.java (UserService) [+30/-45]
```

### Detener el modo auto-commit

Presiona `Ctrl+C` para detener el bucle cuando esta en modo auto.

---

## buscador_casas.py

Buscador unificado de viviendas que consulta **Idealista**, **Fotocasa**, **Tecnocasa** y **Redpiso** con filtros comunes y muestra los resultados en tabla o JSON.

### Requisitos

- Python 3.10+
- Dependencias: `requests`, `beautifulsoup4`, `lxml`

### Instalacion

```bash
# Instalar dependencias
pip install requests beautifulsoup4 lxml

# Dar permisos de ejecucion
chmod +x buscador_casas.py
```

### Uso

```bash
python3 buscador_casas.py -u <ciudad> [opciones]
```

### Opciones

| Flag | Descripcion |
|------|-------------|
| `-u, --ubicacion <ciudad>` | Ciudad donde buscar **(obligatorio)** |
| `-o, --operacion <tipo>` | `venta` o `alquiler` (default: venta) |
| `--precio-min <euros>` | Precio minimo |
| `--precio-max <euros>` | Precio maximo |
| `--hab-min <n>` | Minimo de habitaciones |
| `--hab-max <n>` | Maximo de habitaciones |
| `--metros-min <m2>` | Superficie minima en m2 |
| `--metros-max <m2>` | Superficie maxima en m2 |
| `--pagina <n>` | Pagina de resultados (default: 1) |
| `-p, --portales <lista>` | Portales a consultar separados por coma (default: todos) |
| `--json` | Salida en formato JSON en vez de tabla |

### Ejemplos

```bash
# Buscar pisos en venta en Madrid
python3 buscador_casas.py -u madrid

# Alquiler en Barcelona hasta 1200 euros
python3 buscador_casas.py -u barcelona -o alquiler --precio-max 1200

# Venta en Valencia, 2+ hab, 80+ m2, hasta 200k
python3 buscador_casas.py -u valencia --hab-min 2 --metros-min 80 --precio-max 200000

# Solo buscar en Idealista y Fotocasa, salida JSON
python3 buscador_casas.py -u sevilla -p idealista,fotocasa --json

# Pisos grandes en Malaga entre 100k y 300k
python3 buscador_casas.py -u malaga --precio-min 100000 --precio-max 300000 --hab-min 3
```

### Ciudades soportadas

Madrid, Barcelona, Valencia, Sevilla, Malaga, Zaragoza, Bilbao, Alicante, Cordoba, Granada, Murcia, Palma, Valladolid, Santander, Pamplona, y mas.

Para otras ciudades, usa el nombre directamente (ej: `-u toledo`) y el script intentara construir la URL automaticamente.

### Notas

- Los portales pueden bloquear peticiones automatizadas. Si recibes errores 403, espera unos minutos antes de reintentar.
- Los selectores HTML dependen de la estructura actual de cada web. Si un portal cambia su diseño, puede ser necesario actualizar los selectores.
- Usa la opcion `--json` para procesar los resultados con otras herramientas (jq, scripts, etc.).
