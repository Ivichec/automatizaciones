#!/usr/bin/env bash
#
# auto_commit.sh — Detecta cambios locales en un repositorio Git,
# los commitea y pushea automáticamente a GitHub.
#
# Uso:
#   ./auto_commit.sh [opciones]
#
# Opciones:
#   -r, --repo <ruta>         Ruta al repositorio (por defecto: directorio actual)
#   -m, --message <mensaje>   Mensaje de commit (por defecto: generado automáticamente)
#   -a, --auto                Modo auto-commit: ejecuta en bucle cada N minutos
#   -i, --interval <minutos>  Intervalo en minutos para el modo auto (por defecto: 5)
#   -b, --branch <rama>       Rama remota contra la que comparar/pushear (por defecto: la actual)
#   -p, --push                Pushear después de commitear
#   -h, --help                Mostrar esta ayuda

set -euo pipefail

# ─── Valores por defecto ───
REPO_PATH="."
COMMIT_MSG=""
AUTO_MODE=false
INTERVAL=5
BRANCH=""
DO_PUSH=false

# ─── Colores ───
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # Sin color

log_info()  { echo -e "${BLUE}[INFO]${NC}  $(date '+%Y-%m-%d %H:%M:%S') — $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $(date '+%Y-%m-%d %H:%M:%S') — $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%Y-%m-%d %H:%M:%S') — $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') — $*"; }

show_help() {
    sed -n '2,/^$/s/^# \?//p' "$0"
    exit 0
}

# ─── Parseo de argumentos ───
while [[ $# -gt 0 ]]; do
    case "$1" in
        -r|--repo)     REPO_PATH="$2";    shift 2 ;;
        -m|--message)  COMMIT_MSG="$2";   shift 2 ;;
        -a|--auto)     AUTO_MODE=true;    shift   ;;
        -i|--interval) INTERVAL="$2";     shift 2 ;;
        -b|--branch)   BRANCH="$2";       shift 2 ;;
        -p|--push)     DO_PUSH=true;      shift   ;;
        -h|--help)     show_help                  ;;
        *)
            log_error "Opción desconocida: $1"
            show_help
            ;;
    esac
done

# ─── Validaciones ───
if [[ ! -d "$REPO_PATH/.git" ]]; then
    log_error "No se encontró un repositorio Git en: $REPO_PATH"
    exit 1
fi

# Resolver ruta absoluta
REPO_PATH="$(cd "$REPO_PATH" && pwd)"

# Detectar rama actual si no se especificó
if [[ -z "$BRANCH" ]]; then
    BRANCH="$(git -C "$REPO_PATH" rev-parse --abbrev-ref HEAD)"
fi

# ─── Generar mensaje de commit inteligente basado en el diff ───
generate_commit_msg() {
    local diff_stat diff_content files_list
    diff_stat="$(git -C "$REPO_PATH" diff --cached --stat)"
    diff_content="$(git -C "$REPO_PATH" diff --cached)"
    files_list="$(git -C "$REPO_PATH" diff --cached --name-status)"

    # Clasificar archivos por tipo de cambio
    local new_files=() modified_files=() deleted_files=() renamed_files=()
    while IFS=$'\t' read -r change_type file_name extra; do
        [[ -z "$change_type" ]] && continue
        case "$change_type" in
            A)  new_files+=("$file_name") ;;
            M)  modified_files+=("$file_name") ;;
            D)  deleted_files+=("$file_name") ;;
            R*) renamed_files+=("$file_name -> $extra") ;;
        esac
    done <<< "$files_list"

    # --- Detectar tipo de cambio dominante ---
    local action=""
    local subject=""

    # Si solo hay archivos nuevos
    if [[ ${#new_files[@]} -gt 0 && ${#modified_files[@]} -eq 0 && ${#deleted_files[@]} -eq 0 ]]; then
        action="add"
    # Si solo hay eliminados
    elif [[ ${#deleted_files[@]} -gt 0 && ${#new_files[@]} -eq 0 && ${#modified_files[@]} -eq 0 ]]; then
        action="remove"
    # Si solo hay renombrados
    elif [[ ${#renamed_files[@]} -gt 0 && ${#new_files[@]} -eq 0 && ${#modified_files[@]} -eq 0 && ${#deleted_files[@]} -eq 0 ]]; then
        action="rename"
    else
        action="update"
    fi

    # --- Detectar el contexto según extensiones y contenido ---
    local all_files=("${new_files[@]}" "${modified_files[@]}" "${deleted_files[@]}")
    local extensions=()
    for f in "${all_files[@]}"; do
        local ext="${f##*.}"
        [[ "$ext" != "$f" ]] && extensions+=("$ext")
    done
    # Deduplicar extensiones
    local unique_exts
    unique_exts="$(printf '%s\n' "${extensions[@]}" 2>/dev/null | sort -u | tr '\n' ',' | sed 's/,$//')"

    # --- Detectar patrones específicos en el diff ---
    local context_hints=()

    # Funciones nuevas/modificadas
    local func_changes
    func_changes="$(echo "$diff_content" | grep -E '^\+\s*(function |def |fn |func |public |private |protected |const |let |var |class )' | head -5 || true)"
    if [[ -n "$func_changes" ]]; then
        # Extraer nombres de funciones/clases
        local func_names
        func_names="$(echo "$func_changes" | sed -E 's/^\+\s*//' | sed -E 's/\{.*//;s/\(.*//;s/[:,=].*//' | awk '{print $NF}' | head -3 | tr '\n' ', ' | sed 's/,$//')"
        [[ -n "$func_names" ]] && context_hints+=("$func_names")
    fi

    # Imports/dependencias
    if echo "$diff_content" | grep -qE '^\+\s*(import |from |require\(|#include )'; then
        context_hints+=("dependencias")
    fi

    # Configuración
    local config_files=0
    for f in "${all_files[@]}"; do
        case "$f" in
            *.json|*.yml|*.yaml|*.toml|*.ini|*.cfg|*.conf|*.env*|Makefile|Dockerfile|*.docker*|*config*)
                config_files=$((config_files + 1)) ;;
        esac
    done
    [[ $config_files -gt 0 ]] && context_hints+=("config")

    # Tests
    local test_files=0
    for f in "${all_files[@]}"; do
        case "$f" in
            *test*|*spec*|*__tests__*) test_files=$((test_files + 1)) ;;
        esac
    done
    [[ $test_files -gt 0 ]] && context_hints+=("tests")

    # Docs
    local doc_files=0
    for f in "${all_files[@]}"; do
        case "$f" in
            *.md|*.rst|*.txt|docs/*|README*|CHANGELOG*) doc_files=$((doc_files + 1)) ;;
        esac
    done
    [[ $doc_files -gt 0 ]] && context_hints+=("docs")

    # Fix patterns
    if echo "$diff_content" | grep -qiE '^\+.*(fix|bug|error|patch|hotfix|workaround)'; then
        action="fix"
    fi

    # Refactor patterns
    if echo "$diff_content" | grep -qiE '^\+.*(refactor|cleanup|clean up|reorganize|simplify)'; then
        [[ "$action" != "fix" ]] && action="refactor"
    fi

    # --- Construir mensaje ---
    local total_files=$(( ${#new_files[@]} + ${#modified_files[@]} + ${#deleted_files[@]} + ${#renamed_files[@]} ))

    # Descripción de archivos
    if [[ $total_files -eq 1 ]]; then
        subject="${all_files[0]:-${renamed_files[0]:-archivo}}"
    elif [[ $total_files -le 3 ]]; then
        subject="$(printf '%s\n' "${all_files[@]}" "${renamed_files[@]}" | head -3 | tr '\n' ', ' | sed 's/,$//')"
    else
        # Agrupar por directorio común
        local common_dir
        common_dir="$(printf '%s\n' "${all_files[@]}" | sed 's|/[^/]*$||' | sort -u | head -1)"
        if [[ -n "$common_dir" && "$common_dir" != "${all_files[0]}" ]]; then
            subject="${common_dir}/ (${total_files} archivos)"
        else
            subject="${total_files} archivos [${unique_exts}]"
        fi
    fi

    # Agregar contexto
    local context_str=""
    if [[ ${#context_hints[@]} -gt 0 ]]; then
        context_str=" ($(IFS=', '; echo "${context_hints[*]}"))"
    fi

    # Líneas añadidas/eliminadas
    local insertions deletions
    insertions="$(echo "$diff_stat" | tail -1 | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' || echo 0)"
    deletions="$(echo "$diff_stat" | tail -1 | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' || echo 0)"
    local delta="+${insertions}/-${deletions}"

    echo "${action}: ${subject}${context_str} [${delta}]"
}

# ─── Función principal: detectar, commitear y pushear ───
run_cycle() {
    log_info "Revisando repositorio: $REPO_PATH (rama: $BRANCH)"

    # Fetch para comparar con remoto
    if git -C "$REPO_PATH" remote get-url origin &>/dev/null; then
        log_info "Obteniendo cambios remotos (fetch)..."
        git -C "$REPO_PATH" fetch origin "$BRANCH" 2>/dev/null || log_warn "No se pudo hacer fetch (¿sin conexión?)"
    fi

    # Verificar si hay cambios locales (tracked modificados + untracked)
    local status
    status="$(git -C "$REPO_PATH" status --porcelain)"

    if [[ -z "$status" ]]; then
        log_info "No hay cambios locales."

        # Comparar con remoto
        local local_hash remote_hash
        local_hash="$(git -C "$REPO_PATH" rev-parse HEAD 2>/dev/null || echo "")"
        remote_hash="$(git -C "$REPO_PATH" rev-parse "origin/$BRANCH" 2>/dev/null || echo "")"

        if [[ -n "$remote_hash" && "$local_hash" != "$remote_hash" ]]; then
            local ahead behind
            ahead="$(git -C "$REPO_PATH" rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo 0)"
            behind="$(git -C "$REPO_PATH" rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo 0)"
            [[ "$ahead" -gt 0 ]] && log_warn "La rama local está $ahead commit(s) adelante del remoto."
            [[ "$behind" -gt 0 ]] && log_warn "La rama local está $behind commit(s) detrás del remoto."
        else
            log_ok "Repositorio sincronizado con el remoto."
        fi
        return 0
    fi

    # Mostrar resumen de cambios
    local modified added deleted
    modified="$(echo "$status" | grep -c '^ M\| M ' || true)"
    added="$(echo "$status" | grep -c '^??' || true)"
    deleted="$(echo "$status" | grep -c '^ D\| D ' || true)"

    log_info "Cambios detectados: ${modified} modificado(s), ${added} nuevo(s), ${deleted} eliminado(s)"
    echo "$status" | while IFS= read -r line; do
        echo -e "  ${YELLOW}${line}${NC}"
    done

    # Agregar todos los cambios
    git -C "$REPO_PATH" add -A

    # Generar mensaje de commit si no se proporcionó uno
    local msg="$COMMIT_MSG"
    if [[ -z "$msg" ]]; then
        msg="$(generate_commit_msg)"
    fi

    # Commitear
    git -C "$REPO_PATH" commit -m "$msg"
    log_ok "Commit creado: $msg"

    # Push si se solicitó
    if [[ "$DO_PUSH" == true ]]; then
        log_info "Pusheando a origin/$BRANCH..."
        local attempt=0
        local max_retries=4
        local wait_time=2

        while [[ $attempt -lt $max_retries ]]; do
            if git -C "$REPO_PATH" push -u origin "$BRANCH" 2>/dev/null; then
                log_ok "Push exitoso a origin/$BRANCH"
                return 0
            fi
            attempt=$((attempt + 1))
            if [[ $attempt -lt $max_retries ]]; then
                log_warn "Push falló. Reintentando en ${wait_time}s... (intento $attempt/$max_retries)"
                sleep "$wait_time"
                wait_time=$((wait_time * 2))
            fi
        done
        log_error "No se pudo pushear después de $max_retries intentos."
        return 1
    fi
}

# ─── Ejecución ───
if [[ "$AUTO_MODE" == true ]]; then
    log_info "Modo auto-commit activado. Intervalo: cada ${INTERVAL} minuto(s)."
    log_info "Presiona Ctrl+C para detener."
    echo ""

    # Manejar señal de interrupción
    trap 'echo ""; log_info "Auto-commit detenido."; exit 0' INT TERM

    while true; do
        run_cycle
        echo ""
        log_info "Próxima revisión en ${INTERVAL} minuto(s)..."
        sleep "$((INTERVAL * 60))"
    done
else
    run_cycle
fi
